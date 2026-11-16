# Jitter | C++20 Market-Data Replay & Order-Book Engine

Jitter replays a simulated trading-desk incident. A C++20 receiver reconstructs and recovers a market-data book over redundant UDP. A SQLite audit matches the desk's booked fills against a simulated venue drop copy.

## Run the desk incident

```sh
python3 tools/desk_incident.py --output results/desk-incident
```

Open `results/desk-incident/report.html`. It shows a lost packet repaired by the second feed, a dual-feed gap and verified snapshot recovery, and a missing fill that affects a position until the delayed report arrives. The report links the source records, state proof and operator CSV. [Runbook and reproduction](docs/desk-incident.md) · [Receiver measurements](docs/desk-results.md)

On the native M4 Pro, dual-feed steady replay at 100K events/s published every event across five runs with median receiver p99 **0.084 µs** and scheduled-to-publication p99 **16.125 µs**. The receiver clock begins after the local receive system call. In the separate burst stress test, bounded drains reduced local send-to-receipt p99 **18.554 → 0.147 ms**. [Raw paired runs](docs/evidence/desk/udp-confirmation-arm64.json)

## In-process market replay

The original deterministic book engine and benchmarks isolate book work and measure open-loop queue handoff. These controlled results use different timing boundaries from the UDP lab.

| Experiment on native M4 Pro | Baseline → candidate | Evidence |
| --- | ---: | --- |
| Direct replay, sparse reference → dense book | **14.51M → 36.83M events/s** | [Eight book pairs](docs/evidence/market/paired-arm64.json) |
| 500K steady feed, same mutex queue, reference → dense book | **27.333 → 17.771 µs scheduled p99** | [Eight book pairs](docs/evidence/market/paired-arm64.json) |
| 500K steady feed, same dense book, sleeping mutex → polling SPSC | **10.313 → 0.542 µs scheduled p99** | [Eight queue confirmation pairs](docs/evidence/market/queue-confirmation-arm64.json) |
| Process CPU in that queue comparison | **1.36 → 2.00 approximate cores** | [Queue study](docs/market-queue.md) |

Values are medians of per-run measurements. The dense-book and queue studies used separate paired runs. Their median paired p99 reductions were **38.4%** and **94.8%**, respectively, with every measured event published. Scheduled p99 starts at the prescribed release and ends when the consumer writes its in-process update. These are synthetic replay measurements.

The dense book uses fixed order-ID and tick arrays with a bitmap per side. The feed supports partial fills, duplicate suppression and sequence-gap detection. A gap suppresses further delta publication until a validated authoritative snapshot covers the discarded sequences. Differential replay tests compare its published updates and recovered state with the sparse reference. [Architecture and correctness](docs/market-replay.md).

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
# On Apple Silicon, add -DCMAKE_OSX_ARCHITECTURES=arm64 to configure.
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
build/jitter_market --mode direct --book dense --events 100000 --warmup 20000 --seed 17
build/jitter_market --mode feed --book dense --queue spsc --events 400000 --warmup 20000 --seed 1001 --rate 500000
```

The sleeping mutex is the default. The original sweep's passing tested prefix ended at 500K events/s for the mutex and 2M for polling. Polling passed p99 and failed p99.9 at 4M. In a later trace, startup release lateness dominated the 4M tail. The matching uninstrumented reruns showed that the failing percentile varies across runs. [Load envelope](docs/market-queue.md#load-envelope) · [4M investigation and Linux reproduction](docs/market-tail.md).

## Performance investigation toolkit

The original `jitter` CLI investigates C++ tail latency using dependency evidence, adaptive experiments, correctness checks and fresh confirmation. It supports the shared-index service, pinned nlohmann/json parsing and pinned SQLite aggregation. An offline dashboard opens the resulting evidence. Market replay emits a separate JSON schema.

## Install and check

Requires Linux or macOS, Python 3.9+, Git, CMake 3.20+, a C compiler and a C++20 compiler. Upstream sources are vendored; building the application requires no dependency download. The optional editable CLI installation downloads its Python build tooling.

```sh
git clone https://github.com/bforce541/jitter.git
cd jitter
python3 -m venv jitter-env
. jitter-env/bin/activate
python3 -m pip install --no-deps -e .
jitter doctor
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
```

Keep the source checkout with an editable installation. A standalone Python wheel without the C/C++ source tree is not the supported distribution. Without installation, run the same commands as `python3 tools/jitter.py ...`.

## Run an investigation

```sh
jitter run examples/baseline.json --output results/baseline
jitter investigate examples/investigation.json --dry-run
jitter investigate examples/investigation.json --output results/investigation
jitter report results/investigation
jitter verify results/investigation
jitter reproduce results/investigation --output results/replay
```

The default investigation builds isolated implementation engines. To reuse the engine already built above, add `--binary build/jitter`. `examples/json-investigation.json` and `examples/sqlite-investigation.json` exercise the upstream integrations.

Read `report.md` for the outcome and `report.json` for the full structured evidence. A recommendation needs zero lost work, passing resource/throughput constraints, adequate tail samples and a fresh fixed confirmation block. Inconclusive results and budget stops are legitimate outcomes. Timings and selections may change on reproduction.

## Inspect and preserve evidence

```sh
jitter run examples/contention.json --output results/observation
jitter diagnose results/observation --run 1
jitter overhead examples/contention.json --output results/overhead
jitter pack results/investigation --output results/evidence.tar.gz
jitter unpack results/evidence.tar.gz --output results/restored
```

All output paths must be new. Generated local results are excluded from Git. Portable archives preserve raw inputs, responses, traces, source, executables and manifests while deduplicating repeated content.

## Local visual inspection

```sh
jitter ui results
```

Opens a polished, read-only localhost dashboard for real investigations, search decisions, fresh confirmation, dependency paths, metrics, uncertainty, budgets, process logs and instrumentation overhead. It works offline after installation and has no accounts, API keys or per-use API cost. The CLI remains primary. [Dashboard guide](docs/dashboard.md).

## Evaluate

```sh
jitter evaluate examples/evaluation.json --binary build/jitter --output results/evaluation
jitter calibrate --trials 1000 --output results/calibration
python3 tools/evaluate_diagnostics.py --output results/diagnostic-controls
```

The full evaluation is substantially longer than a smoke test. It compares guided, random, grid and local search, plus the hypothesis-disabled ablation, under identical declared per-scenario budgets. It preserves negative, failed and inconclusive cases. Shared CI runners validate behavior; they do not provide performance gates.

[Evaluation report](docs/evaluation-report.md) · [Controller and statistics](docs/phase3.md) · [Variants and CLI](docs/phase4.md) · [Architecture](docs/architecture.md) · [Troubleshooting](docs/troubleshooting.md)

[Baseline contracts](docs/phase1.md) · [Observation contracts](docs/phase2.md) · [JSON integration](docs/json-contract.md) · [SQLite integration](docs/sqlite-contract.md) · [Roadmap](docs/roadmap.md)
