#include "jitter/spsc.hpp"

#include <cstdint>
#include <stdexcept>
#include <thread>

using jitter::market::SpscPipeline;

int main() {
    auto require = [](bool pass) {
        if (!pass) throw std::runtime_error("SPSC FIFO, capacity or drain invariant failed");
    };
    std::uint64_t value{};
    std::size_t peak{};
    SpscPipeline<std::uint64_t> single(1);
    require(single.push(3, peak));
    require(!single.push(4, peak) && peak == 1);
    require(single.pop(value) && value == 3);
    require(single.push(5, peak));
    single.close();
    require(single.pop(value) && value == 5 && !single.pop(value));

    for (std::size_t capacity : {1u, 2u, 64u, 4096u}) {
        SpscPipeline<std::uint64_t> queue(capacity);
        constexpr std::uint64_t count = 200000;
        peak = 0;
        bool correct = true;
        std::thread consumer([&] {
            std::uint64_t expected = 0, item;
            while (queue.pop(item)) correct &= item == expected++;
            correct &= expected == count;
        });
        for (std::uint64_t i = 0; i < count; ++i)
            while (!queue.push(i, peak)) {}
        queue.close();
        consumer.join();
        require(correct && peak <= capacity);
    }
}
