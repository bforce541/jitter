#include "jitter/experiment.hpp"

#include <algorithm>
#include <charconv>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <fstream>
#include <functional>
#include <limits>
#include <mutex>
#include <stdexcept>
#include <string_view>
#include <thread>
#include <utility>
#include <sys/resource.h>

namespace jitter {
namespace {
using Clock = std::chrono::steady_clock;
using Nanoseconds = std::chrono::nanoseconds;

std::int64_t since(Clock::time_point start) {
    return std::chrono::duration_cast<Nanoseconds>(Clock::now() - start).count();
}

std::uint64_t number(std::string_view input) {
    std::uint64_t value{};
    const auto result = std::from_chars(input.data(), input.data() + input.size(), value);
    if (input.empty() || result.ec != std::errc{} || result.ptr != input.data() + input.size()) {
        throw std::invalid_argument("invalid integer in workload");
    }
    return value;
}

rusage usage() {
    rusage result{};
    if (getrusage(RUSAGE_SELF, &result) != 0) {
        throw std::runtime_error("getrusage failed");
    }
    return result;
}

std::int64_t nanos(timeval value) {
    return static_cast<std::int64_t>(value.tv_sec) * 1000000000 + value.tv_usec * 1000;
}

std::uint64_t rss(const rusage& value) {
#if defined(__APPLE__)
    return static_cast<std::uint64_t>(value.ru_maxrss);
#else
    return static_cast<std::uint64_t>(value.ru_maxrss) * 1024;
#endif
}

class WorkerPool {
public:
    explicit WorkerPool(const ApplicationConfig& config) : app_(config), capacity_(config.queue_capacity) {
        if (config.workers == 0 || capacity_ == 0) {
            throw std::invalid_argument("worker count and queue capacity must be positive");
        }
        try {
            for (std::uint32_t i = 0; i < config.workers; ++i) {
                workers_.emplace_back([this] { work(); });
            }
            std::unique_lock lock(mutex_);
            ready_.wait(lock, [this, &config] { return ready_count_ == config.workers; });
        } catch (...) {
            stop();
            throw;
        }
    }

    ~WorkerPool() { stop(); }
    WorkerPool(const WorkerPool&) = delete;
    WorkerPool& operator=(const WorkerPool&) = delete;

    bool submit(RequestResult& result, Clock::time_point start) {
        std::lock_guard lock(mutex_);
        // Submission time includes contention with the queue's admission lock.
        result.submitted_ns = since(start);
        if (queue_.size() >= capacity_) {
            result.status = "rejected";
            return false;
        }
        queue_.push_back({&result, start});
        ++outstanding_;
        available_.notify_one();
        return true;
    }

    void drain() {
        std::unique_lock lock(mutex_);
        drained_.wait(lock, [this] { return outstanding_ == 0; });
    }

private:
    void stop() {
        {
            std::lock_guard lock(mutex_);
            stopping_ = true;
        }
        available_.notify_all();
        for (auto& thread : workers_) {
            if (thread.joinable()) thread.join();
        }
    }

    void work() {
        {
            std::lock_guard lock(mutex_);
            ++ready_count_;
            ready_.notify_one();
        }
        for (;;) {
            std::pair<RequestResult*, Clock::time_point> item;
            {
                std::unique_lock lock(mutex_);
                available_.wait(lock, [this] { return stopping_ || !queue_.empty(); });
                if (queue_.empty()) return;
                item = queue_.front();
                queue_.pop_front();
            }
            auto& result = *item.first;
            result.started_ns = since(item.second);
            try {
                result.value = app_.lookup(result.request.key, result.request.heavy);
                result.status = "completed";
            } catch (...) {
                result.status = "error";
            }
            result.completed_ns = since(item.second);
            {
                std::lock_guard lock(mutex_);
                --outstanding_;
                if (outstanding_ == 0) drained_.notify_one();
            }
        }
    }

    SharedIndex app_;
    std::size_t capacity_;
    std::mutex mutex_;
    std::condition_variable available_, drained_, ready_;
    std::deque<std::pair<RequestResult*, Clock::time_point>> queue_;
    std::vector<std::thread> workers_;
    std::size_t outstanding_{};
    std::uint32_t ready_count_{};
    bool stopping_{};
};
} // namespace

std::vector<Request> read_workload(const std::filesystem::path& path,
                                 const ApplicationConfig& config,
                                 std::int64_t warmup_ns, std::int64_t measurement_ns) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open workload");
    std::string line;
    if (!std::getline(input, line) || line != "id,phase,scheduled_ns,key,heavy") {
        throw std::invalid_argument("invalid workload header");
    }
    std::vector<Request> requests;
    bool measurement_seen = false;
    std::int64_t last_schedule = -1;
    bool previous_phase = false;
    while (std::getline(input, line)) {
        std::vector<std::string_view> fields;
        std::string_view remaining(line);
        for (;;) {
            const auto comma = remaining.find(',');
            fields.push_back(remaining.substr(0, comma));
            if (comma == std::string_view::npos) break;
            remaining.remove_prefix(comma + 1);
        }
        if (fields.size() != 5 || (fields[1] != "warmup" && fields[1] != "measurement")) {
            throw std::invalid_argument("invalid workload row");
        }
        const auto id = number(fields[0]);
        const bool measured = fields[1] == "measurement";
        const auto schedule = number(fields[2]);
        const auto key = number(fields[3]);
        const auto heavy = number(fields[4]);
        if (id != requests.size() || key >= config.key_count || heavy > 1 ||
            schedule >= static_cast<std::uint64_t>(measured ? measurement_ns : warmup_ns) ||
            (measurement_seen && !measured)) {
            throw std::invalid_argument("workload violates request contract");
        }
        if (measured != previous_phase) last_schedule = -1;
        if (schedule < static_cast<std::uint64_t>(std::max<std::int64_t>(0, last_schedule))) {
            throw std::invalid_argument("workload schedules must be nondecreasing");
        }
        requests.push_back({id, measured, static_cast<std::int64_t>(schedule),
                            static_cast<std::uint32_t>(key), heavy == 1});
        previous_phase = measured;
        last_schedule = static_cast<std::int64_t>(schedule);
        measurement_seen = measurement_seen || measured;
        if (requests.size() > 1000000) throw std::invalid_argument("workload exceeds one million requests");
    }
    if (!measurement_seen) throw std::invalid_argument("measurement workload is empty");
    return requests;
}

RunOutput run_application(const ApplicationConfig& config,
                          const std::vector<Request>& requests,
                          std::int64_t warmup_ns, std::int64_t measurement_ns) {
    RunOutput output;
    output.requests.reserve(requests.size());
    for (const auto& request : requests) output.requests.push_back({request});
    WorkerPool pool(config);
    for (const bool measured : {false, true}) {
        const auto duration = measured ? measurement_ns : warmup_ns;
        if (duration == 0) continue;
        const auto before = usage();
        const auto start = Clock::now();
        for (auto& result : output.requests) {
            if (result.request.measured != measured) continue;
            // Open-loop: intended arrivals never wait for previous completions.
            std::this_thread::sleep_until(start + Nanoseconds(result.request.scheduled_ns));
            pool.submit(result, start);
        }
        std::this_thread::sleep_until(start + Nanoseconds(duration));
        pool.drain(); // No warmup work leaks into the measurement phase.
        const auto elapsed = since(start);
        const auto after = usage();
        output.phases.push_back({measured ? "measurement" : "warmup", duration, elapsed,
            nanos(after.ru_utime) - nanos(before.ru_utime),
            nanos(after.ru_stime) - nanos(before.ru_stime), rss(after)});
    }
    return output;
}

void write_output(const std::filesystem::path& path, const RunOutput& output) {
    std::filesystem::create_directories(path);
    std::ofstream requests(path / "requests.csv");
    requests << "id,phase,scheduled_ns,key,heavy,status,submitted_ns,started_ns,completed_ns,value\n";
    for (const auto& result : output.requests) {
        requests << result.request.id << ',' << (result.request.measured ? "measurement" : "warmup")
                 << ',' << result.request.scheduled_ns << ',' << result.request.key << ','
                 << result.request.heavy << ',' << result.status << ',' << result.submitted_ns << ','
                 << result.started_ns << ',' << result.completed_ns << ',' << result.value << '\n';
    }
    std::ofstream resources(path / "resources.csv");
    resources << "phase,arrival_window_ns,elapsed_ns,cpu_user_ns,cpu_system_ns,peak_rss_bytes\n";
    for (const auto& phase : output.phases) {
        resources << phase.phase << ',' << phase.arrival_window_ns << ',' << phase.elapsed_ns << ','
                  << phase.cpu_user_ns << ',' << phase.cpu_system_ns << ',' << phase.peak_rss_bytes << '\n';
    }
    requests.flush();
    resources.flush();
    if (!requests || !resources) throw std::runtime_error("cannot write experiment output");
}

} // namespace jitter
