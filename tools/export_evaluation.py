#!/usr/bin/env python3
"""Generate the checked-in release report from verified, unfiltered evidence."""

import argparse
import json
from pathlib import Path

from jitter_runner.evaluation import verify_evaluation
from jitter_runner.calibration import verify_calibration
from jitter_runner.investigation import verify_investigation
from jitter_runner.spec import load_json, ExperimentError
from jitter_runner.statistics import compare_pairs
from jitter_runner.storage import sha256, verify_integrity, write_json
from jitter_runner.search import normalize_space
from jitter_runner.overhead import verify_overhead


def compact_study(path):
    spec = load_json(path / "investigation-spec.json")
    report = load_json(path / "report.json")
    state = load_json(path / "state.json")
    cache = {}
    def paired(trial):
        result = []
        for entry in trial["pairs"]:
            row = {}
            for side in ("baseline", "candidate"):
                name = entry[side]
                if not name or state["records"][name]["status"] != "complete":
                    row[side] = {}
                    continue
                if name not in cache:
                    cache[name] = (load_json(path / name / "summary.json"), load_json(path / name / "metadata.json"))
                summary, metadata = cache[name]
                row[side] = summary["runs"][0]
                row[side + "_host"] = metadata["host"]
            result.append(row)
        return result
    audits = []
    trials = report["trials"] + ([report["confirmation"]] if report["confirmation"] else [])
    for trial in trials:
        if not trial.get("comparison"):
            continue
        confirmation = trial is report["confirmation"]
        checked = compare_pairs(paired(trial), spec["comparison"], confirmation=confirmation)
        unchanged = all(checked[key] == trial["comparison"][key] for key in ("status", "accepted", "effect", "uncertainty"))
        audits.append({"candidate":trial["id"],"confirmation":confirmation,"recorded_schema":trial["comparison"]["schema_version"],
                       "current_schema":checked["schema_version"],"decision_and_effect_unchanged":unchanged,
                       "current_status":checked["status"]})
    if any(not row["decision_and_effect_unchanged"] for row in audits):
        raise ExperimentError(f"current-rule revalidation changed a recorded decision at {path}; investigate before publication")
    return {"spec":spec,"report":report,"current_rule_audit":audits,
            "engine_identities":{name:row.get("engine_sha256") for name,row in state["builds"].items()},
            "engine_builds":{name:{key:row.get(key) for key in ("build","origin","seconds")} for name,row in state["builds"].items()},
            "confirmation_candidate_bundle":state["confirmation"]["pairs"][0]["candidate"] if state["confirmation"] and state["confirmation"]["pairs"] else None}


def pct(value):
    return "—" if value is None else f"{100*value:.2f}%"


def report_text(catalog):
    summary = catalog["evaluation"]["summary"]
    groups = summary["groups"]
    studies = catalog["studies"]
    first = next(iter(studies.values()))["report"]
    source, host = first["source"], first["host"]
    totals = summary["totals"]
    engine = next(iter(next(iter(studies.values()))["engine_builds"].values()))["build"]
    lines = ["# Jitter evaluation report", "",
             "This report is generated from verified raw experiment bundles. Every planned outcome is retained. The checked-in catalog preserves policies, configurations, run-level comparisons, confirmation, costs and manifest identities; portable release evidence additionally contains raw requests, traces, sources and executables.", "",
             "## Supported capabilities and claim boundary", "",
             "Jitter autonomously selects registered controls, measures paired candidates, enforces correctness/constraints, and freshly confirms one frozen finalist. Supported applications are the controlled shared-index service, nlohmann/json projection and SQLite aggregation. The upstream engines are independently maintained; Jitter owns their service drivers. Arbitrary rewriting, production-service generality, optimal search and universal superiority are unsupported claims.", "",
             "## Environment and provenance", "",
             f"Recorded host: `{json.dumps(host, sort_keys=True)}`.", "",
             f"Measured source revision: `{source['revision']}`; recorded dirty state: `{source['dirty']}`. The evaluation uses one frozen inventory and pinned executable bytes across strategies. Compiler and binary identities are in the catalog and raw bundles.", "",
             f"Pinned engine build: {engine['compiler_id']} {engine['compiler_version']}, {engine['build_type']}, source `{engine['source_revision']}`, dirty `{engine['source_dirty']}`. The supplied executable is recorded independently from the controller's source snapshot; it is not claimed to have been compiled by the evaluation wrapper.", "",
             f"Evaluation manifest SHA256: `{catalog['evaluation']['manifest_sha256']}`.", "",
             "Frequency, thermal state, cache state and background activity were uncontrolled. No local build, sanitizer suite or other benchmark ran concurrently with this measured cohort. Interactive host and lightweight report/tool activity were not isolated. Linux/macOS CI results are functional validation, not additional laboratory performance hosts.", "",
             "## Workloads, spaces and baselines", "",
             "| Scenario | Baseline adapter / implementation | Data settings | Arrivals | Space / candidate budget |", "| --- | --- | --- | --- | --- |"]
    for scenario in catalog["evaluation"]["plan"]["scenarios"]:
        inv = scenario["investigation"]
        app, workload = inv["experiment"]["application"], inv["experiment"]["workload"]
        settings = {key:value for key,value in app.items() if key not in ("adapter","implementation","workers","queue_capacity")}
        arrival = f"{workload['rate_per_second']}/s, {workload['measurement_ms']} ms, burst {workload['burst_size']}, heavy {workload['heavy_fraction']}"
        lines.append(f"| {scenario['name']} | {app['adapter']} / {app['implementation']} | `{json.dumps(settings,sort_keys=True)}` | {arrival} | {len(normalize_space(inv['experiment'],inv['search']))} points / {inv['budget']['max_candidates']} candidates |")
    lines += ["", "Every process has a separate declared warmup, then a drained measurement window. All strategies receive the same per-scenario input bytes, supported space, maximum runs/candidates/builds, resource limits and confirmation policy. Grid, random without replacement, local search and the hypothesis-disabled guided ablation are tuning baselines. Strategy order varies deterministically within scenario/seed blocks. The original low-load cases remain alongside saturated and database cases; they were not removed after a weak result.", "",
              "## Controller and statistics", "",
              "Exploration ranks candidates using temporal hypotheses, local rank moves, measured effects and cost, with periodic global exploration. Exploration never recommends. Exactly one finalist and eight fresh randomized-order confirmation pairs are frozen before confirmation; failed confirmation cannot switch to another finalist. Comparisons use paired run p99 values, a practical margin of 5%, 95% requested one-sided confidence and a minimum 2,000 completed requests per run. There is no pooling of requests as independent replications.", "",
              "The exact median-effect lower bound at eight pairs is the second-smallest effect (96.484375% conservative one-sided coverage). Every measured hard constraint and per-pair throughput tolerance must pass. Tail counts are an evidence guard, not a guarantee of precise p99 estimates. Nominal coverage assumes representative independent paired effects; that assumption cannot be established from host metadata. Repeated investigations and strategy comparisons have no family-wise false-positive guarantee.", "",
              "The measured cohort's original comparison schemas are retained. The current stricter schema additionally vetoes incomplete warmup coverage. The exporter recomputes every comparison under the current rule and refuses publication if decision, effect or uncertainty changes. This cohort's current-rule audit leaves all recorded outcomes unchanged.", "",
              "## Main results and repeated outcomes", "",
              "Cells show confirmed recommendations / planned studies, not exploratory wins. Mixed outcomes are explicit; these small counts do not establish a statistically superior strategy.", "",
              "| Scenario | Guided | Random | Grid | Local | Hypotheses disabled | Outcome variation |", "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for scenario in catalog["evaluation"]["plan"]["scenarios"]:
        selected = {row["strategy"]:row for row in groups if row["scenario"] == scenario["name"]}
        cells = [f"{selected[strategy]['confirmed_recommendations']}/{selected[strategy]['planned_studies']}" for strategy in ("guided","random","grid","local","unguided")]
        mixed = [strategy for strategy,row in selected.items() if len(row["decisions"]) > 1]
        lines.append("| " + " | ".join([scenario["name"],*cells, "mixed: " + ", ".join(mixed) if mixed else "consistent observed outcomes"]) + " |")
    lines += ["", f"Planned: **{totals['planned_studies']}**; complete wrappers: **{totals['complete_studies']}**; failed: **{totals['failed_studies']}**; interrupted: **{totals['interrupted_studies']}**; unexecuted: **{totals['not_run_studies']}**; confirmed recommendations: **{totals['confirmed_recommendations']}**.", "",
              "## Cost and efficiency", "",
              "| Strategy | Verified processes | Engine preparations | Total child seconds | Confirmed recommendations |", "| --- | ---: | ---: | ---: | ---: |"]
    for strategy in catalog["evaluation"]["plan"]["strategies"]:
        rows = [row for row in groups if row["strategy"] == strategy]
        lines.append(f"| {strategy} | {sum(row['runs'] for row in rows)} | {sum(row['builds'] for row in rows)} | {sum(row['wall_seconds'] for row in rows):.3f} | {sum(row['confirmed_recommendations'] for row in rows)} |")
    if catalog.get("baseline_evaluation"):
        previous = catalog["baseline_evaluation"]
        lines += ["", "## Preserved original policy and revised policy", "",
                  "The original five-scenario cohort is retained in full. It motivated a declared strong-gain exploration stop and priorities for registered alternatives under serialization. The new cohort preserves those five workloads and adds a 30-point search-space ablation with the old and new policies under the same budgets. Fresh fixed confirmation is unchanged. These are repeated observations on one host, not a randomized causal estimate of policy effects.", "",
                  "| Comparable five-scenario cohort | Strategy | Accepted / planned | Processes | Child seconds |",
                  "| --- | --- | ---: | ---: | ---: |"]
        old_names = {row["name"] for row in previous["plan"]["scenarios"]}
        for cohort, selected_groups in (("Original",previous["summary"]["groups"]),
                                        ("Revised",[row for row in groups if row["scenario"] in old_names])):
            for strategy in catalog["evaluation"]["plan"]["strategies"]:
                rows = [row for row in selected_groups if row["strategy"] == strategy]
                lines.append(f"| {cohort} | {strategy} | {sum(row['confirmed_recommendations'] for row in rows)}/{sum(row['planned_studies'] for row in rows)} | {sum(row['runs'] for row in rows)} | {sum(row['wall_seconds'] for row in rows):.3f} |")
    lines += ["", "Engine preparations in this cohort use `--binary`; they are verified materializations, not compiler invocations. Full wrapper seconds include analysis and final verification. The ablation saves its diagnostic process and that cost remains counted. Logical child file bytes count repeated paths; private hardlinks and portable chunk archives reduce physical storage. Conditional effects among accepted studies are selected outcomes, not unbiased average strategy effects.", "",
              "## Fresh-confirmation examples and resource tradeoffs", "",
              "| Scenario / seed / strategy | Candidate | Median paired p99 improvement | One-sided lower bound | Median baseline → candidate p99 (ms) | Median CPU cores | Median peak RSS (bytes) |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for scenario in catalog["evaluation"]["plan"]["scenarios"]:
        candidates = [row for row in summary["studies"] if row["scenario"] == scenario["name"] and row["strategy"] == "guided" and row["decision"] == "accepted_recommendation"]
        if not candidates:
            lines.append(f"| {scenario['name']} / guided | no confirmed recommendation | — | — | — | — | — |")
            continue
        row = min(candidates, key=lambda item:item["seed"])
        report = studies[row["path"]]["report"]
        comp = report["confirmation"]["comparison"]
        metrics = comp["metrics"]
        latency = metrics["p99"]
        cpu, rss = metrics["cpu_cores"], metrics["peak_rss_bytes"]
        lines.append(f"| {row['scenario']} / {row['seed']} / guided | `{json.dumps(report['recommendation'],sort_keys=True)}` | {pct(comp['effect']['median_fraction'])} | {pct(comp['uncertainty']['lower_bound'])} | {latency['baseline']['median']/1e6:.3f} → {latency['candidate']['median']/1e6:.3f} | {cpu['baseline']['median']:.3f} → {cpu['candidate']['median']:.3f} | {rss['baseline']['median']:.0f} → {rss['candidate']['median']:.0f} |")
    lines += ["", "Examples use the smallest accepted guided seed per scenario for inspection, not the largest effect. Every other seed and nonaccepted result remains in the catalog. Saturation can make a large end-to-end latency reduction correspond to a much smaller change in service capacity; inspect throughput and CPU rather than interpreting the percentage as a compute-speed multiplier.", "",
              "## Negative, invalid and inconclusive evidence", "",
              "The table retains low-load/noisy cases and unsuccessful confirmation. Per-trial statuses distinguish invalid data, resource violations, regression, practical equivalence and uncertainty. C++/Python adversarial integration tests additionally exercise wrong outputs, warmup loss, dropped traces, failed builds/executors, impossible resources, timeout, interruption, corrupt/resealed reports and unsafe archives. Synthetic controls are separate from measured application outcomes.", "",
              "## Calibration and diagnostic baselines", ""]
    for name, result in catalog["calibration"]["scenarios"].items():
        lines.append(f"- Synthetic {name}: {result['accepted']}/{result['trials']} accepted; Monte Carlo rate interval `{json.dumps(result['monte_carlo_rate_uncertainty'],sort_keys=True)}`.")
    selection = catalog["calibration"]["winner_selection"]
    lines += ["", f"Winner-selection control: fresh holdout accepted {selection['fresh_holdout']['accepted']}/{selection['fresh_holdout']['trials']}; deliberately unsafe reuse accepted {selection['unsafe_reused_exploration']['accepted']}/{selection['unsafe_reused_exploration']['trials']}. This is simulated selection bias, not a measured controller benchmark.", "",
              f"Hand-timed diagnostic controls: dependency analysis matched {catalog['diagnostic_controls']['matches']['dependency_match']}/6 known mechanisms; delay-only {catalog['diagnostic_controls']['matches']['delay_only_match']}/6; region-only {catalog['diagnostic_controls']['matches']['region_only_match']}/6. The controls include an unresolved owner and explicit trace-loss abstention. These are transparent weak baselines on six constructed fixtures, not a claim against production profilers.", "",
              "## Cold build, correctness and reproduction", ""]
    if catalog.get("verification_cost"):
        cost = catalog["verification_cost"]
        lines += ["The same immutable cold-study evidence was verified in three alternating cached/uncached pairs, after one warmup. Every path and raw response was still checked; only repeated identical source-archive decomposition was reused within each pass.", "",
                  f"Observed median verification time: {cost['median_wall_seconds']['uncached']:.3f} s without reuse and {cost['median_wall_seconds']['cached']:.3f} s with reuse ({cost['observed_speedup_ratio']:.2f}× descriptive ratio). The resulting reports and manifest identity matched in every run. This is tooling cost on one host, with no significance or universal speedup claim.", ""]
    if catalog.get("overhead"):
        lines += ["## Instrumentation overhead", "",
                  "Five alternating tracing-off/on pairs per listed configuration use identical inputs and executable bytes. Signed medians describe observations; they are not causal overhead estimates. If any pair loses trace events or changes coverage/delivery, aggregate deltas are withheld.", "",
                  "| Adapter / implementation / workers | Comparable pairs | Median p99 on-minus-off (ms) | Median CPU ns/request delta | Median RSS delta (bytes) | Reserved trace bytes | Dropped events |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for row in catalog["overhead"]:
            app, report = row["spec"]["application"], row["report"]
            pairs = report["pairs"]
            def median_delta(key, scale=1):
                value = (report.get("paired_deltas") or {}).get(key, {}).get("p50")
                return "withheld" if value is None else f"{value/scale:.3f}"
            lines.append(f"| {app['adapter']} / {app['implementation']} / {app['workers']} | {sum(pair['comparable'] for pair in pairs)}/{len(pairs)} | {median_delta('p99_latency_ns',1e6)} | {median_delta('cpu_ns_per_completed_request')} | {median_delta('peak_rss_bytes')} | {max(pair['trace_reserved_bytes'] for pair in pairs)} | {sum(pair['trace_events_dropped'] for pair in pairs)} |")
        lines += ["", "The catalog retains every signed pair, throughput change, trace volume, resource feasibility and host identity. Reserved buffers and process peak RSS are different measurements. These few configurations do not establish overhead scaling for arbitrary services.", ""]
    cold = catalog["cold_study"]["report"]
    lines += [f"The separate default-path SQLite study returned `{cold['decision']}`. It used {cold['cost']['runs']} processes and {cold['cost']['builds']} isolated compiler builds; recorded build/preparation time {cold['cost']['build_seconds']:.3f} s, orchestration through decision {cold['cost']['seconds']:.3f} s, timed application CPU {cold['cost'].get('timed_application_cpu_seconds',0):.3f} s, aggregate application setup {cold['cost'].get('application_setup_seconds',0):.3f} s. Index creation is outside request quantiles, inside investigation cost and peak RSS.", "",
              "All source/binary/input identities and per-response correctness checks are retained. Exact binary replay needs the recorded OS/architecture; other hosts can validate raw evidence and rebuild frozen sources for new timings. The fresh source-install and CLI/portable-evidence flow and sanitizer results are recorded in the release verification record.", "",
              "```sh", "python3 tools/jitter.py evaluate examples/evaluation.json --binary build/jitter --output results/new-evaluation",
              "python3 tools/jitter.py calibrate --trials 1000 --output results/new-calibration",
              "python3 tools/evaluate_diagnostics.py --output results/new-controls",
              "python3 tools/jitter.py unpack evaluation-evidence.tar.gz --output results/restored-evaluation",
              "python3 tools/jitter.py verify results/restored-evaluation",
              "python3 tools/jitter.py reproduce results/restored-evaluation --output results/new-replay", "```", "",
              "Reproduce a recommended configuration's exact single-process inputs by replaying its saved confirmation candidate sub-bundle. That functional replay alone is not a new statistical confirmation. Full investigation/evaluation replay collects new independent evidence and may choose differently.", "",
              "## Limitations and unsupported claims", "",
              "One uncontrolled host, three order/search seeds and related controlled workloads do not establish general performance, broad profiler accuracy or universal superiority over random/local/grid search. The JSON/SQLite service drivers are maintained here, not independent production deployments. No arbitrary source rewriting, per-request p99 confidence interval, proof of causality, global optimum, hard real-time CLI deadline or campaign-wide multiplicity guarantee is claimed. Weak/mixed outcomes remain evidence rather than being relabeled wins.", "",
              "[Machine-readable unfiltered catalog](evidence/evaluation-catalog.json) · [Method and workflow](phase5.md) · [Statistical contract](phase3.md)", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle","calibration","diagnostics","cold-study","output"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--baseline-bundle",type=Path)
    parser.add_argument("--verification-cost",type=Path)
    parser.add_argument("--overhead",type=Path,action="append",default=[])
    args = parser.parse_args()
    plan, summary, state = verify_evaluation(args.bundle)
    calibration = verify_calibration(args.calibration)
    verify_integrity(args.diagnostics, required=("diagnostic-evaluation.json",))
    verify_investigation(args.cold_study)
    studies = {row["path"]:compact_study(args.bundle / row["path"]) for row in summary["studies"] if row["status"] == "complete"}
    catalog = {"schema_version":1,"evidence_kind":"verified measured application reports, with separately labeled synthetic controls",
               "evaluation":{"manifest_sha256":sha256(args.bundle / "manifest.json"),"plan":plan,"summary":summary},
               "studies":studies,"calibration":calibration,"diagnostic_controls":load_json(args.diagnostics / "diagnostic-evaluation.json"),
               "cold_study":compact_study(args.cold_study)}
    if args.baseline_bundle:
        old_plan, old_summary, _ = verify_evaluation(args.baseline_bundle)
        catalog["baseline_evaluation"] = {"manifest_sha256":sha256(args.baseline_bundle / "manifest.json"),
            "plan":old_plan,"summary":old_summary,
            "studies":{row["path"]:compact_study(args.baseline_bundle / row["path"]) for row in old_summary["studies"] if row["status"] == "complete"}}
    if args.verification_cost:
        catalog["verification_cost"] = load_json(args.verification_cost)
        if catalog["verification_cost"]["manifest_sha256"] != sha256(args.cold_study / "manifest.json"):
            raise ExperimentError("verification benchmark did not use the supplied cold-study evidence")
    catalog["overhead"] = []
    for path in args.overhead:
        overhead_spec, overhead_report = verify_overhead(path)
        catalog["overhead"].append({"manifest_sha256":sha256(path / "manifest.json"),"spec":overhead_spec,"report":overhead_report})
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output / "evidence").mkdir(exist_ok=True)
    write_json(args.output / "evidence/evaluation-catalog.json", catalog)
    (args.output / "evaluation-report.md").write_text(report_text(catalog), encoding="utf-8")
    print(f"Generated unfiltered release evidence and report in {args.output}")


if __name__ == "__main__":
    main()
