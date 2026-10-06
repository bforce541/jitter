#include "jitter/trace.hpp"

#include <fstream>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace jitter {
namespace {
constexpr std::array<std::string_view, 3> phase_names{"lifecycle", "warmup", "measurement"};
constexpr std::array<std::string_view, 15> kind_names{
    "thread_start", "thread_stop", "worker_wait_begin", "worker_wait_end",
    "request_submit", "request_reject", "queue_enter", "queue_exit", "request_start",
    "request_complete", "lock_wait", "lock_acquire", "lock_release", "region_begin", "region_end"};
}

TraceBuffer::TraceBuffer(std::uint32_t thread_id, std::uint32_t capacity, TraceClock::time_point epoch)
    : epoch_(epoch) {
    if (capacity == 0) throw std::invalid_argument("trace buffer capacity must be positive");
    data_.thread_id = thread_id;
    data_.capacity = capacity;
    data_.events.resize(capacity);
}

void TraceBuffer::emit(EventKind kind, TracePhase phase, std::uint64_t request,
                       std::uint32_t resource, std::int64_t timestamp) noexcept {
    auto& counts = data_.counts[static_cast<std::size_t>(phase)];
    const auto sequence = attempted_++;
    ++counts.attempted;
    if (written_ == data_.capacity) {
        ++counts.dropped; // Drop new events; never overwrite earlier evidence.
        return;
    }
    if (timestamp < 0) {
        timestamp = std::chrono::duration_cast<std::chrono::nanoseconds>(TraceClock::now() - epoch_).count();
    }
    data_.events[written_++] = {sequence, request, timestamp, resource, kind, phase};
    ++counts.written;
}

BufferSnapshot TraceBuffer::take() {
    data_.events.resize(written_);
    return std::move(data_);
}

TraceSession::TraceSession(TraceConfig config, std::uint32_t thread_count)
    : config_(config), thread_count_(thread_count) {
    const auto bytes = static_cast<std::uint64_t>(config.events_per_thread) * thread_count * sizeof(TraceEvent);
    if (thread_count == 0 || config.events_per_thread == 0 || config.events_per_thread > 1000000 ||
        (config.enabled && bytes > UINT64_C(268435456))) {
        throw std::invalid_argument("trace configuration exceeds supported bounds (256 MiB)");
    }
    if (!config.enabled) return;
    for (std::uint32_t id = 0; id < thread_count; ++id) {
        buffers_.push_back(std::make_unique<TraceBuffer>(id, config.events_per_thread, epoch_));
    }
    buffers_[0]->emit(EventKind::thread_start);
}

TraceBuffer* TraceSession::buffer(std::uint32_t thread_id) {
    return config_.enabled ? buffers_.at(thread_id).get() : nullptr;
}

std::int64_t TraceSession::timestamp(TraceClock::time_point time) const {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(time - epoch_).count();
}

void TraceSession::phase(TracePhase phase, TraceClock::time_point start, std::int64_t elapsed_ns) {
    if (config_.enabled) phases_.push_back({phase, timestamp(start), timestamp(start) + elapsed_ns});
}

TraceOutput TraceSession::take() {
    TraceOutput output{config_.enabled, config_.events_per_thread, thread_count_,
        config_.enabled ? static_cast<std::uint64_t>(config_.events_per_thread) * thread_count_ * sizeof(TraceEvent) : 0, {}, {}};
    if (config_.enabled) {
        buffers_[0]->emit(EventKind::thread_stop);
        for (auto& buffer : buffers_) output.buffers.push_back(buffer->take());
        output.phases = std::move(phases_);
    }
    return output;
}

TracedLock::TracedLock(std::mutex& mutex, TraceContext context, std::uint32_t resource)
    : mutex_(mutex), context_(context), resource_(resource) {
    context_.emit(EventKind::lock_wait, resource_);
    mutex_.lock();
    context_.emit(EventKind::lock_acquire, resource_);
}

TracedLock::~TracedLock() {
    context_.emit(EventKind::lock_release, resource_);
    mutex_.unlock();
}

void write_trace(const std::filesystem::path& directory, const TraceOutput& output) {
    for (const auto* name : {"trace-info.json", "events.csv", "trace-phases.csv"}) {
        if (std::filesystem::exists(directory / name)) throw std::runtime_error("refusing to overwrite trace evidence");
    }
    std::ofstream info(directory / "trace-info.json");
    info << "{\"schema_version\":1,\"enabled\":" << (output.enabled ? "true" : "false")
         << ",\"event_size_bytes\":" << sizeof(TraceEvent) << ",\"capacity_per_thread\":" << output.capacity
         << ",\"thread_count\":" << output.thread_count << ",\"reserved_bytes\":" << output.reserved_bytes
         << ",\"clock\":\"steady_clock_nanoseconds\",\"retention\":\"prefix_drop_new\",\"buffers\":[";
    bool first = true;
    for (const auto& buffer : output.buffers) {
        if (!first) info << ',';
        first = false;
        info << "{\"thread_id\":" << buffer.thread_id << ",\"capacity\":" << buffer.capacity << ",\"phases\":{";
        for (std::size_t phase = 0; phase < 3; ++phase) {
            if (phase) info << ',';
            const auto& counts = buffer.counts[phase];
            info << '\"' << phase_names[phase] << "\":{\"attempted\":" << counts.attempted
                 << ",\"written\":" << counts.written << ",\"dropped\":" << counts.dropped << '}';
        }
        info << "}}";
    }
    info << "]}\n";
    info.flush();
    if (!info) throw std::runtime_error("cannot write trace metadata");
    if (!output.enabled) return;
    std::ofstream events(directory / "events.csv");
    events << "thread_id,sequence,phase,timestamp_ns,request_id,kind,resource_id\n";
    for (const auto& buffer : output.buffers) {
        for (const auto& event : buffer.events) {
            events << buffer.thread_id << ',' << event.sequence << ',' << phase_names[static_cast<std::size_t>(event.phase)]
                   << ',' << event.timestamp_ns << ',' << event.request_id << ','
                   << kind_names[static_cast<std::size_t>(event.kind)] << ',' << event.resource_id << '\n';
        }
    }
    std::ofstream phases(directory / "trace-phases.csv");
    phases << "phase,start_ns,end_ns\n";
    for (const auto& phase : output.phases) {
        phases << phase_names[static_cast<std::size_t>(phase.phase)] << ',' << phase.start_ns << ',' << phase.end_ns << '\n';
    }
    events.flush();
    phases.flush();
    if (!events || !phases) throw std::runtime_error("cannot write trace events");
}
} // namespace jitter
