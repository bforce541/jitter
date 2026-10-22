# Jitter evaluation report

This report is generated from verified raw experiment bundles. Every planned outcome is retained. The checked-in catalog preserves policies, configurations, run-level comparisons, confirmation, costs and manifest identities; portable release evidence additionally contains raw requests, traces, sources and executables.

## Supported capabilities and claim boundary

Jitter autonomously selects registered controls, measures paired candidates, enforces correctness/constraints, and freshly confirms one frozen finalist. Supported applications are the controlled shared-index service, nlohmann/json projection and SQLite aggregation. The upstream engines are independently maintained; Jitter owns their service drivers. Arbitrary rewriting, production-service generality, optimal search and universal superiority are unsupported claims.

## Environment and provenance

Recorded host: `{"cpu_model": "Apple M4 Pro", "logical_cpus": 12, "machine": "arm64", "python": "3.12.2", "release": "25.6.0", "system": "Darwin"}`.

Measured source revision: `bc9ffd7f9436cb07f6672105bcef15988af0aa30`; recorded dirty state: `False`. The evaluation uses one frozen inventory and pinned executable bytes across strategies. Compiler and binary identities are in the catalog and raw bundles.

Pinned engine build: AppleClang 21.0.0.21000334, Release, source `c89ea4ca053f83b4373302e9a27d9b091e1affe9`, dirty `False`. The supplied executable is recorded independently from the controller's source snapshot; it is not claimed to have been compiled by the evaluation wrapper.

Evaluation manifest SHA256: `3ef121a5b5b32687318cbc8873f6c09913dba71520750960a043447ff4173591`.

Frequency, thermal state, cache state and background activity were uncontrolled. No local build, sanitizer suite or other benchmark ran concurrently with this measured cohort. Interactive host and lightweight report/tool activity were not isolated. Linux/macOS CI results are functional validation, not additional laboratory performance hosts.

## Workloads, spaces and baselines

| Scenario | Baseline adapter / implementation | Data settings | Arrivals | Space / candidate budget |
| --- | --- | --- | --- | --- |
| shared-contention | shared-index-v1 / coarse | `{"dataset_seed": 17, "heavy_rounds": 131072, "key_count": 8, "light_rounds": 64}` | 20000/s, 100 ms, burst 16, heavy 0.05 | 4 points / 2 candidates |
| json-small | json-projection-v1 / map | `{"dataset_seed": 17, "heavy_fields": 16, "key_count": 8, "light_fields": 4}` | 20000/s, 100 ms, burst 16, heavy 0.05 | 4 points / 2 candidates |
| json-large | json-projection-v1 / ordered | `{"dataset_seed": 17, "heavy_fields": 512, "key_count": 8, "light_fields": 128}` | 20000/s, 100 ms, burst 16, heavy 0.05 | 4 points / 2 candidates |
| shared-saturated | shared-index-v1 / coarse | `{"dataset_seed": 17, "heavy_rounds": 500000, "key_count": 256, "light_rounds": 256}` | 20000/s, 100 ms, burst 32, heavy 0.25 | 4 points / 2 candidates |
| sqlite-index | sqlite-aggregate-v1 / scan | `{"dataset_seed": 17, "heavy_rows": 256, "key_count": 64, "light_rows": 8}` | 20000/s, 100 ms, burst 16, heavy 0.05 | 4 points / 2 candidates |
| wide-v1 | shared-index-v1 / coarse | `{"dataset_seed": 17, "heavy_rounds": 500000, "key_count": 256, "light_rounds": 256}` | 20000/s, 100 ms, burst 32, heavy 0.25 | 30 points / 3 candidates |
| wide-v2 | shared-index-v1 / coarse | `{"dataset_seed": 17, "heavy_rounds": 500000, "key_count": 256, "light_rounds": 256}` | 20000/s, 100 ms, burst 32, heavy 0.25 | 30 points / 3 candidates |

Every process has a separate declared warmup, then a drained measurement window. All strategies receive the same per-scenario input bytes, supported space, maximum runs/candidates/builds, resource limits and confirmation policy. Grid, random without replacement, local search and the hypothesis-disabled guided ablation are tuning baselines. Strategy order varies deterministically within scenario/seed blocks. The original low-load cases remain alongside saturated and database cases; they were not removed after a weak result.

## Controller and statistics

Exploration ranks candidates using temporal hypotheses, local rank moves, measured effects and cost, with periodic global exploration. Exploration never recommends. Exactly one finalist and eight fresh randomized-order confirmation pairs are frozen before confirmation; failed confirmation cannot switch to another finalist. Comparisons use paired run p99 values, a practical margin of 5%, 95% requested one-sided confidence and a minimum 2,000 completed requests per run. There is no pooling of requests as independent replications.

The exact median-effect lower bound at eight pairs is the second-smallest effect (96.484375% conservative one-sided coverage). Every measured hard constraint and per-pair throughput tolerance must pass. Tail counts are an evidence guard, not a guarantee of precise p99 estimates. Nominal coverage assumes representative independent paired effects; that assumption cannot be established from host metadata. Repeated investigations and strategy comparisons have no family-wise false-positive guarantee.

The measured cohort's original comparison schemas are retained. The current stricter schema additionally vetoes incomplete warmup coverage. The exporter recomputes every comparison under the current rule and refuses publication if decision, effect or uncertainty changes. This cohort's current-rule audit leaves all recorded outcomes unchanged.

## Main results and repeated outcomes

Cells show confirmed recommendations / planned studies, not exploratory wins. Mixed outcomes are explicit; these small counts do not establish a statistically superior strategy.

| Scenario | Guided | Random | Grid | Local | Hypotheses disabled | Outcome variation |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| shared-contention | 3/3 | 2/3 | 3/3 | 2/3 | 3/3 | mixed: random, local |
| json-small | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | consistent observed outcomes |
| json-large | 3/3 | 3/3 | 2/3 | 3/3 | 3/3 | mixed: grid |
| shared-saturated | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | consistent observed outcomes |
| sqlite-index | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | consistent observed outcomes |
| wide-v1 | 0/3 | 3/3 | 0/3 | 3/3 | 2/3 | mixed: unguided |
| wide-v2 | 3/3 | 3/3 | 0/3 | 2/3 | 2/3 | mixed: local, unguided |

Planned: **105**; complete wrappers: **105**; failed: **0**; interrupted: **0**; unexecuted: **0**; confirmed recommendations: **75**.

## Cost and efficiency

| Strategy | Verified processes | Engine preparations | Total child seconds | Confirmed recommendations |
| --- | ---: | ---: | ---: | ---: |
| guided | 413 | 39 | 366.888 | 15 |
| random | 485 | 42 | 430.900 | 17 |
| grid | 429 | 42 | 377.226 | 11 |
| local | 477 | 41 | 422.752 | 16 |
| unguided | 404 | 40 | 346.184 | 16 |

## Preserved original policy and revised policy

The original five-scenario cohort is retained in full. It motivated a declared strong-gain exploration stop and priorities for registered alternatives under serialization. The new cohort preserves those five workloads and adds a 30-point search-space ablation with the old and new policies under the same budgets. Fresh fixed confirmation is unchanged. Source-archive validation reuse also changed between cohorts, so their wall-time difference cannot be assigned entirely to policy. The two wide-space cases use the same validation implementation; their policy blocks are sequential. These are repeated observations on one host, not a randomized causal estimate of policy effects.

| Comparable five-scenario cohort | Strategy | Accepted / planned | Processes | Child seconds |
| --- | --- | ---: | ---: | ---: |
| Original | guided | 11/15 | 343 | 321.547 |
| Original | random | 12/15 | 359 | 340.834 |
| Original | grid | 11/15 | 343 | 326.870 |
| Original | local | 12/15 | 327 | 315.062 |
| Original | unguided | 10/15 | 328 | 301.324 |
| Revised | guided | 12/15 | 311 | 275.048 |
| Revised | random | 11/15 | 331 | 294.205 |
| Revised | grid | 11/15 | 351 | 304.979 |
| Revised | local | 11/15 | 319 | 284.828 |
| Revised | unguided | 12/15 | 284 | 244.775 |

Engine preparations in this cohort use `--binary`; they are verified materializations, not compiler invocations. Full wrapper seconds include analysis and final verification. The ablation saves its diagnostic process and that cost remains counted. Logical child file bytes count repeated paths; private hardlinks and portable chunk archives reduce physical storage. Conditional effects among accepted studies are selected outcomes, not unbiased average strategy effects.

## Time to decision and recommendation

| Strategy | Median full wrapper seconds, all outcomes | Median seconds through accepted decision | Accepted decisions timed |
| --- | ---: | ---: | ---: |
| guided | 18.070 | 17.316 | 15 |
| random | 20.573 | 19.772 | 17 |
| grid | 18.864 | 19.935 | 11 |
| local | 20.529 | 20.160 | 16 |
| unguided | 16.729 | 17.468 | 16 |

Accepted-decision times include exploration, preparation and fresh confirmation through the controller decision. Wrapper time additionally includes final verification. Accepted-only times are conditional, so the all-outcome costs and counts must be considered alongside them. Precise time from investigation start to the first promising exploratory point was not separately journaled in these cohorts; individual trial durations are retained without relabeling them as that missing metric.

## Fresh-confirmation examples and resource tradeoffs

| Scenario / seed / strategy | Candidate | Median paired p99 improvement | One-sided lower bound | Median baseline → candidate p99 (ms) | Median CPU cores | Median peak RSS (bytes) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| shared-contention / 1 / guided | `{"implementation": "narrow", "queue_capacity": 4096, "workers": 4}` | 31.63% | 26.63% | 0.773 → 0.506 | 0.271 → 0.260 | 3964928 → 3960832 |
| json-small / guided | no confirmed recommendation | — | — | — | — | — |
| json-large / 1 / guided | `{"implementation": "map", "queue_capacity": 4096, "workers": 4}` | 50.54% | 49.11% | 1.178 → 0.585 | 1.761 → 0.927 | 4651008 → 4630528 |
| shared-saturated / 1 / guided | `{"implementation": "narrow", "queue_capacity": 4096, "workers": 4}` | 98.30% | 98.25% | 122.556 → 2.093 | 1.019 → 2.495 | 3981312 → 3956736 |
| sqlite-index / 1 / guided | `{"implementation": "indexed", "queue_capacity": 4096, "workers": 4}` | 99.89% | 99.88% | 426.964 → 0.476 | 1.012 → 0.302 | 5410816 → 6244352 |
| wide-v1 / guided | no confirmed recommendation | — | — | — | — | — |
| wide-v2 / 1 / guided | `{"implementation": "narrow", "queue_capacity": 4096, "workers": 4}` | 98.31% | 98.28% | 124.124 → 2.092 | 1.021 → 2.505 | 3985408 → 3954688 |

Examples use the smallest accepted guided seed per scenario for inspection, not the largest effect. Every other seed and nonaccepted result remains in the catalog. Saturation can make a large end-to-end latency reduction correspond to a much smaller change in service capacity; inspect throughput and CPU rather than interpreting the percentage as a compute-speed multiplier.

## Negative, invalid and inconclusive evidence

The table retains low-load/noisy cases and unsuccessful confirmation. Per-trial statuses distinguish invalid data, resource violations, regression, practical equivalence and uncertainty. C++/Python adversarial integration tests additionally exercise wrong outputs, warmup loss, dropped traces, failed builds/executors, impossible resources, timeout, interruption, corrupt/resealed reports and unsafe archives. Synthetic controls are separate from measured application outcomes.

| Measured stage | Outcome | Count |
| --- | --- | ---: |
| Exploration | constraint_violation | 7 |
| Exploration | exploratory | 46 |
| Exploration | inconclusive | 1 |
| Exploration | invalid | 29 |
| Exploration | promising | 100 |
| Fresh confirmation | accepted | 75 |
| Fresh confirmation | constraint_violation | 3 |
| Fresh confirmation | inconclusive | 9 |

Measured journaled child/build failures: 0. Exploratory failure kinds: `{}`. Zero observed correctness failures in a measured cohort is not proof of arbitrary transformation safety; every output still passes its registered independent oracle.

## Calibration and diagnostic baselines

- Synthetic boundary_null: 33/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.023592927958, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 0.045981264467}`.
- Synthetic constraint_violation: 0/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.0, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 0.003826758486}`.
- Synthetic host_drift: 0/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.0, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 0.003826758486}`.
- Synthetic insufficient_tail: 0/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.0, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 0.003826758486}`.
- Synthetic no_meaningful_difference: 0/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.0, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 0.003826758486}`.
- Synthetic noisy_null: 9/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.004742060667, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 0.017015816165}`.
- Synthetic regression: 0/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.0, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 0.003826758486}`.
- Synthetic true_improvement: 1000/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.996173241514, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 1}`.
- Synthetic winner_curse_null: 6/1000 accepted; Monte Carlo rate interval `{"confidence": 0.95, "lower": 0.002752669142, "method": "Wilson binomial Monte Carlo rate interval", "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee", "upper": 0.013028168242}`.

Winner-selection control: fresh holdout accepted 6/1000; deliberately unsafe reuse accepted 63/1000. This is simulated selection bias, not a measured controller benchmark.

Hand-timed diagnostic controls: dependency analysis matched 6/6 known mechanisms; delay-only 5/6; region-only 2/6. The controls include an unresolved owner and explicit trace-loss abstention. These are transparent weak baselines on six constructed fixtures, not a claim against production profilers.

## Cold build, correctness and reproduction

The same immutable cold-study evidence was verified in three alternating cached/uncached pairs, after one warmup. Every path and raw response was still checked; only repeated identical source-archive decomposition was reused within each pass.

| Verification-cost trial | Without reuse (s) | With reuse (s) | Descriptive ratio | Tool provenance |
| --- | ---: | ---: | ---: | --- |
| 1 | 1.511 | 0.897 | 1.68× | not separately recorded in the initial exploratory trial |
| 2 | 1.381 | 0.888 | 1.55× | 1f729abed730fe2fa94272d341af3e63f751a34e |

The resulting reports and manifest identity matched in every run. All recorded trials are retained, including the preliminary trial before tool-source provenance was added. These are tooling costs on one host, with no significance or universal speedup claim.

## Instrumentation overhead

Five alternating tracing-off/on pairs per listed configuration use identical inputs and executable bytes. Signed medians describe observations; they are not causal overhead estimates. If any pair loses trace events or changes coverage/delivery, aggregate deltas are withheld.

| Adapter / implementation / workers | Comparable pairs | Median p99 on-minus-off (ms) | Median CPU ns/request delta | Median RSS delta (bytes) | Reserved trace bytes | Dropped events |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| shared-index-v1 / narrow / 1 | 5/5 | -2.140 | -454.000 | 4198400.000 | 4194304 | 0 |
| shared-index-v1 / narrow / 8 | 5/5 | 0.004 | 5225.000 | 18886656.000 | 18874368 | 0 |
| json-projection-v1 / map / 4 | 5/5 | -0.002 | 1829.500 | 10481664.000 | 10485760 | 0 |
| sqlite-aggregate-v1 / indexed / 4 | 5/5 | 0.030 | 1806.500 | 10543104.000 | 10485760 | 0 |

The catalog retains every signed pair, throughput change, trace volume, resource feasibility and host identity. Reserved buffers and process peak RSS are different measurements. These few configurations do not establish overhead scaling for arbitrary services.

The separate default-path SQLite study returned `accepted_recommendation`. It used 27 processes and 2 isolated compiler builds; recorded build/preparation time 24.131 s, orchestration through decision 52.649 s, timed application CPU 10.239 s, aggregate application setup 0.156 s. Index creation is outside request quantiles, inside investigation cost and peak RSS.

All source/binary/input identities and per-response correctness checks are retained. Exact binary replay needs the recorded OS/architecture; other hosts can validate raw evidence and rebuild frozen sources for new timings. The fresh source-install and CLI/portable-evidence flow and sanitizer results are recorded in the release verification record.

```sh
python3 tools/jitter.py evaluate examples/evaluation.json --binary build/jitter --output results/new-evaluation
python3 tools/jitter.py calibrate --trials 1000 --output results/new-calibration
python3 tools/evaluate_diagnostics.py --output results/new-controls
python3 tools/jitter.py unpack jitter-evaluation-v2.tar.gz --output results/restored-evaluation
python3 tools/jitter.py verify results/restored-evaluation
python3 tools/jitter.py reproduce results/restored-evaluation --output results/new-replay
```

Reproduce a recommended configuration's exact single-process inputs by replaying its saved confirmation candidate sub-bundle. That functional replay alone is not a new statistical confirmation. Full investigation/evaluation replay collects new independent evidence and may choose differently.

## Limitations and unsupported claims

One uncontrolled host, three order/search seeds and related controlled workloads do not establish general performance, broad profiler accuracy or universal superiority over random/local/grid search. The JSON/SQLite service drivers are maintained here, not independent production deployments. No arbitrary source rewriting, per-request p99 confidence interval, proof of causality, global optimum, hard real-time CLI deadline or campaign-wide multiplicity guarantee is claimed. Weak/mixed outcomes remain evidence rather than being relabeled wins.

[Machine-readable unfiltered catalog](evidence/evaluation-catalog.json) · [Method and workflow](phase5.md) · [Statistical contract](phase3.md)
