#include "jitter/itch_window.hpp"

#include <algorithm>
#include <stdexcept>

namespace jitter::itch {
namespace {

constexpr std::string_view magic = "JITSNAP1";
constexpr std::uint64_t max_orders = 10000000;

void put(std::ostream& out, std::uint64_t value, std::size_t width) {
    std::array<std::uint8_t, 8> bytes{};
    for (std::size_t i = width; i > 0; value >>= 8) bytes[--i] = static_cast<std::uint8_t>(value);
    out.write(reinterpret_cast<const char*>(bytes.data()), width);
}

std::uint64_t get(std::istream& in, std::size_t width) {
    std::array<std::uint8_t, 8> bytes{};
    if (!in.read(reinterpret_cast<char*>(bytes.data()), width))
        throw std::runtime_error("truncated Jitter snapshot");
    return read_be({bytes.data(), width});
}
}

void write_snapshot(const std::filesystem::path& path, Snapshot snapshot) {
    std::ofstream out(path, std::ios::binary);
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out.write(magic.data(), magic.size());
    put(out, snapshot.through, 8);
    put(out, snapshot.orders.size(), 8);
    for (const auto& [id, order] : snapshot.orders) {
        put(out, id, 8);
        put(out, order.locate, 2);
        put(out, static_cast<unsigned>(order.side), 1);
        put(out, order.price, 4);
        put(out, order.quantity, 4);
    }
    out.close();
}

Snapshot read_snapshot(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    std::array<char, magic.size()> header{};
    if (!in.read(header.data(), header.size()) || std::string_view(header.data(), header.size()) != magic)
        throw std::runtime_error("invalid Jitter snapshot header: " + path.string());
    Snapshot snapshot;
    snapshot.through = get(in, 8);
    const auto count = get(in, 8);
    if (count > max_orders) throw std::runtime_error("snapshot order count exceeds limit");
    snapshot.orders.reserve(count);
    OrderId previous = 0;
    for (std::uint64_t index = 0; index < count; ++index) {
        const auto id = get(in, 8);
        const auto locate = static_cast<Locate>(get(in, 2));
        const auto side = get(in, 1);
        const auto price = static_cast<Price>(get(in, 4));
        const auto quantity = static_cast<std::uint32_t>(get(in, 4));
        if (id <= previous || !locate || side > 1 || !price || !quantity)
            throw std::runtime_error("invalid canonical order in snapshot");
        previous = id;
        snapshot.orders.emplace_back(id, Order{locate, static_cast<Side>(side), price, quantity});
    }
    if (in.peek() != EOF || in.bad()) throw std::runtime_error("invalid snapshot trailer");
    return snapshot;
}

Book restore(const Snapshot& snapshot) {
    Book book;
    for (const auto& [id, order] : snapshot.orders)
        if (!book.insert(id, order)) throw std::runtime_error("snapshot contains an invalid order");
    return book;
}

void Window::before(Sequence sequence, const Message& message, const Reference& reference) {
    if (first || message.time < target_ns) return;
    first = sequence;
    first_market_ns = message.time;
    if (!std::filesystem::create_directory(root))
        throw std::runtime_error("window output already exists");
    write_snapshot(root / "initial.snapshot", {sequence - 1, reference.orders()});
    output.open(root / "messages.itch", std::ios::binary);
    output.exceptions(std::ios::badbit | std::ios::failbit);
}

void Window::after(Sequence sequence, const Message& message, Bytes bytes, const Reference& reference) {
    if (!first || complete()) return;
    put(output, bytes.size(), 2);
    output.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    last_market_ns = message.time;
    ++count;
    if (count == recovery_offset)
        write_snapshot(root / "repair.snapshot", {sequence, reference.orders()});
    if (complete()) {
        output.close();
        write_snapshot(root / "final.snapshot", {sequence, reference.orders()});
    }
}
} // namespace jitter::itch
