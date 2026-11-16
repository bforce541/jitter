#pragma once

#include "jitter/market.hpp"

#include <chrono>
#include <map>
#include <optional>
#include <stdexcept>
#include <string_view>

namespace jitter::market {

using ReceiverClock = std::chrono::steady_clock;
using ReceiverTime = ReceiverClock::time_point;

struct Arrival {
    Event event;
    ReceiverTime received, decoded;
    char channel;
};

struct Publication {
    Update update;
    ReceiverTime received, decoded, apply_started, published;
    bool replayed{};
    char channel;
};

class Receiver {
public:
    Receiver(std::size_t capacity, ReceiverClock::duration gap_wait)
        : capacity_(capacity), gap_wait_(gap_wait), recent_(capacity) {
        if (!capacity || gap_wait <= ReceiverClock::duration::zero())
            throw std::invalid_argument("invalid receiver bounds");
    }

    bool initialize(const Entries& initial) {
        if (initialized_) return false;
        initialized_ = feed_.snapshot(0, initial);
        return initialized_;
    }

    bool current() const { return active() && !deadline_; }
    bool waiting() const { return active() && deadline_.has_value(); }
    std::string_view reason() const { return reason_; }
    std::uint64_t last() const { return next_ - 1; }
    std::size_t backlog() const { return pending_.size(); }
    std::size_t peak_backlog() const { return peak_; }
    std::size_t duplicates() const { return duplicates_; }
    std::size_t discarded() const { return discarded_; }
    const DenseBook& book() const {
        if (!current()) throw std::logic_error("book is stale");
        return feed_.book();
    }

    void expire(ReceiverTime now) {
        if (active() && deadline_ && now >= *deadline_) reason_ = "gap-timeout";
    }

    void receive(Arrival arrival, ReceiverTime policy_now, std::vector<Publication>& output) {
        if (!initialized_) throw std::logic_error("receiver has no initial snapshot");
        expire(policy_now);
        const auto seq = arrival.event.sequence;
        if (seq < next_) {
            const auto& old = recent_[seq % capacity_];
            if (old.sequence == seq && !equal(old, arrival.event)) quarantine(seq, "conflicting-copy");
            else ++duplicates_;
            return;
        }
        if (auto it = pending_.find(seq); it != pending_.end()) {
            if (equal(it->second.event, arrival.event)) ++duplicates_;
            else {
                pending_.erase(it);
                quarantine(seq, "conflicting-copy");
            }
            return;
        }
        if (active() && seq == next_) {
            if (!apply(arrival, output)) return;
            while (!pending_.empty() && pending_.begin()->first == next_) {
                const auto entry = pending_.begin()->second;
                pending_.erase(pending_.begin());
                if (!apply(entry, output)) return;
            }
            if (pending_.empty()) deadline_.reset();
            else if (!deadline_) deadline_ = policy_now + gap_wait_;
            return;
        }
        if (pending_.size() == capacity_) {
            ++discarded_;
            quarantine(seq, "buffer-exhausted");
            return;
        }
        pending_.emplace(seq, arrival);
        peak_ = std::max(peak_, pending_.size());
        if (active() && !deadline_) deadline_ = policy_now + gap_wait_;
    }

    std::string_view recover(std::uint64_t through, const Entries& entries, std::vector<Publication>& output) {
        if (!initialized_ || active()) return "recovery-unavailable";
        if (through < last() || through < snapshot_floor_) return "snapshot-too-old";
        Feed<DenseBook> candidate;
        if (!candidate.snapshot(through, entries)) return "invalid-snapshot";
        auto cursor = through + 1;
        std::vector<Publication> staged;
        for (auto it = pending_.upper_bound(through); it != pending_.end(); ++it) {
            if (it->first != cursor) return "remaining-gap";
            const auto& arrival = it->second;
            const auto start = ReceiverClock::now();
            const auto update = candidate.on_event(arrival.event);
            if (update.status != Status::published) return "invalid-buffered-update";
            staged.push_back({update, arrival.received, arrival.decoded, start, {}, true, arrival.channel});
            ++cursor;
        }
        feed_ = std::move(candidate);
        next_ = cursor;
        std::fill(recent_.begin(), recent_.end(), Event{});
        for (auto it = pending_.upper_bound(through); it != pending_.end(); ++it)
            recent_[it->first % capacity_] = it->second.event;
        pending_.clear();
        deadline_.reset();
        snapshot_floor_ = 0;
        reason_ = {};
        for (auto& item : staged) {
            output.push_back(item);
            output.back().published = ReceiverClock::now();
        }
        return "snapshot-applied";
    }

private:
    bool active() const { return initialized_ && reason_.empty(); }
    static bool equal(const Event& a, const Event& b) {
        return a.sequence == b.sequence && a.kind == b.kind && a.id == b.id && a.order == b.order;
    }
    void quarantine(std::uint64_t through, std::string_view why) {
        snapshot_floor_ = std::max(through, snapshot_floor_);
        reason_ = why;
    }
    bool apply(const Arrival& arrival, std::vector<Publication>& output) {
        const auto start = ReceiverClock::now();
        const auto update = feed_.on_event(arrival.event);
        if (update.status != Status::published) {
            quarantine(arrival.event.sequence, "invalid-update");
            return false;
        }
        recent_[next_ % capacity_] = arrival.event;
        ++next_;
        output.push_back({update, arrival.received, arrival.decoded, start, {}, false, arrival.channel});
        output.back().published = ReceiverClock::now();
        return true;
    }

    std::size_t capacity_, peak_{}, duplicates_{}, discarded_{};
    ReceiverClock::duration gap_wait_;
    std::vector<Event> recent_;
    Feed<DenseBook> feed_;
    std::uint64_t next_{1}, snapshot_floor_{};
    std::optional<ReceiverTime> deadline_;
    std::string_view reason_;
    bool initialized_{};
    std::map<std::uint64_t, Arrival> pending_;
};
}
