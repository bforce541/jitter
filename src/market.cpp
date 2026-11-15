#include "jitter/market.hpp"

#include <bit>
#include <stdexcept>

namespace jitter::market {

bool valid_order(std::uint32_t id, Order order) {
    return id > 0 && id < id_limit && order.tick > 0 && order.tick <= tick_limit &&
           order.quantity != 0 && (order.side == Side::bid || order.side == Side::ask);
}

bool ReferenceBook::apply(const Event& e) {
    if (e.kind == Kind::add) {
        if (!valid_order(e.id, e.order) || !orders_.emplace(e.id, e.order).second) return false;
        levels_[static_cast<unsigned>(e.order.side)][e.order.tick] += e.order.quantity;
        return true;
    }
    auto it = orders_.find(e.id);
    if (it == orders_.end()) return false;
    auto& order = it->second;
    const auto amount = e.kind == Kind::cancel ? order.quantity : e.order.quantity;
    if ((e.kind != Kind::execute && e.kind != Kind::cancel) || amount == 0 || amount > order.quantity)
        return false;
    auto& side = levels_[static_cast<unsigned>(order.side)];
    auto level = side.find(order.tick);
    level->second -= amount;
    if (!level->second) side.erase(level);
    order.quantity -= amount;
    if (!order.quantity) orders_.erase(it);
    return true;
}

Top ReferenceBook::top() const {
    Top result;
    if (!levels_[0].empty()) {
        const auto [tick, amount] = *levels_[0].rbegin();
        result.bid = {tick, amount};
    }
    if (!levels_[1].empty()) {
        const auto [tick, amount] = *levels_[1].begin();
        result.ask = {tick, amount};
    }
    return result;
}

Entries ReferenceBook::entries() const {
    Entries result(orders_.begin(), orders_.end());
    std::sort(result.begin(), result.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
    return result;
}

DenseBook::DenseBook() : orders_(id_limit) {}

void DenseBook::adjust(Side side, std::uint32_t tick, std::int64_t amount) {
    const auto index = static_cast<unsigned>(side);
    auto& total = levels_[index][tick];
    const bool was_empty = total == 0;
    if (amount > 0) total += static_cast<std::uint64_t>(amount);
    else total -= static_cast<std::uint64_t>(-amount);
    if (was_empty != (total == 0)) {
        const auto bit = tick - 1;
        occupied_[index][bit / 64] ^= std::uint64_t{1} << (bit % 64);
    }
}

bool DenseBook::apply(const Event& e) {
    if (e.id == 0 || e.id >= id_limit) return false;
    auto& slot = orders_[e.id];
    if (e.kind == Kind::add) {
        if (slot.quantity || !valid_order(e.id, e.order)) return false;
        slot = e.order;
        adjust(slot.side, slot.tick, slot.quantity);
        return true;
    }
    const auto amount = e.kind == Kind::cancel ? slot.quantity : e.order.quantity;
    if ((e.kind != Kind::execute && e.kind != Kind::cancel) || amount == 0 || amount > slot.quantity)
        return false;
    adjust(slot.side, slot.tick, -static_cast<std::int64_t>(amount));
    slot.quantity -= amount;
    return true;
}

Top DenseBook::top() const {
    Top result;
    for (std::size_t i = occupied_[0].size(); i-- != 0;) {
        const auto word = occupied_[0][i];
        if (word) {
            const auto tick = static_cast<std::uint32_t>(i * 64 + 64 - std::countl_zero(word));
            result.bid = {tick, levels_[0][tick]};
            break;
        }
    }
    for (std::size_t i = 0; i < occupied_[1].size(); ++i) {
        const auto word = occupied_[1][i];
        if (word) {
            const auto tick = static_cast<std::uint32_t>(i * 64 + 1 + std::countr_zero(word));
            result.ask = {tick, levels_[1][tick]};
            break;
        }
    }
    return result;
}

Entries DenseBook::entries() const {
    Entries result;
    for (std::uint32_t id = 1; id < orders_.size(); ++id)
        if (orders_[id].quantity) result.emplace_back(id, orders_[id]);
    return result;
}

namespace {
class Random {
public:
    explicit Random(std::uint64_t seed) : state_(seed) {}
    std::uint64_t next() {
        auto z = (state_ += 0x9e3779b97f4a7c15ULL);
        z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
        z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
        return z ^ (z >> 31);
    }
private:
    std::uint64_t state_;
};
}

Replay generate(std::size_t count, std::uint64_t seed) {
    constexpr std::uint32_t slots = 2048;
    Random random(seed);
    std::array<Order, slots> state{};
    auto order = [&] {
        const auto side = random.next() % 2 ? Side::ask : Side::bid;
        const auto tick = static_cast<std::uint32_t>(random.next() % 512 + (side == Side::bid ? 1 : 513));
        return Order{side, tick, static_cast<std::uint32_t>(random.next() % 100 + 1)};
    };
    Replay replay;
    replay.events.reserve(count);
    for (std::uint32_t i = 0; i < slots; i += 2)
        replay.initial.emplace_back(i + 1, state[i] = order());
    for (std::size_t i = 0; i < count; ++i) {
        const auto index = static_cast<std::uint32_t>(random.next() % slots);
        auto& current = state[index];
        Event e{i + 1, Kind::add, index + 1, {}};
        if (!current.quantity) e.order = current = order();
        else if (random.next() % 3 == 0) {
            e.kind = Kind::cancel;
            current.quantity = 0;
        } else {
            e.kind = Kind::execute;
            e.order.quantity = random.next() % 2 ? current.quantity :
                static_cast<std::uint32_t>(random.next() % current.quantity + 1);
            current.quantity -= e.order.quantity;
        }
        replay.events.push_back(e);
    }
    return replay;
}

void verify(const Replay& replay) {
    Feed<ReferenceBook> reference;
    Feed<DenseBook> candidate;
    if (!reference.snapshot(0, replay.initial) || !candidate.snapshot(0, replay.initial) ||
        reference.book().entries() != candidate.book().entries())
        throw std::runtime_error("initial book mismatch");
    for (std::size_t i = 0; i < replay.events.size(); ++i) {
        const auto first = reference.on_event(replay.events[i]);
        const auto second = candidate.on_event(replay.events[i]);
        if (first.status != Status::published || first != second ||
            ((i % 1024 == 0 || i + 1 == replay.events.size()) &&
             reference.book().entries() != candidate.book().entries()))
            throw std::runtime_error("book or published update mismatch at event " + std::to_string(i + 1));
    }
}

std::uint64_t fingerprint(const Replay& replay) {
    std::uint64_t value = 14695981039346656037ULL;
    auto mix = [&](std::uint64_t word) {
        for (unsigned i = 0; i != 8; ++i) {
            value ^= word & 255;
            value *= 1099511628211ULL;
            word >>= 8;
        }
    };
    auto fields = [&](Order order) {
        mix(static_cast<unsigned>(order.side)); mix(order.tick); mix(order.quantity);
    };
    mix(replay.initial.size());
    for (const auto& [id, order] : replay.initial) { mix(id); fields(order); }
    mix(replay.events.size());
    for (const auto& e : replay.events) {
        mix(e.sequence); mix(static_cast<unsigned>(e.kind)); mix(e.id); fields(e.order);
    }
    return value;
}

} // namespace jitter::market
