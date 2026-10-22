"""Bounded configuration search with reproducible, evidence-adaptive choices.

The declared Cartesian product is limited to 4096 points before exclusions;
the returned space, including the exact baseline, has the same limit. Search
scores order experiments. They are neither confidence estimates nor claims
that a selected configuration improves performance.
"""

import hashlib
import itertools
import json
import math

from .spec import ExperimentError, integer, object_keys

KEYS = ("workers", "queue_capacity", "implementation")
STRATEGIES = ("guided", "random", "grid", "local")
MAX_CONFIGURATIONS = 4096
IMPLEMENTATIONS = {"shared-index-v1": ("coarse", "narrow"),
                   "json-projection-v1": ("map", "ordered"), "sqlite-aggregate-v1": ("scan", "indexed")}
FAILED_STATUSES = {"failed", "infeasible", "invalid", "rejected", "error",
                   "interrupted", "timeout", "incomparable", "constraint_violation",
                   "inconclusive"}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def candidate_id(config):
    """Stable content identity; the full configuration remains authoritative."""
    return hashlib.sha256(_canonical(config).encode("utf-8")).hexdigest()[:12]


def configuration(experiment):
    app = experiment["application"]
    allowed = IMPLEMENTATIONS.get(app["adapter"])
    if allowed is None:
        raise ExperimentError("search does not support this application adapter")
    return {"workers": app["workers"], "queue_capacity": app["queue_capacity"],
            "implementation": app.get("implementation", allowed[0])}


def _value(key, value, implementations):
    if key == "implementation":
        if type(value) is not str or value not in implementations:
            raise ExperimentError(f"search implementation must be one of {', '.join(implementations)}")
    else:
        integer(value, 1, 256 if key == "workers" else 1000000, f"search.{key}")


def _values(key, value, implementations):
    if isinstance(value, dict):
        if key == "implementation":
            raise ExperimentError("implementation search values must be a list")
        object_keys(value, ("min", "max", "step"), f"search.parameters.{key}")
        _value(key, value["min"], implementations)
        _value(key, value["max"], implementations)
        integer(value["step"], 1, 1000000, f"search.parameters.{key}.step")
        if value["max"] < value["min"]:
            raise ExperimentError("search range maximum is below its minimum")
        count = (value["max"] - value["min"]) // value["step"] + 1
        if count > MAX_CONFIGURATIONS:
            raise ExperimentError("raw search space exceeds 4096 configurations")
        return list(range(value["min"], value["max"] + 1, value["step"]))
    if not isinstance(value, list) or not value or len(value) > MAX_CONFIGURATIONS:
        raise ExperimentError("search parameter values must be a nonempty bounded list or integer range")
    for entry in value:
        _value(key, entry, implementations)
    if len(set(value)) != len(value):
        raise ExperimentError("duplicate search parameter values")
    return sorted(value)


def _key(config):
    return tuple(config[key] for key in KEYS)


def normalize_space(experiment, search):
    object_keys(search, ("strategy", "seed", "parameters", "exclude"), "search")
    if search["strategy"] not in STRATEGIES:
        raise ExperimentError(f"search.strategy must be one of {', '.join(STRATEGIES)}")
    integer(search["seed"], 0, (1 << 63) - 1, "search.seed")
    parameters = search["parameters"]
    if not isinstance(parameters, dict) or not set(parameters).issubset(KEYS):
        raise ExperimentError("search parameters may only contain workers, queue_capacity, implementation")
    baseline = configuration(experiment)
    implementations = IMPLEMENTATIONS[experiment["application"]["adapter"]]
    for key in KEYS:
        _value(key, baseline[key], implementations)
    dimensions = {key: _values(key, parameters.get(key, [baseline[key]]), implementations) for key in KEYS}
    product_size = math.prod(len(values) for values in dimensions.values())
    if product_size > MAX_CONFIGURATIONS:
        raise ExperimentError("raw search space exceeds 4096 configurations before exclusions")
    exclusions = search["exclude"]
    if not isinstance(exclusions, list) or len(exclusions) > MAX_CONFIGURATIONS:
        raise ExperimentError("search.exclude must be a bounded list of partial configurations")
    for excluded in exclusions:
        if not isinstance(excluded, dict) or not excluded or not set(excluded).issubset(KEYS):
            raise ExperimentError("search exclusions must be nonempty partial configurations")
        for key, value in excluded.items():
            _value(key, value, implementations)
            if value not in dimensions[key] and value != baseline[key]:
                raise ExperimentError("excluded values must occur in the search space or baseline")
        if all(baseline[key] == value for key, value in excluded.items()):
            raise ExperimentError("search exclusions must not exclude the baseline")
    configs = [dict(zip(KEYS, values)) for values in itertools.product(*(dimensions[key] for key in KEYS))]
    configs = [config for config in configs if not any(all(config[key] == value for key, value in excluded.items())
                                                    for excluded in exclusions)]
    if baseline not in configs:
        configs.append(baseline)
    if len(configs) > MAX_CONFIGURATIONS:
        raise ExperimentError("search space including baseline exceeds 4096 configurations")
    return sorted(configs, key=_key)


def _seed_key(seed, config, step=0):
    return hashlib.sha256(f"{seed}:{step}:{_canonical(config)}".encode("utf-8")).hexdigest()


def _support(hypotheses, model_version=1):
    result = {}
    for hypothesis in hypotheses:
        if isinstance(hypothesis, str):
            name, weight = hypothesis, 1.0
        elif isinstance(hypothesis, dict):
            name = hypothesis.get("id")
            weight = hypothesis.get("tail_component_fraction", 1.0)
        else:
            raise ExperimentError("hypotheses must contain identifiers or evidence objects")
        if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ExperimentError("hypothesis support must be a finite fraction in [0, 1]")
        if name in ("shared_lock_contention", "queue_pressure"):
            result[name] = max(result.get(name, 0), weight)
        if model_version >= 2 and name == "digest_execution":
            result[name] = max(result.get(name, 0), weight)
        if name == "sql_execution" and isinstance(hypothesis, dict):
            suggestion = hypothesis.get("suggested_experiment", {})
            if suggestion.get("parameter") == "application.implementation" and "indexed" in suggestion.get("values", []):
                result["variant:indexed"] = max(result.get("variant:indexed", 0), weight)
    return result


def choose_candidate(space, baseline, history, hypotheses, strategy, seed, model_version=1):
    """Choose one untried point; positive effects mean fractional improvement.

    Every history entry, including failed or interrupted entries, consumes its
    configuration. Callers must explicitly start a new study to retest it.
    """
    if strategy not in STRATEGIES:
        raise ExperimentError("unsupported search strategy")
    integer(model_version, 1, 2, "search model version")
    integer(seed, 0, (1 << 63) - 1, "search.seed")
    if not isinstance(space, list) or not 1 <= len(space) <= MAX_CONFIGURATIONS:
        raise ExperimentError("search space must contain 1 to 4096 configurations")
    if not isinstance(history, list) or len(history) > MAX_CONFIGURATIONS:
        raise ExperimentError("search history must be a bounded list")
    known = {_canonical(config) for config in space}
    known.add(_canonical(baseline))
    if len({_canonical(config) for config in space}) != len(space):
        raise ExperimentError("search space contains duplicate configurations")
    observations = {_canonical(baseline): {"configuration": baseline, "effect": 0.0, "cost_seconds": 0.0}}
    seen = {_canonical(baseline)}
    for entry in history:
        if not isinstance(entry, dict) or not {"configuration", "status", "effect", "cost_seconds"}.issubset(entry):
            raise ExperimentError("history entries require configuration, status, effect, cost_seconds")
        identity = _canonical(entry["configuration"])
        if identity not in known or not isinstance(entry["status"], str) or not entry["status"]:
            raise ExperimentError("history contains an unknown configuration or invalid status")
        effect, cost = entry["effect"], entry["cost_seconds"]
        if effect is not None and (type(effect) not in (int, float) or not math.isfinite(effect)):
            raise ExperimentError("observed search effects must be finite numbers or null")
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise ExperimentError("observed search costs must be finite nonnegative numbers")
        seen.add(identity)
        if entry["status"] not in FAILED_STATUSES and effect is not None:
            observations[identity] = entry
    remaining = sorted((config for config in space if _canonical(config) not in seen), key=_key)
    incumbent_record = min(observations.values(), key=lambda entry: (-entry["effect"],
                           entry["configuration"] != baseline, _key(entry["configuration"])))
    incumbent = incumbent_record["configuration"]
    reason = {"strategy": strategy, "mode": "exhausted", "incumbent": dict(incumbent), "score": None,
              "basis": "Every supported configuration has already been tried or is the baseline.",
              "hypothesis_support": {}, "observed_marginals": [], "estimated_cost_seconds": None}
    if not remaining:
        return None, reason
    step = len(seen - {_canonical(baseline)}) + 1
    if strategy == "grid":
        reason.update(mode="grid", basis="Canonical configuration order among untried points.")
        return dict(remaining[0]), reason
    if strategy == "random" or (strategy == "guided" and step % 3 == 0):
        selected = min(remaining, key=lambda config: _seed_key(seed, config, 0 if strategy == "random" else step))
        reason.update(mode="exploration", basis="Seeded exploration among every remaining supported configuration.")
        return dict(selected), reason
    ranks = {key: {value: index for index, value in enumerate(sorted({config[key] for config in space} | {baseline[key]}))}
             for key in KEYS}

    def distance(left, right):
        return sum((left[key] != right[key]) if key == "implementation" else
                   abs(ranks[key][left[key]] - ranks[key][right[key]]) for key in KEYS)

    neighbors = [config for config in remaining if distance(config, incumbent) == 1]
    if strategy == "local":
        if not neighbors:
            reason.update(mode="local_exhausted", basis="All adjacent rank neighbors of the promising incumbent have been tried.")
            return None, reason
        remaining = neighbors
    support = _support(hypotheses, model_version) if strategy == "guided" else {}
    learned = {}
    # Compare observations that differ in exactly one parameter. Adjacent
    # observations within a context prevent quadratic work as the study grows.
    for key in KEYS:
        contexts = {}
        for entry in observations.values():
            config = entry["configuration"]
            context = tuple(config[other] for other in KEYS if other != key)
            contexts.setdefault(context, []).append(entry)
        slopes = []
        for group in contexts.values():
            ordered = sorted(group, key=lambda entry: ranks[key][entry["configuration"][key]])
            for left, right in zip(ordered, ordered[1:]):
                rank_change = ranks[key][right["configuration"][key]] - ranks[key][left["configuration"][key]]
                if not rank_change:
                    continue
                weight = 1 / (1 + math.log1p(left["cost_seconds"] + right["cost_seconds"]))
                slopes.append(((right["effect"] - left["effect"]) / rank_change, weight))
        if slopes:
            learned[key] = sum(slope * weight for slope, weight in slopes) / sum(weight for _, weight in slopes)
    timed = [entry for entry in observations.values() if entry["cost_seconds"] > 0]
    typical_cost = sum(entry["cost_seconds"] for entry in timed) / len(timed) if timed else None

    def score(config):
        span = distance(config, incumbent)
        value = (3.0 if span == 1 else 0.0) - span
        directional = []
        if config["implementation"] != incumbent["implementation"] and support.get("variant:" + config["implementation"], 0):
            value += 2 * support["variant:" + config["implementation"]]
            directional.append("observed SQL execution supports testing the registered index alternative")
        contention, queue = support.get("shared_lock_contention", 0), support.get("queue_pressure", 0)
        if model_version >= 2:
            alternatives = {"coarse":"narrow", "scan":"indexed"}
            if queue and alternatives.get(incumbent["implementation"]) == config["implementation"]:
                value += 3 * queue
                directional.append("queue propagation through the registered serialized resource supports testing its bounded alternative before adding contenders")
            if (support.get("digest_execution",0) and incumbent["implementation"] in ("map","ordered")
                    and config["implementation"] != incumbent["implementation"]):
                value += 2 * support["digest_execution"]
                directional.append("observed JSON execution supports testing the registered representation alternative")
        if contention:
            if config["implementation"] == "narrow" and incumbent["implementation"] == "coarse":
                value += 2 * contention
                directional.append("contention supports testing the narrower lock scope")
            if config["workers"] < incumbent["workers"]:
                value += contention
                directional.append("contention supports testing fewer workers")
        if queue:
            if config["workers"] > incumbent["workers"]:
                value += 2 * queue
                directional.append("queue pressure supports testing more workers")
            if config["queue_capacity"] > incumbent["queue_capacity"]:
                value += 1.5 * queue
                directional.append("queue pressure supports testing greater queue capacity")
        marginal = []
        for key, slope in learned.items():
            change = ranks[key][config[key]] - ranks[key][incumbent[key]]
            if change:
                prediction = max(-1.0, min(1.0, slope * change))
                value += 4 * prediction
                marginal.append({"parameter": key, "effect_per_rank": slope,
                                 "predicted_effect": prediction})
        estimated_cost = None
        if timed:
            weights = [1 / (1 + distance(config, entry["configuration"])) for entry in timed]
            estimated_cost = sum(weight * entry["cost_seconds"] for weight, entry in zip(weights, timed)) / sum(weights)
            value -= .15 * math.log1p(estimated_cost / typical_cost)
        return value, directional, marginal, estimated_cost

    selected = min(remaining, key=lambda config: (-score(config)[0], _seed_key(seed, config)))
    value, directional, marginal, estimated_cost = score(selected)
    reason.update(mode="exploitation", score=value, hypothesis_support=support,
                  observed_marginals=marginal, estimated_cost_seconds=estimated_cost,
                  basis="; ".join(["Adjacent rank moves around the best observed promising incumbent receive priority",
                                    *directional, "Observed marginal effects and experiment cost refine the ordering",
                                    "Selection scores order tests and do not establish causality or improvement"]))
    return dict(selected), reason
