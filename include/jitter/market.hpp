#pragma once

#include <array>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <map>
#include <span>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>

namespace jitter::market {

inline constexpr std::uint32_t id_limit = 16384;
inline constexpr std::uint32_t tick_limit = 1024;
enum class Side : std::uint8_t { bid, ask };
enum class Kind : std::uint8_t { add, cancel, execute };
struct Order {
    Side side{};
    std::uint32_t tick{}, quantity{};
    bool operator==(const Order&) const = default;
};
struct Event {
    std::uint64_t sequence{};
    Kind kind{};
    std::uint32_t id{};
    Order order{}; // Cancels ignore this field; executions use only quantity.
};
struct Quote {
    std::uint32_t tick{};
    std::uint64_t quantity{};
    bool operator==(const Quote&) const = default;
};
struct Top {
    Quote bid{}, ask{};
    bool operator==(const Top&) const = default;
};
using Entries = std::vector<std::pair<std::uint32_t, Order>>;

bool valid_order(std::uint32_t id, Order order);

class ReferenceBook {
public:
    bool apply(const Event& event);
    Top top() const;
    Entries entries() const;
private:
    std::unordered_map<std::uint32_t, Order> orders_;
    std::array<std::map<std::uint32_t, std::uint64_t>, 2> levels_;
};

template<bool CacheBest>
class ArrayBook {
public:
    ArrayBook();
    bool apply(const Event& event);
    Top top() const;
    Entries entries() const;
private:
    std::vector<Order> orders_;
    std::array<std::array<std::uint64_t, tick_limit + 1>, 2> levels_{};
    std::array<std::array<std::uint64_t, tick_limit / 64>, 2> occupied_{};
    struct NoCache {};
    [[no_unique_address]] std::conditional_t<CacheBest, std::array<std::uint32_t, 2>, NoCache> best_{};
    std::uint32_t best_tick(unsigned index) const;
    void adjust(Side side, std::uint32_t tick, std::int64_t quantity);
};

using DenseBook = ArrayBook<false>;
using CachedBook = ArrayBook<true>;

enum class Status : std::uint8_t { published, duplicate, gap, stale, invalid };
struct Update {
    std::uint64_t sequence{};
    Status status{};
    Top top{};
    bool operator==(const Update&) const = default;
};

template<class Book>
class Feed {
public:
    Update on_event(const Event& event) {
        if (event.sequence < next_) return {event.sequence, Status::duplicate, {}};
        if (stale_) {
            required_ = std::max(required_, event.sequence);
            return {event.sequence, Status::stale, {}};
        }
        if (event.sequence != next_) return lose(event.sequence, Status::gap);
        if (event.sequence == std::numeric_limits<std::uint64_t>::max() || !book_.apply(event))
            return lose(event.sequence, Status::invalid);
        ++next_;
        return {event.sequence, Status::published, book_.top()};
    }

    // Authoritative state through last, inclusive. A snapshot must cover every
    // discarded sequence; the caller delivers subsequent events in feed order.
    bool snapshot(std::uint64_t last, std::span<const Entries::value_type> orders) {
        if (last == std::numeric_limits<std::uint64_t>::max() ||
            last < required_ || last < next_ - 1 || orders.size() >= id_limit)
            return false;
        Book candidate;
        for (const auto& [id, order] : orders)
            if (!candidate.apply({0, Kind::add, id, order})) return false;
        book_ = std::move(candidate);
        next_ = last + 1;
        required_ = 0;
        stale_ = false;
        return true;
    }

    bool stale() const { return stale_; }
    const Book& book() const { return book_; }
private:
    Update lose(std::uint64_t seq, Status reason) {
        stale_ = true;
        required_ = seq;
        return {seq, reason, {}};
    }
    Book book_;
    std::uint64_t next_{1}, required_{};
    bool stale_{};
};

struct Replay {
    Entries initial;
    std::vector<Event> events;
};
Replay generate(std::size_t count, std::uint64_t seed);
void verify(const Replay& replay); // Differential check outside timed runs.
std::uint64_t fingerprint(const Replay& replay);

} // namespace jitter::market
