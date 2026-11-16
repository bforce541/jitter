# Redundant UDP and execution audit results

Measured November 15, 2026 on a native Apple M4 Pro, macOS 26.6.2 / Darwin 25.6.0, Apple clang 21.0.0, CMake 4.1.1. The clean confirmation build at `3f7b6d0` used Release `-O3 -DNDEBUG -std=c++20 -arch arm64`. The [raw confirmation](evidence/desk/udp-confirmation-arm64.json) retains all 40 invocations, binary identity, process CPU time, arrival and publication counts and representative worst-event timestamps. The [initial run](evidence/desk/udp-initial-arm64.json) preserves the earlier three-pair campaign at `01c62a9`. The runs used `caffeinate -i` without pinning cores or controlling background activity.

## Steady and burst replay

Five repeats per row, seeds 17–21, 40,000 events per run at 100,000 average offered events/s. All 20 confirmation runs published all 40,000 events, with zero duplicate publications, malformed packets or unplanned missing datagrams. No ordinary-path update entered the reorder buffer in this block. Latency values are medians of per-run percentiles.

| Delivery | Burst | Receiver p99 / p99.9 (µs) | Local send-to-receipt p99 / p99.9 (µs) | Scheduled publication p99 / p99.9 (µs) | Median process CPU (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| A only | 1 | 0.208 / 2.125 | 15.459 / 26.166 | 16.541 / 39.291 | 399.286 |
| A + B | 1 | 0.084 / 1.541 | 15.875 / 20.500 | 16.125 / 74.209 | 399.781 |
| A only | 32 | 0.125 / 1.708 | 78.167 / 125.875 | 103.375 / 170.584 | 175.032 |
| A + B | 32 | 0.125 / 1.333 | 138.959 / 335.458 | 177.750 / 420.958 | 269.871 |

The receiver clock starts after `recv()` returns. It can observe fast book publication while the simulator spends substantially longer sending or waiting to drain. The burst32 dual-feed replay also used more CPU than the single-feed replay. Each dual-feed event generates two datagrams and one published update. Per-stage quantiles are marginal, so adding them does not recover an end-to-end quantile. [Timing definitions](desk-incident.md#measure-and-reproduce).

## What bounded drains changed

A 4,096-event local burst exposed the weakest boundary. Sending the entire burst before draining let thousands of datagrams accumulate before receiving the first ones. The candidate services both sockets every 32 events. The five confirmation pairs used the same executable, seeds, input and release schedules. All 400,000 offered events published exactly once across the ten runs, with no unplanned missing datagrams.

| Dual-feed burst 4,096 | Drain at end | Drain every 32 |
| --- | ---: | ---: |
| Local send-to-receipt p99 | 18.554 ms | 0.147 ms |
| Local send-to-receipt p99.9 | 19.889 ms | 0.167 ms |
| Sender lateness p99.9 | 24.493 ms | 24.537 ms |
| Scheduled publication p99 / p99.9 | 26.415 / 26.595 ms | 23.839 / 24.611 ms |
| Largest observed sent-minus-received count on one feed | 4,096 | 90 |
| Median process CPU time | 193.466 ms | 180.584 ms |

In the drain-at-end worst local sample, sequence 16,385 spent 22.137 ms between entering the send path and returning from receive. It spent another 42 ns decoding, 125 ns from decode to apply, and 5.125 µs applying and publishing. In the bounded-drain worst local sample, sequence 26,657 reached receive in 2.892 ms, after 20.744 ms of sender lateness. Large bursts still delay their own later sends in this serial simulator. The earlier campaign also reduced local delivery p99 (17.089 → 0.128 ms) while scheduled p99.9 was effectively flat (28.801 → 28.993 ms). The retained change reduces outstanding transport work. The results support no general scheduled latency or network claim.

## Detection, recovery and positions

In all five dual-feed fault repeats, B supplied A's omitted sequence 2 exactly once. The dual omission of sequence 5 stopped normal publication. Detection took **4.039–5.033 ms** from the start of the omission step, including the requested wall-clock sleep and observed wakeup. Once detected, snapshot and sparse-reference verification took **68.917–126.334 µs**. Recovery matched the full book at sequence 7, then finished at sequence 12 with eleven delta publications and zero duplicates. Sequence 5 is represented by the snapshot.

The independently generated desk and venue views confirmed case EX-02 **50 ms** after first observation and resolved it **40 ms** after confirmation on the simulation clock. ABC's desk-minus-venue position difference went **−30 → 0**. Both sides finished **ABC +70 and XYZ +5**, with no open discrepancy. Reimporting the six original execution records left the reconciled positions and transition history unchanged. The [demo JSON](evidence/desk/incident-arm64.json) records reconciliation execution time separately. The [operator CSV](evidence/desk/discrepancies.csv), [desk input](evidence/desk/desk-bookings.csv), [initial drop copy](evidence/desk/venue-initial.csv) and [late drop copy](evidence/desk/venue-late.csv) are sufficient to reproduce the execution audit with the documented grace and clocks.
