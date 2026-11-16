#include "jitter/receiver.hpp"

#include <iostream>

using namespace jitter::market;
using namespace std::chrono_literals;

int main() {
    try {
        auto check = [](bool valid) { if (!valid) throw std::logic_error("receiver invariant failed"); };
        const auto input = generate(10, 17);
        Feed<ReferenceBook> oracle;
        check(oracle.snapshot(0, input.initial));
        std::vector<Entries> snapshots{input.initial};
        std::vector<Update> expected;
        for (const auto& event : input.events) {
            expected.push_back(oracle.on_event(event));
            snapshots.push_back(oracle.book().entries());
        }
        Receiver rx(2, 3ms);
        std::vector<Publication> output;
        check(rx.initialize(input.initial));
        check(!rx.initialize(input.initial));
        const auto zero = ReceiverTime{};
        auto push = [&](std::size_t seq, auto time) {
            rx.receive({input.events.at(seq - 1), zero, zero, 'A'}, zero + time, output);
        };
        push(2, 0ms);
        check(rx.waiting() && !rx.current() && output.empty());
        push(2, 1ms);
        push(1, 2ms);
        check(rx.current() && output.size() == 2 && output[1].update == expected[1]);
        push(4, 3ms);
        push(3, 6ms); // Deadline is exclusive; late arrival cannot publish.
        check(rx.reason() == "gap-timeout" && output.size() == 2);
        push(5, 7ms); // Third future update exceeds the buffer.
        check(rx.reason() == "buffer-exhausted" && rx.discarded() == 1);
        check(rx.recover(4, snapshots[4], output) == "snapshot-too-old" && !rx.current());
        check(rx.recover(5, {{1, {Side::bid, 0, 1}}}, output) == "invalid-snapshot");
        push(6, 8ms); // Still full. Snapshot must include this loss as well.
        check(rx.recover(5, snapshots[5], output) == "snapshot-too-old");
        check(rx.recover(6, snapshots[6], output) == "snapshot-applied" && output.size() == 2);
        push(8, 9ms);
        rx.expire(zero + 12ms);
        push(9, 13ms);
        check(rx.recover(6, snapshots[6], output) == "remaining-gap" && output.size() == 2);
        check(rx.recover(7, snapshots[7], output) == "snapshot-applied");
        check(output.size() == 4 && output[2].update == expected[7] && output[3].update == expected[8]);
        check(rx.current() && rx.book().entries() == snapshots[9]);
        auto conflicting = input.events[8];
        conflicting.order.quantity += 1;
        rx.receive({conflicting, zero, zero, 'B'}, zero + 14ms, output);
        check(!rx.current() && rx.reason() == "conflicting-copy" && output.size() == 4);
        check(rx.recover(8, snapshots[8], output) == "snapshot-too-old");
        check(rx.recover(9, snapshots[9], output) == "snapshot-applied");
        push(10, 15ms);
        check(rx.current() && output.back().update == expected[9] && rx.book().entries() == snapshots[10]);
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
