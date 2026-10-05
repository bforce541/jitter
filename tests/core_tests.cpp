#include "jitter/experiment.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {
void check(bool value, const std::string& message) {
    if (!value) throw std::runtime_error(message);
}

void check_digest() {
    jitter::ApplicationConfig config;
    config.dataset_seed = 17;
    config.key_count = 2;
    config.light_rounds = 1;
    config.heavy_rounds = 2;
    jitter::SharedIndex app(config);
    check(jitter::initial_value(17, 0) == UINT64_C(9260656408219841379), "dataset golden value");
    check(app.lookup(0, false) == UINT64_C(15189048924701222248), "light digest golden value");
    check(app.lookup(0, true) == UINT64_C(7502008099585157961), "heavy digest golden value");
    bool threw = false;
    try { app.lookup(2, false); } catch (const std::out_of_range&) { threw = true; }
    check(threw, "out-of-range lookup must fail");
}

void check_accounting() {
    jitter::ApplicationConfig config;
    config.workers = 2;
    config.queue_capacity = 1;
    config.heavy_rounds = 100000;
    std::vector<jitter::Request> requests;
    for (std::uint64_t i = 0; i < 1000; ++i) requests.push_back({i, true, 0, 0, true});
    const auto output = jitter::run_application(config, requests, 0, 1000000);
    std::size_t completed = 0, rejected = 0;
    for (const auto& result : output.requests) {
        check(result.submitted_ns >= 0, "every request was offered");
        if (result.status == "completed") {
            ++completed;
            check(result.completed_ns >= result.started_ns && result.started_ns >= result.submitted_ns,
                  "monotonic request timestamps");
        } else {
            ++rejected;
            check(result.status == "rejected" && result.started_ns == -1 && result.completed_ns == -1,
                  "rejection contract");
        }
    }
    check(completed + rejected == 1000 && completed > 0 && rejected > 0, "overload accounting");
    check(output.phases.size() == 1 && output.phases[0].elapsed_ns >= 1000000, "arrival window and drain");
}

void check_parser() {
    const auto path = std::filesystem::temp_directory_path() /
        ("jitter-core-" + std::to_string(getpid()) + ".csv");
    const std::vector<std::string> invalid{
        "id,phase,scheduled_ns,key,heavy\n0,measurement,0,0,0\n0,measurement,1,0,0\n",
        "invalid header\n",
        "id,phase,scheduled_ns,key,heavy\n0,measurement,1000,0,0\n",
        "id,phase,scheduled_ns,key,heavy\n0,measurement,0,256,0\n",
        "id,phase,scheduled_ns,key,heavy\n0,measurement,0,0,2\n",
        "id,phase,scheduled_ns,key,heavy\n0,measurement,2,0,0\n1,measurement,1,0,0\n",
        "id,phase,scheduled_ns,key,heavy\n0,measurement,-1,0,0\n",
        "id,phase,scheduled_ns,key,heavy\n0,measurement,0,0,0\n1,warmup,0,0,0\n"
    };
    for (const auto& contents : invalid) {
        { std::ofstream out(path); out << contents; }
        bool threw = false;
        try { jitter::read_workload(path, {}, 1000, 1000); }
        catch (const std::invalid_argument&) { threw = true; }
        check(threw, "invalid workload must fail");
    }
    {
        std::ofstream out(path);
        out << "id,phase,scheduled_ns,key,heavy\n0,warmup,5,0,1\n1,measurement,0,1,0\n";
    }
    const auto valid = jitter::read_workload(path, {}, 1000, 1000);
    check(valid.size() == 2 && !valid[0].measured && valid[1].measured, "phase schedule reset");
    std::filesystem::remove(path);
}
}

int main() {
    try {
        check_digest();
        check_accounting();
        check_parser();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
