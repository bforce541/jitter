#pragma once

#include "jitter/application.hpp"

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace jitter {

struct Request {
    std::uint64_t id{};
    bool measured{};
    std::int64_t scheduled_ns{};
    std::uint32_t key{};
    bool heavy{};
};

struct RequestResult {
    Request request;
    std::string status{"pending"};
    std::int64_t submitted_ns{-1};
    std::int64_t started_ns{-1};
    std::int64_t completed_ns{-1};
    std::uint64_t value{};
};

struct PhaseResources {
    std::string phase;
    std::int64_t arrival_window_ns{};
    std::int64_t elapsed_ns{};
    std::int64_t cpu_user_ns{};
    std::int64_t cpu_system_ns{};
    std::uint64_t peak_rss_bytes{};
};

struct RunOutput {
    std::vector<RequestResult> requests;
    std::vector<PhaseResources> phases;
    TraceOutput trace;
};

std::vector<Request> read_workload(const std::filesystem::path& path,
                                 const ApplicationConfig& config,
                                 std::int64_t warmup_ns, std::int64_t measurement_ns);
RunOutput run_application(const ApplicationConfig& config,
                          const std::vector<Request>& requests,
                          std::int64_t warmup_ns, std::int64_t measurement_ns,
                          TraceConfig tracing = {});
void write_output(const std::filesystem::path& path, const RunOutput& output);

} // namespace jitter
