"""Reproducible synthetic decision calibration, never application benchmarks.

All measurements here are generated run-summary numbers. No application is
executed, no raw tail is sampled, and no controller speedup is established.
The winner's-curse control deliberately reuses selected exploratory evidence to
illustrate an unsafe baseline that production investigations must never use.
"""

from collections import Counter
import copy
import hashlib
import math
from pathlib import Path
import random

from .spec import ExperimentError, integer, load_json
from .statistics import compare_pairs, median_bounds
from .storage import seal_bundle, verify_integrity, write_json


GENERATOR_VERSION = "synthetic-paired-effects-v1"
POLICY = {"metric": "p99", "min_effect": .05, "confidence": .95,
          "max_throughput_loss": .05, "min_tail_observations": 20,
          "max_baseline_spread": 3.0}
PARAMETERS = {"pairs": 8, "selection_candidates": 16, "completed_per_run": 2000,
              "baseline_p99_ns": 1000000, "baseline_throughput_per_second": 1000,
              "shared_host_log_sd": .08, "null_independent_log_sd": .12,
              "control_independent_log_sd": .02, "true_improvement_fraction": .30,
              "true_regression_fraction": -.20, "boundary_null_effects": [-.05, .15],
              "wilson_z": 1.959963984540054}
SCENARIOS = ("boundary_null", "noisy_null", "winner_curse_null", "true_improvement",
             "regression", "no_meaningful_difference", "constraint_violation",
             "insufficient_tail", "host_drift")


def _inputs(seed, trials):
    integer(seed, 0, (1 << 64) - 1, "calibration.seed")
    integer(trials, 1, 10000, "calibration.trials")


def _stream(seed, scenario, trial, phase):
    # Stream identity isolates holdout draws from exploratory effort and order.
    encoded = f"{GENERATOR_VERSION}:{seed}:{scenario}:{trial}:{phase}".encode("ascii")
    return random.Random(int.from_bytes(hashlib.sha256(encoded).digest(), "big"))


def _normal(rng):
    # Explicit Box-Muller transform avoids hidden gaussian-cache state.
    radius = math.sqrt(-2 * math.log(1 - rng.random()))
    return radius * math.cos(2 * math.pi * rng.random())


def _run(latency, completed):
    return {"correctness": "passed", "feasible": True,
            "constraints": {"throughput": True, "cpu": True, "memory": True,
                            "rejections": True, "scheduler_lag": True, "latency_observed": True},
            "phases": {"measurement": {"offered": completed, "completed": completed,
                "rejected": 0, "errors": 0, "latency_ns": {"count": completed,
                    "p50": latency / 2, "p95": latency * .9, "p99": latency, "p999": latency * 1.1},
                "throughput_per_second": PARAMETERS["baseline_throughput_per_second"],
                "cpu_cores": 1.0, "peak_rss_bytes": 1000000}}}


def _pair(baseline, candidate, completed=2000):
    return {"baseline": _run(baseline, completed), "candidate": _run(candidate, completed),
            "baseline_host": {"system": "synthetic", "machine": "calibration"},
            "candidate_host": {"system": "synthetic", "machine": "calibration"}}


def _pairs(seed, scenario, trial, phase, effect=0, noise=.12, completed=2000):
    rng = _stream(seed, scenario, trial, phase)
    rows = []
    for _ in range(PARAMETERS["pairs"]):
        shared = PARAMETERS["baseline_p99_ns"] * math.exp(PARAMETERS["shared_host_log_sd"] * _normal(rng))
        before = shared * math.exp(noise * _normal(rng))
        after = shared * (1 - effect) * math.exp(noise * _normal(rng))
        rows.append(_pair(before, after, completed))
    return rows


def _wilson(successes, trials):
    probability = successes / trials
    z = PARAMETERS["wilson_z"]
    denominator = 1 + z * z / trials
    center = (probability + z * z / (2 * trials)) / denominator
    radius = z * math.sqrt(probability * (1 - probability) / trials + z * z / (4 * trials * trials)) / denominator
    return {"confidence": .95, "method": "Wilson binomial Monte Carlo rate interval",
            "lower": round(max(0, center - radius), 12), "upper": round(min(1, center + radius), 12),
            "scope": "simulator acceptance probability; not an application improvement interval or formal calibration guarantee"}


def _rate(outcomes, trials):
    accepted = outcomes.get("accepted", 0)
    return {"trials": trials, "accepted": accepted, "acceptance_rate": accepted / trials,
            "outcomes": dict(sorted(outcomes.items())), "monte_carlo_rate_uncertainty": _wilson(accepted, trials)}


def calibration_report(seed=42, trials=1000):
    """Pure deterministic report; changing streams or policy changes its version."""
    _inputs(seed, trials)
    outcomes = {scenario: Counter() for scenario in SCENARIOS}
    reused, selected = Counter(), 0
    for trial in range(trials):
        rng = _stream(seed, "boundary_null", trial, "confirmation")
        boundary = [_pair(PARAMETERS["baseline_p99_ns"], PARAMETERS["baseline_p99_ns"] *
                          (1 - PARAMETERS["boundary_null_effects"][rng.randrange(2)]))
                    for _ in range(PARAMETERS["pairs"])]
        outcomes["boundary_null"][compare_pairs(boundary, POLICY, confirmation=True)["status"]] += 1
        normal = _pairs(seed, "noisy_null", trial, "confirmation", noise=PARAMETERS["null_independent_log_sd"])
        outcomes["noisy_null"][compare_pairs(normal, POLICY, confirmation=True)["status"]] += 1

        best = None
        for candidate in range(PARAMETERS["selection_candidates"]):
            exploration = _pairs(seed, "winner_curse_null", trial, f"exploration-{candidate}",
                                 noise=PARAMETERS["null_independent_log_sd"])
            comparison = compare_pairs(exploration, POLICY)
            if comparison["status"] == "promising":
                score = (comparison["effect"]["median_fraction"], candidate)
                if best is None or score > best[0]:
                    best = (score, exploration)
        if best is None:
            outcomes["winner_curse_null"]["no_finalist"] += 1
            reused["no_finalist"] += 1
        else:
            selected += 1
            # Identity is frozen before looking at this independent stream.
            holdout = _pairs(seed, "winner_curse_null", trial, "confirmation",
                             noise=PARAMETERS["null_independent_log_sd"])
            outcomes["winner_curse_null"][compare_pairs(holdout, POLICY, confirmation=True)["status"]] += 1
            reused[compare_pairs(best[1], POLICY, confirmation=True)["status"]] += 1

        for scenario in SCENARIOS[3:]:
            effect = (PARAMETERS["true_regression_fraction"] if scenario == "regression" else
                      0 if scenario == "no_meaningful_difference" else PARAMETERS["true_improvement_fraction"])
            rows = _pairs(seed, scenario, trial, "confirmation", effect=effect,
                          noise=0 if scenario == "no_meaningful_difference" else PARAMETERS["control_independent_log_sd"],
                          completed=PARAMETERS["completed_per_run"] - (scenario == "insufficient_tail"))
            if scenario == "constraint_violation":
                rows[-1]["candidate"]["feasible"] = False
                rows[-1]["candidate"]["constraints"]["memory"] = False
            elif scenario == "host_drift":
                rows[-1]["candidate_host"]["machine"] = "changed"
            outcomes[scenario][compare_pairs(rows, POLICY, confirmation=True)["status"]] += 1

    bounds = median_bounds(list(range(PARAMETERS["pairs"])), POLICY["confidence"])
    cutoff = PARAMETERS["pairs"] - bounds["order_statistic_rank"] + 1
    numerator = sum(math.comb(PARAMETERS["pairs"], count) for count in range(cutoff, PARAMETERS["pairs"] + 1))
    denominator = 1 << PARAMETERS["pairs"]
    return {"schema_version": 1, "generator_version": GENERATOR_VERSION, "inputs": {"seed": seed, "trials": trials},
            "evidence_kind": "synthetic run-summary decision calibration; no application or controller benchmark",
            "policy": dict(POLICY), "generator_parameters": copy.deepcopy(PARAMETERS),
            "stream_derivation": "SHA256(version:seed:scenario:trial:phase), Python Random uniforms, explicit Box-Muller normal",
            "analytical_boundary_null": {"pairs": PARAMETERS["pairs"], "one_sided_order_rank": bounds["order_statistic_rank"],
                "required_strict_margin_wins": cutoff, "accepted_sign_patterns": numerator,
                "equiprobable_sign_patterns": denominator, "false_acceptance_probability": numerator / denominator,
                "scope": "independent equiprobable paired effects around the practical margin; a specific exact null case"},
            "scenarios": {scenario: _rate(outcomes[scenario], trials) for scenario in SCENARIOS},
            "winner_selection": {"candidates_per_trial": PARAMETERS["selection_candidates"], "selected": selected,
                "fresh_holdout": _rate(outcomes["winner_curse_null"], trials),
                "unsafe_reused_exploration": _rate(reused, trials),
                "unsafe_baseline_is_production_behavior": False},
            "limitations": [
                "These are synthetic aggregate summaries, not measured request streams, empirical p99 precision, or real controller performance.",
                "The simulator assumes independent streams and representative paired effects; it does not establish those assumptions on a real host.",
                "The sixteen-candidate selection case uses eight exploratory pairs to expose selection bias; production sampling and search can differ.",
                "The deliberately reused exploratory baseline is statistically unsafe and exists only as a negative control.",
                "Monte Carlo uncertainty describes simulator rates; a finite seed is not proof of universal false-positive control.",
                "No tail percentile is estimated from raw requests here; the supplied count exercises the production sufficiency gate only.",
            ], "rate_interval_reference": "https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm"}


def _markdown(report):
    lines = ["# Synthetic decision calibration", "", report["evidence_kind"], "",
             f"Seed: {report['inputs']['seed']}; trials per scenario: {report['inputs']['trials']}.", "",
             "| Scenario | Accepted | Trials | Acceptance rate |", "| --- | ---: | ---: | ---: |"]
    for scenario, result in report["scenarios"].items():
        lines.append(f"| {scenario} | {result['accepted']} | {result['trials']} | {result['acceptance_rate']:.4f} |")
    reused = report["winner_selection"]["unsafe_reused_exploration"]
    lines += ["", f"Unsafe reused exploratory evidence accepted {reused['accepted']}/{reused['trials']} cases.", "",
              "The exact practical-boundary null accepts 9/256 equiprobable sign patterns at eight pairs and 95% one-sided confidence.", "",
              *[f"- {limitation}" for limitation in report["limitations"]], ""]
    return "\n".join(lines)


def run_calibration(output, seed=42, trials=1000):
    _inputs(seed, trials)
    path = Path(output).absolute()
    path.mkdir(parents=True, exist_ok=False)
    report = calibration_report(seed, trials)
    write_json(path / "calibration.json", report)
    (path / "report.md").write_text(_markdown(report), encoding="utf-8")
    seal_bundle(path, "complete")
    verify_calibration(path)
    return path.resolve(), report


def verify_calibration(path):
    path = Path(path).resolve()
    verify_integrity(path, required=("calibration.json", "report.md"))
    saved = load_json(path / "calibration.json")
    try:
        expected = calibration_report(saved["inputs"]["seed"], saved["inputs"]["trials"])
    except (KeyError, TypeError) as error:
        raise ExperimentError("malformed calibration report inputs") from error
    if saved != expected or (path / "report.md").read_text(encoding="utf-8") != _markdown(expected):
        raise ExperimentError("calibration report differs from its deterministic synthetic evidence")
    return saved
