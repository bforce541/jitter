#include "jitter/market.hpp"

#include <iostream>
#include <stdexcept>

using namespace jitter::market;

void check(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

template<class Book>
void recovery() {
    Feed<Book> feed;
    check(feed.on_event({1, Kind::add, 1, {Side::bid, 64, 12}}) ==
          Update{1, Status::published, {{64, 12}, {}}}, "bid add");
    check(feed.on_event({2, Kind::add, 2, {Side::ask, 1024, 10}}).top.ask == Quote{1024, 10}, "ask add");
    check(feed.on_event({3, Kind::execute, 1, {{}, 0, 5}}).top.bid == Quote{64, 7}, "partial fill");
    check(feed.on_event({3, Kind::cancel, 1}).status == Status::duplicate, "duplicate must not cancel");
    check(feed.on_event({5, Kind::cancel, 1}).status == Status::gap && feed.stale(), "gap");
    check(feed.on_event({7, Kind::cancel, 1}).status == Status::stale, "discard while stale");
    const Entries reset{{3, {Side::bid, 512, 100}}, {4, {Side::ask, 513, 40}}};
    check(!feed.snapshot(6, reset) && feed.stale(), "snapshot must cover discarded event");
    auto malformed = reset;
    malformed.push_back(reset.front());
    check(!feed.snapshot(7, malformed) && feed.stale(), "duplicate IDs in snapshot are invalid");
    check(feed.book().top().bid == Quote{64, 7}, "failed snapshot must be atomic");
    check(feed.snapshot(7, reset) && !feed.stale(), "recover snapshot");
    check(feed.on_event({8, Kind::execute, 3, {{}, 0, 100}}).top.bid == Quote{}, "full fill after recovery");
    check(feed.on_event({9, Kind::cancel, 4}).top.ask == Quote{}, "cancel after recovery");
    check(feed.on_event({10, Kind::execute, 4, {{}, 0, 1}}).status == Status::invalid, "unknown order invalid");
}

void recovery_replay(std::uint64_t seed) {
    const auto replay = generate(2100, seed);
    Feed<ReferenceBook> truth;
    Feed<ReferenceBook> reference;
    Feed<DenseBook> dense;
    check(truth.snapshot(0, replay.initial) && reference.snapshot(0, replay.initial) &&
          dense.snapshot(0, replay.initial), "seeded snapshots");
    for (const auto& event : replay.events) {
        const auto expected = truth.on_event(event);
        if (event.sequence == 1000 || event.sequence == 1001) continue;
        const auto a = reference.on_event(event);
        const auto b = dense.on_event(event);
        check(a == b, "feed statuses differ");
        if (event.sequence == 1002) {
            check(a.status == Status::gap && a.top == Top{}, "detected gap suppresses publication");
        } else if (event.sequence <= 1020 && event.sequence > 1002) {
            check(a.status == Status::stale && a.top == Top{}, "stale suppression");
        } else {
            check(a == expected, "unexpected publication after recovery");
            check(reference.book().entries() == dense.book().entries() &&
                  dense.book().entries() == truth.book().entries(), "canonical state differs after update");
        }
        if (event.sequence == 700)
            check(reference.on_event(event).status == Status::duplicate &&
                  dense.on_event(event).status == Status::duplicate, "suppressed duplicate");
        if (event.sequence == 1020) {
            const auto snapshot = truth.book().entries();
            check(reference.snapshot(event.sequence, snapshot) && dense.snapshot(event.sequence, snapshot),
                  "authoritative snapshot must recover");
            check(reference.book().entries() == snapshot && dense.book().entries() == snapshot,
                  "recovered canonical state differs");
        }
    }
}

int main() {
    try {
        recovery<ReferenceBook>();
        recovery<DenseBook>();
        for (const auto seed : {0, 17, 2028, 4321}) {
            verify(generate(40000, seed));
            recovery_replay(seed);
        }
        auto replay = generate(3000, 17);
        check(fingerprint(replay) == fingerprint(generate(3000, 17)), "deterministic replay");
        check(fingerprint(replay) != fingerprint(generate(3000, 18)), "seed affects replay");
        std::cout << "Market book, per-event differential replay and recovery checks passed\n";
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
