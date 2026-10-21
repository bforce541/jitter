#include "jitter/experiment.hpp"
#include "jitter/build_info.hpp"

#include <charconv>
#include <cstdint>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {
std::uint64_t parse(const std::string& value, std::uint64_t minimum, std::uint64_t maximum) {
    std::uint64_t result{};
    const auto converted = std::from_chars(value.data(), value.data() + value.size(), result);
    if (converted.ec != std::errc{} || converted.ptr != value.data() + value.size() ||
        result < minimum || result > maximum || value.empty()) {
        throw std::invalid_argument("invalid numeric option: " + value);
    }
    return result;
}
}

int main(int argc, char** argv) {
    try {
        if (argc == 1 || (argc == 2 && std::string_view(argv[1]) == "--help")) {
            std::cout << "Jitter application engine\n"
                      << "Use: python3 tools/jitter.py run examples/baseline.json\n"
                      << "Engine: jitter --workload FILE --output DIR --workers N --queue-capacity N\n"
                      << "        --key-count N --dataset-seed N --light-rounds N --heavy-rounds N\n"
                      << "        --warmup-ns N --measurement-ns N\n"
                      << "        [--adapter shared-index-v1 --implementation coarse|narrow]\n"
                      << "        JSON: --adapter json-projection-v1 --implementation map|ordered\n"
                      << "              --light-fields N --heavy-fields N (instead of digest rounds)\n"
                      << "        [--trace 0|1 --trace-capacity N]\n"
                      << "Metadata: jitter --build-info\n";
            return 0;
        }
        if (argc == 2 && std::string_view(argv[1]) == "--build-info") {
            std::cout << JITTER_BUILD_INFO << '\n';
            return 0;
        }
        std::map<std::string, std::string> options;
        const std::map<std::string, bool> allowed{
            {"--workload", true}, {"--output", true}, {"--workers", true},
            {"--queue-capacity", true}, {"--key-count", true}, {"--dataset-seed", true},
            {"--light-rounds", true}, {"--heavy-rounds", true},
            {"--adapter", true}, {"--implementation", true}, {"--light-fields", true}, {"--heavy-fields", true},
            {"--warmup-ns", true}, {"--measurement-ns", true}, {"--trace", true}, {"--trace-capacity", true}};
        for (int i = 1; i < argc; i += 2) {
            const std::string name(argv[i]);
            if (i + 1 >= argc || !allowed.contains(name) || options.contains(name)) {
                throw std::invalid_argument("unknown, repeated, or incomplete option: " + name);
            }
            options.emplace(name, argv[i + 1]);
        }
        jitter::TraceConfig tracing;
        if (options.contains("--trace")) {
            tracing.enabled = parse(options.at("--trace"), 0, 1) == 1;
            options.erase("--trace");
        }
        if (options.contains("--trace-capacity")) {
            tracing.events_per_thread = static_cast<std::uint32_t>(parse(options.at("--trace-capacity"), 1, 1000000));
            options.erase("--trace-capacity");
        }
        jitter::ApplicationConfig config;
        if (options.contains("--adapter")) {
            config.adapter = options.at("--adapter");
            options.erase("--adapter");
        }
        if (options.contains("--implementation")) {
            config.implementation = options.at("--implementation");
            options.erase("--implementation");
        }
        for (const auto* name : {"--workload", "--output", "--workers", "--queue-capacity", "--key-count",
                                 "--dataset-seed", "--warmup-ns", "--measurement-ns"}) {
            if (!options.contains(name)) throw std::invalid_argument("missing engine option: " + std::string(name));
        }
        if (options.size() != 10) throw std::invalid_argument("all application engine options are required");
        config.workers = static_cast<std::uint32_t>(parse(options.at("--workers"), 1, 256));
        config.queue_capacity = static_cast<std::uint32_t>(parse(options.at("--queue-capacity"), 1, 1000000));
        config.key_count = static_cast<std::uint32_t>(parse(options.at("--key-count"), 1, 65536));
        config.dataset_seed = parse(options.at("--dataset-seed"), 0, UINT64_MAX);
        if (config.adapter == "shared-index-v1") {
            if (!options.contains("--light-rounds") || !options.contains("--heavy-rounds")) {
                throw std::invalid_argument("shared-index-v1 requires both digest-round options");
            }
            config.light_rounds = static_cast<std::uint32_t>(parse(options.at("--light-rounds"), 1, 10000000));
            config.heavy_rounds = static_cast<std::uint32_t>(parse(options.at("--heavy-rounds"), 1, 10000000));
        } else if (config.adapter == "json-projection-v1") {
            if (!options.contains("--light-fields") || !options.contains("--heavy-fields")) {
                throw std::invalid_argument("json-projection-v1 requires both field-count options");
            }
            config.light_fields = static_cast<std::uint32_t>(parse(options.at("--light-fields"), 1, 4096));
            config.heavy_fields = static_cast<std::uint32_t>(parse(options.at("--heavy-fields"), 1, 4096));
        } else {
            throw std::invalid_argument("unsupported application adapter");
        }
        const auto warmup = static_cast<std::int64_t>(parse(options.at("--warmup-ns"), 0, UINT64_C(3600000000000)));
        const auto measurement = static_cast<std::int64_t>(parse(options.at("--measurement-ns"), 1, UINT64_C(3600000000000)));
        const auto requests = jitter::read_workload(options.at("--workload"), config, warmup, measurement);
        const auto output = jitter::run_application(config, requests, warmup, measurement, tracing);
        jitter::write_output(options.at("--output"), output);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "jitter: " << error.what() << '\n';
        return 1;
    }
}
