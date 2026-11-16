#include "jitter/receiver.hpp"

#include <nlohmann/json.hpp>
#include <arpa/inet.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <charconv>
#include <iostream>
#include <system_error>
#include <thread>

using namespace jitter::market;
using json = nlohmann::json;
using namespace std::chrono_literals;

namespace {
using Wire = std::array<std::uint8_t, 24>;

std::int64_t ns(ReceiverTime start, ReceiverTime end) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();
}

Wire encode(const Event& event) {
    Wire wire{};
    auto put = [&](unsigned start, unsigned width, std::uint64_t n) {
        for (auto i = start + width; i > start; n >>= 8) wire[--i] = n & 255;
    };
    put(0, 8, event.sequence);
    put(8, 4, event.id);
    put(12, 4, event.order.tick);
    put(16, 4, event.order.quantity);
    wire[20] = static_cast<std::uint8_t>(event.kind);
    wire[21] = static_cast<std::uint8_t>(event.order.side);
    wire[22] = 1;
    return wire;
}

std::optional<Event> decode(const Wire& wire) {
    auto get = [&](unsigned start, unsigned width) {
        std::uint64_t n{};
        for (auto i = start; i < start + width; ++i) n = (n << 8) | wire[i];
        return n;
    };
    const auto seq = get(0, 8);
    const auto id = static_cast<std::uint32_t>(get(8, 4));
    if (!seq || seq == UINT64_MAX || id == 0 || id >= id_limit ||
        wire[20] > 2 || wire[21] > 1 || wire[22] != 1 || wire[23]) return {};
    return Event{seq, static_cast<Kind>(wire[20]), id,
                 {static_cast<Side>(wire[21]), static_cast<std::uint32_t>(get(12, 4)),
                  static_cast<std::uint32_t>(get(16, 4))}};
}

void checked(int result, const char* operation) {
    if (result < 0) throw std::system_error(errno, std::generic_category(), operation);
}

class Socket {
public:
    Socket() : fd(socket(AF_INET, SOCK_DGRAM, 0)) { checked(fd, "socket"); }
    ~Socket() { close(fd); }
    Socket(const Socket&) = delete;
    Socket& operator=(const Socket&) = delete;
    const int fd;
};

struct Channel {
    Socket rx, tx;
    std::size_t sent{}, received{}, malformed{};
    Channel() {
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        checked(bind(rx.fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)), "bind loopback");
        socklen_t size = sizeof(addr);
        checked(getsockname(rx.fd, reinterpret_cast<sockaddr*>(&addr), &size), "getsockname");
        checked(connect(tx.fd, reinterpret_cast<sockaddr*>(&addr), size), "connect");
        // Receive only from this simulator's particular sender.
        checked(getsockname(tx.fd, reinterpret_cast<sockaddr*>(&addr), &size), "sender getsockname");
        checked(connect(rx.fd, reinterpret_cast<sockaddr*>(&addr), size), "connect receiver");
    }
    void send_event(Event event) {
        const auto wire = encode(event);
        checked(static_cast<int>(send(tx.fd, wire.data(), wire.size(), 0)), "send UDP");
        ++sent;
    }
    void drain(Receiver& receiver, std::vector<Publication>& output, char channel, std::optional<ReceiverTime> policy = {}) {
        while (true) {
            // Extra byte detects both oversized and truncated datagrams portably.
            std::array<std::uint8_t, Wire{}.size() + 1> bytes{};
            const auto size = recv(rx.fd, bytes.data(), bytes.size(), MSG_DONTWAIT);
            if (size < 0) {
                if (errno == EINTR) continue;
                if (errno == EAGAIN || errno == EWOULDBLOCK) return;
                checked(-1, "recv UDP");
            }
            const auto arrived = ReceiverClock::now();
            ++received;
            Wire wire;
            std::copy_n(bytes.begin(), wire.size(), wire.begin());
            const auto event = size == static_cast<ssize_t>(wire.size()) ? decode(wire) : std::nullopt;
            if (event) {
                const auto decoded = ReceiverClock::now();
                receiver.receive({*event, arrived, decoded, channel}, policy.value_or(decoded), output);
            }
            else ++malformed;
        }
    }
};

json distribution(std::vector<std::int64_t> data) {
    std::sort(data.begin(), data.end());
    auto q = [&](std::size_t permille, std::size_t minimum) -> json {
        return data.size() < minimum ? json(nullptr) : json(data[(data.size() * permille + 999) / 1000 - 1]);
    };
    return {{"samples", data.size()}, {"p50", q(500, 40)}, {"p99", q(990, 2000)}, {"p999", q(999, 20000)}};
}

struct Options {
    bool benchmark{}, dual{true};
    std::size_t count{12}, burst{1}, seed{17}, rate{100000};
};

json run(Options options) {
    const auto input = generate(options.count, options.seed);
    Feed<ReferenceBook> oracle;
    if (!oracle.snapshot(0, input.initial)) throw std::logic_error("initial oracle");
    std::vector<Update> expected;
    Entries recovery_snapshot, stale_snapshot, recovered_reference;
    for (const auto& event : input.events) {
        const auto publication = oracle.on_event(event);
        if (publication.status != Status::published) throw std::logic_error("invalid simulator input");
        expected.push_back(publication);
        if (event.sequence == 4) stale_snapshot = oracle.book().entries();
        if (event.sequence == 5) recovery_snapshot = oracle.book().entries();
        if (event.sequence == 7) recovered_reference = oracle.book().entries();
    }
    Channel a, b;
    Receiver receiver(256, 3ms);
    std::vector<Publication> output;
    output.reserve(options.count);
    if (!receiver.initialize(input.initial)) throw std::logic_error("initial snapshot");
    json timeline = json::array();
    const auto start = ReceiverClock::now();
    auto note = [&](std::string_view operation, bool recovering = false) {
        timeline.push_back({{"operation", operation}, {"elapsed_ns", ns(start, ReceiverClock::now())},
                            {"current", receiver.current()}, {"state", recovering ? "recovering" : receiver.current() ? "current" : receiver.waiting() ? "waiting-gap" : "stale"}, {"reason", receiver.reason()},
                            {"sequence", receiver.last()}, {"backlog", receiver.backlog()}});
    };
    auto send = [&](std::size_t seq, bool to_a, bool to_b) {
        if (to_a) a.send_event(input.events.at(seq - 1));
        if (to_b && options.dual) b.send_event(input.events.at(seq - 1));
    };
    auto drain = [&](bool wait = false, std::optional<ReceiverTime> policy = {}) {
        const auto deadline = ReceiverClock::now() + 250ms;
        do {
            a.drain(receiver, output, 'A', policy);
            b.drain(receiver, output, 'B', policy);
            if (!wait || (a.sent == a.received && b.sent == b.received)) return true;
            if (ReceiverClock::now() >= deadline) return false;
            pollfd sockets[] = {{a.rx.fd, POLLIN, 0}, {b.rx.fd, POLLIN, 0}};
            if (poll(sockets, 2, 2) < 0 && errno != EINTR) checked(-1, "poll");
        } while (true);
    };
    auto step = [&](ReceiverTime policy) {
        if (!drain(true, policy)) throw std::logic_error("scheduled local datagram not received");
    };
    std::optional<std::int64_t> detection, recovery;
    if (options.benchmark) {
        for (std::size_t i = 1; i <= options.count; i += options.burst) {
            const auto release = start + std::chrono::nanoseconds((i - 1) * 1000000000ULL / options.rate);
            if (release - ReceiverClock::now() > 150us) std::this_thread::sleep_until(release - 100us);
            while (ReceiverClock::now() < release) {}
            for (auto j = i; j <= options.count && j < i + options.burst; ++j) send(j, true, true);
            drain();
        }
        drain(true);
        receiver.expire(ReceiverClock::now());
    } else {
        const auto policy = ReceiverTime{};
        note("book initialized");
        send(1, true, true); step(policy);
        send(2, !options.dual, true); step(policy);
        note(options.dual ? "feed A loss repaired by feed B" : "single feed delivered sequence 2");
        send(4, true, true); step(policy);
        note("sequence 4 held while awaiting 3");
        send(4, true, false); step(policy);
        send(3, true, true); step(policy);
        note("reordered and duplicate packets drained");
        const auto missing = ReceiverClock::now();
        send(6, true, true); step(policy);
        std::this_thread::sleep_for(4ms);
        receiver.expire(policy + 4ms);
        const auto detected = ReceiverClock::now();
        detection = ns(missing, detected);
        if (receiver.current()) throw std::logic_error("gap failed to mark book stale");
        note("sequence 5 missing on all enabled feeds");
        if (receiver.recover(4, stale_snapshot, output) != "remaining-gap")
            throw std::logic_error("stale snapshot escaped gap");
        note("snapshot through 4 rejected: sequence 5 still missing");
        send(7, true, true); step(policy + 4ms);
        if (receiver.last() != 4) throw std::logic_error("publication while stale");
        note("sequence 7 buffered while stale");
        note("validating snapshot and buffered updates", true);
        if (receiver.recover(5, recovery_snapshot, output) != "snapshot-applied" ||
            receiver.book().entries() != recovered_reference || receiver.last() != 7 ||
            output.size() != 6 || output[4].update != expected[5] || output[5].update != expected[6])
            throw std::logic_error("snapshot or buffered replay differs from reference");
        const auto recovered = ReceiverClock::now();
        recovery = ns(detected, recovered);
        note("snapshot through 5 installed; 6 and 7 replayed");
        for (std::size_t i = 8; i <= options.count; ++i) { send(i, true, true); step(policy + 4ms); }
        note("replay complete");
    }
    const auto end = ReceiverClock::now();
    std::size_t next{1}, duplicate_publications{}, a_publications{}, b_publications{};
    bool quotes_match = true;
    std::vector<bool> published_sequences(options.count + 1);
    std::vector<std::int64_t> decode_ns, handoff_ns, apply_ns, receipt_ns;
    json publications = json::array();
    for (const auto& item : output) {
        if (!options.benchmark && next == 5) ++next;
        const auto sequence = item.update.sequence;
        if (sequence > options.count) throw std::logic_error("publication outside input");
        if (published_sequences[sequence]) ++duplicate_publications;
        published_sequences[sequence] = true;
        quotes_match = quotes_match && sequence == next && item.update == expected.at(sequence - 1);
        ++next;
        if (item.channel == 'A') ++a_publications;
        else ++b_publications;
        if (!options.benchmark)
            publications.push_back({{"sequence", item.update.sequence}, {"replayed", item.replayed}, {"channel", std::string(1, item.channel)},
                                    {"elapsed_ns", ns(start, item.published)},
                                    {"bid_tick", item.update.top.bid.tick}, {"ask_tick", item.update.top.ask.tick}});
        if (item.replayed) continue;
        decode_ns.push_back(ns(item.received, item.decoded));
        handoff_ns.push_back(ns(item.decoded, item.apply_started));
        apply_ns.push_back(ns(item.apply_started, item.published));
        receipt_ns.push_back(ns(item.received, item.published));
    }
    const bool verified = receiver.current() && quotes_match && duplicate_publications == 0 &&
        receiver.last() == options.count && next == options.count + 1 && receiver.book().entries() == oracle.book().entries();
    if (!options.benchmark && (options.dual ? b_publications != 1 : b_publications != 0))
        throw std::logic_error("unexpected feed repair attribution");
    auto channel = [](const Channel& c) {
        return json{{"sent", c.sent}, {"received", c.received}, {"missing_datagrams", c.sent - c.received},
                    {"malformed", c.malformed}};
    };
    auto optional = [](auto n) { return n ? json(*n) : json(nullptr); };
    return {{"schema", 1}, {"mode", options.benchmark ? "benchmark" : "incident"}, {"dual", options.dual},
            {"events", options.count}, {"seed", options.seed}, {"burst", options.burst}, {"offered_rate", options.benchmark ? json(options.rate) : json(nullptr)}, {"input_id", fingerprint(input)},
            {"elapsed_ns", ns(start, end)}, {"current", receiver.current()}, {"sequence", receiver.last()},
            {"book_verified", verified}, {"published", output.size()}, {"duplicates_suppressed", receiver.duplicates()},
            {"duplicate_publications", duplicate_publications}, {"feed_a_publications", a_publications}, {"feed_b_publications", b_publications}, {"discarded_buffer", receiver.discarded()}, {"peak_reorder", receiver.peak_backlog()},
            {"feed_a_scheduled_omissions", options.benchmark ? 0 : options.dual ? 2 : 1}, {"feed_b_scheduled_omissions", options.benchmark || !options.dual ? 0 : 1},
            {"feed_a", channel(a)}, {"feed_b", channel(b)}, {"detection_ns", optional(detection)},
            {"recovery_ns", optional(recovery)}, {"receipt_to_publication_ns", distribution(std::move(receipt_ns))},
            {"decode_ns", distribution(std::move(decode_ns))}, {"sequencing_wait_ns", distribution(std::move(handoff_ns))},
            {"apply_and_publish_ns", distribution(std::move(apply_ns))}, {"timeline", timeline},
            {"publications", publications}};
}
std::size_t number(std::string_view s) {
    std::size_t n{};
    auto [last, err] = std::from_chars(s.data(), s.data() + s.size(), n);
    if (err != std::errc{} || last != s.data() + s.size()) throw std::invalid_argument("invalid numeric option");
    return n;
}
}

int main(int argc, char** argv) {
    try {
        Options options;
        bool count_set = false;
        for (int i = 1; i < argc; ++i) {
            const std::string_view arg(argv[i]);
            if (arg == "--benchmark") { options.benchmark = true; }
            else if (arg == "--single-feed") options.dual = false;
            else if ((arg == "--events" || arg == "--seed" || arg == "--burst" || arg == "--rate") && i + 1 < argc) {
                const auto n = number(argv[++i]);
                if (arg == "--events") { options.count = n; count_set = true; }
                else if (arg == "--seed") options.seed = n;
                else if (arg == "--rate") options.rate = n;
                else options.burst = n;
            } else throw std::invalid_argument("use [--single-feed] [--benchmark --events N --burst N --rate HZ] [--seed N]");
        }
        if (options.benchmark && !count_set) options.count = 20000;
        if (!options.rate || options.rate > 1000000) throw std::invalid_argument("rate must be 1..1000000");
        if (options.count < 8 || options.count > 1000000 || !options.burst || options.burst > 64)
            throw std::invalid_argument("events must be 8..1000000 and burst 1..64");
        const auto result = run(options);
        std::cout << result.dump() << '\n';
        return result.at("book_verified").get<bool>() ? 0 : 2;
    } catch (const std::exception& error) {
        std::cerr << "incident: " << error.what() << '\n';
        return 1;
    }
}
