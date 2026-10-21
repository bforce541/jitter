"""Budgeted adaptive investigation with a single, fresh, frozen confirmation block."""

import copy
from datetime import datetime, timezone
import random
import re
import time
import math
import json
from pathlib import Path

from .investigation_spec import validate_investigation
from .runner import ROOT, host_info, new_output, run_experiment, verify_bundle
from .search import candidate_id, choose_candidate, configuration, normalize_space
from .spec import ExperimentError, load_json, validate_spec
from .statistics import compare_pairs
from .storage import seal_bundle, sha256, verify_integrity, verify_source_archive, write_json
from .variants import build_variant, freeze_source

CONTROLLER_VERSION = "adaptive-paired-v1"


class BudgetExhausted(Exception):
    pass


def experiment_for(spec, config, diagnostic=False):
    result = copy.deepcopy(spec["experiment"])
    result["application"].update(config)
    result["instrumentation"]["enabled"] = diagnostic
    return validate_spec(result)


def failure_kind(error):
    if isinstance(error, KeyboardInterrupt):
        return "interrupted"
    message = str(error).lower()
    if "incorrect response" in message or "accounting failed" in message or "application error" in message:
        return "correctness_failure"
    if "trace" in message or "diagnostic" in message:
        return "instrumentation_failure"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    return "environment_failure"


def pair_order(seed, index):
    order = ["baseline", "candidate"]
    random.Random(f"{seed}:{index}").shuffle(order)
    return order


def _history(trials):
    return [{"configuration": trial["configuration"], "status": trial["comparison"]["status"] if trial.get("comparison") and not trial.get("failure") else "invalid",
             "effect": trial["comparison"]["effect"]["median_fraction"] if trial.get("comparison") else None,
             "cost_seconds": trial.get("seconds", 0)} for trial in trials]


def select_finalist(trials):
    promising = [trial for trial in trials if not trial.get("failure") and (trial.get("comparison") or {}).get("status") == "promising"]
    return max(promising, key=lambda trial: (trial["comparison"]["effect"]["median_fraction"], trial["id"])) if promising else None


def _pairs_from_records(root, trial, records):
    result = []
    for pair in trial["pairs"]:
        if any(name is None or records[name]["status"] != "complete" for name in (pair["baseline"], pair["candidate"])):
            result.append({"baseline": {}, "candidate": {}})
            continue
        row = {}
        for side in ("baseline", "candidate"):
            _, summary, metadata = verify_bundle(root / pair[side])
            row[side] = summary["runs"][0]
            row[side + "_host"] = metadata["host"]
        result.append(row)
    return result


def _comparison(root, spec, trial, records, confirmation=False):
    if not trial["pairs"]:
        return None
    return compare_pairs(_pairs_from_records(root, trial, records), spec["comparison"], confirmation=confirmation)


def derive_decision(state):
    confirmation = state.get("confirmation")
    if confirmation and (confirmation.get("comparison") or {}).get("accepted") and state["stop_reason"] == "confirmed_improvement":
        return "accepted_recommendation"
    if state["stop_reason"] in ("baseline_failure", "diagnostic_failure", "internal_failure"):
        return "invalid_investigation"
    if state["stop_reason"] == "budget_exhausted":
        return "budget_exhausted"
    if state["stop_reason"] == "interrupted":
        return "interrupted"
    if confirmation or any((trial.get("comparison") or {}).get("status") == "inconclusive" for trial in state["trials"]):
        return "inconclusive"
    if any(trial.get("failure") for trial in state["trials"]):
        return "inconclusive"
    if state["trials"] and all((trial.get("comparison") or {}).get("status") in ("regression", "no_meaningful_difference") for trial in state["trials"]):
        return "no_improvement"
    # Two descriptive pairs cannot establish a population-level absence of benefit.
    return "inconclusive"


def make_report(spec, state):
    decision = derive_decision(state)
    accepted = decision == "accepted_recommendation"
    return {"schema_version": 1, "controller_version": CONTROLLER_VERSION, "decision": decision,
            "stop_reason": state["stop_reason"], "baseline": configuration(spec["experiment"]),
            "recommendation": state["confirmation"]["configuration"] if accepted else None,
            "best_exploratory_candidate": state["finalist"], "trials": state["trials"],
            "confirmation": state["confirmation"], "hypotheses": state["hypotheses"],
            "cost": state["cost"], "host": state["host"], "source": state["source"],
            "limitations": ["Only registered controls and implementation alternatives are searched; application behavior and inputs stay fixed.",
                            "One finalist is frozen before fresh, fixed-size confirmation. Failed confirmation never triggers another finalist in this investigation.",
                            "Tracing is a separate diagnostic process; final performance comparisons disable it.",
                            "Recorded host equality does not establish independent effects, quiet hardware, or absence of thermal drift.",
                            "No result generalizes beyond this workload, host, implementation contract, and observed resource envelope."]}


def markdown_report(report):
    stable = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=True)
    lines = ["# Jitter investigation", "", f"Decision: **{report['decision']}**", "",
             f"Stop reason: `{report['stop_reason']}`", "",
             f"Baseline: `{stable(report['baseline'])}`", "",
             f"Recommendation: `{stable(report['recommendation'])}`", "",
             "## Experiments", "", "| Candidate | Configuration | Outcome | Median paired improvement |", "| --- | --- | --- | ---: |"]
    for trial in report["trials"]:
        comparison = trial.get("comparison")
        center = comparison["effect"]["median_fraction"] if comparison else None
        effect = f"{100*center:.2f}%" if center is not None else "unavailable"
        lines.append(f"| {trial['id']} | `{stable(trial['configuration'])}` | {comparison['status'] if comparison else stable(trial.get('failure', 'unfinished'))} | {effect} |")
    confirmation = report["confirmation"]
    if confirmation and confirmation.get("comparison"):
        evidence = confirmation["comparison"]
        lines += ["", "## Fresh confirmation", "", f"Outcome: `{evidence['status']}` from {evidence['pairs_requested']} fresh pairs.", "",
                  f"Median paired improvement: `{evidence['effect']['median_fraction']}` (fraction).", "",
                  f"One-sided lower bound: `{evidence['uncertainty']['lower_bound']}` at confidence `{evidence['uncertainty']['confidence']}`.", "",
                  f"Tail support: `{stable(evidence['tail_support'])}`."]
        lines += ["", *[f"- {reason}" for reason in evidence["reasons"]]]
    lines += ["", "## Cost", "", f"`{stable(report['cost'])}`", "", "## Limits", "", *[f"- {item}" for item in report["limitations"]], "",
              "## Reproduction", "", "From the repository root, replace BUNDLE with this directory:", "",
              "```sh", "python3 tools/jitter.py verify BUNDLE", "python3 tools/jitter.py reproduce BUNDLE --output results/replay", "```", "",
              "Replay reruns the adaptive investigation with saved executables and inputs; timing, selections and conclusions may differ.", ""]
    return "\n".join(lines)


def run_investigation(spec, output=None, binary=None, reproduce_from=None):
    validate_investigation(spec)
    original = Path(reproduce_from).resolve() if reproduce_from else None
    if original:
        original_spec, _, original_state = verify_investigation(original)
        if original_spec != spec:
            raise ExperimentError("investigation replay changed its specification")
    root = new_output(output)
    start = time.monotonic()
    deadline = start + spec["budget"]["wall_seconds"]
    write_json(root / "investigation-spec.json", spec)
    state = {"schema_version": 1, "controller_version": CONTROLLER_VERSION, "status": "running",
             "created_at_utc": datetime.now(timezone.utc).isoformat(), "host": host_info(),
             "reproduce_from": str(original) if original else None, "source": None, "hypotheses": [],
             "diagnostic": None, "records": {}, "builds": {}, "trials": [], "finalist": None,
             "confirmation": None, "stop_reason": None,
             "cost": {"runs": 0, "builds": 0, "failures": 0, "seconds": 0, "build_seconds": 0}}
    write_json(root / "manifest.json", {"schema_version": 1, "status": "running", "artifacts": {}})
    (root / "measurements").mkdir()
    (root / "engines").mkdir()
    context = None

    def persist():
        state["cost"]["seconds"] = time.monotonic() - start
        write_json(root / "state.json", state)

    def remaining():
        return deadline - time.monotonic()

    def guard(runs=0, builds=0):
        limits, used = spec["budget"], state["cost"]
        if remaining() <= 0 or used["runs"] + runs > limits["max_runs"] or used["builds"] + builds > limits["max_builds"] or used["failures"] >= limits["max_failures"]:
            raise BudgetExhausted()

    def engine(config):
        name = config["implementation"]
        if name not in state["builds"]:
            guard(builds=1)
            state["cost"]["builds"] += 1
            state["builds"][name] = {"status": "running"}
            persist()
            before_build = time.monotonic()
            try:
                supplied = original / "engines" / name / "jitter" if original and name in original_state["builds"] else binary
                # A replay cannot silently compile an unarchived implementation.
                if original and not supplied:
                    raise ExperimentError("replay selected an implementation with no saved executable")
                result = build_variant(spec["experiment"]["application"]["adapter"], name, context,
                                       root / "engines" / name, timeout=max(.001, min(180, remaining())), supplied=supplied)
                state["builds"][name] = result
                state["cost"]["build_seconds"] += result["seconds"]
            except (Exception, KeyboardInterrupt) as error:
                state["builds"][name] = {"status": "failed", "error": str(error)}
                directory = root / "engines" / name
                directory.mkdir(exist_ok=True)
                if not (directory / "build.json").exists():
                    write_json(directory / "build.json", state["builds"][name])
                state["cost"]["build_seconds"] += time.monotonic() - before_build
                state["cost"]["failures"] += 1
                persist()
                raise
        if state["builds"][name]["status"] != "complete":
            raise ExperimentError("previous candidate build failed; logs are retained")
        return root / "engines" / name / "jitter"

    def measure(config, stage, diagnostic=False):
        executable = engine(config)
        guard(runs=1)
        state["cost"]["runs"] += 1
        name = f"measurements/run-{state['cost']['runs']:05d}"
        row = {"status": "running", "stage": stage, "configuration": config, "diagnostic": diagnostic}
        state["records"][name] = row
        persist()
        before = time.monotonic()
        try:
            path, summary = run_experiment(experiment_for(spec, config, diagnostic), root / name,
                                          binary=executable, source_context=context, deadline=deadline)
            row["status"] = "complete"
            return name, summary
        except (Exception, KeyboardInterrupt) as error:
            row.update(status="failed", failure=failure_kind(error), error=str(error))
            state["cost"]["failures"] += 1
            raise
        finally:
            row["seconds"] = time.monotonic() - before
            persist()

    def collect(trial, count, stage):
        for _ in range(count):
            guard(runs=2)
            index = sum(len(item["pairs"]) for item in state["trials"]) + (len(state["confirmation"]["pairs"]) if state["confirmation"] else 0)
            order = pair_order(spec["search"]["seed"], index)
            pair = {"baseline": None, "candidate": None, "order": order}
            trial["pairs"].append(pair)
            persist()
            for side in order:
                config = configuration(spec["experiment"]) if side == "baseline" else trial["configuration"]
                previous_runs = state["cost"]["runs"]
                try:
                    pair[side], _ = measure(config, stage)
                except BudgetExhausted:
                    raise
                except (Exception, KeyboardInterrupt):
                    if state["cost"]["runs"] > previous_runs:
                        pair[side] = f"measurements/run-{state['cost']['runs']:05d}"
                    if side == "baseline":
                        state["stop_reason"] = "baseline_failure"
                    raise
            trial["comparison"] = _comparison(root, spec, trial, state["records"], confirmation=stage == "confirmation")
            persist()

    try:
        previous = {"archive": original / "sources/source.tar.gz", "source": original_state["source"]} if original else None
        context = freeze_source(ROOT, root / "sources", previous)
        state["source"] = context["source"]
        persist()
        baseline = configuration(spec["experiment"])
        if spec["diagnostic"]:
            try:
                name, _ = measure(baseline, "diagnostic", diagnostic=True)
                state["diagnostic"] = name
                state["hypotheses"] = load_json(root / name / "run-001/diagnostics.json")["hypotheses"]
            except BudgetExhausted:
                raise
            except (Exception, KeyboardInterrupt):
                attempted = [name for name, record in state["records"].items() if record["stage"] == "diagnostic"]
                if attempted:
                    state["diagnostic"] = attempted[-1]
                state["stop_reason"] = "diagnostic_failure"
                raise
        space = normalize_space(spec["experiment"], spec["search"])
        for _ in range(spec["budget"]["max_candidates"]):
            guard()
            candidate, reason = choose_candidate(space, baseline, _history(state["trials"]), state["hypotheses"], spec["search"]["strategy"], spec["search"]["seed"])
            if candidate is None:
                break
            # Reserve a whole confirmatory holdout, rather than spending the last
            # run on another attractive but unconfirmable exploratory point.
            reserved = 2 * (spec["sampling"]["exploration_pairs"] + spec["sampling"]["confirmation_pairs"])
            if state["cost"]["runs"] + reserved > spec["budget"]["max_runs"]:
                state["stop_reason"] = "budget_exhausted"
                break
            trial = {"id": candidate_id(candidate), "configuration": candidate, "selection": reason,
                     "pairs": [], "comparison": None}
            state["trials"].append(trial)
            before = time.monotonic()
            print(f"Investigating {candidate}: {reason.get('mode', spec['search']['strategy'])}", flush=True)
            try:
                engine(candidate)
                collect(trial, spec["sampling"]["exploration_pairs"], "exploration")
                while len(trial["pairs"]) < spec["sampling"]["max_exploration_pairs"]:
                    comparison = trial["comparison"]
                    center = comparison["effect"]["median_fraction"]
                    if comparison["status"] not in ("promising", "exploratory") or center is None or center <= spec["comparison"]["min_effect"] / 2:
                        break
                    if state["cost"]["runs"] + 2 + 2 * spec["sampling"]["confirmation_pairs"] > spec["budget"]["max_runs"]:
                        break
                    collect(trial, 1, "exploration")
            except BudgetExhausted:
                raise
            except Exception as error:
                trial["failure"] = {"kind": failure_kind(error), "message": str(error)}
                trial["comparison"] = None
                if state["stop_reason"] == "baseline_failure":
                    raise
            finally:
                trial["seconds"] = time.monotonic() - before
                persist()
        finalist = select_finalist(state["trials"])
        if finalist:
            state["finalist"] = finalist["configuration"]
            state["confirmation"] = {"id": finalist["id"], "configuration": finalist["configuration"],
                                     "pairs_planned": spec["sampling"]["confirmation_pairs"], "pairs": [], "comparison": None}
            # Freeze the finalist and fixed confirmation size durably BEFORE any
            # holdout observations; no second finalist is allowed after failure.
            persist()
            duration = sum(spec["experiment"]["workload"][key] for key in ("warmup_ms", "measurement_ms")) / 1000
            if remaining() < 2 * spec["sampling"]["confirmation_pairs"] * duration:
                raise BudgetExhausted()
            collect(state["confirmation"], spec["sampling"]["confirmation_pairs"], "confirmation")
            accepted = state["confirmation"]["comparison"]["accepted"]
            state["stop_reason"] = "confirmed_improvement" if accepted else "confirmation_not_accepted"
        elif not state["stop_reason"]:
            state["stop_reason"] = "no_promising_candidates"
    except BudgetExhausted:
        state["stop_reason"] = "budget_exhausted"
    except KeyboardInterrupt:
        state["stop_reason"] = "interrupted"
    except Exception as error:
        state["error"] = {"kind": failure_kind(error), "message": str(error)}
        state["stop_reason"] = state["stop_reason"] or "internal_failure"
    state["status"] = "complete" if state["stop_reason"] != "interrupted" else "interrupted"
    for trial in state["trials"]:
        trial["comparison"] = _comparison(root, spec, trial, state["records"])
    if state["confirmation"]:
        state["confirmation"]["comparison"] = _comparison(root, spec, state["confirmation"], state["records"], confirmation=True)
    persist()
    report = make_report(spec, state)
    write_json(root / "report.json", report)
    (root / "report.md").write_text(markdown_report(report), encoding="utf-8")
    if report["recommendation"]:
        write_json(root / "recommendation.json", experiment_for(spec, report["recommendation"]))
    seal_bundle(root, "complete")
    verify_investigation(root)
    return root, report


def verify_investigation(path):
    root = Path(path).resolve()
    verify_integrity(root, required=("investigation-spec.json", "state.json", "report.json", "report.md"))
    spec = validate_investigation(load_json(root / "investigation-spec.json"))
    state = load_json(root / "state.json")
    if state.get("controller_version") != CONTROLLER_VERSION or state.get("status") not in ("complete", "interrupted"):
        raise ExperimentError("incompatible or unfinished controller journal")
    if state["source"]:
        verify_source_archive(root / "sources/source.tar.gz", state["source"]["files"])
        for name, checksum in state["source"]["files"].items():
            if sha256(root / "sources/tree" / name) != checksum:
                raise ExperimentError("frozen source tree differs from its archive")
    budget, cost = spec["budget"], state["cost"]
    if len(state["trials"]) > budget["max_candidates"]:
        raise ExperimentError("controller exceeded candidate budget")
    names = list(state["records"])
    if names != [f"measurements/run-{index:05d}" for index in range(1, len(names) + 1)]:
        raise ExperimentError("measurement journal paths must be sequential and confined to the bundle")
    if state["diagnostic"] is not None and (state["diagnostic"] not in state["records"] or state["diagnostic"] != names[0] or not spec["diagnostic"]):
        raise ExperimentError("invalid diagnostic record reference")
    for name, record in state["records"].items():
        if record.get("status") not in ("complete", "failed") or type(record.get("diagnostic")) is not bool:
            raise ExperimentError("unfinished or malformed measurement journal record")
        if record["diagnostic"] != (record["stage"] == "diagnostic"):
            raise ExperimentError("measurement mode differs from its stage")
        if record["stage"] not in ("diagnostic", "exploration", "confirmation"):
            raise ExperimentError("unsupported measurement stage")
        if record["diagnostic"] and (name != state["diagnostic"] or record["configuration"] != configuration(spec["experiment"])):
            raise ExperimentError("diagnostic did not measure the baseline")
    for key, value in cost.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ExperimentError("invalid cost measurement")
    failures = sum(record["status"] == "failed" for record in state["records"].values()) + sum(build["status"] == "failed" for build in state["builds"].values())
    if cost["failures"] != failures or failures > budget["max_failures"]:
        raise ExperimentError("failure budget differs from retained attempts")
    if cost["runs"] != len(state["records"]) or cost["builds"] != len(state["builds"]) or not 0 <= cost["runs"] <= budget["max_runs"] or not 0 <= cost["builds"] <= budget["max_builds"]:
        raise ExperimentError("investigation budget accounting differs from journal")
    for name, build in state["builds"].items():
        if name not in ("coarse", "narrow", "map", "ordered"):
            raise ExperimentError("unsafe implementation build path")
        saved = load_json(root / "engines" / name / "build.json")
        if build["status"] == "complete" and (build != saved or build["engine_sha256"] != sha256(root / "engines" / name / "jitter") or build["source_files"] != state["source"]["files"]):
            raise ExperimentError("candidate build provenance mismatch")
    baseline = configuration(spec["experiment"])
    space = normalize_space(spec["experiment"], spec["search"])
    history, referenced, pair_index, execution = [], set(), 0, []
    if state["diagnostic"]:
        referenced.add(state["diagnostic"])
        execution.append(state["diagnostic"])
        if state["records"][state["diagnostic"]]["status"] == "complete":
            report = load_json(root / state["diagnostic"] / "run-001/diagnostics.json")
            if state["hypotheses"] != report["hypotheses"]:
                raise ExperimentError("controller hypotheses differ from diagnostic evidence")
    elif state["hypotheses"]:
        raise ExperimentError("hypotheses lack diagnostic evidence")
    for trial in state["trials"]:
        expected, reason = choose_candidate(space, baseline, _history(history), state["hypotheses"], spec["search"]["strategy"], spec["search"]["seed"])
        if expected != trial["configuration"] or trial["id"] != candidate_id(expected) or trial["selection"] != reason:
            raise ExperimentError("controller selection differs from prior evidence")
        history.append(trial)
    finalist = select_finalist(state["trials"])
    if state["finalist"] is not None and (not finalist or state["finalist"] != finalist["configuration"]):
        raise ExperimentError("finalist was not selected from the exploratory evidence")
    trials = state["trials"] + ([state["confirmation"]] if state["confirmation"] else [])
    for trial in trials:
        confirm = trial is state["confirmation"]
        if len(trial["pairs"]) > (spec["sampling"]["confirmation_pairs"] if confirm else spec["sampling"]["max_exploration_pairs"]):
            raise ExperimentError("trial exceeded its fixed sample bound")
        for pair in trial["pairs"]:
            if pair["order"] != pair_order(spec["search"]["seed"], pair_index):
                raise ExperimentError("paired execution order differs from randomized plan")
            pair_index += 1
            for side in pair["order"]:
                name = pair[side]
                if name is None:
                    continue
                if name in referenced or name not in state["records"]:
                    raise ExperimentError("paired evidence was reused or not journaled")
                referenced.add(name)
                execution.append(name)
                expected_config = baseline if side == "baseline" else trial["configuration"]
                if state["records"][name]["configuration"] != expected_config or state["records"][name]["stage"] != ("confirmation" if confirm else "exploration"):
                    raise ExperimentError("paired trial configuration or stage mismatch")
        if trial.get("comparison") != _comparison(root, spec, trial, state["records"], confirmation=confirm):
            # Failed attempts may have no completed pair and no comparison.
            if trial.get("comparison") is not None or any(all(pair[side] for side in ("baseline", "candidate")) for pair in trial["pairs"]):
                raise ExperimentError("comparison differs from validated raw paired evidence")
        if confirm and (trial["configuration"] != state["finalist"] or trial["pairs_planned"] != spec["sampling"]["confirmation_pairs"]):
            raise ExperimentError("confirmation changed the frozen finalist or fixed sample size")
    if referenced != set(names) or execution != names:
        raise ExperimentError("measurement was omitted, reused, reordered, or detached from its trial")
    for index, (name, record) in enumerate(state["records"].items(), 1):
        if not re.fullmatch(r"measurements/run-[0-9]{5}", name):
            raise ExperimentError("unsafe measurement path in journal")
        if record["status"] == "complete":
            saved, _, metadata = verify_bundle(root / name)
            if saved != experiment_for(spec, record["configuration"], record["diagnostic"]) or metadata["source"] != state["source"]:
                raise ExperimentError("measurement changed fixed behavior, inputs or frozen source")
            build = state["builds"][record["configuration"]["implementation"]]
            if metadata["engine_sha256"] != build["engine_sha256"]:
                raise ExperimentError("measurement did not use its isolated pinned executable")
    if derive_decision(state) == "accepted_recommendation":
        confirmation = state["confirmation"]
        if len(confirmation["pairs"]) != spec["sampling"]["confirmation_pairs"] or any(not all(pair[side] for side in ("baseline", "candidate")) for pair in confirmation["pairs"]):
            raise ExperimentError("incomplete confirmation cannot authorize a recommendation")
        if load_json(root / "recommendation.json") != experiment_for(spec, confirmation["configuration"]):
            raise ExperimentError("recommendation differs from confirmed configuration")
    report = make_report(spec, state)
    if report != load_json(root / "report.json") or markdown_report(report) != (root / "report.md").read_text(encoding="utf-8"):
        raise ExperimentError("investigation report differs from journal and raw evidence")
    return spec, report, state
