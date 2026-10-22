# Jitter

Jitter investigates C++ tail latency with controlled workloads, dependency evidence, adaptive experiments, independent correctness checks, and fresh confirmation. It returns a supported configuration with evidence, or explains why it cannot recommend one.

Supported integrations are an immutable shared-index service, pinned nlohmann/json parsing and projection, and pinned SQLite aggregation with explicit indexing alternatives. Jitter owns the workload drivers. It does not safely rewrite arbitrary C++ or establish performance in an untested production service.

## Install and check

Requires Linux or macOS, Python 3.9+, Git, CMake 3.20+, a C compiler and a C++20 compiler. Upstream sources are vendored; building the application requires no dependency download. The optional editable CLI installation downloads its Python build tooling.

```sh
git clone https://github.com/bforce541/jitter.git
cd jitter
python3 -m venv .venv
. .venv/bin/activate
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

## Evaluate

```sh
jitter evaluate examples/evaluation.json --binary build/jitter --output results/evaluation
jitter calibrate --trials 1000 --output results/calibration
python3 tools/evaluate_diagnostics.py --output results/diagnostic-controls
```

The full evaluation is substantially longer than a smoke test. It compares guided, random, grid and local search, plus the hypothesis-disabled ablation, under identical declared per-scenario budgets. It preserves negative, failed and inconclusive cases. Shared CI runners validate behavior; they do not provide performance gates.

[Evaluation report](docs/evaluation-report.md) · [Controller and statistics](docs/phase3.md) · [Variants and CLI](docs/phase4.md) · [Architecture](docs/architecture.md) · [Troubleshooting](docs/troubleshooting.md)

[Baseline contracts](docs/phase1.md) · [Observation contracts](docs/phase2.md) · [JSON integration](docs/json-contract.md) · [SQLite integration](docs/sqlite-contract.md) · [Roadmap](docs/roadmap.md)
