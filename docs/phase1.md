# Phase 1: Experiment foundation

Jitter can build a supported multithreaded C++ application, run a controlled workload, check every response, measure baseline performance, and reproduce saved inputs. The C++ engine handles the application, worker pool, scheduling, and measurements. A Python standard-library driver handles specifications, build/run orchestration, the independent response oracle, and persistence.

Phase 2 adds [lock tracing, dependency analysis, hypotheses, and overhead studies](phase2.md). Autonomous search, proposed patches, and HTML reports remain later-phase work. No optimization or significance claim is made by Phase 1.

## Requirements and first run

- Linux or macOS.
- A C++20 compiler, CMake 3.20+, Git, and Python 3.9+.
- A single-configuration CMake generator, such as Unix Makefiles or Ninja.
- No Python package installation or external service is required.

From the repository root:

```sh
python3 tools/jitter.py run examples/baseline.json --output results/baseline
python3 tools/jitter.py verify results/baseline
python3 tools/jitter.py summarize results/baseline
python3 tools/jitter.py reproduce results/baseline --output results/reproduction
```

The runner builds a Release executable automatically. Omit `--output` to create a timestamped directory with a unique suffix under `results/`. Existing output directories are refused. Runs execute sequentially in fresh application processes; they use identical request inputs.

`--build-dir PATH` selects the CMake build directory. `--binary PATH` skips building and uses an existing engine that supports the adapter's contract. These options are mutually exclusive. When using a supplied binary, the captured checkout is explicitly not guaranteed to be that binary's source.

Successful measurement and correctness validation return exit status 0, even when performance constraints fail. Inspect `all_runs_feasible` and each run's `constraints` before interpreting a baseline as feasible. Invalid specifications, application failures, timeouts, incomplete output, wrong responses, and failed verification return nonzero. Failed experiments retain inputs, logs, and a failed manifest; they cannot verify as complete evidence.

## Supported application: shared-index-v1

This is an intentionally controlled, in-process request-processing application, maintained in this repository. It models a shared index with a coarse mutex: normal lookups perform a short digest, and a configured fraction perform a longer digest while holding the same mutex. A fixed worker pool consumes a bounded FIFO queue. Admission immediately rejects a request when the queue is full. Capacity counts waiting requests, excluding active workers.

The initial dataset is immutable. For key `k`, its value is the SplitMix64 finalizer applied to `dataset_seed + 0x9e3779b97f4a7c15 * (k + 1)`, using unsigned arithmetic modulo 2^64. Each digest round applies `x = x * 6364136223846793005 + ((k + 1) | 1)` modulo 2^64. The selected request class determines the number of rounds.

The Python oracle computes the same affine recurrence using exponentiation by squaring, independently of the C++ iterative implementation. Every completed response is checked against it, including warmup responses. Results depend only on key, request class, and application configuration, so worker execution order cannot change a valid response.

This application demonstrates the foundation and exposes a controlled contention mechanism. It is not independently maintained software, a network service, or evidence of real-world optimization success. Independent integrations and diagnostic evaluation remain Phase 5 work.

## Specification contract

See `examples/baseline.json` for a complete specification and `examples/burst.json` for an intentionally overloaded example. Schema version 1 requires every documented field; unknown fields, duplicate JSON keys, booleans in numeric fields, and nonfinite numbers are rejected.

| Section | Fields and meaning |
| --- | --- |
| `schema_version` | Exactly `1`. |
| `application.adapter` | Exactly `shared-index-v1`. |
| `application.workers` | Worker count, 1–256. |
| `application.queue_capacity` | Maximum waiting requests, 1–1,000,000. |
| `application.key_count` | Dataset keys, 1–65,536. |
| `application.dataset_seed` | Unsigned 64-bit dataset seed. |
| `application.light_rounds`, `heavy_rounds` | Digest rounds per request class, each 1–10,000,000. |
| `workload.seed` | Unsigned 64-bit request-generation seed. |
| `workload.arrival` | `steady` or `burst`. |
| `workload.rate_per_second` | Intended average request rate, integer 1–1,000,000. |
| `workload.warmup_ms` | Warmup arrival window, 0–3,600,000 ms. |
| `workload.measurement_ms` | Measurement arrival window, 1–3,600,000 ms. |
| `workload.heavy_fraction` | Probability of a heavy request, 0–1; realized counts are deterministic for the seed. |
| `workload.burst_size` | Requests sharing an intended arrival in burst mode; must be 1 for steady mode. |
| `objective.metric` | Exactly `p99_latency_ns`; Phase 1 records it without searching. |
| `objective.min_throughput_per_second` | Minimum successfully completed requests per drained measurement second. |
| `objective.max_peak_rss_bytes` | Maximum application-process peak resident memory, in bytes. |
| `objective.max_cpu_cores` | Maximum process CPU seconds / measurement wall seconds; 1.0 represents one fully used core on average. |
| `objective.max_rejection_fraction` | Maximum measurement rejections / offered requests; examples require zero. |
| `objective.max_scheduler_lag_p99_ns` | Maximum p99 submission delay relative to intended arrivals; a workload-delivery quality constraint. |
| `repetitions` | Fresh-process repetitions, 1–100. |
| `timeout_seconds` | Per-process deadline, including startup, warmup, measurement, drain, and output writing. |

The total warmup and measurement input is limited to one million requests per repetition. Finite numeric and integer bounds are enforced in `tools/jitter_runner/spec.py`. Objectives do not constitute hard OS resource limits; Phase 1 observes and reports violations.

## Workload contract

For each phase, request count is `ceil(rate_per_second * duration_ms / 1000)`. Steady request `i` is scheduled at `floor(i * 1e9 / rate_per_second)` nanoseconds. Burst mode replaces `i` with `floor(i / burst_size) * burst_size`. A partial final burst is allowed. All intended arrivals lie inside the configured window.

Request IDs are contiguous across warmup and measurement. Keys and classes are generated with explicitly specified 64-bit arithmetic, not implementation-dependent C++ hashing or a platform's random generator. `workload.csv` stores the complete inputs, and verification regenerates its bytes from the saved specification.

Scheduling uses a monotonic clock and absolute intended arrival times. The producer never waits for an earlier request to complete. A delayed producer offers overdue requests without shifting their intended arrivals. Submission still incurs queue admission locking; its delay is included in scheduler lag. This is an in-process generator, not a guarantee of precisely delivered external traffic. OS scheduling delays are visible and can make a run infeasible.

Workers are created before warmup. Warmup requests finish or reject before measurement starts; the same index and workers remain alive. Measurement also drains all admitted work. Worker teardown and CSV serialization occur afterward. No warmup request enters the measured sample.

## Measurements and interpretation

Every offered request has exactly one row in `requests.csv`. Ordered IDs and input fields must match the workload exactly. Final statuses are `completed` or `rejected`. Application errors, missing/duplicate/extra responses, pending states, and incorrect digests invalidate a run.

All times are integer nanoseconds relative to the start of the request's own phase:

| Measurement | Definition |
| --- | --- |
| Primary latency | Completion − intended arrival, for completed requests. Includes producer delay, queue delay, lock waiting, and execution. |
| Response latency | Completion − actual submission, for completed requests. |
| Queue delay | Worker start − actual submission. |
| Service time | Completion − worker start. Includes shared-lock waiting; Phase 1 does not measure lock waits separately. |
| Scheduler lag | Actual submission − intended arrival, for all offered requests, including rejected requests. |
| Completed throughput | Valid completions / elapsed wall time from phase start through arrival window and final drain. |
| In-window throughput | Completions by the end of the arrival window / configured window seconds. |
| Offered rate | All offered requests / configured window seconds. |
| CPU cores | User + system process CPU time during the phase / phase elapsed wall time. |
| Peak RSS | Process lifetime peak resident memory sampled at the end of the phase, normalized to bytes. Includes startup, workload/response buffers, dataset, workers, and prior warmup. |

CPU measurements include worker and generator activity in the C++ process. They exclude Python validation, building, and reporting. Peak memory is a process high-water mark, not a phase-local allocation delta; it excludes the Python driver's memory. Linux reports `ru_maxrss` in KiB, converted to bytes; macOS reports bytes. [Linux resource documentation](https://www.man7.org/linux/man-pages/man2/getrusage.2.html) and [Apple resource documentation](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/getrusage.2.html) describe the underlying resource API.

Percentiles use nearest rank: sorted sample at `ceil(p * N)`, with one-based indexing. An empty completed sample has null percentiles and fails `latency_observed`. Rejected requests have no completion latency; their counts and fractions remain explicit and must meet the configured constraint. Fewer than 1,000 completions produce a warning about limited tail support. There is no confidence interval or hypothesis test in Phase 1.

Each repetition retains its own p99 and resource measurements. The aggregate summarizes those per-run p99 values without pooling samples. It distinguishes:

- **Correctness passed:** every input and response was accounted for and validated.
- **Feasible:** that repetition met every objective and scheduling constraint.
- **All runs feasible:** every requested repetition was valid and feasible.
- **Failed:** execution, correctness, integrity, or output validation failed.

A lower p99 from rejected work is not an accepted optimization. Phase 1 makes no optimization decisions.

## Result bundle

```text
spec.json                 exact experiment specification
workload.csv              generated input, identical across repetitions
metadata.json             UTC time, host, source hashes/revision, build info, binary identity
source.tar.gz             source snapshot, including uncommitted supported source files
engine/jitter             exact executable used in every repetition
build-logs/               configure/build logs (empty when reusing an executable)
run-001/
  execution.json          exact command and timeout
  stdout.log, stderr.log   process output
  requests.csv            raw request records
  resources.csv           phase resource observations
  summary.json            validated metrics and constraint checks
run-002/ ...
summary.json              repeated-run summary
manifest.json             final status and SHA-256 hashes of artifacts
```

A bundle is marked `running` until it is sealed. Completed bundles are verified before the runner reports success. Verification checks file integrity, input regeneration, every response and timestamp, resource records, and recomputed summaries. SHA-256 hashes provide integrity checks, not authentication against a person who can rewrite the bundle and its manifest.

## Reproduction

`reproduce` verifies the original bundle before launching its saved executable against the saved specification. It creates a new bundle and preserves the original source snapshot. It requires the same OS and architecture as the original driver; changed kernel, CPU, available affinity, and Python information are recorded. It does not overwrite the original.

Reproduction means identical declared application inputs and executable, not identical timing. Thermal state, background processes, CPU frequency, scheduler behavior, and system settings can still change results. Compare raw measurements and host metadata; Phase 1 does not establish statistical equivalence.

For a different OS/architecture, rebuild the archived source in a separate directory using the recorded compiler/build settings, then run the saved specification with the new binary. This creates a new build identity and is not an exact-executable replay. For example:

```sh
mkdir replay-source
# Only extract a source archive you trust.
tar -xzf results/baseline/source.tar.gz -C replay-source
cmake -S replay-source -B replay-build -DCMAKE_BUILD_TYPE=Release
cmake --build replay-build --parallel
python3 tools/jitter.py run results/baseline/spec.json --binary replay-build/jitter
```

## Development and verification

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
ctest --test-dir build --output-on-failure
```

CTest runs the C++ core checks and the Python suite, including actual-engine integration. Checks cover digest values, invalid workloads, overload accounting, deterministic generation, independent oracle agreement, timestamp/response corruption, constraints, timeout and application failure retention, integrity checks, and saved-executable reproduction. Release builds use real assertions in the test harness rather than assertions disabled by `NDEBUG`.

GitHub Actions runs these checks on Ubuntu and macOS, then builds a baseline bundle and reproduces it. CI is functional verification on shared runners, not a controlled performance experiment.

## Module boundaries

- `include/jitter/application.hpp`, `src/application.cpp`: supported application and response contract.
- `include/jitter/experiment.hpp`, `src/experiment.cpp`: C++ workload admission, workers, timestamps, resource measurements, and raw serialization.
- `tools/jitter_runner/adapters.py`: adapter registry and build/launch/validate integration points.
- `tools/jitter_runner/spec.py`, `workload.py`: strict experiment contracts, seeded inputs, independent response oracle.
- `tools/jitter_runner/metrics.py`: raw-evidence validation and metric definitions.
- `tools/jitter_runner/storage.py`, `runner.py`: source snapshots, content integrity, orchestration, and reproduction.
- `tools/jitter.py`: thin baseline command interface.

Future observation, controller, and search modules can consume these inputs and results without embedding application-specific correctness logic.
