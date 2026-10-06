"""Alternating paired observation-overhead studies, with no significance claims."""

import copy
from datetime import datetime, timezone
from pathlib import Path

from .metrics import distribution
from .runner import new_output, run_experiment, verify_bundle
from .spec import ExperimentError, load_json, load_spec, validate_spec
from .storage import seal_bundle, write_json, verify_integrity


def plan(spec):
    return [{"pair": index + 1, "mode": mode, "bundle": f"pair-{index + 1:03d}-{mode}"}
            for index in range(spec["repetitions"])
            for mode in (("off", "on") if index % 2 == 0 else ("on", "off"))]


def child_spec(spec, mode):
    child = copy.deepcopy(spec)
    child["repetitions"] = 1
    child["instrumentation"]["enabled"] = mode == "on"
    return child


def compare_pairs(pairs):
    comparisons = []
    for index, pair in enumerate(pairs):
        off, on = pair["off"], pair["on"]
        before, after = off["phases"]["measurement"], on["phases"]["measurement"]
        reasons = []
        if not on["observation"]["complete"]:
            reasons.append("traced events were dropped")
        if before["rejected"] or after["rejected"] or before["completed"] != after["completed"]:
            reasons.append("completion coverage differs or requests were rejected")
        if not off["constraints"]["scheduler_lag"] or not on["constraints"]["scheduler_lag"]:
            reasons.append("workload delivery exceeded scheduling tolerance")
        def delta(old, new):
            return {"off": old, "on": new, "delta": new - old if old is not None and new is not None else None,
                    "percent": (new - old) / old * 100 if old is not None and new is not None and old != 0 else None}
        def cpu_per_request(measurement):
            return (measurement["cpu_user_ns"] + measurement["cpu_system_ns"]) / measurement["completed"] if measurement["completed"] else None
        comparisons.append({"pair": index + 1, "comparable": not reasons, "limitations": reasons,
                            "p99_latency_ns": delta(before["latency_ns"]["p99"], after["latency_ns"]["p99"]),
                            "throughput_per_second": delta(before["throughput_per_second"], after["throughput_per_second"]),
                            "cpu_cores": delta(before["cpu_cores"], after["cpu_cores"]),
                            "cpu_ns_per_completed_request": delta(cpu_per_request(before), cpu_per_request(after)),
                            "peak_rss_bytes": delta(before["peak_rss_bytes"], after["peak_rss_bytes"]),
                            "trace_reserved_bytes": on["observation"]["reserved_bytes"],
                            "trace_events_written": on["observation"]["written"], "trace_events_dropped": on["observation"]["dropped"],
                            "off_feasible": off["feasible"], "on_feasible": on["feasible"]})
    all_comparable = all(pair["comparable"] for pair in comparisons)
    # No filtering of noisy pairs into a convenient headline estimate.
    estimates = {key: distribution([pair[key]["delta"] for pair in comparisons if pair[key]["delta"] is not None])
                 for key in ("p99_latency_ns", "throughput_per_second", "cpu_cores", "cpu_ns_per_completed_request", "peak_rss_bytes")} if all_comparable else None
    return {"schema_version": 1, "status": "complete", "pairs": comparisons, "all_pairs_comparable": all_comparable,
            "paired_deltas": estimates,
            "interpretation": "Observed on-minus-off differences for identical inputs and executable. Signed deltas may reflect noise; no causal overhead estimate, confidence interval, significance, or performance win is claimed.",
            "limitations": ["Alternating order reduces a systematic order bias; it does not control thermal state, CPU frequency, or background work.",
                            "CPU and memory cover the C++ application process, not Python analysis or CSV export after measurement.",
                            "If any pair lacks complete coverage or acceptable delivery, headline deltas are withheld and every pair is retained."]}


def verify_overhead(path):
    path = Path(path).resolve()
    verify_integrity(path, required=("overhead-spec.json", "execution.json", "comparison.json"))
    spec = load_spec(path / "overhead-spec.json")
    if spec["schema_version"] != 2:
        raise ExperimentError("overhead requires a schema-version-2 specification")
    execution = load_json(path / "execution.json")
    if not isinstance(execution, dict) or execution.get("order") != plan(spec):
        raise ExperimentError("overhead execution order differs from its alternating plan")
    pairs = [{"off": None, "on": None} for _ in range(spec["repetitions"])]
    identity, source, workload = None, None, None
    for entry in plan(spec):
        saved_spec, summary, metadata = verify_bundle(path / entry["bundle"])
        if saved_spec != child_spec(spec, entry["mode"]):
            raise ExperimentError("overhead pair changed settings beyond the tracing mode")
        if identity is None:
            identity, source, workload = metadata["engine_sha256"], metadata["source"]["files"], metadata["workload_sha256"]
        if (identity, source, workload) != (metadata["engine_sha256"], metadata["source"]["files"], metadata["workload_sha256"]):
            raise ExperimentError("overhead pairs do not share executable, source snapshot content, and inputs")
        pairs[entry["pair"] - 1][entry["mode"]] = summary["runs"][0]
    report = compare_pairs(pairs)
    if report != load_json(path / "comparison.json"):
        raise ExperimentError("overhead report differs from validated paired evidence")
    return spec, report


def run_overhead(spec, output=None, binary=None, build_dir=None, reproduce_from=None):
    validate_spec(spec)
    if spec["schema_version"] != 2:
        raise ExperimentError("overhead requires schema version 2 with an instrumentation configuration")
    validate_spec(child_spec(spec, "on"))
    original = Path(reproduce_from).resolve() if reproduce_from else None
    if original:
        original_spec, _ = verify_overhead(original)
        if original_spec != spec:
            raise ExperimentError("overhead reproduction changed the specification")
    root = new_output(output)
    write_json(root / "manifest.json", {"schema_version": 1, "status": "running", "artifacts": {}})
    write_json(root / "overhead-spec.json", spec)
    write_json(root / "execution.json", {"schema_version": 1, "order": plan(spec),
                                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                                        "reproduce_from": str(original) if original else None})
    pairs = [{"off": None, "on": None} for _ in range(spec["repetitions"])]
    pinned_binary = binary
    try:
        for entry in plan(spec):
            print(f"Overhead pair {entry['pair']}/{spec['repetitions']}: tracing {entry['mode']}", flush=True)
            path, summary = run_experiment(child_spec(spec, entry["mode"]), root / entry["bundle"],
                                           binary=pinned_binary, build_dir=build_dir if pinned_binary is None else None,
                                           reproduce_from=original / entry["bundle"] if original else None)
            pinned_binary = path / "engine" / "jitter"
            pairs[entry["pair"] - 1][entry["mode"]] = summary["runs"][0]
        report = compare_pairs(pairs)
        write_json(root / "comparison.json", report)
        seal_bundle(root, "complete")
        verify_overhead(root)
        return root, report
    except (Exception, KeyboardInterrupt) as error:
        write_json(root / "comparison.json", {"schema_version": 1, "status": "failed", "failure": str(error)})
        seal_bundle(root, "failed")
        raise ExperimentError(f"overhead study failed: {error}; evidence retained at {root}") from error
