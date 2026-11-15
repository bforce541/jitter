# Market-data replay and order-book performance

Jitter’s market engine replays a deterministic, order-by-order synthetic feed into a single-instrument, two-sided limit book. It compares a sparse reference representation with a preallocated dense representation. The benchmark targets state reconstruction and publication, not matching, order entry, a trading strategy, or exchange connectivity.

The benchmark is a separate C++20 executable, `jitter_market`. It extends Jitter’s open-loop measurement approach and reuses the Python runner’s paired median bounds. Existing `jitter` commands and adapters are unchanged. Market studies emit their own JSON schemas; the existing dashboard and `jitter investigate` do not ingest them.

## Run

On Apple Silicon use the explicit architecture flag below. On Linux omit `-DCMAKE_OSX_ARCHITECTURES=arm64`.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure

# Single-thread input already in memory: order mutation and top-of-book publication.
build/jitter_market --mode direct --book dense --events 100000 --warmup 20000 --seed 17

# Two-thread feed: 32 events every 64 µs, averaging 500,000 offered events/s.
build/jitter_market --mode feed --book dense --events 100000 --warmup 20000 --rate 500000 --burst 32 --seed 17

# Eight same-input pairs per scenario plus the initial rate sweep.
python3 tools/market_study.py --binary build/jitter_market --output results/market-paired

# Longer overload sweep. Output paths must be new.
python3 tools/market_study.py --binary build/jitter_market --output results/market-saturation \
  --sweep-only --sweep-rate 4000000 --sweep-rate 6000000 --sweep-rate 8000000 \
  --sweep-rate 10000000 --sweep-events 1000000 --sweep-repeats 2
```

## What the engine guarantees

- Add creates a live order with a positive ID, tick and quantity. Cancel removes its remaining quantity. Execution removes an explicitly specified positive quantity and can partially fill or fully remove the order. Duplicate live IDs, unknown cancel/execute IDs, and overfills are invalid. IDs can be reused after removal. Ticks are integers from 1 to 1,024 and IDs are from 1 to 16,383. Both books maintain aggregate volume per side and tick; they publish best bid and ask, each with its aggregate quantity. Incoming executions already identify the resting order. There is no price/time matching logic.
- The feed starts at sequence 1. An older sequence is a duplicate and does not mutate or publish. A forward gap or invalid event at the expected sequence marks the feed stale. Subsequent deltas are discarded without quote publication. An authoritative snapshot declares the last sequence it includes and must cover the highest discarded sequence. Validation builds a replacement off to the side; malformed or too-old snapshots cannot partially change the book. The next delta must follow the snapshot. There is no hidden buffering of stale deltas.
- The seeded generator starts with 1,024 live orders and evolves 2,048 possible IDs over 1,024 ticks, with both sides, cancellations, full fills and partial fills. SplitMix64 and explicitly ordered integer operations make the stream reproducible without a dataset. JSON includes a deterministic fingerprint of the initial snapshot and events; it is an identity check, not a cryptographic digest. The recovery tests derive a subsequent authoritative snapshot from the complete reference stream, deliberately omit two deltas, inject a duplicate, discard additional stale deltas and verify the recovered book at every subsequent publication.
- Every measured, healthy publication and final book is checked against a separate sparse reference. Seeded differential tests compare published updates at every event and canonical entries throughout replay. The targeted seeded recovery tests compare canonical entries at **every** successfully published event and after each replacement snapshot. Profiling mode intentionally drops timestamping and uses a digest to keep the loop observable; its output is not a latency benchmark.

## Timing and evidence rules

| Field | Boundary |
| --- | --- |
| Direct processing | Timestamp just before mutation to timestamp after writing the update into a preallocated output slot. No producer or queue. The two latency fields coincide in direct mode. |
| Arrival → publication | Feed producer timestamp immediately before trying to enqueue the event to the consumer timestamp after writing its corresponding update. Includes synchronization, queueing, sequencing and mutation. |
| Scheduled → publication | Prescribed open-loop release timestamp to the same consumer timestamp. Includes producer lateness; this is the primary feed comparison. |
| Producer lag | Prescribed release to actual producer arrival, for published events. The late counter includes all admitted/processed events more than 100 µs late, whether or not they published. |
| Processed/published rates | Counts divided by measured phase duration, at least the nominal arrival window, extended through producer completion and consumer drain. |

Steady events are individually scheduled; a burst assigns the same release time to all 32 events, with spacing equal to 32/rate between bursts. A fixed-capacity (4,096 slots in studies) FIFO drops new arrivals when full. The producer can wait briefly for its next deadline; it never waits for a queue slot or a prior publication. It can still fall behind due to scheduling or enqueue work. The drops, peak queue depth and lateness counters make those limits observable.

A processed count includes stale events consumed by the feed, while a published count includes only updates that passed sequencing. **Processed is not the same as published.** If overflow occurs during warmup, the measured phase can process all of its input and still publish nothing. Neither situation qualifies for paired speedup reporting. The timed workload does not inject recovery snapshots on overflow: it exposes the stale state. Recovery is tested separately without conflating snapshot reconstruction with the latency of normal deltas. Percentiles for an incomplete run describe only successfully published events, and the run is marked incomplete. The benchmark is in-process: there are no network, kernel receive, exchange-clock, or NIC timestamps.

All main comparison runs had 20,000 warmup and 100,000 measured events, using seeds 17–24 with identical input within each pair and alternating execution order. The main sweep used seed 17 and one run per point; the longer sweep uses seeds 17–18 with 1,000,000 measured events each. A nearest-rank p99.9 is reported only with at least 20,000 published observations; that gives a minimum tail count, not a precision guarantee. P99 requires 2,000; p50 requires 40. Pair summaries use per-process quantiles, not pooled events. The exact one-sided lower bound is the second-smallest paired effect for eight pairs at nominal 95%. That interpretation assumes representative, independent pairs; alternating order on a single uncontrolled host does not establish independence. No correction across the multiple cases and percentiles is claimed.

Several direct median observations are 0 clock ticks after conversion to nanoseconds. This means elapsed time was **below observable clock granularity**, not zero work. The original raw study’s arithmetic 100% p50 effect is retained as recorded and is not a valid improvement claim. The current summarizer withholds relative effects when either variant reports zero. Use the aggregate direct replay rate as a complementary measurement across the timed loop.

## Profile → change → measure

Measured November 15, 2026 on Apple M4 Pro (24 GiB), macOS 26.6.2 / Darwin 25.6.0, Apple clang 21.0.0 (clang-2100.4.1.1), CMake 4.1.1. Native ARM64, C++20, `-O3 -DNDEBUG -arch arm64`. A preliminary build had accidentally targeted x86-64 under Rosetta. It is excluded from market performance claims. The [original non-market baseline](evidence/market/legacy-baseline.md) records that distinction.

macOS `sample` collected 4 seconds at 1 ms intervals from each direct replay loop. The [reference profile](evidence/market/reference-arm64.sample.txt) recorded 1,970 leaf observations in `ReferenceBook::apply` and additional hash-table, tree-maintenance and allocator frames. The [dense profile](evidence/market/dense-arm64.sample.txt) shifted observed work toward array updates and best-quote bitmap scans; it recorded 1,231 leaf observations in `DenseBook::top`. These are profiler samples, not temporal percentages of the feed pipeline.

The dense book addresses that original mutation hotspot by indexing orders directly by ID and quantities by tick. A side-specific bitmap tracks occupied levels; leading/trailing zero counts locate the best bid/ask. No heap allocation occurs during ordinary updates of the dense book after construction. The tradeoff is bounded IDs and ticks and about 208 KiB of fixed order/level/bitmap payload even when nearly empty, on the measured ABI. A sparse or unbounded price universe would need a different representation.

| Scope | Reference p50 / p99 / p99.9 | Dense p50 / p99 / p99.9 | Median paired p99 change; one-sided lower bound |
| --- | --- | --- | --- |
| Direct | 42 / 125 / 187.5 ns | Below one tick / 42 / 83 ns | 66.4% lower; +66.4% |
| Feed, 100k steady | 5.792 / 40.958 / 1,524.104 µs | 5.771 / 339.750 / 1,926.334 µs | 56.3% worse; −496.3% |
| Feed, 100k burst32 | 8.271 / 58.021 / 624.542 µs | 5.584 / 51.771 / 808.709 µs | 17.6% lower; −411.3% |
| Feed, 500k steady | 2.480 / 27.333 / 279.500 µs | 2.417 / 17.771 / 239.271 µs | 38.4% lower; +10.8% |
| Feed, 500k burst32 | 8.000 / 14.958 / 282.813 µs | 6.125 / 15.355 / 247.646 µs | 16.1% lower; −147.5% |

Cells list medians of the per-run percentiles. The effect summarizes paired ratios and can differ from taking a ratio of medians. Feed values are **scheduled** to publication. Results were mixed: direct replay is faster, but once two threads are involved, producer scheduling and queue delay affect the tail. The negative or wide bounds remain visible; no general feed speedup is established. Eight direct pairs had median 14.51M vs 36.83M processed events/s over the isolated timed loop (2.54×). This is not feed throughput. For the 500k steady feed case, every event published and both variants sustained about 500k events/s, with a scheduled p99 one-sided lower bound of 10.8% improvement.

**Attempted and rejected:** the dense profile suggested caching the best quote index. The candidate updated that cache when a new price became best or the old best level emptied. Its median paired 500k burst scheduled p99 was 18.1% lower (lower bound +5.4%), but direct median throughput fell from 36.46M to 34.96M events/s and steady scheduled p99 effect was 1.2% worse. We kept the simpler bitmap scan. [Cached-quote raw observations](evidence/market/cached-quote-arm64.json) retain the controlled comparison. Rebuild commit `a7a51b94e5fd9101f3db94b0a1a7f9ea64317968` to reproduce it with the same Release flags, then run:

```sh
python3 tools/market_study.py --binary build/jitter_market --output results/market-cached \
  --reference dense --candidate cached --case direct --case steady-500k --case burst32-500k --skip-sweep
```

## Saturation and recovery

The [initial sweep](evidence/market/paired-arm64.json) completed 100,000 events per point and had full coverage through 4M offered events/s in both shapes. The [longer sweep](evidence/market/saturation-arm64.json) revealed losses the shorter sweep missed. These are complete healthy runs out of two at each longer-sweep point, in **steady / burst32** order:

| Offered events/s | Reference full coverage | Dense full coverage |
| ---: | ---: | ---: |
| 4,000,000 | 1/2 · 2/2 | 2/2 · 2/2 |
| 6,000,000 | 0/2 · 0/2 | 2/2 · 2/2 |
| 8,000,000 | 0/2 · 0/2 | 2/2 · 2/2 |
| 10,000,000 | 0/2 · 0/2 | 1/2 · 1/2 |

The reference first lost coverage at 4M steady and both shapes failed coverage at 6M. Dense delivered full coverage in the four 8M runs, but 147,077–407,880 of one million processed events were more than 100 µs late in the steady runs; full coverage is not an acceptable latency SLA. At 10M dense dropped events in one run per shape; even the zero-drop steady run published at 7.73M/s with all events more than 100 µs late. These are host/workload-specific observations, not a capacity guarantee. Warmup losses, gaps, stale events and actual published counts are kept in the data.

## Reproduction and further profiling

The first paired campaign and sampling used clean commit `d09bff657289ab186fb871615e4e432f2a3d84fd`; the overload sweep used clean `1e1ddcbfacdab58bde7d1bb763df6e905eb990ef`. Each JSON records executable SHA256, checkout, seed, measurements and exact per-run argument vector. Replaying the commands requires remapping the local binary path. The published comparisons are not timing gates in CI.

On macOS, sample a direct loop separately from benchmarks:

```sh
build/jitter_market --mode direct --book reference --events 200000 --profile-ms 7000 &
bench_pid=$!
sample "$bench_pid" 4 1 -file reference.sample.txt
wait "$bench_pid"
```

Repeat with `--book dense` and a separate output path. On Linux, the following are **future diagnostic commands**, based on the [Linux kernel profiling guide](https://kernel.org/doc/html/latest/admin-guide/workload-tracing.html); no Linux hardware counters or NIC timings were measured here. Compare both books on the same dedicated host and ensure the platform exposes the listed events:

```sh
perf stat -r 5 -e cycles,instructions,cache-misses,context-switches,cpu-migrations -- \
  build/jitter_market --mode direct --book reference --events 1000000 --warmup 20000
perf record --call-graph dwarf -- \
  build/jitter_market --mode direct --book reference --events 200000 --profile-ms 7000
perf report --stdio
```

Counters cover the whole process (including setup and validation in normal benchmark mode); use the profile to identify hot sections. Before claiming transport latency, introduce a separate transport benchmark with documented receive and publication clock boundaries.
