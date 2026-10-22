"""Conservative, run-level paired comparisons for a frozen investigation policy.

Requests within a process are dependent: they are never treated as independent
replications. Exact binomial order statistics bound the median *paired run*
fractional improvement, without a normality or symmetric-effect assumption.
These are two separately one-sided bounds, not a joint confidence interval.

Method references (NIST's descriptions of sign tests and median order statistics):
https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/signtest.htm
https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mediancl.htm
"""

import math

from .metrics import distribution
from .spec import ExperimentError


METRICS = {"p50": (0.5, 2), "p95": (0.95, 20),
           "p99": (0.99, 100), "p999": (0.999, 1000)}
MIN_CONFIRMATION_PAIRS = 8
REFERENCES = [
    "https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/signtest.htm",
    "https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/mediancl.htm",
]


def _finite(value, minimum=0):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= minimum
    except OverflowError:
        return False


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else ordered[middle - 1] / 2 + ordered[middle] / 2


def _greater(value, threshold):
    # Avoid treating a quotient's rounding at the policy boundary as evidence
    # of a strict practical improvement (for example 1-9500/10000 versus .05).
    return value > threshold and not math.isclose(value, threshold, rel_tol=1e-12, abs_tol=1e-12)


def validate_policy(policy):
    """The caller may carry additional controller settings in the policy."""
    if not isinstance(policy, dict) or not isinstance(policy.get("metric"), str) or policy["metric"] not in METRICS:
        raise ExperimentError("comparison metric must be p50, p95, p99, or p999")
    for key, low, high in (("min_effect", 0, 1), ("confidence", 0.5, 1),
                           ("max_throughput_loss", 0, 1)):
        value = policy.get(key)
        if not _finite(value, low) or value >= high:
            raise ExperimentError(f"comparison {key} must be finite in [{low}, {high})")
    if type(policy.get("min_tail_observations")) is not int or not 1 <= policy["min_tail_observations"] <= 1000000:
        raise ExperimentError("comparison min_tail_observations must be an integer in [1, 1000000]")
    if not _finite(policy.get("max_baseline_spread"), 1):
        raise ExperimentError("comparison max_baseline_spread must be finite and at least 1")
    return policy


def median_bounds(values, confidence):
    """Exact conservative one-sided bounds; null denotes an unbounded end.

    For rank k, P(X_(k) <= population median) >= 1-P(Bin(n,.5)<k).
    Atoms/ties make coverage conservative. Pair effects must be independent and
    sampled from a common population; fresh processes alone do not ensure that.
    """
    if any(not _finite(value, -math.inf) for value in values):
        raise ExperimentError("paired effects must be finite numbers")
    if not _finite(confidence, 0.5) or confidence >= 1:
        raise ExperimentError("confidence must be finite in [0.5, 1)")
    ordered = sorted(values)
    count = len(ordered)
    rank, excluded = 0, 0
    denominator = 1 << count
    for k in range(1, (count + 1) // 2 + 1):
        excluded += math.comb(count, k - 1)
        if excluded / denominator <= 1 - confidence:
            rank = k
        else:
            break
    achieved = (1 - sum(math.comb(count, j) for j in range(rank)) / denominator) if rank else None
    return {"lower_bound": ordered[rank - 1] if rank else None,
            "upper_bound": ordered[count - rank] if rank else None,
            "order_statistic_rank": rank if rank else None,
            "confidence": confidence, "achieved_one_sided_confidence": achieved,
            "method": "exact_binomial_median_order_statistics",
            "bounds_are_separately_one_sided": True}


def _describe(values):
    result = distribution(values)
    result["median"] = _median(values) if values else None
    result["median_absolute_deviation"] = _median([abs(value - result["median"]) for value in values]) if values else None
    return result


def _sign_probability(values, margin):
    # Ties remain in n and count against a strict improvement; no favorable
    # deletion of ties or incomparable pairs is allowed.
    count, wins = len(values), sum(_greater(value, margin) for value in values)
    return sum(math.comb(count, j) for j in range(wins, count + 1)) / (1 << count)


def compare_pairs(pairs, policy, confirmation=False, version=2):
    """Compare verified run summaries; acceptance also requires caller protocol.

    The caller must freeze one finalist and the number of fresh confirmation
    pairs before observing confirmation outcomes, randomize/interleave order,
    retain failed attempts, and avoid testing another finalist on the same holdout.
    This function cannot establish freshness from aggregate run summaries.
    """
    validate_policy(policy)
    if type(version) is not int or version not in (1, 2):
        raise ExperimentError("unsupported paired comparison schema")
    if type(confirmation) is not bool:
        raise ExperimentError("confirmation must be boolean")
    if not isinstance(pairs, list) or len(pairs) > 1000:
        raise ExperimentError("comparison pairs must be a list of at most 1000 entries")
    metric = policy["metric"]
    quantile, tail_factor = METRICS[metric]
    required = max(40, policy["min_tail_observations"] * tail_factor)
    reasons = {key: [] for key in ("invalid", "constraint", "quality", "support")}
    measurements, evidence, effects, absolute = [], [], [], []
    host = None
    for index, pair in enumerate(pairs, 1):
        label = f"pair {index}"
        try:
            if not isinstance(pair, dict):
                raise ValueError("pair must be an object")
            left, right = pair["baseline"], pair["candidate"]
            if not isinstance(left, dict) or not isinstance(right, dict):
                raise ValueError("run summaries must be objects")
            before, after = left["phases"]["measurement"], right["phases"]["measurement"]
            if any(run.get("correctness") != "passed" for run in (left, right)):
                raise ValueError("correctness did not pass")
            if version >= 2:
                for run in (left, right):
                    warmup = run["phases"].get("warmup")
                    if warmup is not None and (not isinstance(warmup, dict) or
                            any(type(warmup.get(key)) is not int or warmup[key] < 0 for key in ("offered", "completed", "rejected", "errors")) or
                            warmup["rejected"] or warmup["errors"] or warmup["offered"] != warmup["completed"]):
                        raise ValueError("warmup must also have complete, rejection-free request coverage")
            for phase in (before, after):
                if not isinstance(phase, dict) or not isinstance(phase.get("latency_ns"), dict):
                    raise ValueError("measurement and latency summary must be objects")
                if any(type(phase[key]) is not int or phase[key] < 0 for key in ("offered", "completed", "rejected", "errors")):
                    raise ValueError("request counts are malformed")
                if phase["errors"] or phase["rejected"] or phase["offered"] != phase["completed"] or phase["completed"] == 0:
                    raise ValueError("complete error-free, rejection-free request coverage is required")
                if type(phase["latency_ns"]["count"]) is not int or phase["latency_ns"]["count"] != phase["completed"]:
                    raise ValueError("latency count differs from completion count")
                if not _finite(phase["latency_ns"].get(metric)) or phase["latency_ns"][metric] == 0:
                    raise ValueError("primary latency must be finite and positive")
                if not _finite(phase["throughput_per_second"]) or phase["throughput_per_second"] == 0:
                    raise ValueError("throughput must be finite and positive")
                if not _finite(phase["cpu_cores"]) or not _finite(phase["peak_rss_bytes"]):
                    raise ValueError("resource measurements must be finite and nonnegative")
            if before["offered"] != after["offered"]:
                raise ValueError("paired offered request counts differ")
            if any(run.get("feasible") is not True or not isinstance(run.get("constraints"), dict)
                   or not run["constraints"] or any(check is not True for check in run["constraints"].values())
                   for run in (left, right)):
                reasons["constraint"].append(f"{label}: baseline or candidate violated a hard constraint")
            for side, run in (("baseline", left), ("candidate", right)):
                observation = run.get("observation")
                if observation is not None and (not isinstance(observation, dict) or type(observation.get("enabled")) is not bool):
                    raise ValueError("malformed trace-quality metadata")
                if observation and observation["enabled"] and (observation.get("complete") is not True or observation.get("dropped") != 0):
                    reasons["quality"].append(f"{label}: {side} trace is incomplete")
            if ((left.get("observation") or {}).get("enabled", False)
                    != (right.get("observation") or {}).get("enabled", False)):
                reasons["quality"].append(f"{label}: instrumentation modes differ")
            for side in ("baseline", "candidate"):
                recorded = pair.get(side + "_host")
                if not isinstance(recorded, dict) or not recorded:
                    reasons["quality"].append(f"{label}: {side} host metadata is missing")
                elif host is None:
                    host = recorded
                elif recorded != host:
                    reasons["quality"].append(f"{label}: recorded host properties changed")
            throughput_loss = 1 - after["throughput_per_second"] / before["throughput_per_second"]
            if not math.isfinite(throughput_loss):
                raise ValueError("relative throughput loss overflowed")
            if _greater(throughput_loss, policy["max_throughput_loss"]):
                reasons["constraint"].append(f"{label}: relative throughput loss exceeded tolerance")
            insufficient = min(before["completed"], after["completed"]) < required
            if insufficient:
                reasons["support"].append(f"{label}: {metric} requires at least {required} completions per run")
            effect = 1 - after["latency_ns"][metric] / before["latency_ns"][metric]
            if not math.isfinite(effect):
                raise ValueError("paired fractional improvement overflowed")
            effects.append(effect)
            absolute.append(before["latency_ns"][metric] - after["latency_ns"][metric])
            measurements.append((before, after))
            evidence.append({"pair": index, "baseline_latency_ns": before["latency_ns"][metric],
                             "candidate_latency_ns": after["latency_ns"][metric],
                             "improvement_fraction": effect, "throughput_loss_fraction": throughput_loss,
                             "baseline_completed": before["completed"], "candidate_completed": after["completed"],
                             "tail_sufficient": not insufficient})
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            reasons["invalid"].append(f"{label}: {error}")
            evidence.append({"pair": index, "invalid": str(error)})

    if not pairs:
        reasons["support"].append("no paired measurements were collected")
    if confirmation and len(pairs) < MIN_CONFIRMATION_PAIRS:
        reasons["support"].append(f"confirmation requires at least {MIN_CONFIRMATION_PAIRS} fixed fresh pairs")
    baselines = [before["latency_ns"][metric] for before, _ in measurements]
    spread = max(baselines) / min(baselines) if baselines else None
    if spread is not None and not math.isfinite(spread):
        spread = None
        reasons["quality"].append("baseline max/min latency spread overflowed")
    elif spread is not None and _greater(spread, policy["max_baseline_spread"]):
        reasons["quality"].append("baseline max/min latency spread exceeded the environment-stability threshold")

    bounds = median_bounds(effects, policy["confidence"])
    bounds.update({"confirmatory": confirmation, "minimum_confirmation_pairs": MIN_CONFIRMATION_PAIRS,
                   "sign_test_p_at_margin": _sign_probability(effects, policy["min_effect"]) if effects else None,
                   "scope": "population median paired run-level fractional improvement; not a per-request tail confidence interval"})
    center = _median(effects) if effects else None
    lower, upper = bounds["lower_bound"], bounds["upper_bound"]
    if reasons["invalid"]:
        status = "invalid"
    elif reasons["constraint"]:
        status = "constraint_violation"
    elif reasons["quality"] or reasons["support"]:
        status = "inconclusive"
    elif confirmation and lower is not None and _greater(lower, policy["min_effect"]):
        status = "accepted"
    elif upper is not None and _greater(-upper, policy["min_effect"]):
        status = "regression"
    elif lower is not None and upper is not None and not _greater(-lower, policy["min_effect"]) and not _greater(upper, policy["min_effect"]):
        status = "no_meaningful_difference"
    elif confirmation:
        status = "inconclusive"
    elif center is not None and _greater(center, policy["min_effect"]):
        status = "promising"
    else:
        status = "exploratory"

    metrics = {}
    for name in (*METRICS, "throughput_per_second", "cpu_cores", "peak_rss_bytes"):
        values = [[], []]
        paired_changes = []
        for before, after in measurements:
            paired = []
            for position, phase in enumerate((before, after)):
                value = phase["latency_ns"].get(name) if name in METRICS else phase[name]
                if _finite(value):
                    values[position].append(value)
                paired.append(value)
            if all(_finite(value) for value in paired) and paired[0] > 0:
                change = paired[1] / paired[0] - 1
                if math.isfinite(change):
                    paired_changes.append(change)
        metrics[name] = {"baseline": _describe(values[0]), "candidate": _describe(values[1]),
                         "candidate_relative_change_fraction": _describe(paired_changes),
                         "descriptive_only": name != metric}
        if name in METRICS:
            threshold = max(40, policy["min_tail_observations"] * METRICS[name][1])
            metrics[name]["tail_support"] = {"required_completed_per_run": threshold,
                "sufficient": bool(measurements) and len(values[0]) == len(measurements) == len(values[1])
                and all(min(before["completed"], after["completed"]) >= threshold for before, after in measurements)}
    return {"schema_version": version, "status": status, "accepted": status == "accepted",
            "confirmation": confirmation, "pairs_requested": len(pairs), "pairs_valid": len(measurements),
            "pairs": evidence, "metrics": metrics,
            "effect": {"metric": metric, "positive_means": "candidate improvement",
                       "median_fraction": center, "median_absolute_ns": _median(absolute) if absolute else None,
                       "paired_fraction": effects, "distribution": _describe(effects),
                       "practical_minimum_fraction": policy["min_effect"]},
            "uncertainty": bounds,
            "tail_support": {"quantile": quantile, "minimum_tail_observations": policy["min_tail_observations"],
                             "required_completed_per_run": required,
                             "sufficient": bool(pairs) and all(pair.get("tail_sufficient", False) for pair in evidence)},
            "baseline_spread": spread, "reasons": [reason for group in reasons.values() for reason in group],
            "limitations": [
                "Exploration is descriptive and never authorizes a recommendation.",
                "Confirmation requires one frozen finalist, a fixed fresh sample, and no optional stopping or repeated holdout testing.",
                "Exact coverage assumes independent representative paired effects; process restarts and recorded host equality do not prove independence or a quiet host.",
                "The tail-count threshold is a minimum evidence guard, not a guarantee that a tail percentile is precise.",
                "Every observed hard constraint and relative throughput check must pass; this does not establish a future resource guarantee.",
            ], "method_references": REFERENCES}
