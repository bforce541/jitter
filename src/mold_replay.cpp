#include "jitter/itch_window.hpp"

#include <nlohmann/json.hpp>

#include <arpa/inet.h>
#include <poll.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <charconv>
#include <chrono>
#include <future>
#include <iostream>
#include <limits>
#include <iterator>
#include <memory>
#include <optional>
#include <stdexcept>
#include <system_error>
#include <thread>

namespace {
using namespace jitter::itch;
using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
using Wire = std::vector<std::uint8_t>;

constexpr std::array<std::uint8_t, 10> session{'J', 'I', 'T', 'T', 'E', 'R', '0', '0', '0', '1'};
constexpr std::size_t header_size = 20, max_datagram = 1400;
constexpr unsigned end_count = 65535, max_request = 16;
constexpr Sequence small_start = 32, small_stop = 40, large_start = 64;

std::uint64_t ns(Clock::time_point time) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(time.time_since_epoch()).count();
}

std::uint64_t number(std::string_view text) {
    std::uint64_t value{};
    const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), value);
    if (error != std::errc{} || end != text.data() + text.size()) throw std::runtime_error("invalid number");
    return value;
}

json resources() {
    struct rusage usage {};
    if (getrusage(RUSAGE_SELF, &usage)) throw std::system_error(errno, std::generic_category(), "getrusage");
    const auto nanos = [](const timeval& t) {
        return std::uint64_t{1000000000} * t.tv_sec + std::uint64_t{1000} * t.tv_usec;
    };
#ifdef __APPLE__
    const auto peak = static_cast<std::uint64_t>(usage.ru_maxrss);
#else
    const auto peak = static_cast<std::uint64_t>(usage.ru_maxrss) * 1024;
#endif
    return {{"cpu_ns", nanos(usage.ru_utime) + nanos(usage.ru_stime)}, {"peak_rss_bytes", peak},
            {"voluntary_switches", usage.ru_nvcsw}, {"involuntary_switches", usage.ru_nivcsw}};
}

int checked(int result, const char* action) {
    if (result < 0) throw std::system_error(errno, std::generic_category(), action);
    return result;
}

sockaddr_in local(unsigned port) {
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(port);
    return address;
}

class Socket {
public:
    Socket() : fd_(checked(socket(AF_INET, SOCK_DGRAM, 0), "socket")) {
        auto address = local(0);
        checked(bind(fd_, reinterpret_cast<const sockaddr*>(&address), sizeof(address)), "bind loopback");
        constexpr int buffer = 1 << 20;
        if (setsockopt(fd_, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof(buffer)) < 0 && errno != ENOBUFS)
            checked(-1, "setsockopt");
    }
    ~Socket() { close(fd_); }
    Socket(const Socket&) = delete;
    Socket& operator=(const Socket&) = delete;
    int fd() const { return fd_; }
    unsigned port() const {
        sockaddr_in address{};
        socklen_t size = sizeof(address);
        checked(getsockname(fd_, reinterpret_cast<sockaddr*>(&address), &size), "getsockname");
        return ntohs(address.sin_port);
    }
    void send(Bytes payload, const sockaddr_in& address) const {
        if (sendto(fd_, payload.data(), payload.size(), 0,
                   reinterpret_cast<const sockaddr*>(&address), sizeof(address)) < 0)
            checked(-1, "sendto");
    }
    std::size_t read(std::span<std::uint8_t> payload, sockaddr_in& source) const {
        socklen_t size = sizeof(source);
        const auto count = recvfrom(fd_, payload.data(), payload.size(), MSG_DONTWAIT,
                                    reinterpret_cast<sockaddr*>(&source), &size);
        if (count >= 0) return count;
        if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return 0;
        checked(-1, "recvfrom");
        return 0;
    }
private:
    int fd_;
};

Wire header(Sequence first, unsigned count) {
    Wire result(session.begin(), session.end());
    append_be(result, first, 8);
    append_be(result, count, 2);
    return result;
}

Wire packet(Sequence first, std::span<const Wire> messages) {
    auto out = header(first, messages.size());
    for (Bytes message : messages) {
        append_be(out, message.size(), 2);
        out.insert(out.end(), message.begin(), message.end());
    }
    if (out.size() > max_datagram) throw std::runtime_error("packet exceeds replay datagram budget");
    return out;
}

struct Packet {
    Sequence first{};
    unsigned count{};
    std::array<Bytes, 128> messages{};
};

Packet unpack(Bytes wire) {
    if (wire.size() < header_size || wire.size() > max_datagram ||
        !std::equal(session.begin(), session.end(), wire.begin()))
        throw std::runtime_error("wrong MoldUDP64 session or packet size");
    Packet result;
    result.first = read_be(wire.subspan(10, 8));
    result.count = static_cast<unsigned>(read_be(wire.subspan(18, 2)));
    const auto blocks = result.count == end_count ? 0 : result.count;
    if (!result.first || blocks > result.messages.size() ||
        result.first > std::numeric_limits<Sequence>::max() - blocks)
        throw std::runtime_error("invalid MoldUDP64 message range");
    wire = wire.subspan(header_size);
    for (unsigned i = 0; i < blocks; ++i) {
        if (wire.size() < 2) throw std::runtime_error("truncated MoldUDP64 block length");
        const auto length = read_be(wire.first(2));
        wire = wire.subspan(2);
        if (!length || length > wire.size()) throw std::runtime_error("truncated MoldUDP64 block");
        result.messages[i] = wire.first(length);
        wire = wire.subspan(length);
    }
    if (!wire.empty()) throw std::runtime_error("trailing bytes in MoldUDP64 packet");
    return result;
}

std::vector<Wire> load(const std::filesystem::path& path) {
    std::unique_ptr<std::FILE, decltype(&std::fclose)> stream(std::fopen(path.c_str(), "rb"), std::fclose);
    if (!stream) throw std::runtime_error("cannot open window tape");
    TapeReader reader(stream.get());
    std::vector<Wire> messages;
    while (reader.next()) {
        if (messages.size() == 1000000) throw std::runtime_error("transport window exceeds 1000000 messages");
        const auto bytes = reader.bytes();
        messages.emplace_back(bytes.begin(), bytes.end());
    }
    if (messages.size() <= recovery_offset) throw std::runtime_error("transport window is too short");
    return messages;
}

enum class Scenario { clean, a_only, request, snapshot, reorder, overflow };

Scenario scenario(std::string_view name) {
    if (name == "clean") return Scenario::clean;
    if (name == "a-only") return Scenario::a_only;
    if (name == "re-request") return Scenario::request;
    if (name == "snapshot") return Scenario::snapshot;
    if (name == "reorder") return Scenario::reorder;
    if (name == "overflow") return Scenario::overflow;
    throw std::runtime_error("unknown fault scenario");
}

json send_replay(std::span<char*> args) {
    if (args.size() != 7)
        throw std::runtime_error("send WINDOW FIRST PORT_A PORT_B SCENARIO RATE BATCH");
    const auto messages = load(std::filesystem::path(args[0]) / "messages.itch");
    const auto first = number(args[1]);
    const auto a = number(args[2]), b = number(args[3]);
    const auto fault = scenario(args[4]);
    const auto rate = number(args[5]), batch = number(args[6]);
    if (!first || first > std::numeric_limits<Sequence>::max() - messages.size() ||
        !a || a > 65535 || !b || b > 65535 || a == b || rate < 1000 || rate > 5000000 ||
        !batch || batch > 8 || (8 % batch))
        throw std::runtime_error("invalid sender arguments");
    const auto target_a = local(a), target_b = local(b);
    Socket socket;
    std::vector<Wire> packets;
    const std::span<const Wire> tape(messages);
    for (std::size_t offset = 0; offset < messages.size(); offset += batch)
        packets.push_back(packet(first + offset, tape.subspan(offset, std::min<std::size_t>(batch, messages.size() - offset))));

    std::exception_ptr reply_error;
    std::uint64_t replies = 0;
    const auto before = resources();
    const auto start = Clock::now() + 10ms;
    std::jthread replay_requests([&](std::stop_token stop) {
        try {
            std::array<std::uint8_t, 64> buffer{};
            while (!stop.stop_requested()) {
                pollfd waiter{socket.fd(), POLLIN, 0};
                if (poll(&waiter, 1, 2) <= 0) continue;
                sockaddr_in peer{};
                const auto size = socket.read(buffer, peer);
                if (size != header_size || !std::equal(session.begin(), session.end(), buffer.begin()))
                    continue;
                const auto start_sequence = read_be(Bytes(buffer).subspan(10, 8));
                const auto requested = read_be(Bytes(buffer).subspan(18, 2));
                if (!requested || requested > max_request || start_sequence < first ||
                    start_sequence - first >= tape.size() || start_sequence - first + requested > tape.size())
                    continue;
                for (unsigned offset = 0; offset < requested; offset += batch)
                    socket.send(packet(start_sequence + offset,
                        tape.subspan(start_sequence - first + offset, std::min(batch, requested - offset))), peer);
                ++replies;
            }
        } catch (...) {
            reply_error = std::current_exception();
        }
    });

    socket.send(header(first, 0), target_a);
    socket.send(header(first, 0), target_b);
    std::uint64_t omitted_a = 0, omitted_b = 0;
    for (std::size_t i = 0; i < packets.size(); ++i) {
        const auto offset = i * batch;
        const auto deadline = start + std::chrono::nanoseconds(offset * 1000000000ull / rate);
        if (deadline - Clock::now() > 130us) std::this_thread::sleep_until(deadline - 80us);
        while (Clock::now() < deadline) {}
        const bool small = offset >= small_start && offset < small_stop;
        const bool large = offset >= large_start && offset < recovery_offset;
        if (fault == Scenario::reorder && offset == small_start) continue;
        const auto skip_a = (small && (fault == Scenario::a_only || fault == Scenario::request)) ||
                            (large && (fault == Scenario::snapshot || fault == Scenario::overflow));
        const auto skip_b = (small && fault == Scenario::request) ||
                            (large && (fault == Scenario::snapshot || fault == Scenario::overflow));
        if (skip_a) ++omitted_a;
        else socket.send(packets[i], target_a);
        if (skip_b) ++omitted_b;
        else socket.send(packets[i], target_b);
        if (fault == Scenario::reorder && offset == small_start + batch) {
            socket.send(packets[i - 1], target_a);
            socket.send(packets[i - 1], target_b);
        }
    }
    const auto finish = Clock::now();
    const auto end = header(first + messages.size(), end_count);
    for (int i = 0; i < 3; ++i) {
        socket.send(end, target_a);
        socket.send(end, target_b);
        std::this_thread::sleep_for(8ms);
    }
    replay_requests.request_stop();
    replay_requests.join();
    if (reply_error) std::rethrow_exception(reply_error);
    auto after = resources();
    for (const auto* key : {"cpu_ns", "voluntary_switches", "involuntary_switches"})
        after[key] = after[key].get<std::uint64_t>() - before[key].get<std::uint64_t>();
    return {{"first", first}, {"messages", messages.size()}, {"start_ns", ns(start)},
            {"active_ns", ns(finish) - ns(start)}, {"batch", batch}, {"rate", rate},
            {"omitted_datagrams_a", omitted_a}, {"omitted_datagrams_b", omitted_b},
            {"retransmission_responses", replies}, {"process", after}};
}

struct Observation {
    Sequence sequence{};
    std::uint64_t received{}, published{};
};

class Receiver {
public:
    Receiver(Snapshot start, Sequence final, std::size_t capacity)
        : book_(restore(start)), next_(start.through + 1), initial_(start.through),
          final_(final), capacity_(capacity), announced_(start.through) {
        times.reserve(final - initial_);
        mark("initial");
    }

    void ingest(const Packet& packet, Clock::time_point received, unsigned feed) {
        if (packet.count == end_count || packet.count == 0) {
            if (packet.first > final_ + 1) throw std::runtime_error("control packet exceeds expected window");
            announced_ = std::max(announced_, packet.first - 1);
            if (announced_ >= next_ && !gap_at_) stale("gap");
            return;
        }
        for (unsigned index = 0; index < packet.count; ++index) {
            const Sequence sequence = packet.first + index;
            if (sequence <= initial_ || sequence > final_) throw std::runtime_error("packet outside window");
            if (sequence < next_) {
                ++duplicates;
                continue;
            }
            const auto message = decode(packet.messages[index]);
            announced_ = std::max(announced_, sequence);
            if (!gap_at_ && sequence == next_) {
                apply(message, sequence);
                if (feed == 0) ++published_a;
                else ++published_b;
                times.push_back({sequence, ns(received), ns(Clock::now())});
                continue;
            }
            if (!gap_at_) stale("gap");
            const auto found = pending_.find(sequence);
            if (found != pending_.end()) {
                if (found->second != message) {
                    snapshot_floor_ = std::max(snapshot_floor_, sequence);
                    pending_.erase(found);
                    ++conflicts;
                } else ++duplicates;
            } else if (pending_.size() == capacity_) {
                snapshot_floor_ = std::max(snapshot_floor_, sequence);
                ++discarded;
            } else {
                pending_.emplace(sequence, message);
                peak_pending = std::max(peak_pending, pending_.size());
            }
        }
        if (gap_at_ && !snapshot_floor_ && contiguous(next_)) catch_up();
    }

    std::pair<Sequence, Sequence> missing_range() const {
        auto first = next_;
        for (const auto& [sequence, message] : pending_) {
            if (sequence != first) return {first, sequence - first};
            ++first;
        }
        return {first, announced_ + 1 - first};
    }
    bool has_gap() const { return gap_at_.has_value(); }
    bool done() const { return next_ == final_ + 1 && !gap_at_; }
    bool can_request(Clock::time_point now) const {
        if (!gap_at_ || now - *gap_at_ < 1ms || snapshot_floor_) return false;
        const auto [first, count] = missing_range();
        return count && count <= max_request && first != requested_;
    }
    Wire request() {
        const auto [first, count] = missing_range();
        ++requests;
        requested_ = first;
        mark("re-request");
        return header(first, static_cast<unsigned>(count));
    }
    bool needs_snapshot() const {
        return gap_at_ && (snapshot_floor_ || missing_range().second > max_request);
    }
    void starting_snapshot() { mark("snapshot-requested"); }
    bool install(Sequence through, Book& candidate) {
        if (!gap_at_ || through < next_ - 1 || through < snapshot_floor_ || through > final_ ||
            through > announced_ || !contiguous(through + 1)) return false;
        for (auto it = pending_.upper_bound(through); it != pending_.end(); ++it)
            if (!candidate.apply(it->second)) throw std::runtime_error("snapshot suffix is invalid");
        skipped += through + 1 - next_;
        const auto buffered = std::distance(pending_.upper_bound(through), pending_.end());
        replayed += buffered;
        applied += buffered;
        book_ = std::move(candidate);
        next_ = announced_ + 1;
        // Preserve the size of the validated suffix before releasing the buffer.
        verified_suffix = buffered;
        pending_.clear();
        snapshot_floor_ = 0;
        recovered("snapshot-applied");
        return true;
    }
    Orders orders() const { return book_.orders(); }
    Sequence last() const { return next_ - 1; }

    std::vector<Observation> times;
    json timeline = json::array();
    std::uint64_t applied{}, replayed{}, skipped{}, requests{}, conflicts{}, duplicates{}, discarded{},
                  published_a{}, published_b{}, recovery_ns{}, verified_suffix{};
    std::size_t peak_pending{};

private:
    bool contiguous(Sequence from) const {
        if (from > announced_) return true;
        const auto first = pending_.lower_bound(from);
        // Pending keys are unique and never exceed announced_.
        const auto size = first == pending_.begin() ? pending_.size() : std::distance(first, pending_.end());
        return first != pending_.end() && first->first == from &&
               static_cast<Sequence>(size) == announced_ - from + 1;
    }
    void apply(const Message& message, Sequence sequence) {
        if (!book_.apply(message))
            throw std::runtime_error("invalid book update at MoldUDP64 sequence " + std::to_string(sequence));
        ++next_;
        ++applied;
    }
    void catch_up() {
        for (const auto& [sequence, message] : pending_) {
            apply(message, sequence);
            ++replayed;
        }
        pending_.clear();
        recovered("gap-filled");
    }
    void stale(const char* event) {
        gap_at_ = Clock::now();
        mark(event);
    }
    void recovered(const char* event) {
        recovery_ns += ns(Clock::now()) - ns(*gap_at_);
        gap_at_.reset();
        mark(event);
    }
    void mark(const char* event) {
        if (timeline.size() < 48)
            timeline.push_back({{"event", event}, {"sequence", last()}, {"received_through", announced_},
                                {"buffered", pending_.size()}, {"stale", has_gap()}, {"time_ns", ns(Clock::now())}});
    }
    Book book_;
    Sequence next_, initial_, final_;
    std::size_t capacity_;
    Sequence announced_, requested_{}, snapshot_floor_{};
    std::map<Sequence, Message> pending_;
    std::optional<Clock::time_point> gap_at_;
};

struct Repair { Sequence sequence; Book book; };

json receive_replay(std::span<char*> args) {
    if (args.size() != 5) throw std::runtime_error("receive WINDOW SCENARIO CAPACITY TIMEOUT_MS TIMINGS");
    const std::filesystem::path window = args[0];
    const auto fault = scenario(args[1]);
    const auto capacity = number(args[2]), timeout_ms = number(args[3]);
    if (!capacity || capacity > 1000000 || timeout_ms < 100 || timeout_ms > 1200000)
        throw std::runtime_error("invalid receiver arguments");
    const auto initial = read_snapshot(window / "initial.snapshot");
    const auto expected = read_snapshot(window / "final.snapshot");
    if (expected.through <= initial.through || expected.through - initial.through > 1000000 ||
        expected.through == std::numeric_limits<Sequence>::max())
        throw std::runtime_error("invalid expected transport sequence range");
    Receiver receiver(initial, expected.through, capacity);
    Socket channels[2];
    std::cout << json{{"port_a", channels[0].port()}, {"port_b", channels[1].port()}}.dump() << std::endl;
    const auto before = resources();
    const auto start = Clock::now();
    std::optional<Clock::time_point> end;
    std::array<std::uint8_t, max_datagram + 1> bytes{};
    std::future<Repair> reading;
    std::optional<Repair> repair;
    bool started_snapshot = false;
    std::uint64_t datagrams[2]{};
    while (!receiver.done() && Clock::now() - start < std::chrono::milliseconds(timeout_ms)) {
        pollfd waiters[]{ {channels[0].fd(), POLLIN, 0}, {channels[1].fd(), POLLIN, 0} };
        if (poll(waiters, 2, 1) < 0 && errno != EINTR) checked(-1, "poll");
        for (unsigned feed = 0; feed < 2; ++feed) {
            sockaddr_in peer{};
            for (int i = 0; i < 32; ++i) {
                const auto count = channels[feed].read(bytes, peer);
                if (!count) break;
                const auto received = Clock::now();
                ++datagrams[feed];
                const auto data = unpack(Bytes(bytes).first(count));
                receiver.ingest(data, received, feed);
                if (data.count == end_count && !end) end = received;
                if (receiver.can_request(received))
                    channels[feed].send(receiver.request(), peer);
            }
        }
        if (fault == Scenario::snapshot && receiver.needs_snapshot() && !started_snapshot) {
            started_snapshot = true;
            receiver.starting_snapshot();
            reading = std::async(std::launch::async, [window] {
                const auto snap = read_snapshot(window / "repair.snapshot");
                return Repair{snap.through, restore(snap)};
            });
        }
        if (reading.valid() && reading.wait_for(0ms) == std::future_status::ready) repair = reading.get();
        if (repair && receiver.install(repair->sequence, repair->book)) repair.reset();
        if (end && Clock::now() - *end > 200ms) break;
    }
    const auto finish = Clock::now();
    auto after = resources();
    for (const auto* key : {"cpu_ns", "voluntary_switches", "involuntary_switches"})
        after[key] = after[key].get<std::uint64_t>() - before[key].get<std::uint64_t>();
    const auto orders = receiver.orders();
    const auto verified = receiver.done() && orders == expected.orders;
    if (std::string_view(args[4]) != "-") {
        std::ofstream log(args[4], std::ios::binary);
        log.exceptions(std::ios::badbit | std::ios::failbit);
        Wire row;
        row.reserve(24);
        for (const auto& item : receiver.times) {
            row.clear();
            append_be(row, item.sequence, 8);
            append_be(row, item.received, 8);
            append_be(row, item.published, 8);
            log.write(reinterpret_cast<const char*>(row.data()), row.size());
        }
        log.close();
    }
    return {{"verified", verified}, {"stale", receiver.has_gap()}, {"through", receiver.last()},
            {"expected_through", expected.through}, {"expected_fingerprint", fingerprint(expected.orders)},
            {"book_fingerprint", fingerprint(orders)}, {"applied", receiver.applied},
            {"replayed", receiver.replayed}, {"snapshot_covered", receiver.skipped},
            {"snapshot_buffered_suffix", receiver.verified_suffix},
            {"published_immediately_a", receiver.published_a}, {"published_immediately_b", receiver.published_b},
            {"retransmission_requests", receiver.requests}, {"duplicates", receiver.duplicates},
            {"conflicts", receiver.conflicts}, {"discarded", receiver.discarded}, {"peak_buffered", receiver.peak_pending},
            {"recovery_ns", receiver.recovery_ns}, {"elapsed_ns", ns(finish) - ns(start)},
            {"datagrams_a", datagrams[0]}, {"datagrams_b", datagrams[1]}, {"process", after},
            {"timeline", receiver.timeline}};
}
}

int main(int argc, char** argv) {
    try {
        const std::string_view command = argc > 1 ? argv[1] : "";
        if (command == "send") std::cout << send_replay({argv + 2, static_cast<std::size_t>(argc - 2)}).dump() << '\n';
        else if (command == "receive") std::cout << receive_replay({argv + 2, static_cast<std::size_t>(argc - 2)}).dump() << '\n';
        else throw std::runtime_error("choose send or receive");
    } catch (const std::exception& error) {
        std::cerr << "jitter_mold: " << error.what() << '\n';
        return 1;
    }
}
