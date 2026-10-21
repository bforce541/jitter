#pragma once

#include "jitter/trace.hpp"

#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace jitter {

struct ApplicationConfig {
    std::uint32_t workers{4};
    std::uint32_t queue_capacity{1024};
    std::uint32_t key_count{256};
    std::uint64_t dataset_seed{17};
    std::uint32_t light_rounds{256};
    std::uint32_t heavy_rounds{131072};
    std::string adapter{"shared-index-v1"};
    std::string implementation{"coarse"};
    std::uint32_t light_fields{8};
    std::uint32_t heavy_fields{256};
};

// SplitMix64's finalizer: specified integer operations, independent of std::hash.
std::uint64_t mix64(std::uint64_t value);
std::uint64_t initial_value(std::uint64_t seed, std::uint32_t key);

// A deliberately coarse shared-index implementation. The immutable dataset and
// digest contract make responses independent of worker execution order.
class SharedIndex {
public:
    explicit SharedIndex(const ApplicationConfig& config);
    ~SharedIndex();
    std::uint64_t lookup(std::uint32_t key, bool heavy, TraceContext context = {});

private:
    ApplicationConfig config_;
    std::vector<std::uint64_t> values_;
    std::mutex mutex_;
    struct JsonDocuments;
    std::unique_ptr<JsonDocuments> json_;
};

} // namespace jitter
