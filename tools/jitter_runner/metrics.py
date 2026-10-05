"""Validate every response before deriving baseline measurements."""

import csv
import math
from pathlib import Path

from .spec import ExperimentError
from .workload import expected_value

REQUEST_FIELDS = ("id", "phase", "scheduled_ns", "key", "heavy", "status", "submitted_ns", "started_ns", "completed_ns", "value")
RESOURCE_FIELDS = ("phase", "arrival_window_ns", "elapsed_ns", "cpu_user_ns", "cpu_system_ns", "peak_rss_bytes")


def read_csv(path, fields):
    with Path(path).open(newline="", encoding="ascii") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(fields):
            raise ExperimentError(f"unexpected CSV header: {path}")
        rows = list(reader)
    if any(set(row) != set(fields) or any(value is None for value in row.values()) for row in rows):
        raise ExperimentError(f"malformed CSV row: {path}")
    return rows


def numeric(value, minimum=0, maximum=(1 << 64) - 1):
    if not isinstance(value, str) or not value or (value[0] == "-" and not value[1:].isdigit()) or (value[0] != "-" and not value.isdigit()):
        raise ExperimentError("invalid integer in application output")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ExperimentError("out-of-range integer in application output")
    return parsed


def distribution(values):
    if not values:
        return {"count": 0, "min": None, "p50": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)
    def quantile(percentile):
        return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]
    return {"count": len(values), "min": ordered[0], "p50": quantile(.5),
            "p95": quantile(.95), "p99": quantile(.99), "max": ordered[-1]}


def analyze_run(spec, workload, run_dir):
    rows = read_csv(Path(run_dir) / "requests.csv", REQUEST_FIELDS)
    if len(rows) != len(workload):
        raise ExperimentError("request accounting failed: missing or extra responses")
    normalized = []
    oracle = {}
    for expected, row in zip(workload, rows):
        for key in ("id", "scheduled_ns", "key", "heavy"):
            if numeric(row[key]) != expected[key]:
                raise ExperimentError(f"request identity or workload mismatch: {key}")
        if row["phase"] != expected["phase"]:
            raise ExperimentError("request phase mismatch")
        submitted = numeric(row["submitted_ns"])
        started = numeric(row["started_ns"], -1)
        completed = numeric(row["completed_ns"], -1)
        value = numeric(row["value"])
        if submitted < expected["scheduled_ns"]:
            raise ExperimentError("application offered a request before its intended arrival")
        if row["status"] == "completed":
            if not submitted <= started <= completed:
                raise ExperimentError("nonmonotonic request timestamps")
            identity = (expected["key"], expected["heavy"])
            if identity not in oracle:
                oracle[identity] = expected_value(spec["application"], *identity)
            if value != oracle[identity]:
                raise ExperimentError(f"incorrect response for request {expected['id']}")
        elif row["status"] == "rejected":
            if started != -1 or completed != -1 or value != 0:
                raise ExperimentError("rejected request has execution data")
        elif row["status"] == "error":
            raise ExperimentError(f"application error for request {expected['id']}")
        else:
            raise ExperimentError(f"unknown or unfinished request status: {row['status']}")
        normalized.append({**expected, "status": row["status"], "submitted_ns": submitted,
                           "started_ns": started, "completed_ns": completed})

    resources = read_csv(Path(run_dir) / "resources.csv", RESOURCE_FIELDS)
    expected_phases = ["warmup", "measurement"] if spec["workload"]["warmup_ms"] else ["measurement"]
    if [row["phase"] for row in resources] != expected_phases:
        raise ExperimentError("resource phase accounting failed")
    summaries = {}
    for resource in resources:
        phase = resource["phase"]
        data = {key: numeric(resource[key]) for key in RESOURCE_FIELDS if key != "phase"}
        duration_key = "warmup_ms" if phase == "warmup" else "measurement_ms"
        if data["arrival_window_ns"] != spec["workload"][duration_key] * 1000000:
            raise ExperimentError("arrival window differs from specification")
        if data["elapsed_ns"] < data["arrival_window_ns"] or data["peak_rss_bytes"] == 0:
            raise ExperimentError("invalid elapsed time or memory measurement")
        phase_rows = [row for row in normalized if row["phase"] == phase]
        if any(max(row["submitted_ns"], row["completed_ns"]) > data["elapsed_ns"] for row in phase_rows):
            raise ExperimentError("request timestamp extends beyond drained phase")
        completed = [row for row in phase_rows if row["status"] == "completed"]
        rejected = len(phase_rows) - len(completed)
        elapsed_seconds = data["elapsed_ns"] / 1e9
        summaries[phase] = {
            "offered": len(phase_rows), "completed": len(completed), "rejected": rejected, "errors": 0,
            "completion_fraction": len(completed) / len(phase_rows) if phase_rows else 0,
            "rejection_fraction": rejected / len(phase_rows) if phase_rows else 0,
            "latency_ns": distribution([row["completed_ns"] - row["scheduled_ns"] for row in completed]),
            "response_latency_ns": distribution([row["completed_ns"] - row["submitted_ns"] for row in completed]),
            "queue_delay_ns": distribution([row["started_ns"] - row["submitted_ns"] for row in completed]),
            "service_time_ns": distribution([row["completed_ns"] - row["started_ns"] for row in completed]),
            "scheduler_lag_ns": distribution([row["submitted_ns"] - row["scheduled_ns"] for row in phase_rows]),
            "offered_per_second": len(phase_rows) / (data["arrival_window_ns"] / 1e9),
            "throughput_per_second": len(completed) / elapsed_seconds,
            "in_window_throughput_per_second": sum(row["completed_ns"] <= data["arrival_window_ns"] for row in completed) / (data["arrival_window_ns"] / 1e9),
            "cpu_cores": (data["cpu_user_ns"] + data["cpu_system_ns"]) / data["elapsed_ns"],
            **data,
        }
    measurement = summaries["measurement"]
    objective = spec["objective"]
    checks = {
        "throughput": measurement["throughput_per_second"] >= objective["min_throughput_per_second"],
        "memory": measurement["peak_rss_bytes"] <= objective["max_peak_rss_bytes"],
        "cpu": measurement["cpu_cores"] <= objective["max_cpu_cores"],
        "rejections": measurement["rejection_fraction"] <= objective["max_rejection_fraction"],
        "scheduler_lag": measurement["scheduler_lag_ns"]["p99"] <= objective["max_scheduler_lag_p99_ns"],
        "latency_observed": measurement["latency_ns"]["p99"] is not None,
    }
    warnings = []
    if measurement["completed"] < 1000:
        warnings.append("Fewer than 1000 completions: p99 has little tail support; no significance claim is made.")
    if measurement["rejected"]:
        warnings.append("Latency percentiles describe completed requests only; rejected requests remain in accounting and constraints.")
    if not checks["scheduler_lag"]:
        warnings.append("Load generator fell behind its configured scheduling tolerance.")
    if "warmup" in summaries and summaries["warmup"]["rejected"]:
        warnings.append("Warmup rejected requests; inspect whether the intended application state was reached.")
    return {"schema_version": 1, "correctness": "passed", "constraints": checks,
            "feasible": all(checks.values()), "phases": summaries, "warnings": warnings}


def summarize_runs(spec, runs):
    valid = [run for run in runs if run.get("correctness") == "passed"]
    return {"schema_version": 1, "status": "complete" if len(valid) == spec["repetitions"] else "failed",
            "repetitions_requested": spec["repetitions"], "repetitions_valid": len(valid),
            "all_runs_feasible": len(valid) == spec["repetitions"] and all(run["feasible"] for run in valid),
            "p99_latency_ns_across_runs": distribution([run["phases"]["measurement"]["latency_ns"]["p99"] for run in valid if run["phases"]["measurement"]["latency_ns"]["p99"] is not None]),
            "runs": runs,
            "interpretation": "Baseline measurements only. Repeated-run p99 values are not pooled; no improvement or statistical significance is claimed."}
