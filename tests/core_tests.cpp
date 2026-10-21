#include "jitter/experiment.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
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

void check_variants() {
    jitter::ApplicationConfig coarse;
    coarse.key_count = 8;
    coarse.light_rounds = 7;
    coarse.heavy_rounds = 100;
    auto narrow = coarse;
    narrow.implementation = "narrow";
    jitter::SharedIndex first(coarse), second(narrow);
    for (std::uint32_t key = 0; key < coarse.key_count; ++key) {
        for (bool heavy : {false, true}) {
            check(first.lookup(key, heavy) == second.lookup(key, heavy), "lock scope preserves digest contract");
        }
    }
    const auto events_for = [](const jitter::ApplicationConfig& config) {
        jitter::SharedIndex app(config);
        jitter::TraceBuffer buffer(1, 16, jitter::TraceClock::now());
        app.lookup(0, false, {&buffer, jitter::TracePhase::measurement, 0});
        std::vector<jitter::EventKind> events;
        for (const auto& event : buffer.take().events) events.push_back(event.kind);
        return events;
    };
    using Kind = jitter::EventKind;
    check(events_for(coarse) == std::vector<Kind>{Kind::lock_wait, Kind::lock_acquire,
          Kind::region_begin, Kind::region_end, Kind::lock_release}, "coarse trace scope");
    const std::vector<Kind> narrow_order{Kind::lock_wait, Kind::lock_acquire, Kind::lock_release,
                                        Kind::region_begin, Kind::region_end};
    check(events_for(narrow) == narrow_order, "narrow releases before digest");

    jitter::ApplicationConfig json;
    json.adapter = "json-projection-v1";
    json.implementation = "map";
    json.key_count = 8;
    json.light_fields = 1;
    json.heavy_fields = 256;
    auto ordered = json;
    ordered.implementation = "ordered";
    jitter::SharedIndex map_app(json), ordered_app(ordered);
    check(map_app.lookup(0, false) == UINT64_C(444051756), "JSON light independent golden value");
    check(map_app.lookup(0, true) == UINT64_C(16464020396125), "JSON heavy independent golden value");
    for (std::uint32_t key = 0; key < json.key_count; ++key) {
        for (bool heavy : {false, true}) {
            check(map_app.lookup(key, heavy) == ordered_app.lookup(key, heavy), "JSON variants preserve projection");
        }
    }
    check(events_for(json) == narrow_order && events_for(ordered) == narrow_order,
          "JSON parsing occurs after document lock release");
    auto maximum = json;
    maximum.light_fields = 8;
    maximum.heavy_fields = 4096;
    jitter::SharedIndex maximum_app(maximum);
    check(maximum_app.lookup(1, false) == UINT64_C(17208641377), "JSON multi-field golden value");
    check(maximum_app.lookup(7, true) == UINT64_C(4194286716285739), "JSON maximum-field golden value");
    std::vector<std::thread> threads;
    std::vector<std::uint64_t> responses(8);
    for (std::size_t i = 0; i < responses.size(); ++i) {
        threads.emplace_back([&, i] {
            for (int repeat = 0; repeat < 10; ++repeat) {
                responses[i] = ordered_app.lookup(static_cast<std::uint32_t>(i), true);
            }
        });
    }
    for (auto& thread : threads) thread.join();
    for (std::size_t i = 0; i < responses.size(); ++i) {
        check(responses[i] == map_app.lookup(static_cast<std::uint32_t>(i), true), "concurrent JSON immutable access");
    }
    for (const auto& invalid : {std::string("not-an-adapter"), std::string("shared-index-v1")}) {
        auto bad = json;
        bad.adapter = invalid;
        bool threw = false;
        try { jitter::SharedIndex app(bad); } catch (const std::invalid_argument&) { threw = true; }
        check(threw, "unsupported adapter or implementation rejected");
    }
    auto excessive = json;
    excessive.key_count = 65536;
    excessive.light_fields = excessive.heavy_fields = 4096;
    bool threw = false;
    try { jitter::SharedIndex app(excessive); } catch (const std::invalid_argument&) { threw = true; }
    check(threw, "JSON preparation memory bound");
    excessive = json;
    excessive.heavy_fields = 4097;
    threw = false;
    try { jitter::SharedIndex app(excessive); } catch (const std::invalid_argument&) { threw = true; }
    check(threw, "JSON field bound");
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

void check_tracing() {
    jitter::TraceBuffer buffer(7, 2, jitter::TraceClock::now());
    for (int i = 0; i < 5; ++i) buffer.emit(jitter::EventKind::request_submit, jitter::TracePhase::measurement, i, 0, i);
    const auto snapshot = buffer.take();
    check(snapshot.events.size() == 2 && snapshot.events[0].sequence == 0 && snapshot.events[1].sequence == 1,
          "bounded trace preserves its prefix");
    check(snapshot.counts[2].attempted == 5 && snapshot.counts[2].written == 2 && snapshot.counts[2].dropped == 3,
          "trace loss accounting");
    jitter::ApplicationConfig config;
    config.workers = 2;
    config.heavy_rounds = 100;
    std::vector<jitter::Request> input{{0, false, 0, 0, true}, {1, true, 0, 1, false}};
    const auto enabled = jitter::run_application(config, input, 1000000, 1000000, {true, 128});
    check(enabled.trace.buffers.size() == 3 && enabled.trace.phases.size() == 2, "trace writers and phase origins");
    std::size_t acquire = 0, release = 0, region = 0;
    for (const auto& writer : enabled.trace.buffers) {
        for (const auto& event : writer.events) {
            if (event.kind == jitter::EventKind::lock_acquire) ++acquire;
            if (event.kind == jitter::EventKind::lock_release) ++release;
            if (event.kind == jitter::EventKind::region_begin) ++region;
        }
        for (const auto& counts : writer.counts) check(counts.dropped == 0, "small trace is lossless");
    }
    check(acquire == 2 && release == 2 && region == 2, "every lookup records ownership and its digest region");
    const auto disabled = jitter::run_application(config, input, 1000000, 1000000);
    check(!disabled.trace.enabled && disabled.trace.reserved_bytes == 0 && disabled.trace.buffers.empty(),
          "disabled tracing allocates no event buffers");
    check(enabled.requests[0].value == disabled.requests[0].value && enabled.requests[1].value == disabled.requests[1].value,
          "tracing preserves responses");
    bool threw = false;
    try { jitter::TraceSession too_large({true, 1000000}, 256); }
    catch (const std::invalid_argument&) { threw = true; }
    check(threw, "trace memory bounds");
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
        check_variants();
        check_accounting();
        check_parser();
        check_tracing();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
