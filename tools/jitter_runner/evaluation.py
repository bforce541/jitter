"""Retained, equal-budget controller/baseline studies over real investigations.

This wrapper never promotes an exploratory minimum into a recommendation.
Every accepted outcome comes from the child controller's fresh confirmation.
The small checked-in plan is an executable smoke evaluation, not an estimate
of general controller superiority or an independent production deployment.
"""

import copy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import statistics
import time

from .investigation import run_investigation, verify_investigation
from .investigation_spec import validate_investigation
from .runner import ROOT, new_output
from .spec import ExperimentError, integer, load_json, object_keys
from .storage import seal_bundle, verify_integrity, verify_source_archive, write_json
from .variants import freeze_source


STRATEGIES = ("guided", "random", "grid", "local", "unguided")
VERSION = "retained-equal-budget-v1"


def validate_evaluation(plan):
    object_keys(plan, ("schema_version", "seeds", "strategies", "scenarios"), "evaluation")
    integer(plan["schema_version"], 1, 1, "evaluation.schema_version")
    seeds, strategies, scenarios = plan["seeds"], plan["strategies"], plan["scenarios"]
    if not isinstance(seeds, list) or not 1 <= len(seeds) <= 20:
        raise ExperimentError("evaluation seeds must contain 1 to 20 integers")
    for seed in seeds:
        integer(seed, 0, (1 << 63) - 1, "evaluation.seed")
    if len(set(seeds)) != len(seeds):
        raise ExperimentError("evaluation seeds must be unique")
    if not isinstance(strategies, list) or not 1 <= len(strategies) <= len(STRATEGIES):
        raise ExperimentError("evaluation strategies must be a bounded nonempty list")
    if any(type(strategy) is not str or strategy not in STRATEGIES for strategy in strategies):
        raise ExperimentError("unsupported evaluation strategy")
    if len(set(strategies)) != len(strategies):
        raise ExperimentError("evaluation strategies must be unique")
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 32:
        raise ExperimentError("evaluation scenarios must contain 1 to 32 entries")
    names = set()
    for scenario in scenarios:
        object_keys(scenario, ("name", "investigation"), "evaluation.scenario")
        name = scenario["name"]
        if type(name) is not str or re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name) is None or name in names:
            raise ExperimentError("scenario names must be unique safe lowercase names")
        names.add(name)
        validate_investigation(scenario["investigation"])
        if not scenario["investigation"]["diagnostic"]:
            raise ExperimentError("evaluation scenarios must enable diagnostics; only unguided disables them")
    if len(seeds) * len(strategies) * len(scenarios) > 200:
        raise ExperimentError("evaluation exceeds 200 planned investigations")
    return plan


def child_spec(scenario, seed, strategy):
    """Only these controls differ; all timing, resource and evidence budgets stay fixed."""
    if strategy not in STRATEGIES:
        raise ExperimentError("unsupported evaluation strategy")
    spec = copy.deepcopy(scenario["investigation"])
    spec["search"]["strategy"] = "guided" if strategy == "unguided" else strategy
    spec["search"]["seed"] = seed
    spec["diagnostic"] = strategy != "unguided"
    return validate_investigation(spec)


def execution_order(plan):
    """Deterministic interleaving; no strategy always receives the first time slot."""
    result = []
    for scenario in plan["scenarios"]:
        for seed in plan["seeds"]:
            ordered = sorted(plan["strategies"], key=lambda strategy: hashlib.sha256(
                f"{scenario['name']}:{seed}:{strategy}".encode()).hexdigest())
            for strategy in ordered:
                result.append({"scenario": scenario["name"], "seed": seed, "strategy": strategy,
                               "path": f"studies/{scenario['name']}/seed-{seed}/{strategy}"})
    return result


def _bytes(path):
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def _exploratory_best(report):
    values = []
    for trial in report["trials"]:
        comparison = trial.get("comparison")
        if trial.get("failure") or not comparison or comparison["status"] not in ("promising", "exploratory"):
            continue
        value = comparison["effect"]["median_fraction"]
        if (type(value) in (int, float) and math.isfinite(value) and value > 0
                and comparison["pairs_valid"] == comparison["pairs_requested"]
                and comparison["tail_support"]["sufficient"]):
            values.append((value, trial))
    if not values:
        return None
    value, trial = max(values, key=lambda row: (row[0], row[1]["id"]))
    return {"configuration": trial["configuration"], "median_fraction": value,
            "comparison_status": trial["comparison"]["status"], "is_recommendation": False,
            "selection_bias": "Maximum observed exploratory effect; descriptive and optimistically selected."}


def make_evaluation_report(plan, state, children, sizes):
    """Pure summary over verified child reports and retained controller failures."""
    studies = []
    for record in state["records"]:
        row = {key: record[key] for key in ("scenario", "seed", "strategy", "path", "status")}
        row.update(wall_seconds=record["wall_seconds"], storage_bytes=sizes[record["path"]],
                   decision=None, stop_reason=None, recommendation=None,
                   confirmed_improvement_fraction=None, best_exploratory=None, cost=None)
        if record["status"] == "complete":
            report = children[record["path"]]
            accepted = report["decision"] == "accepted_recommendation"
            confirmation = report["confirmation"]
            row.update(decision=report["decision"], stop_reason=report["stop_reason"],
                       recommendation=report["recommendation"] if accepted else None,
                       confirmed_improvement_fraction=(confirmation["comparison"]["effect"]["median_fraction"]
                                                       if accepted else None),
                       best_exploratory=_exploratory_best(report), cost=report["cost"])
        else:
            row["failure"] = record.get("failure")
        studies.append(row)
    groups = []
    for scenario in plan["scenarios"]:
        for strategy in plan["strategies"]:
            rows = [row for row in studies if row["scenario"] == scenario["name"] and row["strategy"] == strategy]
            accepted = [row for row in rows if row["decision"] == "accepted_recommendation"]
            exploratory = [row["best_exploratory"]["median_fraction"] for row in rows if row["best_exploratory"]]
            decisions = {}
            for row in rows:
                decision = row["decision"] or row["status"]
                decisions[decision] = decisions.get(decision, 0) + 1
            groups.append({"scenario": scenario["name"], "strategy": strategy,
                           "planned_studies": len(rows), "decisions": decisions,
                           "confirmed_recommendations": len(accepted),
                           "observed_confirmation_effects": [row["confirmed_improvement_fraction"] for row in accepted],
                           "median_best_positive_exploratory_effect": statistics.median(exploratory) if exploratory else None,
                           "exploratory_positive_studies": len(exploratory),
                           "exploratory_is_recommendation": False,
                           "runs": sum(row["cost"]["runs"] for row in rows if row["cost"]),
                           "builds": sum(row["cost"]["builds"] for row in rows if row["cost"]),
                           "reported_child_failures": sum(row["cost"]["failures"] for row in rows if row["cost"]),
                           "cost_accounted_studies": sum(row["cost"] is not None for row in rows),
                           "cost_unknown_studies": sum(row["cost"] is None and row["status"] != "not_run" for row in rows),
                           "wall_seconds": sum(row["wall_seconds"] for row in rows),
                           "storage_bytes": sum(row["storage_bytes"] for row in rows)})
    return {"schema_version": 1, "evaluation_version": VERSION, "status": state["status"],
            "reproduce_from": state.get("reproduce_from"),
            "studies": studies, "groups": groups,
            "totals": {"planned_studies": len(studies),
                       "complete_studies": sum(row["status"] == "complete" for row in studies),
                       "failed_studies": sum(row["status"] == "failed" for row in studies),
                       "interrupted_studies": sum(row["status"] == "interrupted" for row in studies),
                       "not_run_studies": sum(row["status"] == "not_run" for row in studies),
                       "confirmed_recommendations": sum(row["decision"] == "accepted_recommendation" for row in studies),
                       "wall_seconds": sum(row["wall_seconds"] for row in studies),
                       "storage_bytes": sum(row["storage_bytes"] for row in studies)},
            "limitations": [
                "Every strategy has the same declared per-scenario budgets, sampling policy and search space; actual consumption differs and is reported.",
                "Unguided is guided search with its diagnostic process and hypothesis evidence disabled; saved diagnostic cost is included in the ablation.",
                "Only child fresh-confirmation decisions count as recommendations. Selected exploratory minima are descriptive and optimistically biased.",
                "Confirmation effects are reported only where confirmation accepted; their conditional distribution is not an unbiased average strategy effect.",
                "Failed, interrupted, inconclusive and unexecuted studies remain in the planned denominator; they are never silently dropped.",
                "Wrapper failures can have incomplete child cost journals; wall time and retained storage are reported, and unverified run counts remain unknown.",
                "Strategies run sequentially in reproducible interleaved order; thermal drift and host interference remain possible.",
                "One seed and a small scenario set are a smoke evaluation. General superiority requires more seeds, independent workloads and hosts.",
                "No multiple-strategy family-wise error guarantee is claimed; repeated accepted child results do not establish universal controller superiority."]}


def markdown_evaluation(report):
    lines = ["# Jitter evaluation", "", f"Status: **{report['status']}**", "",
             "Every row retains the same declared scenario budget. Confirmation outcomes and exploratory selections are separate.", "",
             "| Scenario | Strategy | Planned | Confirmed recommendations | Verified runs | Verified builds | Seconds | Bytes |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for group in report["groups"]:
        lines.append(f"| {group['scenario']} | {group['strategy']} | {group['planned_studies']} | {group['confirmed_recommendations']} | {group['runs']} | {group['builds']} | {group['wall_seconds']:.3f} | {group['storage_bytes']} |")
    lines += ["", "Totals: `" + str(report["totals"]) + "`", "", "## Limits", "",
              *["- " + limitation for limitation in report["limitations"]], ""]
    return "\n".join(lines)


def run_evaluation(plan, output=None, binary=None, reproduce_from=None):
    validate_evaluation(plan)
    original = Path(reproduce_from).resolve() if reproduce_from else None
    original_records = {}
    if original:
        original_plan, _, original_state = verify_evaluation(original)
        if original_plan != plan:
            raise ExperimentError("evaluation replay changed its original plan")
        if binary is not None:
            raise ExperimentError("evaluation replay must use saved child executables, not an override binary")
        original_records = {row["path"]: row for row in original_state["records"]}
    root = new_output(output)
    write_json(root / "evaluation-plan.json", plan)
    previous_source = ({"archive": original / "sources/source.tar.gz", "source": original_state["source"]}
                       if original and original_state.get("source") else None)
    frozen = freeze_source(ROOT, root / "sources", previous_source)
    state = {"schema_version": 1, "evaluation_version": VERSION, "status": "running",
             "created_at_utc": datetime.now(timezone.utc).isoformat(),
             "reproduce_from": str(original) if original else None,
             "source": frozen["source"],
             "records": [{**row, "status": "not_run", "wall_seconds": 0.0} for row in execution_order(plan)]}
    write_json(root / "evaluation-state.json", state)
    write_json(root / "manifest.json", {"schema_version": 1, "status": "running", "artifacts": {}})
    scenarios = {row["name"]: row for row in plan["scenarios"]}
    interrupted = False
    for record in state["records"]:
        start = time.monotonic()
        record["status"] = "running"
        write_json(root / "evaluation-state.json", state)
        print(f"Evaluating {record['scenario']} seed {record['seed']}: {record['strategy']}", flush=True)
        try:
            kwargs = {"output": root / record["path"], "binary": binary, "source_context": frozen}
            if original:
                if original_records[record["path"]]["status"] != "complete":
                    raise ExperimentError("original child has no verified saved executables; exact replay cannot silently rebuild it")
                kwargs["reproduce_from"] = original / record["path"]
            _, child_report = run_investigation(child_spec(scenarios[record["scenario"]], record["seed"], record["strategy"]), **kwargs)
            record["status"] = "complete"
            interrupted = child_report["stop_reason"] == "interrupted"
        except KeyboardInterrupt:
            record.update(status="interrupted", failure="KeyboardInterrupt: evaluation interrupted")
            interrupted = True
        except Exception as error:
            record.update(status="failed", failure=f"{type(error).__name__}: {error}")
        record["wall_seconds"] = time.monotonic() - start
        write_json(root / "evaluation-state.json", state)
        if interrupted:
            break
    state["status"] = "interrupted" if interrupted else "complete"
    write_json(root / "evaluation-state.json", state)
    children, sizes = _read_children(root, plan, state)
    report = make_evaluation_report(plan, state, children, sizes)
    write_json(root / "evaluation-report.json", report)
    (root / "evaluation-report.md").write_text(markdown_evaluation(report), encoding="utf-8")
    seal_bundle(root, "complete")
    verify_evaluation(root)
    return root, report


def _read_children(root, plan, state, archive_cache=None):
    if archive_cache is None:
        archive_cache = set()
    object_keys(state, ("schema_version", "evaluation_version", "status", "created_at_utc", "reproduce_from", "records") +
                (("source",) if "source" in state else ()), "evaluation state")
    if "source" in state:
        verify_source_archive(root / "sources/source.tar.gz", state["source"]["files"], verified=archive_cache)
        for name, expected in state["source"]["files"].items():
            from .storage import sha256
            if sha256(root / "sources/tree" / name) != expected:
                raise ExperimentError("evaluation frozen source tree differs from its archive")
    integer(state["schema_version"], 1, 1, "evaluation state.schema_version")
    if (state["evaluation_version"] != VERSION
            or state["status"] not in ("complete", "interrupted") or type(state["created_at_utc"]) is not str):
        raise ExperimentError("incompatible or unfinished evaluation journal")
    if state["reproduce_from"] is not None and type(state["reproduce_from"]) is not str:
        raise ExperimentError("evaluation replay identity must be a path or null")
    expected = execution_order(plan)
    if not isinstance(state["records"], list) or len(state["records"]) != len(expected):
        raise ExperimentError("evaluation journal changed its planned study count")
    scenarios = {row["name"]: row for row in plan["scenarios"]}
    children, sizes = {}, {}
    stopped = False
    for record, identity in zip(state["records"], expected):
        if not isinstance(record, dict) or any(record.get(key) != value for key, value in identity.items()):
            raise ExperimentError("evaluation study identity or execution order changed")
        status = record.get("status")
        required = (*identity, "status", "wall_seconds") + (("failure",) if status in ("failed", "interrupted") else ())
        object_keys(record, required, "evaluation study")
        if status not in ("complete", "failed", "interrupted", "not_run"):
            raise ExperimentError("evaluation contains an unfinished study")
        seconds = record["wall_seconds"]
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ExperimentError("evaluation wall time must be finite and nonnegative")
        if status in ("failed", "interrupted") and (type(record["failure"]) is not str or not record["failure"]):
            raise ExperimentError("failed evaluation studies must retain an error")
        if stopped and status != "not_run":
            raise ExperimentError("evaluation resumed after an interruption without a new plan")
        stopped = stopped or status in ("interrupted", "not_run")
        if status == "not_run" and (seconds != 0 or (root / identity["path"]).exists()):
            raise ExperimentError("unexecuted study unexpectedly contains measurements or cost")
        if state["status"] == "complete" and status in ("interrupted", "not_run"):
            raise ExperimentError("complete evaluation omitted planned studies")
        path = root / identity["path"]
        expected_spec = child_spec(scenarios[identity["scenario"]], identity["seed"], identity["strategy"])
        if (path / "investigation-spec.json").exists():
            if validate_investigation(load_json(path / "investigation-spec.json")) != expected_spec:
                raise ExperimentError("retained child changed its expected investigation specification")
        if status == "complete":
            spec, report, child_state = verify_investigation(path, archive_cache=archive_cache)
            if "source" in state and child_state.get("source") != state["source"]:
                raise ExperimentError("evaluation child changed its frozen source snapshot")
            if spec != expected_spec:
                raise ExperimentError("evaluation child changed its fixed workload, budget, policy or search controls")
            children[identity["path"]] = report
            stopped = stopped or report["stop_reason"] == "interrupted"
            if state["status"] == "complete" and stopped:
                raise ExperimentError("complete evaluation contains an interrupted child")
        elif status in ("failed", "interrupted") and (path / "manifest.json").exists():
            # A resealed parent cannot relabel a valid completed child as a
            # wrapper failure to remove an unfavorable (or favorable) outcome.
            try:
                verify_investigation(path)
            except (ExperimentError, OSError, KeyError, TypeError):
                pass
            else:
                raise ExperimentError("failed study label hides a valid completed investigation")
        sizes[identity["path"]] = _bytes(path)
    if state["status"] == "interrupted" and not stopped:
        raise ExperimentError("interrupted evaluation contains no interruption")
    return children, sizes


def verify_evaluation(path, archive_cache=None):
    root = Path(path).resolve()
    verify_integrity(root, required=("evaluation-plan.json", "evaluation-state.json", "evaluation-report.json", "evaluation-report.md"))
    plan = validate_evaluation(load_json(root / "evaluation-plan.json"))
    state = load_json(root / "evaluation-state.json")
    children, sizes = _read_children(root, plan, state, archive_cache=archive_cache)
    report = make_evaluation_report(plan, state, children, sizes)
    if (report != load_json(root / "evaluation-report.json")
            or markdown_evaluation(report) != (root / "evaluation-report.md").read_text(encoding="utf-8")):
        raise ExperimentError("evaluation report differs from retained child evidence and plan")
    return plan, report, state
