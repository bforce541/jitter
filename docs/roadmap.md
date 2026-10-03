# Implementation roadmap

Phase 0 establishes the build, repository conventions, and CI. It contains no performance research functionality.

## Phase 1: Experiment foundation

Application adapters, controlled workloads, baseline latency and resource metrics, correctness contracts, experiment specifications, and reproducible result storage.

Acceptance: repeatable baseline runs account for every request and can be reproduced from a saved specification.

## Phase 2: Observation and bottleneck analysis

Efficient C++ instrumentation, trace loss accounting, cross-thread dependencies, hypothesis generation, and observation overhead measurement.

Acceptance: controlled scenarios produce relevant, testable hypotheses with documented overhead and trace limitations.

## Phase 3: Autonomous investigation

Configuration search, hypothesis-informed experiment selection, repeated comparisons, uncertainty estimates, constraints, fresh confirmation runs, budgets, and stopping rules.

Acceptance: complete autonomous configuration investigations accept, reject, or return inconclusive results based on evidence.

## Phase 4: Variants and product completion

Explicit implementation alternatives, isolated candidate builds, bounded transformations with applicability and legality conditions, correctness validation, CLI, reports, and failure recovery.

Acceptance: all promised public functionality and failure paths work, and recommendations or proposed patches have reproducible evidence.

## Phase 5: Evaluation and release

Independently maintained application integrations, credible tuning and diagnostic baselines, experiment-cost evaluation, analysis ablations, negative cases, reliability fixes, documentation, and a reproducible evaluation report.

Acceptance: a fresh user can install Jitter, investigate a supported application, inspect evidence, and reproduce results. Claims must match observed results; weak controller performance requires further work.

## Module boundaries

- Application adapters define supported behavior, settings, and validation.
- Workload and experiment orchestration control execution and provenance.
- Metrics and instrumentation collect measurements and traces.
- Dependency analysis proposes hypotheses.
- Search and the controller choose experiments.
- Statistics, correctness validation, and constraints determine acceptable conclusions.
- Reporting presents decisions and reproduction instructions.

Concrete application selection, workload contracts, storage formats, and transformation scope will be decided during their respective phases.
