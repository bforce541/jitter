#include "jitter/market.hpp"

#include <charconv>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string_view>
#include <thread>

using namespace jitter::market;
using Clock = std::chrono::steady_clock;
using Nanoseconds = std::chrono::nanoseconds;

namespace {
std::int64_t ns(Clock::duration duration) { return std::chrono::duration_cast<Nanoseconds>(duration).count(); }
struct Options {
    std::uint64_t seed{17}, count{100000}, warmup{20000}, rate{100000}, burst{1}, capacity{4096};
    bool feed{};
    std::string_view book{"reference"};
    std::uint64_t profile_ms{};
};
std::uint64_t integer(std::string_view value) {
    std::uint64_t out;
    const auto [last, ec] = std::from_chars(value.data(), value.data() + value.size(), out);
    if (ec != std::errc{} || last != value.data() + value.size()) throw std::runtime_error("invalid integer");
    return out;
}
Options parse(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; i += 2) {
        if (i + 1 == argc) throw std::runtime_error("options take a value; see --help");
        const std::string_view k = argv[i], v = argv[i+1];
        if (k == "--mode" && (v == "direct" || v == "feed")) o.feed = v == "feed";
        else if (k == "--book" && (v == "reference" || v == "dense" || v == "cached")) o.book = v;
        else if (k == "--seed") o.seed = integer(v);
        else if (k == "--events") o.count = integer(v);
        else if (k == "--warmup") o.warmup = integer(v);
        else if (k == "--rate") o.rate = integer(v);
        else if (k == "--burst") o.burst = integer(v);
        else if (k == "--capacity") o.capacity = integer(v);
        else if (k == "--profile-ms") o.profile_ms = integer(v);
        else throw std::runtime_error("unknown option or value: " + std::string(k));
    }
    if (o.count < 1 || o.count > 2000000 || o.warmup > 200000 || o.rate < 1000 || o.rate > 10000000 ||
        o.burst < 1 || o.burst > 1024 || o.capacity < 1 || o.capacity > 1048576 || o.profile_ms > 30000 ||
        (o.feed && (o.profile_ms || o.count % o.burst || o.warmup % o.burst)))
        throw std::runtime_error("out-of-range option or feed event counts not divisible by burst");
    return o;
}

struct Timing {
    Update update;
    std::int64_t scheduled{}, arrival{}, published{};
};
struct Result {
    std::vector<Timing> samples;
    Entries final;
    std::size_t peak_depth{}, dropped{};
    std::size_t warmup_dropped{};
    std::int64_t elapsed{};
};
struct Item {
    std::size_t index;
    std::int64_t scheduled, arrival;
};

// Bounded, preallocated FIFO. Never waits for space; overload remains visible.
class Pipeline {
public:
    explicit Pipeline(std::size_t size) : queue_(size) {}
    bool push(Item item, std::size_t& peak) {
        {
            std::lock_guard lock(mutex_);
            if (size_ == queue_.size()) return false;
            queue_[(head_ + size_) % queue_.size()] = item;
            peak = std::max(peak, ++size_);
        }
        ready_.notify_one();
        return true;
    }
    bool pop(Item& item) {
        std::unique_lock lock(mutex_);
        ready_.wait(lock, [&] { return size_ || done_; });
        if (!size_) return false;
        item = queue_[head_];
        if (++head_ == queue_.size()) head_ = 0;
        --size_;
        return true;
    }
    void close() {
        { std::lock_guard lock(mutex_); done_ = true; }
        ready_.notify_one();
    }
private:
    std::vector<Item> queue_;
    std::mutex mutex_;
    std::condition_variable ready_;
    std::size_t head_{}, size_{};
    bool done_{};
};

template<class Book>
void profile(const Replay& replay, const Options& o) {
    const auto end = Clock::now() + std::chrono::milliseconds(o.profile_ms);
    std::uint64_t sum{};
    std::size_t total{};
    do {
        Book book;
        for (const auto& [id, order] : replay.initial) book.apply({0, Kind::add, id, order});
        for (const auto& event : replay.events) {
            if (!book.apply(event)) throw std::runtime_error("invalid profile workload");
            const auto top = book.top();
            sum = sum * 31 + top.bid.quantity + top.ask.quantity;
        }
        total += replay.events.size();
    } while (Clock::now() < end);
    std::cout << "{\"mode\":\"profile\",\"events\":" << total << ",\"digest\":" << sum << "}\n";
}

template<class Book>
Result direct(const Replay& replay, const Options& o) {
    Book book;
    for (const auto& [id, order] : replay.initial) book.apply({0, Kind::add, id, order});
    for (std::size_t i = 0; i < o.warmup; ++i)
        if (!book.apply(replay.events[i])) throw std::runtime_error("invalid direct warmup");
    Result out;
    out.samples.resize(o.count);
    const auto origin = Clock::now();
    for (std::size_t i = o.warmup; i < replay.events.size(); ++i) {
        const auto start = ns(Clock::now() - origin);
        auto& row = out.samples[i - o.warmup];
        const auto& event = replay.events[i];
        const auto applied = book.apply(event);
        row.update = {event.sequence, applied ? Status::published : Status::invalid, book.top()};
        row.published = ns(Clock::now() - origin);
        row.arrival = row.scheduled = start;
    }
    out.elapsed = ns(Clock::now() - origin);
    out.final = book.entries();
    return out;
}

template<class Book>
Result feed(const Replay& replay, const Options& o) {
    Feed<Book> handler;
    if (!handler.snapshot(0, replay.initial)) throw std::runtime_error("invalid initial snapshot");
    Result out;
    // Each input has a preallocated slot. An empty sequence identifies an input
    // that was never queued; the consumer owns writes to admitted slots.
    out.samples.resize(o.count);
    auto phase = [&](std::size_t offset, std::size_t count, bool measured) {
        if (!count) return;
        Pipeline queue(o.capacity);
        Clock::time_point origin;
        std::size_t drops{}, max_depth{};
        std::int64_t last_update{};
        std::thread worker([&] {
            Item item{};
            while (queue.pop(item)) {
                const auto update = handler.on_event(replay.events[item.index]);
                if (measured) {
                    auto& row = out.samples[item.index - offset];
                    row.update = update;
                    row.published = last_update = ns(Clock::now() - origin);
                    row.scheduled = item.scheduled;
                    row.arrival = item.arrival;
                }
            }
        });
        // Store the origin before enqueueing the first item; the queue mutex
        // publishes it to the consumer along with the descriptor.
        origin = Clock::now() + std::chrono::milliseconds(2);
        for (std::size_t i = 0; i < count; i += o.burst) {
            const auto release = static_cast<std::int64_t>(i * 1000000000ULL / o.rate);
            const auto deadline = origin + Nanoseconds(release);
            if (deadline - Clock::now() > std::chrono::microseconds(150))
                std::this_thread::sleep_until(deadline - std::chrono::microseconds(100));
            while (Clock::now() < deadline) {}
            for (std::size_t j = 0; j < o.burst; ++j) {
                const auto arrival = ns(Clock::now() - origin);
                if (!queue.push({offset + i + j, release, arrival}, max_depth)) ++drops;
            }
        }
        const auto producer_end = ns(Clock::now() - origin);
        queue.close();
        worker.join();
        if (measured) {
            out.dropped = drops;
            out.peak_depth = max_depth;
            out.elapsed = std::max({producer_end, last_update, static_cast<std::int64_t>(count * 1000000000ULL / o.rate)});
        } else out.warmup_dropped = drops;
    };
    phase(0, o.warmup, false);
    phase(o.warmup, o.count, true);
    out.final = handler.book().entries();
    return out;
}

struct Validated {
    std::size_t processed{}, published{}, late{}, gap{}, stale{}, invalid{}, duplicate{};
    std::vector<std::int64_t> actual, scheduled, delivery;
};
Validated validate(const Replay& replay, const Result& result, const Options& o) {
    // A full stream oracle for valid runs; for unsaturated warmup it also
    // checks every measurable status and update in overloaded runs.
    Feed<ReferenceBook> oracle;
    if (!oracle.snapshot(0, replay.initial)) throw std::logic_error("oracle snapshot");
    if (!result.warmup_dropped)
        for (std::size_t i = 0; i < o.warmup; ++i) oracle.on_event(replay.events[i]);
    Validated out;
    for (std::size_t i = 0; i < result.samples.size(); ++i) {
        const auto& r = result.samples[i];
        if (!r.update.sequence) continue;
        if (r.update.sequence != replay.events[o.warmup+i].sequence ||
            r.scheduled > r.arrival || r.arrival > r.published)
            throw std::runtime_error("invalid measured event ledger");
        if (!result.warmup_dropped && r.update != oracle.on_event(replay.events[o.warmup+i]))
            throw std::runtime_error("measured publication differs from reference at " + std::to_string(i));
        ++out.processed;
        if (r.arrival - r.scheduled > 100000) ++out.late;
        switch (r.update.status) {
        case Status::published:
            ++out.published;
            out.actual.push_back(r.published - r.arrival);
            out.scheduled.push_back(r.published - r.scheduled);
            out.delivery.push_back(r.arrival - r.scheduled);
            break;
        case Status::gap: ++out.gap; break;
        case Status::stale: ++out.stale; break;
        case Status::invalid: ++out.invalid; break;
        case Status::duplicate: ++out.duplicate; break;
        }
    }
    if (out.processed + result.dropped != o.count ||
        (!result.warmup_dropped && !result.dropped && (out.published != o.count || result.final != oracle.book().entries())))
        throw std::runtime_error("measurement accounting or final book mismatch");
    return out;
}

void distribution(std::vector<std::int64_t>& values) {
    std::sort(values.begin(), values.end());
    auto q = [&](std::size_t permille, std::size_t required) {
        if (values.size() < required) std::cout << "null";
        else std::cout << values[(values.size() * permille + 999) / 1000 - 1];
    };
    std::cout << "{\"count\":" << values.size() << ",\"p50\":"; q(500, 40);
    std::cout << ",\"p99\":"; q(990, 2000);
    std::cout << ",\"p999\":"; q(999, 20000);
    std::cout << '}';
}

void run(const Options& o) {
    const auto replay = generate(o.count + o.warmup, o.seed);
    if (o.profile_ms) {
        if (o.book == "cached") profile<CachedBook>(replay, o);
        else if (o.book == "dense") profile<DenseBook>(replay, o);
        else profile<ReferenceBook>(replay, o);
        return;
    }
    if (o.feed) {
        // Ensure the generator's full stream is valid even when the queue drops work.
        verify(replay);
    }
    auto benchmark = [&]<class Book>() { return o.feed ? feed<Book>(replay, o) : direct<Book>(replay, o); };
    const auto r = o.book == "cached" ? benchmark.operator()<CachedBook>() :
                   o.book == "dense" ? benchmark.operator()<DenseBook>() : benchmark.operator()<ReferenceBook>();
    auto v = validate(replay,r,o);
    const bool valid = !r.warmup_dropped && !r.dropped && v.published == o.count;
    std::cout << "{\"schema\":1,\"mode\":\"" << (o.feed ? "feed" : "direct")
              << "\",\"book\":\"" << o.book
              << "\",\"seed\":" << o.seed << ",\"warmup_events\":" << o.warmup
              << ",\"events\":" << o.count << ",\"input_id\":\"" << std::hex << fingerprint(replay) << std::dec
              << "\",\"offered_rate\":";
    if (o.feed) std::cout << o.rate; else std::cout << "null";
    std::cout << ",\"burst\":" << (o.feed ? o.burst : 1)
              << ",\"queue_capacity\":" << (o.feed ? o.capacity : 0)
              << ",\"peak_queue_depth\":" << r.peak_depth << ",\"dropped\":" << r.dropped
              << ",\"warmup_dropped\":" << r.warmup_dropped << ",\"processed\":" << v.processed
              << ",\"published\":" << v.published << ",\"gaps\":" << v.gap << ",\"stale\":" << v.stale
              << ",\"invalid\":" << v.invalid << ",\"duplicates\":" << v.duplicate
              << ",\"late_over_100us_processed\":" << v.late << ",\"elapsed_ns\":" << r.elapsed
              << ",\"processed_per_second\":" << (1e9 * v.processed / std::max<std::int64_t>(1, r.elapsed))
              << ",\"published_per_second\":" << (1e9 * v.published / std::max<std::int64_t>(1, r.elapsed))
              << ",\"full_coverage\":" << (valid ? "true" : "false")
              << ",\"arrival_to_publication_ns\":";
    distribution(v.actual);
    std::cout << ",\"scheduled_to_publication_ns\":"; distribution(v.scheduled);
    std::cout << ",\"producer_lag_ns\":"; distribution(v.delivery);
    std::cout << "}\n";
}
}

int main(int argc, char** argv) {
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
        std::cout << "jitter_market --mode direct|feed --book reference|dense|cached [--seed N] [--events N]\n"
                     "  [--warmup N] [--rate N] [--burst N] [--capacity N] [--profile-ms N (direct)]\n";
        return 0;
    }
    try { run(parse(argc,argv)); }
    catch (const std::exception& e) { std::cerr << "market: " << e.what() << '\n'; return 1; }
}
