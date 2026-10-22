# Phase 3: autonomous investigation

Jitter searches supported controls, measures candidates, and returns a confirmed recommendation, no improvement, inconclusive evidence, an invalid investigation, or a budget stop. It never requires a human to choose the next configuration within a declared search space.

```sh
python3 tools/jitter.py investigate examples/investigation.json --dry-run
python3 tools/jitter.py investigate examples/investigation.json --output results/investigation
python3 tools/jitter.py report results/investigation
python3 tools/jitter.py verify results/investigation
python3 tools/jitter.py reproduce results/investigation --output results/replay
```

Default execution compiles registered implementations separately from one archived source tree. `--binary build/jitter` can reuse a compatible engine and avoid compilation; supplied executables are explicitly identified as supplied, not assumed to correspond to arbitrary source. Reproduction pins the original executables, workload, source snapshot, policy and seed. Adaptive selections and timings may change on replay.

## Investigation contract

The version-2 investigation specification separates a version-3 single-run `experiment` from `search`, `sampling`, `comparison`, `budget`, `diagnostic`, and `stopping`. Every field is checked; unknown fields fail before execution. Version 1 retains its original selection and stopping behavior for archived investigations. See the complete examples for exact JSON.

Version 2 tests registered serialization alternatives when queue propagation points through a serialized resource, and representation alternatives when JSON execution dominates. These are experiment priorities, not causal conclusions. Its declared `stopping.strong_gain_fraction` (25% in the examples) moves to fresh confirmation after the minimum exploration pairs only when every observed paired gain exceeds the threshold and all comparison guards pass. It applies to every tuning strategy. It never accepts exploratory evidence, reduces the fixed holdout, or searches for another finalist after failed confirmation. Disable it explicitly to continue the full candidate search. This policy trades potential additional improvement for lower experiment cost; it does not establish a global optimum.

Only `workers`, `queue_capacity`, and registered `implementation` alternatives vary. Dataset identity, digest rounds, JSON field counts, SQL row counts, request mix, arrival schedule and objective constraints stay fixed. Speeding up a different workload is not an optimization comparison.

Search parameters accept discrete lists or integer `{min,max,step}` ranges. Categorical implementation names are adapter-specific. Partial configuration exclusions express invalid combinations. The Cartesian space, including the exact baseline, is bounded to 4,096 points. The baseline cannot be excluded. This is a bounded product, not a general constraint solver or continuous optimizer.

Strategies are `guided`, `random`, `grid`, and `local`. Guided selection uses adjacent rank moves around the best valid exploratory point, observed marginal effects, trial cost and supported trace hypotheses; every third selection explores globally. Random search samples without replacement under a recorded seed. Grid uses canonical order. Local explores neighboring ranks and can stop at a local neighborhood. Scores prioritize experiments; they are not causal probabilities. Failed or quality-inconclusive points cannot become an incumbent. No configuration is silently retried.

A separate diagnostic process records the baseline trace. Event loss suppresses hypotheses; it does not fabricate missing ownership. Final comparisons always disable tracing. SQL execution evidence can suggest testing the registered index, while lock and queue evidence influence worker/implementation choices. No profiler-only hypothesis authorizes a recommendation.

## Sampling and statistical decision

Each exploratory candidate receives at least two randomized-order baseline/candidate pairs, using fresh application processes and identical inputs. Additional exploratory pairs are bounded by `max_exploration_pairs` and are reserved for promising evidence. Exploration remains descriptive.

Before observing confirmation, Jitter freezes **one** finalist and the fixed number of fresh confirmation pairs in `state.json`. At least eight pairs are required. Confirmation never stops early for a favorable result, never reuses exploratory runs, and never substitutes a second finalist after failure. Incomplete or interrupted blocks cannot recommend a candidate.

For each pair, the effect is `1 - candidate_quantile / baseline_quantile`. Positive means improvement. Jitter reports the median paired effect, absolute nanoseconds, dispersion and exact binomial order-statistic bounds for the population median paired run effect. Requests within one process are not treated as independent experimental replications or pooled across runs.

At eight pairs and 95% requested one-sided confidence, the lower bound is the second-smallest paired effect; achieved conservative one-sided coverage is 96.484375%. Acceptance requires that bound to be strictly above the configured practical improvement margin. Upper and lower bounds are separately one-sided, not a joint confidence interval. Ties and floating boundaries do not count as strictly better. The methods follow [NIST's sign-test discussion](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/signtest.htm) and [median order-statistic limits](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mediancl.htm).

The comparison metric is `p50`, `p95`, `p99`, or `p999` (p99.9). With the minimum 20 tail observations, the corresponding completion guards are 40, 400, 2,000 and 20,000 **per run**. Smaller samples are explicitly inconclusive. Secondary quantiles remain descriptive and carry their own sufficiency indicators. These guards do not establish precise per-request tail estimates.

Every baseline and candidate run must pass correctness, all absolute constraints, complete request coverage, zero rejections/errors, compatible recorded host properties, and any trace-quality checks. Comparison schema 2 also requires complete, rejection-free warmup coverage. Archived comparison schema 1 keeps its original measurement-only guard; new recommendations use schema 2. Each pair must also pass the maximum relative throughput loss. Improving latency by dropping work cannot pass. Baseline max/min variation above the declared stability bound makes the comparison inconclusive. Observed memory and CPU limits are not future guarantees.

Exact coverage assumes independent, representative paired effects. Fresh processes, randomized order and equal host metadata help experimental design but do not establish those assumptions. Thermal drift, scheduling, frequency and interference remain possible. There is no campaign-wide multiple-testing guarantee across repeated investigations, workloads, metrics or restarts.

## Budgets and state

Budgets bound candidate attempts, application processes, candidate builds, failed attempts and wall-time admission. Failures consume budget. The controller reserves confirmation capacity before adding exploratory candidates and can end without a recommendation. Process timeouts use the remaining deadline and clean up the inherited process group. Source preparation, artifact hashing, verification and reporting add bounded orchestration overhead; this is not a hard real-time deadline for the CLI process.

The journal retains every attempted measurement, failed child, pair order, selection reason, comparison, finalist, confirmation status, source and build identity, and consumed cost. No filtered subset of good pairs replaces the attempted block. `report.json` is regenerated from the journal and independently validated raw evidence; a resealed favorable report alone cannot pass verification.

`cost.seconds` measures elapsed orchestration through decision recording; the evaluation wrapper additionally measures the entire child invocation including final verification. Timed application CPU and application preparation wall time are separate. Process preparation (including SQLite table/index construction) is outside the arrival window and inside investigation cost; its memory contributes to peak RSS.

## Recovery

An interrupt preserves child logs and a terminal interrupted journal. `restart BUNDLE --output NEW` rebuilds from the captured source for a new independent investigation; if startup stopped before source capture it uses the current checkout, records that new provenance, and starts fresh. It does not continue a partly observed holdout. A failed build cannot modify the baseline source or cache. Failed final validation removes the generated recommendation and marks the bundle failed. Malformed or failed evidence remains available for diagnosis and cannot verify as a valid recommendation.

[Variants and CLI](phase4.md) · [Architecture](architecture.md) · [Evaluation](evaluation-report.md)
