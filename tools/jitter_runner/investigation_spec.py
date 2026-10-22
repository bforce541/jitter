"""Closed investigation contract: behavior stays fixed while supported controls vary."""

from .spec import ExperimentError, integer, load_json, object_keys, real, validate_spec
from .search import normalize_space


def validate_investigation(spec):
    if not isinstance(spec, dict):
        raise ExperimentError("investigation must be an object")
    integer(spec.get("schema_version"), 1, 2, "investigation.schema_version")
    object_keys(spec, ("schema_version", "experiment", "search", "sampling", "comparison", "budget", "diagnostic") +
                (("stopping",) if spec["schema_version"] == 2 else ()), "investigation")
    experiment = validate_spec(spec["experiment"])
    if experiment["schema_version"] != 3 or experiment["repetitions"] != 1:
        raise ExperimentError("investigation requires a schema-version-3 experiment with repetitions=1")
    if experiment["instrumentation"]["enabled"]:
        raise ExperimentError("investigation timing must disable tracing; diagnostic is a separate run")
    if experiment["objective"]["max_rejection_fraction"] != 0:
        raise ExperimentError("investigation requires zero rejected requests")
    normalize_space(experiment, spec["search"])
    sampling = spec["sampling"]
    object_keys(sampling, ("exploration_pairs", "max_exploration_pairs", "confirmation_pairs"), "sampling")
    integer(sampling["exploration_pairs"], 2, 20, "sampling.exploration_pairs")
    integer(sampling["max_exploration_pairs"], sampling["exploration_pairs"], 20, "sampling.max_exploration_pairs")
    integer(sampling["confirmation_pairs"], 8, 100, "sampling.confirmation_pairs")
    policy = spec["comparison"]
    object_keys(policy, ("metric", "min_effect", "confidence", "max_throughput_loss", "min_tail_observations", "max_baseline_spread"), "comparison")
    if policy["metric"] not in ("p50", "p95", "p99", "p999"):
        raise ExperimentError("comparison.metric must be p50, p95, p99, or p999")
    real(policy["min_effect"], .001, .99, "comparison.min_effect")
    real(policy["confidence"], .9, .999, "comparison.confidence")
    real(policy["max_throughput_loss"], 0, .5, "comparison.max_throughput_loss")
    integer(policy["min_tail_observations"], 20, 1000, "comparison.min_tail_observations")
    real(policy["max_baseline_spread"], 1, 100, "comparison.max_baseline_spread")
    if spec["schema_version"] == 2:
        stopping = spec["stopping"]
        object_keys(stopping, ("enabled", "strong_gain_fraction"), "stopping")
        if type(stopping["enabled"]) is not bool:
            raise ExperimentError("stopping.enabled must be boolean")
        real(stopping["strong_gain_fraction"], policy["min_effect"], .99, "stopping.strong_gain_fraction")
    budget = spec["budget"]
    object_keys(budget, ("max_candidates", "max_runs", "max_builds", "max_failures", "wall_seconds"), "budget")
    for key, high in (("max_candidates", 4096), ("max_runs", 100000), ("max_builds", 16), ("max_failures", 1000)):
        integer(budget[key], 1, high, "budget." + key)
    real(budget["wall_seconds"], .01, 86400, "budget.wall_seconds")
    if type(spec["diagnostic"]) is not bool:
        raise ExperimentError("diagnostic must be boolean")
    if spec["diagnostic"]:
        import copy
        diagnostic = copy.deepcopy(experiment)
        diagnostic["instrumentation"]["enabled"] = True
        validate_spec(diagnostic)
    return spec


def load_investigation(path):
    return validate_investigation(load_json(path))
