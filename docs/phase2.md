# Phase 2: Observation and bottleneck analysis

Jitter now records supported execution events, validates their relationships, builds temporal dependency evidence, generates testable bottleneck hypotheses, and measures the effect of enabling observation. Application correctness and Phase 1 measurement contracts still apply.

The current application remains `shared-index-v1`. This phase does not implement autonomous experiment selection, configuration recommendations, compiler transformations, or a generic C++ profiler. Those require later phases and supported integrations.

## Run, inspect, and reproduce

From the repository root:

```sh
python3 tools/jitter.py run examples/contention.json --output results/observation
python3 tools/jitter.py diagnose results/observation --run 1
python3 tools/jitter.py diagnose results/observation --run 1 --request 50
python3 tools/jitter.py verify results/observation
python3 tools/jitter.py reproduce results/observation --output results/observation-replay
```

The example has 50 warmup requests, followed by 200 measurement requests per repetition. Request 50 is the first measurement request. `--run` is one-based. `--request` accepts a completed measurement request and computes its dependency evidence from verified raw data without modifying the bundle.

`diagnose` normally prints trace quality, hypotheses, suggested experiments, and limitations. Every run also saves the full `diagnostics.json`, with per-request delays, distributions, worker activity, and a selected dependency graph. Warmup events validate the trace and contribute to loss accounting; hypotheses focus on measurement requests.

Measure instrumentation overhead with:

```sh
python3 tools/jitter.py overhead examples/contention.json --output results/overhead
python3 tools/jitter.py summarize results/overhead
python3 tools/jitter.py verify results/overhead
python3 tools/jitter.py reproduce results/overhead --output results/overhead-replay
```

A successful command means the evidence is internally valid. It does not mean performance constraints passed, a trace is lossless, the paired results are comparable, or an optimization was found. Inspect the corresponding fields.

## Versioned specification

Phase 1 schema-version-1 specifications and archived result bundles remain supported. They do not require trace analysis. Schema version 2 requires the same application, workload, objective, repetitions, and timeout fields, plus:

```json
"instrumentation": {
  "enabled": true,
  "events_per_thread": 32768
}
```

`enabled` must be a boolean. Capacity is 1–1,000,000 records per logical thread for the entire process, including startup, warmup, measurement, and shutdown. Total enabled event storage is capped at 256 MiB. This is a separate allocation bound; the experiment's observed memory constraint may be lower and can fail.

The event representation is 32 bytes. With four workers and one producer, the example reserves exactly 5,242,880 event bytes. Metadata and container overhead are additional. Disabled tracing allocates no event buffers, writes only `trace-info.json`, and produces a disabled diagnosis. Schema version 2 requires an engine reporting trace format 1 in `--build-info`; an older saved Phase 1 executable still reproduces its schema-version-1 experiments.

The low-level engine accepts optional `--trace 0|1` and `--trace-capacity N`. The Python adapter supplies these from the specification; baseline application settings remain independent of observation settings.

## Recording design and integration points

`TraceSession` owns preallocated buffers. Logical thread 0 is the workload producer; workers have stable slot IDs 1 through the configured worker count. These are not OS thread IDs.

Each thread exclusively appends to its own buffer. The recording path performs no memory allocation, observer mutex acquisition, atomic operation, or file writing. It writes fixed-size records and samples the monotonic clock when a retained event needs a timestamp. Request boundary events reuse the existing raw-measurement timestamp. The implementation still adds branches, clocks, counter updates, and memory writes; its cost must be measured rather than assumed negligible.

Buffers retain their earliest records. When full, they drop new events, preserve existing evidence, and increment per-phase attempted/written/dropped counters. Dropped events do not sample the clock. There is no live draining or ring-buffer overwrite. Warmup can consume capacity needed for measurement.

The main thread exports buffers only after every worker has joined. String names and CSV serialization occur after measurement. The supplied integration points are:

- `TraceContext`: request, phase, and single-writer buffer context.
- `TracedLock`: wait, acquisition, and release sampling around a supported mutex.
- `TracedRegion`: scoped begin/end events for a supported region.
- `TraceBuffer`: lifecycle, request, and queue event recording.

A new application adapter must define its event/resource contract and validator. This implementation does not infer synchronization from arbitrary C++ or transparently attach to an unmodified process.

## Events and artifacts

Traced runs add:

```text
trace-info.json    mode, format, storage allocation, per-thread/per-phase loss ledger
trace-phases.csv   global start/end clock offsets for each measured phase
events.csv        retained events, serialized by logical thread and sequence
diagnostics.json  validated dependency evidence, hypotheses, and limitations
```

All files live inside each `run-NNN` directory and are covered by the bundle manifest.

`events.csv` columns are `thread_id, sequence, phase, timestamp_ns, request_id, kind, resource_id`. Timestamps are integer nanoseconds since the trace session's monotonic epoch; nanosecond units do not imply nanosecond clock resolution. Request timestamps are converted using the corresponding `trace-phases.csv` origin. Lifecycle events use request ID `18446744073709551615` as the no-request sentinel.

Resource IDs are 1 for the shared-index mutex, 2 for the bounded request queue, and 3 for the digest region. Supported events are:

| Group | Events |
| --- | --- |
| Thread lifecycle | `thread_start`, `thread_stop` |
| Queue wait activity | `worker_wait_begin`, `worker_wait_end` |
| Admission | `request_submit`, `queue_enter`, `request_reject` |
| Dispatch and response | `queue_exit`, `request_start`, `request_complete` |
| Mutex | `lock_wait`, `lock_acquire`, `lock_release` |
| Digest region | `region_begin`, `region_end` |

Queue wait activity includes condition-variable waiting, predicate checks, and queue-mutex reacquisition. It is a wall-time interval, not a measurement of CPU sleep state or OS scheduling events. Request service spans also include lock waiting and preemption.

## Semantic validation and trace loss

Validation checks more than file hashes:

- Mode, capacity, thread inventory, event size, allocation, and clock format match the specification.
- Every buffer preserves a contiguous sequence prefix and monotonic timestamps.
- Retained rows exactly match written counts; attempted = written + dropped for each phase and thread.
- Producer and worker streams follow the supported lifecycle and request-stage grammar.
- Independent request accounting predicts aggregate request and lifecycle attempt counts, including events that were dropped.
- Request identity, phase, resource, status, and worker assignment remain consistent.
- Submission, worker start, and completion agree exactly with Phase 1 raw records.
- Phase origins agree with drained elapsed time; no request event lies outside its phase.
- Observed exclusive mutex ownership intervals do not overlap.
- A lossless queue trace admits a FIFO/capacity/rejection linearization. Equal-clock events permit compatible orders while preserving each writer's order; timestamps do not invent a cross-thread ordering within a clock tie.

Malformed or contradictory traces invalidate the run. Legitimate buffer overflow preserves baseline correctness and metrics but produces `status: insufficient_trace`. Dependency edges and bottleneck hypotheses are withheld whenever any event was dropped, even if some measurement spans remain intact. Partial observed stages and exact loss counts remain available. This is intentionally conservative.

A complete result manifest means all declared artifacts were captured and validated. `observation.complete` separately indicates zero trace loss. The two concepts must not be conflated.

## Dependencies and delay propagation

For each completed measurement request, the analyzer separates:

1. Producer/admission delay from intended arrival to actual submission.
2. Queue delay from submission to worker start.
3. Mutex waiting from the sampled wait boundary to sampled acquisition.
4. Digest-region wall time.
5. Remaining service time not covered by those two application spans.

These nonoverlapping components sum to that request's primary latency. Percentiles of different components cannot be summed to reconstruct a latency percentile.

Lock ownership is sampled **after** acquisition and **before** release. Overlap between a waiting interval and another request's observed ownership is a lower bound on supported blocking evidence. The analyzer records every overlapping observed holder for selected waiters, not just the last holder or the longest-running request. Handoff gaps, unobserved portions of ownership, scheduling, and observer bookkeeping remain unattributed.

For queue propagation, the analyzer finds intervals where all configured workers have observed active request-service spans. It connects those occupying requests to a selected queued request over intersecting intervals. This can expose a chain such as a long holder delaying another worker, which remains occupied while later requests queue. Worker-capacity edges are parallel evidence: summing them multiplies delay by the number of occupied workers. The unique all-workers-busy interval is reported separately.

The graph selects the slowest `min(64, max(8, ceil(completions/100)))` completed requests by default, or one explicitly selected request. It contains request/worker/resource nodes and timed, typed dependency edges, including source and target worker IDs. It is capped at 8,192 edges with an explicit truncation flag. Per-request timings and aggregate span calculations are retained independently of graph truncation. Request-level graphs aggregate temporal spans and are not guaranteed to be a DAG or a valid critical-path sum.

## Hypothesis generation

Rules rank hypotheses by the sum of observed component time in the selected slow requests. This is an interpretable diagnostic heuristic, not a learned causal model or optimizer.

| Hypothesis | Supporting observations | Example next experiment |
| --- | --- | --- |
| Shared-lock contention | Mutex waiting contributes at least 10% of selected latency, with positive observed owner overlap. | Compare supported worker counts under identical inputs; verify whether reduced lock waiting merely shifts delay into queueing. |
| Queue pressure | Queue delay contributes at least 10%, or admission rejects requests. | Compare worker count or queue capacity; keep end-to-end latency, throughput, memory, and rejections explicit. |
| Workload delivery delay | Admission lag contributes at least 10%, or fails its tolerance. | Repeat unchanged inputs on a quieter host before attributing late delivery to application execution. |
| Digest execution | The digest region contributes at least 10%. | Isolate request classes with the same application contract. Changing workload mix is a diagnostic control, not an optimization win. |
| Unresolved service delay | Residual service time contributes at least 25%, or is the only available positive evidence. | Extend a supported region or scheduling measurement and remeasure observation cost. |

Reports contain evidence, suggested experiment parameters or control descriptions, and caveats. They do not select a configuration, execute suggested tuning experiments, or claim that a busy region caused every slow request. Changing digest rounds changes the response contract and is not proposed as an optimization.

## Paired overhead study

`overhead` interprets `repetitions` as a number of pairs. It runs one fresh process per mode per pair, alternating off/on, on/off, off/on, and so forth. The first child builds or uses the supplied engine; every later child uses the same pinned executable. The only experimental setting changed within a pair is `instrumentation.enabled`.

The parent bundle contains:

```text
overhead-spec.json original specification
execution.json     alternating execution plan and optional reproduction origin
pair-001-off/      complete baseline bundle, one repetition
pair-001-on/       complete traced bundle, one repetition
pair-002-on/ ...
comparison.json    all per-pair signed observations and comparability flags
manifest.json      hashes covering parent evidence and all child artifacts
```

Each child is independently validated. The parent additionally checks executable identity, source-content inventory, workload bytes, settings, execution order, and recomputed comparison values. Observations include on-minus-off p99, throughput, average CPU cores, CPU time per completed request, peak RSS, reserved trace bytes, retained events, and losses.

A pair is unsuitable for headline comparison if events were dropped, any requests were rejected, completion coverage differs, delivery exceeded its scheduling tolerance, or recorded host properties changed. Every pair remains in the report. If any pair is unsuitable, the across-pair delta distributions are withheld instead of filtering inconvenient runs into a favorable estimate.

Signed deltas can be negative because host noise may exceed instrumentation effects. Peak RSS is a process high-water mark and includes preallocated event storage; it is not an exact allocation delta. Alternating order does not control frequency, thermal state, or background work. There are no confidence intervals, significance claims, overhead guarantees, or performance-win decisions in Phase 2.

`verify`, `summarize`, and `reproduce` support both ordinary experiment bundles and overhead parent bundles. Reproduction uses each original child's exact saved executable and inputs, creates new evidence, and retains changed timing rather than treating it as corruption.

## Verification and module boundaries

CTest runs C++ core checks and 38 Python tests, including real-engine observation, overflow, ordinary/paired reproduction, corrupted reports, and exact controlled dependencies. Known controls distinguish a mixed holder/queue scenario, isolated digest execution, single-worker queue pressure, and scheduler delay. They check an exact 80 ns ownership overlap and 82 ns unique queue-saturation interval; parallel capacity edges total 164 ns and are deliberately not reported as 164 ns of queue delay.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

Linux/macOS CI exercises capture, diagnosis, request inspection, reproduction, overhead, and parent verification in addition to the baseline workflow. Sanitizer commands from [the Phase 1 verification record](phase1-validation.md) also apply to the full current suite.

- `include/jitter/trace.hpp`, `src/trace.cpp`: record representation, buffers, scopes, and export.
- `src/application.cpp`, `src/experiment.cpp`: supported integration boundaries.
- `tools/jitter_runner/trace_reader.py`: semantic and loss validation.
- `tools/jitter_runner/dependencies.py`: indexed ownership/occupancy intervals and bounded graphs.
- `tools/jitter_runner/diagnostics.py`: component evidence and testable hypotheses.
- `tools/jitter_runner/overhead.py`: paired execution, integrity, comparisons, and reproduction.

[Phase 1 contracts](phase1.md) · [Roadmap](roadmap.md)
