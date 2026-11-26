#pragma once

#include <array>
#include <compare>
#include <cstdint>
#include <cstdio>
#include <map>
#include <span>
#include <string>
#include <unordered_map>
#include <vector>

namespace jitter::itch {

using Bytes = std::span<const std::uint8_t>;
using Locate = std::uint16_t;
using Price = std::uint32_t;
using Sequence = std::uint64_t;
using OrderId = std::uint64_t;
enum class Side : std::uint8_t { buy, sell };

struct Message {
    char type{}, event{};
    Locate locate{};
    std::uint64_t time{}, order{}, replacement{};
    Price price{};
    std::uint32_t quantity{};
    Side side{};
    std::array<char, 8> symbol{};
    bool updates_book() const;
    bool operator==(const Message&) const = default;
};

std::uint64_t read_be(Bytes data);
void append_be(std::vector<std::uint8_t>& out, std::uint64_t value, std::size_t width);
std::size_t message_length(char type);
Message decode(Bytes bytes);
std::string symbol_name(const Message& directory);

// Historical binary-file framing: a two-byte length followed by one ITCH message.
class TapeReader {
public:
    explicit TapeReader(std::FILE* stream) : stream_(stream) {}
    bool next();
    Bytes bytes() const { return {buffer_.data(), size_}; }
    Sequence sequence() const { return sequence_; }
private:
    std::FILE* stream_;
    std::array<std::uint8_t, 50> buffer_{};
    std::size_t size_{};
    Sequence sequence_{};
};

struct Order {
    Locate locate{};
    Side side{};
    Price price{};
    std::uint32_t quantity{};
    auto operator<=>(const Order&) const = default;
};

struct Quote {
    Price price{};
    std::uint64_t size{};
    bool operator==(const Quote&) const = default;
};

using Top = std::array<Quote, 2>;
using Orders = std::vector<std::pair<OrderId, Order>>;

// Order IDs are sparse 64-bit values. Price storage grows with occupied levels.
class Book {
public:
    Book();
    bool apply(const Message& message);
    bool insert(OrderId id, Order order);
    Top top(Locate locate) const;
    Orders orders() const;
    std::size_t size() const { return orders_.size(); }
private:
    using Levels = std::array<std::map<Price, std::uint64_t>, 2>;
    std::vector<Levels> levels_;
    std::unordered_map<OrderId, Order> orders_;
    void add_level(const Order& order);
    void remove_level(const Order& order, std::uint32_t amount);
};

// Independent reconstruction: separate identity tables per symbol and one
// ordered price index per symbol. Buy keys reverse price order.
class Reference {
public:
    bool apply(const Message& message);
    Top top(Locate locate) const;
    Orders orders() const;
private:
    struct Instrument {
        std::unordered_map<OrderId, Order> live;
        std::map<std::uint64_t, std::uint64_t> totals;
    };
    std::unordered_map<Locate, Instrument> instruments_;
};

std::string fingerprint(const Orders& orders);

} // namespace jitter::itch
