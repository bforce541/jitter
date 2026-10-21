#include "jitter/application.hpp"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <stdexcept>

namespace jitter {
namespace {
constexpr std::uint64_t golden = UINT64_C(0x9e3779b97f4a7c15);
constexpr std::uint64_t document_budget = UINT64_C(256) * 1024 * 1024;

std::uint64_t digest(std::uint64_t value, std::uint32_t key, std::uint32_t rounds) {
    const auto increment = (static_cast<std::uint64_t>(key) + 1) | UINT64_C(1);
    for (std::uint32_t i = 0; i < rounds; ++i) {
        value = value * UINT64_C(6364136223846793005) + increment;
    }
    return value;
}

template<class Json>
std::uint64_t projection(const std::string& document, const std::vector<std::string>& names,
                         std::uint32_t fields) {
    const auto parsed = Json::parse(document);
    std::uint64_t result{};
    for (std::uint32_t index = 0; index < fields; ++index) {
        result += (static_cast<std::uint64_t>(index) + 1) *
                  parsed.at(names[index]).template get<std::uint64_t>();
    }
    return result;
}
}

struct SharedIndex::JsonDocuments {
    std::vector<std::string> light;
    std::vector<std::string> heavy;
    std::vector<std::string> names;

    explicit JsonDocuments(const ApplicationConfig& config) {
        const auto maximum = std::max(config.light_fields, config.heavy_fields);
        names.reserve(maximum);
        for (std::uint32_t index = 0; index < maximum; ++index) {
            names.push_back("f" + std::to_string(index));
        }
        light.reserve(config.key_count);
        heavy.reserve(config.key_count);
        for (std::uint32_t key = 0; key < config.key_count; ++key) {
            light.push_back(document(config.dataset_seed, key, config.light_fields));
            heavy.push_back(document(config.dataset_seed, key, config.heavy_fields));
        }
    }

    std::string document(std::uint64_t seed, std::uint32_t key, std::uint32_t fields) const {
        std::string result;
        result.reserve(static_cast<std::size_t>(fields) * 32 + 2);
        result.push_back('{');
        for (std::uint32_t index = 0; index < fields; ++index) {
            if (index != 0) result.push_back(',');
            result.push_back('"');
            result += names[index];
            result += "\":";
            const auto value = mix64(seed + golden * (static_cast<std::uint64_t>(key) + 1) +
                                     golden * (static_cast<std::uint64_t>(index) + 1)) % UINT64_C(1000000007);
            result += std::to_string(value);
        }
        result.push_back('}');
        return result;
    }
};

std::uint64_t mix64(std::uint64_t value) {
    value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);
    return value ^ (value >> 31);
}

std::uint64_t initial_value(std::uint64_t seed, std::uint32_t key) {
    return mix64(seed + UINT64_C(0x9e3779b97f4a7c15) * (static_cast<std::uint64_t>(key) + 1));
}

SharedIndex::SharedIndex(const ApplicationConfig& config) : config_(config) {
    if (config.key_count == 0 || config.key_count > 65536) {
        throw std::invalid_argument("dataset size must be between 1 and 65536");
    }
    if (std::string(JITTER_COMPILED_VARIANT) != "any" && config.implementation != JITTER_COMPILED_VARIANT) {
        throw std::invalid_argument("implementation differs from compiled variant");
    }
    if (config.adapter == "json-projection-v1") {
        if (config.implementation != "map" && config.implementation != "ordered") {
            throw std::invalid_argument("json-projection-v1 requires map or ordered implementation");
        }
        if (config.light_fields == 0 || config.light_fields > 4096 ||
            config.heavy_fields == 0 || config.heavy_fields > 4096) {
            throw std::invalid_argument("JSON field counts must be between 1 and 4096");
        }
        const auto bytes = static_cast<std::uint64_t>(config.key_count) *
            (32 * (static_cast<std::uint64_t>(config.light_fields) + config.heavy_fields) + 128);
        if (bytes > document_budget) {
            throw std::invalid_argument("JSON documents exceed the 256 MiB preparation budget");
        }
        json_ = std::make_unique<JsonDocuments>(config);
        return;
    }
    if (config.adapter != "shared-index-v1" ||
        (config.implementation != "coarse" && config.implementation != "narrow")) {
        throw std::invalid_argument("unsupported application or implementation");
    }
    if (config.light_rounds == 0 || config.heavy_rounds == 0) {
        throw std::invalid_argument("digest rounds must be positive");
    }
    values_.reserve(config.key_count);
    for (std::uint32_t key = 0; key < config.key_count; ++key) {
        values_.push_back(initial_value(config.dataset_seed, key));
    }
}

SharedIndex::~SharedIndex() = default;

std::uint64_t SharedIndex::lookup(std::uint32_t key, bool heavy, TraceContext context) {
    if (key >= config_.key_count) {
        throw std::out_of_range("lookup key is outside the dataset");
    }
    if (json_) {
        const std::string* document{};
        {
            TracedLock lock(mutex_, context, index_lock);
            document = &(heavy ? json_->heavy : json_->light)[key];
        }
        TracedRegion region(context, digest_region);
        const auto fields = heavy ? config_.heavy_fields : config_.light_fields;
        return config_.implementation == "map"
            ? projection<nlohmann::json>(*document, json_->names, fields)
            : projection<nlohmann::ordered_json>(*document, json_->names, fields);
    }
    const auto rounds = heavy ? config_.heavy_rounds : config_.light_rounds;
    if (config_.implementation == "narrow") {
        std::uint64_t value{};
        {
            TracedLock lock(mutex_, context, index_lock);
            value = values_[key];
        }
        TracedRegion region(context, digest_region);
        return digest(value, key, rounds);
    }
    TracedLock lock(mutex_, context, index_lock);
    TracedRegion region(context, digest_region);
    return digest(values_[key], key, rounds);
}

} // namespace jitter
