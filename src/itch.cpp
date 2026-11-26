#include "jitter/itch.hpp"

#include <algorithm>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>

namespace jitter::itch {
namespace {
constexpr std::size_t locate_count = 1u << 16;
constexpr auto lengths = [] {
    std::array<std::size_t, 256> result{};
    for (const auto& [type, width] : std::array{
             std::pair{'S', 12}, std::pair{'R', 39}, std::pair{'H', 25},
             std::pair{'Y', 20}, std::pair{'L', 26}, std::pair{'V', 35},
             std::pair{'W', 12}, std::pair{'K', 28}, std::pair{'J', 35},
             std::pair{'h', 21}, std::pair{'A', 36}, std::pair{'F', 40},
             std::pair{'E', 31}, std::pair{'C', 36}, std::pair{'X', 23},
             std::pair{'D', 19}, std::pair{'U', 35}, std::pair{'P', 44},
             std::pair{'Q', 40}, std::pair{'B', 19}, std::pair{'I', 50},
             std::pair{'N', 20}, std::pair{'O', 48}})
        result[static_cast<unsigned char>(type)] = width;
    return result;
}();

bool valid_order(OrderId id, const Order& order) {
    return id && order.locate && order.price && order.quantity &&
           (order.side == Side::buy || order.side == Side::sell);
}

std::uint64_t price_key(const Order& order) {
    return order.side == Side::buy
        ? std::numeric_limits<Price>::max() - order.price
        : (std::uint64_t{1} << 32) + order.price;
}
}

std::uint64_t read_be(Bytes data) {
    std::uint64_t value = 0;
    for (const auto byte : data) value = (value << 8) | byte;
    return value;
}

void append_be(std::vector<std::uint8_t>& out, std::uint64_t value, std::size_t width) {
    while (width) out.push_back(static_cast<std::uint8_t>(value >> (--width * 8)));
}

std::size_t message_length(char type) {
    return lengths[static_cast<unsigned char>(type)];
}

bool Message::updates_book() const {
    switch (type) {
    case 'A': case 'F': case 'E': case 'C': case 'X': case 'D': case 'U':
        return true;
    default:
        return false;
    }
}

Message decode(Bytes bytes) {
    if (bytes.empty()) throw std::runtime_error("empty ITCH message");
    const auto type = static_cast<char>(bytes.front());
    const auto length = message_length(type);
    if (!length)
        throw std::runtime_error("unknown ITCH message type byte " + std::to_string(bytes.front()));
    if (bytes.size() != length)
        throw std::runtime_error("wrong ITCH length for type " + std::string(1, type));
    const auto number = [&](std::size_t offset, std::size_t width) {
        return read_be(bytes.subspan(offset, width));
    };
    Message message;
    message.type = type;
    message.locate = static_cast<Locate>(number(1, 2));
    message.time = number(5, 6);
    switch (type) {
    case 'S':
        message.event = static_cast<char>(bytes[11]);
        break;
    case 'R':
        std::copy_n(bytes.begin() + 11, message.symbol.size(), message.symbol.begin());
        break;
    case 'H':
        message.event = static_cast<char>(bytes[19]);
        break;
    case 'A': case 'F':
        message.order = number(11, 8);
        if (bytes[19] != 'B' && bytes[19] != 'S')
            throw std::runtime_error("invalid ITCH order side");
        message.side = bytes[19] == 'B' ? Side::buy : Side::sell;
        message.quantity = static_cast<std::uint32_t>(number(20, 4));
        message.price = static_cast<Price>(number(32, 4));
        break;
    case 'E': case 'C': case 'X':
        message.order = number(11, 8);
        message.quantity = static_cast<std::uint32_t>(number(19, 4));
        break;
    case 'D':
        message.order = number(11, 8);
        break;
    case 'U':
        message.order = number(11, 8);
        message.replacement = number(19, 8);
        message.quantity = static_cast<std::uint32_t>(number(27, 4));
        message.price = static_cast<Price>(number(31, 4));
        break;
    default:
        break; // Metadata and trade messages never mutate displayed orders.
    }
    return message;
}

std::string symbol_name(const Message& directory) {
    std::string name(directory.symbol.begin(), directory.symbol.end());
    const auto end = name.find_last_not_of(' ');
    return end == std::string::npos ? std::string{} : name.substr(0, end + 1);
}

bool TapeReader::next() {
    std::array<std::uint8_t, 2> length{};
    const auto got = std::fread(length.data(), 1, length.size(), stream_);
    if (got == 0 && std::feof(stream_)) return false;
    ++sequence_;
    if (got != length.size())
        throw std::runtime_error("missing ITCH record length at sequence " + std::to_string(sequence_));
    size_ = read_be(length);
    if (size_ == 0 || size_ > buffer_.size())
        throw std::runtime_error("invalid ITCH record length at sequence " + std::to_string(sequence_));
    if (std::fread(buffer_.data(), 1, size_, stream_) != size_)
        throw std::runtime_error("truncated ITCH message at sequence " + std::to_string(sequence_));
    return true;
}

Book::Book() : levels_(locate_count) {}
void Book::add_level(const Order& order) {
    levels_[order.locate][static_cast<unsigned>(order.side)][order.price] += order.quantity;
}
void Book::remove_level(const Order& order, std::uint32_t amount) {
    auto& side = levels_[order.locate][static_cast<unsigned>(order.side)];
    const auto level = side.find(order.price);
    level->second -= amount;
    if (level->second == 0) side.erase(level);
}

bool Book::insert(OrderId id, Order order) {
    if (!valid_order(id, order) || !orders_.emplace(id, order).second) return false;
    add_level(order);
    return true;
}

bool Book::apply(const Message& message) {
    if (!message.updates_book()) return true;
    if (message.type == 'A' || message.type == 'F')
        return insert(message.order, {message.locate, message.side, message.price, message.quantity});
    const auto found = orders_.find(message.order);
    if (found == orders_.end() || found->second.locate != message.locate) return false;
    const Order old = found->second;
    if (message.type == 'U') {
        const Order replacement{old.locate, old.side, message.price, message.quantity};
        if (!valid_order(message.replacement, replacement) || orders_.contains(message.replacement))
            return false;
        remove_level(old, old.quantity);
        orders_.erase(found);
        orders_.emplace(message.replacement, replacement);
        add_level(replacement);
        return true;
    }
    const auto reduction = message.type == 'D' ? old.quantity : message.quantity;
    if (!reduction || reduction > old.quantity) return false;
    remove_level(old, reduction);
    if (reduction == old.quantity) orders_.erase(found);
    else found->second.quantity -= reduction;
    return true;
}

Top Book::top(Locate locate) const {
    Top result{};
    const auto& [bids, asks] = levels_[locate];
    if (!bids.empty()) result[0] = {bids.rbegin()->first, bids.rbegin()->second};
    if (!asks.empty()) result[1] = {asks.begin()->first, asks.begin()->second};
    return result;
}

Orders Book::orders() const {
    Orders result(orders_.begin(), orders_.end());
    std::sort(result.begin(), result.end());
    return result;
}

bool Reference::apply(const Message& message) {
    if (!message.updates_book()) return true;
    auto& [live, totals] = instruments_[message.locate];
    const bool add = message.type == 'A' || message.type == 'F';
    const auto old = live.find(message.order);
    if (add != (old == live.end())) return false;
    if (add) {
        const Order order{message.locate, message.side, message.price, message.quantity};
        if (!valid_order(message.order, order)) return false;
        live.emplace(message.order, order);
        totals[price_key(order)] += order.quantity;
        return true;
    }
    const auto order = old->second;
    const auto replace = message.type == 'U';
    const Order next{message.locate, order.side, message.price, message.quantity};
    if (replace && (!valid_order(message.replacement, next) || live.contains(message.replacement)))
        return false;
    const auto removed = replace || message.type == 'D' ? order.quantity : message.quantity;
    if (!removed || removed > order.quantity) return false;
    const auto key = price_key(order);
    if ((totals.at(key) -= removed) == 0) totals.erase(key);
    if (removed == order.quantity) live.erase(old);
    else old->second.quantity = order.quantity - removed;
    if (replace) {
        live.emplace(message.replacement, next);
        totals[price_key(next)] += next.quantity;
    }
    return true;
}

Top Reference::top(Locate locate) const {
    const auto instrument = instruments_.find(locate);
    if (instrument == instruments_.end()) return {};
    constexpr auto sell = std::uint64_t{1} << 32;
    const auto& totals = instrument->second.totals;
    const auto ask = totals.lower_bound(sell);
    Top result{};
    if (totals.begin() != ask)
        result[0] = {std::numeric_limits<Price>::max() - static_cast<Price>(totals.begin()->first),
                     totals.begin()->second};
    if (ask != totals.end()) result[1] = {static_cast<Price>(ask->first - sell), ask->second};
    return result;
}

Orders Reference::orders() const {
    Orders result;
    for (const auto& [locate, instrument] : instruments_)
        result.insert(result.end(), instrument.live.begin(), instrument.live.end());
    std::sort(result.begin(), result.end());
    return result;
}

std::string fingerprint(const Orders& orders) {
    std::uint64_t hash = 14695981039346656037ull;
    const auto mix = [&hash](std::uint64_t value, unsigned bytes) {
        for (unsigned i = 0; i < bytes; ++i) {
            hash ^= static_cast<std::uint8_t>(value >> (i * 8));
            hash *= 1099511628211ull;
        }
    };
    for (const auto& [id, order] : orders) {
        mix(id, 8);
        mix(order.locate, 2);
        mix(static_cast<unsigned>(order.side), 1);
        mix(order.price, 4);
        mix(order.quantity, 4);
    }
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << hash;
    return out.str();
}
} // namespace jitter::itch
