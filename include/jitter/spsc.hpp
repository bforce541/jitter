#pragma once

#include <algorithm>
#include <atomic>
#include <bit>
#include <cstddef>
#include <stdexcept>
#include <type_traits>
#include <vector>

namespace jitter::market {

// Exactly one producer (push/close) and one consumer (pop). A slot is
// published by write.release and reclaimed by read.release. Monotonic
// unsigned cursors wrap naturally; the mask selects the backing slot.
template<class Item>
class SpscPipeline {
    static_assert(std::is_trivially_copyable_v<Item> && std::is_nothrow_copy_assignable_v<Item>);
    static_assert(std::atomic<std::size_t>::is_always_lock_free);
public:
    explicit SpscPipeline(std::size_t size) : slots_(checked_size(size)), mask_(size - 1) {}

    bool push(Item item, std::size_t& peak) noexcept {
        const auto write = producer_.cursor.load(std::memory_order_relaxed);
        auto read = consumer_.cursor.load(std::memory_order_acquire);
        if (write - read == slots_.size()) return false;
        slots_[write & mask_] = item;
        producer_.cursor.store(write + 1, std::memory_order_release);
        peak = std::max(peak, write + 1 - read);
        return true;
    }

    // Polling is deliberate: no syscall or wakeup on the hot path, at the
    // cost of a runnable consumer even while the queue is empty.
    bool pop(Item& item) noexcept {
        const auto read = consumer_.cursor.load(std::memory_order_relaxed);
        while (read == producer_.cursor.load(std::memory_order_acquire)) {
            if (closed_.load(std::memory_order_acquire)) {
                // Close follows the last publication. Re-read after acquiring
                // it so an old empty observation cannot skip the final item.
                if (read == producer_.cursor.load(std::memory_order_acquire)) return false;
                break;
            }
        }
        item = slots_[read & mask_];
        consumer_.cursor.store(read + 1, std::memory_order_release);
        return true;
    }

    void close() noexcept { closed_.store(true, std::memory_order_release); }
private:
    static std::size_t checked_size(std::size_t size) {
        if (!std::has_single_bit(size) || size > 1048576)
            throw std::invalid_argument("SPSC capacity must be a power of two up to 1048576");
        return size;
    }

    struct alignas(128) Cursor {
        std::atomic<std::size_t> cursor{};
    };
    std::vector<Item> slots_;
    std::size_t mask_;
    Cursor producer_, consumer_;
    alignas(128) std::atomic<bool> closed_{};
};

} // namespace jitter::market
