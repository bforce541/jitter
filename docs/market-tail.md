# Why the 4M events/s tail failed

The [original load envelope](market-queue.md#load-envelope) used a dense book, 4,096 FIFO slots, 20,000 warmup events and one million measured events per repeat. It required every repeat to publish every event, scheduled p99 ≤ 20 µs, p99.9 ≤ 500 µs, no more than 1% of processed events over 100 µs late (normalized by offered events), and at most 2.1 approximate CPU cores. The sweep alternated mutex-first and polling-first across seeds 17 and 18 within each rate and shape.

## What reproduced

The original polling runs at 4M published all four million measured events across both shapes. Scheduled p99.9 was **659.625–699.667 µs**, despite scheduled p99 ≤ 6.083 µs in all four runs. Producer-lag p99.9 was already **658.875–699.583 µs**. The original study contains marginal percentiles. Their similarity points to producer lateness but cannot identify the same event or a particular pause. [Original run records](evidence/market/queue-paired-arm64.json).

Two new uninstrumented campaigns used a clean `3a91555` build with **the same binary SHA-256** as the original queue sweep, the same paired seeds and controls, and the same M4 Pro/macOS/Apple clang configuration. Each row below spans four runs of one million measured events, two seeds in each campaign. All published every event.

| Polling at offered rate | Scheduled p99 range (µs) | Scheduled p99.9 range (µs) | Runs meeting all limits |
| --- | ---: | ---: | ---: |
| 2M steady | 0.208–0.583 | 34.333–64.167 | 4/4 |
| 2M, burst of 32 | 2.417–4.417 | 35.917–40.917 | 4/4 |
| 4M steady | 2.166–33.125 | 228.041–233.959 | 2/4 |
| 4M, burst of 32 | 2.459–11.500 | 191.833–201.792 | 4/4 |

The original 500 µs p99.9 breach did not recur. The first repeat block failed p99 at 4M steady (26.583 and 33.125 µs). The second block passed p99 at that point. The same binary and seeds can reach different limits on a shared-with-the-OS host. The historical envelope remains scoped to its original repeats. [First rerun](evidence/market/tail/boundary-rerun-arm64.json) · [Second rerun](evidence/market/tail/boundary-repeat-arm64.json) · [Host and compiler](evidence/market/tail/host-arm64.json).

## Trace a p99.9 event

The diagnostic build adds a timestamp on return from pop, alongside the existing scheduled, producer-arrival and publication timestamps. It records when the first startup sleep starts and returns. It writes the CSV after the timed phase. **Diagnostic timings are separate** because the extra clock read and recording may change scheduling and queue occupancy.

These are the timestamps in nanoseconds relative to the measured phase's first scheduled release, for sequence **21,001** in the 4M steady polling run at seed 17:

| Landmark | Timestamp (ns) | Time since previous landmark (ns) |
| --- | ---: | ---: |
| Scheduled release | 250,000 | — |
| Producer arrival, immediately before enqueue attempt | 472,125 | 222,125 |
| Consumer returned from pop | 472,209 | 84 |
| Consumer wrote update | 472,250 | 41 |

This is the nearest-rank p99.9 witness at **222.250 µs**. Zero prior admitted arrivals were still pending at its producer-arrival timestamp. The maximum in that run, sequence 20,001, was the first scheduled event. The producer called its coarse sleep before the first deadline, asked to wake **100 µs before** that deadline, and returned **381.084 µs after** it. The first event arrived another 83 ns later. The existing producer wait policy falls back to spinning near a release, but the initial coarse sleep had already consumed the deadline.

Across both traced 4M polling shapes and seeds, all **1,001** events from the p99.9 witness through the maximum were scheduled within the first millisecond, and their largest measured component was producer lag. Burst traffic also built backlog during catch-up. In the seed 17 burst run, p99.9's witness had **173.250 µs** producer lag, **28.750 µs** arrival-to-pop handoff and **478** prior admitted arrivals pending a consumer decision at its arrival. Peak producer-observed queue depth was 846 of 4,096. All these runs published every event. At 2M the initial lag reached the extreme tail as well, but cleared before the p99.9 cutoff, where later producer or handoff delays were visible.

Arrival-to-pop is the observable handoff boundary and includes enqueue synchronization and residence before dequeue. Pending arrivals are reconstructed from timestamps and include any item already being processed. They are an outstanding-work count rather than a ring cursor measurement. Pop-to-publication includes consumer processing and time the consumer might have been off-CPU. The trace contains no scheduler events, so it establishes the producer's late return and subsequent catch-up. It cannot identify what delayed the OS wakeup or prove that the historical 500 µs breach and the newer p99 failures had the exact same cause. [Traced runs and witness records](evidence/market/tail/boundary-traced-arm64.json).

## Experiment that stayed out of the engine

The candidate spins instead of sleeping only for the **first deadline** in each phase. Both treatments used the same binary, input fingerprints, dense book, polling ring, timing boundaries and build flags. Four pairs per rate and shape used seeds 17–20 with alternating order, and a separate two-pair 4M trace verified the mechanism.

- All four 4M spin traces reached first arrival in **0 measured ns** at the clock's granularity. None of their p99.9-through-maximum events came from the first millisecond. In the corresponding sleep traces, all 1,001 did.
- In the uninstrumented 4M burst pairs, scheduled p99.9 run medians went **193.563 → 20.542 µs**, and CPU **2.005 → 2.014 approximate cores**, with full publication in all eight runs. At 2M, all spin and sleep repeats published every event.
- At 4M steady, three spin runs published all events with p99.9 **8.416–27.458 µs**. The fourth had **2,173 queue drops** and published only **436,674 / 1,000,000** events after sequencing marked the feed stale. It hit the 4,096-slot cap. Its seed-matched sleep run published every event. The overflow occurred in the uninstrumented run, so there are no event-level timestamps to determine whether receiver scheduling or another interruption produced it.

The candidate isolated one source of lag but failed the pre-existing coverage rule. It remains a reproducible rejected experiment. It is outside the shipped benchmark and the sleeping mutex remains the default. [Uninstrumented probe](evidence/market/tail/startup-probe-arm64.json) · [Traced probe](evidence/market/tail/startup-probe-traced-arm64.json) · [Archived patch](evidence/market/tail/startup-probe.patch).

## Reproduce the boundary and trace

Run from a clean checkout with a Release build as in the README. Choose a new output directory for each invocation. The recorded Mac campaigns used `caffeinate -i` around each study command.

```sh
python3 tools/market_study.py --binary build/jitter_market --output results/boundary \
  --queue-study --sweep-only --sweep-rate 2000000 --sweep-rate 4000000 \
  --sweep-events 1000000 --sweep-repeats 2
python3 tools/market_envelope.py results/boundary/study.json \
  --p99-us 20 --p999-us 500 --max-late-fraction 0.01 --max-cpu-cores 2.1
python3 tools/market_study.py --binary build/jitter_market --output results/boundary-traced \
  --queue-study --sweep-only --trace-sweep --sweep-rate 2000000 --sweep-rate 4000000 \
  --sweep-events 1000000 --sweep-repeats 2
python3 tools/market_tail.py results/boundary-traced/study.json > results/boundary-traced/tail.json
```

The traced command writes one row per measured input, including drops. Keep the CSVs locally (roughly 0.9 GiB for this sweep). The checked-in JSON retains all run aggregates, each first/worst/p99.9 row, tail-stage counts, and CSV checksums. The archived startup probe applies to clean `f92ce5c`. To rerun it, use a separate worktree at that revision, apply the [patch](evidence/market/tail/startup-probe.patch), build with the same Release flags, and replace `--queue-study` with `--startup-study` and `--sweep-repeats 2` with `--sweep-repeats 4` in the uninstrumented command. Its separate diagnostic block used only 4M, two repeats and `--trace-sweep`.

## Native Linux x86-64

Dedicated native Linux performance remains unmeasured. Linux CI exercises replay correctness, FIFO behavior and the traced execution path. For a native comparison, use an authorized Linux x86-64 host with a C++20 compiler, CMake 3.20+, and Python 3.9+. Use an otherwise idle host and retain the CPU, kernel, compiler, effective build commands, frequency policy and CPU-affinity or VM constraints.

```sh
test "$(uname -s)/$(uname -m)" = Linux/x86_64 || exit 1
out=results/market-linux
mkdir -p "$out"
{
  uname -a
  lscpu
  cat /etc/os-release
  c++ --version
  cmake --version
  python3 --version
  git rev-parse HEAD
  git status --short
  taskset -pc "$$"
  grep -H . /sys/devices/system/cpu/cpufreq/policy*/scaling_governor 2>/dev/null || true
} > "$out/host.txt"
cmake -S . -B build-linux -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build-linux --parallel 4
ctest --test-dir build-linux --output-on-failure
cp build-linux/compile_commands.json build-linux/CMakeCache.txt "$out/"
python3 tools/market_study.py --binary build-linux/jitter_market --output "$out/steady-500k" \
  --queue-study --case steady-500k --seed-base 1001 --pair-events 400000 --skip-sweep
python3 tools/market_study.py --binary build-linux/jitter_market --output "$out/boundary" \
  --queue-study --sweep-only --sweep-rate 500000 --sweep-rate 2000000 --sweep-rate 4000000 \
  --sweep-events 1000000 --sweep-repeats 2
python3 tools/market_envelope.py "$out/boundary/study.json" \
  --p99-us 20 --p999-us 500 --max-late-fraction 0.01 --max-cpu-cores 2.1 > "$out/envelope.json"
```

The first study repeats eight matched steady runs per treatment with seeds 1001–1008. The sweep uses two repeats per rate and shape at seeds 17 and 18. Keep Linux results separate from the M4 measurements and record if the environment is virtualized or shared.
