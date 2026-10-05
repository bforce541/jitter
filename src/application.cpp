#include "jitter/application.hpp"

#include <stdexcept>

namespace jitter {

std::uint64_t mix64(std::uint64_t value) {
    value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);
    return value ^ (value >> 31);
}

std::uint64_t initial_value(std::uint64_t seed, std::uint32_t key) {
    return mix64(seed + UINT64_C(0x9e3779b97f4a7c15) * (static_cast<std::uint64_t>(key) + 1));
}

SharedIndex::SharedIndex(const ApplicationConfig& config) : config_(config) {
    if (config.key_count == 0 || config.light_rounds == 0 || config.heavy_rounds == 0) {
        throw std::invalid_argument("dataset size and digest rounds must be positive");
    }
    values_.reserve(config.key_count);
    for (std::uint32_t key = 0; key < config.key_count; ++key) {
        values_.push_back(initial_value(config.dataset_seed, key));
    }
}

std::uint64_t SharedIndex::lookup(std::uint32_t key, bool heavy) {
    if (key >= values_.size()) {
        throw std::out_of_range("lookup key is outside the dataset");
    }
    std::lock_guard lock(mutex_);
    auto value = values_[key];
    const auto increment = (static_cast<std::uint64_t>(key) + 1) | UINT64_C(1);
    const auto rounds = heavy ? config_.heavy_rounds : config_.light_rounds;
    for (std::uint32_t i = 0; i < rounds; ++i) {
        value = value * UINT64_C(6364136223846793005) + increment;
    }
    return value;
}

} // namespace jitter
