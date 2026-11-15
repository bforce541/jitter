# Market feed handoff: latency, CPU, and capacity

The [order-book study](market-replay.md) isolated price-level maintenance. This
iteration holds the dense book fixed and changes only the producer/consumer
handoff. The default `--queue mutex` sleeps when the queue is empty. The optional
`--queue spsc` uses a preallocated, single-producer, single-consumer ring and polls
when empty. Both reject new items at capacity; both sequence and publish through
the same book and check output against the same reference.

## Mechanism

The [ring implementation](../include/jitter/spsc.hpp) uses an unsigned write cursor
owned by the producer and a read cursor owned by the consumer. The producer
writes the item before releasing the write cursor. The consumer acquires that
cursor before reading the item, then releases the read cursor so the producer
can reuse the slot. The cursors are separately aligned. Capacity must be a power
of two; a mask indexes the slots. Closing follows the last enqueue; a consumer
that observes close rechecks the write cursor before returning end-of-stream.
There is no promise that the polling consumer will make progress while the OS
does not schedule it.

On the native ARM64 Mac, a separate 3-second `sample` capture in the 100k burst
case showed 2,117 condition-variable wait observations in the
[mutex capture](evidence/market/mutex-feed-arm64.sample.txt). The
[polling capture](evidence/market/spsc-feed-arm64.sample.txt) instead spent most
consumer observations in the inlined worker path. These snapshots distinguish
sleeping from polling; they do not attribute an exact latency budget to syscalls.

The FIFO stress test transfers 200,000 ordered values at each of four capacities;
it also checks rejection when full, slot reuse, draining on close, and termination
after drain. Feed smoke covers the ordinary public benchmark entry point. Existing
seeded book and recovery tests continue to check publication and canonical state
against the reference.

## Controlled results

Native ARM64, Apple M4 Pro, macOS 26.6.2, Apple clang 21.0.0, CMake Release
`-O3 -DNDEBUG -arch arm64`. The two compiled branches run in the same binary
(SHA256 recorded in both studies). Both use the same workload per pair, 4,096
queue slots, 20,000 warmup events, and alternating treatment order. The
[initial study](evidence/market/queue-paired-arm64.json) has eight pairs per case,
100,000 measured events per run, seeds 17–24. The
[separate longer confirmation](evidence/market/queue-confirmation-arm64.json)
has eight pairs per case, 400,000 measured events per run, seeds 1001–1008.
All runs in both paired studies published every event.

| Longer confirmation, 500k offered/s | Scheduled p99 (µs), mutex → SPSC | Median paired reduction (one-sided lower bound) | Scheduled p99.9 (µs), mutex → SPSC | Approx. CPU cores, mutex → SPSC |
| --- | ---: | ---: | ---: | ---: |
| Steady | 10.313 → 0.542 | 94.8% (81.5%) | 163.354 → 96.709 | 1.36 → 2.00 |
| Burst of 32 | 11.104 → 2.188 | 80.1% (58.4%) | 137.854 → 113.729 | 1.05 → 2.00 |

Latency values are medians of per-run percentiles. The effect is the median of
within-pair effects; the bound uses Jitter's one-sided median method. It assumes
representative, independent pairs, which this single uncontrolled host cannot
establish. The timing definition is unchanged: scheduled arrival through writing
the corresponding in-process update. Producer lag remains in scheduled latency;
there are no NIC or exchange timestamps. CPU is process user + system time from
immediately before starting the measured worker through its join, divided by
the timed feed window (which excludes the 2 ms startup guard). This is an
approximation that includes polling, producer work, and worker startup; it
excludes warmup, generation, and correctness verification. The producer's
observed peak depth uses its acquired read cursor at enqueue, which may lag a
concurrent dequeue.

The result supports a use case for dedicating CPU to a latency-sensitive feed.
**Mutex remains the default.** For burst traffic at 100k offered/s, the initial
paired comparison measured about 0.085 cores with the mutex versus 1.018 with
polling. A tighter CPU budget is a reason to choose the mutex.

## Load envelope

The registered sweep tested 0.5M, 2M, 4M, 8M and 10M offered events/s in both
steady and burst-of-32 shapes. Each rate and treatment had two runs of one
million measured events, with identical seeds and input across treatments.
The [envelope report](evidence/market/queue-envelope-arm64.json) applies the
following limits to **every** observed repeat: full publication coverage,
scheduled p99 ≤ 20 µs, scheduled p99.9 ≤ 500 µs, at most 1% of inputs arriving
more than 100 µs late, and at most 2.1 approximate CPU cores.

| Queue | Highest rate in the passing tested prefix, steady | Burst of 32 |
| --- | ---: | ---: |
| Mutex | 500k/s | 500k/s |
| SPSC | 2M/s | 2M/s |

Both SPSC shapes first failed at 4M/s due to p99.9, despite passing p99 and
coverage at that point. At 8M and 10M, the SPSC runs also violated p99 and the
late-arrival budget. Reducing the CPU cap to 1.5 cores makes SPSC fail the
lowest tested rate for both shapes. This tool distinguishes the highest tested
passing point from the end of a passing prefix, preserving any later isolated
passes rather than extrapolating through a failure. These are observed envelopes,
not capacity guarantees, and no untested rate is implied.

## Reproduce

Build as in the [main replay guide](market-replay.md#run) and run one treatment:

```sh
build/jitter_market --mode feed --book dense --queue spsc \
  --events 400000 --warmup 20000 --seed 1001 --rate 500000 --burst 1
```

Run the initial paired study and the later confirmation:

```sh
python3 tools/market_study.py --binary build/jitter_market \
  --output results/queue-initial --queue-study \
  --sweep-rate 500000 --sweep-rate 2000000 --sweep-rate 4000000 \
  --sweep-rate 8000000 --sweep-rate 10000000 --sweep-events 1000000 --sweep-repeats 2
python3 tools/market_study.py --binary build/jitter_market \
  --output results/queue-confirm --queue-study --case steady-500k --case burst32-500k \
  --seed-base 1001 --pair-events 400000 --skip-sweep
python3 tools/market_envelope.py results/queue-initial/study.json \
  --p99-us 20 --p999-us 500 --max-late-fraction 0.01 --max-cpu-cores 2.1
```

The captured initial study is from `1667a26`; the longer study uses the runner
at `d08727d` and the identical compiled C++ binary. To reproduce sampling, run
either queue at 100k offered/s with `--events 1000000 --warmup 20000 --burst 32`,
then use `sample PID 3 1 -file OUTPUT` during the measured run. Do not compare
benchmark timings collected under the profiler.
