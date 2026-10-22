# Phase 5: evaluation and release

The release workflow combines measured controller studies, synthetic statistical calibration, known-timestamp dependency controls, real-engine failure tests, clean-checkout installation, sanitizers and Linux/macOS CI. [The evaluation report](evaluation-report.md) records actual outcomes and supported claims.

```sh
python3 tools/jitter.py evaluate examples/evaluation.json --binary build/jitter --output results/evaluation
python3 tools/jitter.py verify results/evaluation
python3 tools/jitter.py calibrate --trials 1000 --output results/calibration
python3 tools/evaluate_diagnostics.py --output results/diagnostic-controls
```

The plan preserves the original low-load cases, a saturated shared-index workload and a real SQLite index comparison. It also compares policy versions on a wider 30-point shared-index space. Guided, random, grid, local and hypothesis-disabled strategies receive identical declared scenario budgets and confirmation policies. Seven scenarios, five strategies and three seeds produce 105 investigations; `examples/evaluation-v1.json` retains the original 75-study plan. Three search/order seeds expose some repeated-outcome variation; they do not establish generality across workloads or hardware. Guided search is not presumed superior.

Every study retains correct, invalid, infeasible, failed and inconclusive outcomes. Only accepted fresh confirmation counts as a recommendation. Per-group raw decisions, experiment count, engine preparation count, elapsed time, retained storage and conditional confirmation effects remain available. Engine records produced using `--binary` are executable materializations, not compiler invocations; their cold-build cost must be assessed separately using the default isolated-build path.

The statistical calibration includes a practical-boundary null, continuous noisy null, winner selection with an independent holdout, deliberately unsafe reuse of exploratory evidence, a true improvement, regression, practical equivalence, resource failure, insufficient tails and host drift. These generated aggregate summaries test decision behavior. They are not measured p99 estimates or application speedups.

The diagnostic controls contain six handcrafted trace mechanisms: observed holder overlap, execution region, queueing, delayed admission, unattributed lock waiting and trace loss. Region-only and delay-only baselines are transparent weak heuristics. Their fixture match counts do not establish production-profiler superiority.

Functional CI installs the source CLI, runs the test suite and complete investigation/replay/archive flow on Linux and macOS. A Linux job instruments C and C++ with address and undefined-behavior sanitizers. Local thread-sanitizer coverage is recorded separately. No CI timing win is required.

A portable evidence archive can be restored and independently verified before any saved executable is run. Reproduction reuses original binary bytes only on a compatible OS/architecture. Fresh hosts can rebuild from frozen sources and collect new evidence. Release claims remain bounded to these explicit integrations, workloads and observations; an independently maintained production service and broader hardware evaluation remain unestablished.
