"""Strict, versioned experiment contract."""

import json
import math
from pathlib import Path


class ExperimentError(ValueError):
    pass


def object_keys(value, required, location):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ExperimentError(f"{location} must contain exactly: {', '.join(required)}")


def integer(value, minimum, maximum, location):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ExperimentError(f"{location} must be an integer in [{minimum}, {maximum}]")


def real(value, minimum, maximum, location):
    if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
        raise ExperimentError(f"{location} must be a finite number in [{minimum}, {maximum}]")


def validate_spec(spec):
    if not isinstance(spec, dict):
        raise ExperimentError("spec must be an object")
    integer(spec.get("schema_version"), 1, 2, "schema_version")
    required = ("schema_version", "application", "workload", "objective", "repetitions", "timeout_seconds")
    object_keys(spec, required + (("instrumentation",) if spec["schema_version"] == 2 else ()), "spec")
    app = spec["application"]
    object_keys(app, ("adapter", "workers", "queue_capacity", "key_count", "dataset_seed", "light_rounds", "heavy_rounds"), "application")
    if app["adapter"] != "shared-index-v1":
        raise ExperimentError("only the shared-index-v1 application adapter is supported")
    for key, low, high in (("workers", 1, 256), ("queue_capacity", 1, 1000000), ("key_count", 1, 65536),
                           ("dataset_seed", 0, (1 << 64) - 1), ("light_rounds", 1, 10000000), ("heavy_rounds", 1, 10000000)):
        integer(app[key], low, high, f"application.{key}")
    if spec["schema_version"] == 2:
        tracing = spec["instrumentation"]
        object_keys(tracing, ("enabled", "events_per_thread"), "instrumentation")
        if type(tracing["enabled"]) is not bool:
            raise ExperimentError("instrumentation.enabled must be boolean")
        integer(tracing["events_per_thread"], 1, 1000000, "instrumentation.events_per_thread")
        if tracing["enabled"] and tracing["events_per_thread"] * (app["workers"] + 1) * 32 > 268435456:
            raise ExperimentError("trace buffers exceed the 256 MiB allocation limit")
    workload = spec["workload"]
    object_keys(workload, ("seed", "arrival", "rate_per_second", "warmup_ms", "measurement_ms", "heavy_fraction", "burst_size"), "workload")
    integer(workload["seed"], 0, (1 << 64) - 1, "workload.seed")
    if workload["arrival"] not in ("steady", "burst"):
        raise ExperimentError("arrival must be steady or burst")
    integer(workload["rate_per_second"], 1, 1000000, "workload.rate_per_second")
    integer(workload["warmup_ms"], 0, 3600000, "workload.warmup_ms")
    integer(workload["measurement_ms"], 1, 3600000, "workload.measurement_ms")
    real(workload["heavy_fraction"], 0, 1, "workload.heavy_fraction")
    integer(workload["burst_size"], 1, 1000000, "workload.burst_size")
    if workload["arrival"] == "steady" and workload["burst_size"] != 1:
        raise ExperimentError("steady arrivals require burst_size=1")
    count = sum((workload["rate_per_second"] * workload[key] + 999) // 1000
                for key in ("warmup_ms", "measurement_ms"))
    if count > 1000000:
        raise ExperimentError("workload exceeds one million requests per repetition")
    objective = spec["objective"]
    object_keys(objective, ("metric", "min_throughput_per_second", "max_peak_rss_bytes", "max_cpu_cores",
                            "max_rejection_fraction", "max_scheduler_lag_p99_ns"), "objective")
    if objective["metric"] != "p99_latency_ns":
        raise ExperimentError("Phase 1 supports the p99_latency_ns objective")
    real(objective["min_throughput_per_second"], 0, 1e12, "objective.min_throughput_per_second")
    integer(objective["max_peak_rss_bytes"], 1, (1 << 63) - 1, "objective.max_peak_rss_bytes")
    real(objective["max_cpu_cores"], 0, 1024, "objective.max_cpu_cores")
    real(objective["max_rejection_fraction"], 0, 1, "objective.max_rejection_fraction")
    integer(objective["max_scheduler_lag_p99_ns"], 0, (1 << 63) - 1, "objective.max_scheduler_lag_p99_ns")
    integer(spec["repetitions"], 1, 100, "repetitions")
    real(spec["timeout_seconds"], 0.001, 86400, "timeout_seconds")
    return spec


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExperimentError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ExperimentError(f"invalid JSON constant: {value}")


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=_unique_object,
                         parse_constant=_invalid_constant)


def load_spec(path):
    return validate_spec(load_json(path))
