#pragma once

#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <mutex>
#include <vector>

namespace jitter {
using TraceClock = std::chrono::steady_clock;
constexpr std::uint64_t no_request = UINT64_MAX;
constexpr std::uint32_t index_lock = 1, request_queue = 2, digest_region = 3;

enum class TracePhase : std::uint8_t { lifecycle, warmup, measurement };
enum class EventKind : std::uint8_t {
    thread_start, thread_stop, worker_wait_begin, worker_wait_end,
    request_submit, request_reject, queue_enter, queue_exit,
    request_start, request_complete, lock_wait, lock_acquire, lock_release,
    region_begin, region_end
};

struct TraceConfig {
    bool enabled{false};
    std::uint32_t events_per_thread{32768};
};

struct TraceEvent {
    std::uint64_t sequence{};
    std::uint64_t request_id{no_request};
    std::int64_t timestamp_ns{};
    std::uint32_t resource_id{};
    EventKind kind{};
    TracePhase phase{};
};

static_assert(sizeof(TraceEvent) == 32, "trace format requires a 32-byte event");

struct TraceCounts {
    std::uint64_t attempted{}, written{}, dropped{};
};

struct BufferSnapshot {
    std::uint32_t thread_id{};
    std::uint32_t capacity{};
    std::array<TraceCounts, 3> counts{};
    std::vector<TraceEvent> events;
};

struct PhaseOrigin {
    TracePhase phase{};
    std::int64_t start_ns{}, end_ns{};
};

struct TraceOutput {
    bool enabled{};
    std::uint32_t capacity{}, thread_count{};
    std::uint64_t reserved_bytes{};
    std::vector<BufferSnapshot> buffers;
    std::vector<PhaseOrigin> phases;
};

// One owner writes each buffer. Read/export only after that owner has joined.
// The hot path has no allocations, mutexes, atomics, or background collector.
class alignas(64) TraceBuffer {
public:
    TraceBuffer(std::uint32_t thread_id, std::uint32_t capacity, TraceClock::time_point epoch);
    void emit(EventKind kind, TracePhase phase = TracePhase::lifecycle,
              std::uint64_t request = no_request, std::uint32_t resource = 0,
              std::int64_t timestamp = -1) noexcept;
    BufferSnapshot take();
private:
    BufferSnapshot data_;
    TraceClock::time_point epoch_;
    std::uint64_t attempted_{};
    std::size_t written_{};
};

struct TraceContext {
    TraceBuffer* buffer{};
    TracePhase phase{TracePhase::lifecycle};
    std::uint64_t request{no_request};
    void emit(EventKind kind, std::uint32_t resource = 0, std::int64_t timestamp = -1) const noexcept {
        if (buffer) buffer->emit(kind, phase, request, resource, timestamp);
    }
};

class TraceSession {
public:
    TraceSession(TraceConfig config, std::uint32_t thread_count);
    TraceBuffer* buffer(std::uint32_t thread_id);
    std::int64_t timestamp(TraceClock::time_point time) const;
    void phase(TracePhase phase, TraceClock::time_point start, std::int64_t elapsed_ns);
    TraceOutput take();
private:
    TraceConfig config_;
    std::uint32_t thread_count_;
    TraceClock::time_point epoch_{TraceClock::now()};
    std::vector<std::unique_ptr<TraceBuffer>> buffers_;
    std::vector<PhaseOrigin> phases_;
};

// Acquisition/release events delimit an observed ownership interval, a lower
// bound on real ownership: sampling happens after lock and before unlock.
class TracedLock {
public:
    TracedLock(std::mutex& mutex, TraceContext context, std::uint32_t resource);
    ~TracedLock();
    TracedLock(const TracedLock&) = delete;
    TracedLock& operator=(const TracedLock&) = delete;
private:
    std::mutex& mutex_;
    TraceContext context_;
    std::uint32_t resource_;
};

class TracedRegion {
public:
    TracedRegion(TraceContext context, std::uint32_t region) : context_(context), region_(region) {
        context_.emit(EventKind::region_begin, region_);
    }
    ~TracedRegion() { context_.emit(EventKind::region_end, region_); }
    TracedRegion(const TracedRegion&) = delete;
    TracedRegion& operator=(const TracedRegion&) = delete;
private:
    TraceContext context_;
    std::uint32_t region_;
};

void write_trace(const std::filesystem::path& directory, const TraceOutput& output);
} // namespace jitter
