#pragma once

#include "jitter/itch.hpp"

#include <filesystem>
#include <fstream>

namespace jitter::itch {

struct Snapshot {
    Sequence through{};
    Orders orders;
};

void write_snapshot(const std::filesystem::path& path, Snapshot snapshot);
Snapshot read_snapshot(const std::filesystem::path& path);
Book restore(const Snapshot& snapshot);

struct Window {
    std::string name;
    std::uint64_t target_ns{}, first_market_ns{}, last_market_ns{};
    Sequence first{}, count{}, limit{};
    std::filesystem::path root;
    std::ofstream output;

    // The reference is still at sequence - 1 when this method is called.
    void before(Sequence sequence, const Message& message, const Reference& reference);
    void after(Sequence sequence, const Message& message, Bytes bytes, const Reference& reference);
    bool complete() const { return count == limit; }
};

inline constexpr Sequence recovery_offset = 96;

} // namespace jitter::itch
