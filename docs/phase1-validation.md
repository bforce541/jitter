# Phase 1 verification record

Verified on October 5, 2026. These are functional checks and observations from an uncontrolled local host, not an optimization study.

## Acceptance evidence

| Phase 1 requirement | Implemented evidence |
| --- | --- |
| Supported application and integration points | `shared-index-v1` C++ application; adapter owns build, launch, and validation. |
| Defined correctness | Independent affine-response oracle checks every completion, including warmup. Input identity, ordering, timestamps, and final status are checked. |
| Controlled workloads | Versioned specifications, portable seeded inputs, steady/burst arrivals, explicit warmup and measurement windows. |
| Latency and resources | Intended-arrival latency, response/queue/service delay, scheduler lag, completed/offered rates, process CPU time, normalized peak RSS. |
| Complete request accounting | Every input must have one completed or rejected result. Lost, duplicate, extra, incorrect, pending, or errored results invalidate the run. |
| Repeated baselines | Fresh application processes with identical declared inputs; per-run samples remain separate. |
| Reproducible storage | Saved specification, workload, raw evidence, source snapshot, exact executable, build settings, host details, hashes, and logs. |
| Reproduction | Saved inputs and executable replay into a new bundle after full original-bundle verification. |
| Honest outcomes | Correctness, feasibility, execution failure, scheduling quality, and statistical limitations are separate. |

## Automated verification

The Release CTest suite passes the C++ core checks and 20 Python tests, including actual-engine integration. Coverage includes response golden values, workload parsing and seeds, independent-oracle agreement, overload, raw-output corruption, each constraint, empty completion samples, failed processes, timeout retention, source/archive integrity, modified summaries, and exact-executable reproduction.

The same suite passes local Debug builds using AddressSanitizer with UndefinedBehaviorSanitizer, and a separate ThreadSanitizer build. Sanitizer timings are not used as performance evidence.

Reproduce these checks with:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure

cmake -S . -B build-sanitized -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS='-fsanitize=address,undefined -fno-omit-frame-pointer' \
  -DCMAKE_EXE_LINKER_FLAGS='-fsanitize=address,undefined'
cmake --build build-sanitized --parallel 2
ctest --test-dir build-sanitized --output-on-failure

cmake -S . -B build-thread -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS='-fsanitize=thread -fno-omit-frame-pointer' \
  -DCMAKE_EXE_LINKER_FLAGS='-fsanitize=thread'
cmake --build build-thread --parallel 2
ctest --test-dir build-thread --output-on-failure
```

GitHub Actions also runs the Release suite, builds a baseline bundle, verifies it, and reproduces it on Ubuntu and macOS. See the repository's Actions history for the result associated with each commit.

## Local end-to-end observations

The final local example bundles were produced from clean revision `a9e3290dec8575d08267ad15f4b8f1248d1361eb`, using a Release AppleClang build. Their saved engine SHA-256 is `65544c40569eab193d3d27581772b31b707056e2d0da5cf36d02df55496a90c4` for the baseline and its reproduction. Artifact hashes and raw-evidence validation pass for all three bundles.

| Scenario | Measurement requests per repetition | Observed outcome |
| --- | --- | --- |
| Steady baseline, 3 repetitions | 2,000 | All responses correct; zero rejected; all configured constraints passed. Per-run p99: 0.260, 0.243, 0.243 ms. |
| Saved-executable reproduction, 3 repetitions | 2,000 | Same workload bytes and engine identity; all responses correct; zero rejected. One repetition failed the scheduling-lag constraint. Per-run p99: 0.244, 12.476, 2.584 ms. |
| Burst overload, 3 repetitions | 4,000 | All admitted responses correct and all offered work accounted for. Rejections: 2,499, 2,435, 2,459. Every repetition failed throughput and rejection constraints; one also failed scheduling tolerance. |

The timing variation in reproduction is retained rather than filtered away. Identical inputs and executable do not control host scheduling. No comparison above establishes an improvement, an equivalence claim, or statistical significance.

The bundles are local generated outputs under `results/phase1-final-baseline`, `results/phase1-final-reproduction`, and `results/phase1-final-burst`; they are intentionally excluded from Git. A fresh checkout can recreate equivalent inputs with:

```sh
python3 tools/jitter.py run examples/baseline.json --output results/baseline
python3 tools/jitter.py reproduce results/baseline --output results/reproduction
python3 tools/jitter.py run examples/burst.json --output results/burst
```

Phase 2 has not started. Lock-wait tracing, dependency graphs, observation-overhead analysis, autonomous search, and independent-software evaluation remain outside this milestone.
