#!/usr/bin/env python3
"""Known-timestamp mechanism controls; deliberately not application benchmarks."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_observation import controlled_case, write_csv
from jitter_runner.diagnostics import diagnose
from jitter_runner.metrics import analyze_run, read_csv, REQUEST_FIELDS
from jitter_runner.trace_reader import EVENT_FIELDS
from jitter_runner.storage import write_json, seal_bundle


def evaluate(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    cases = [("owner", "mixed", 1, "shared_lock_contention"),
             ("region", "region", 0, "digest_execution"),
             ("queue", "queue", 2, "queue_pressure"),
             ("scheduler", "mixed", 1, "workload_delivery_delay"),
             ("unowned", "mixed", 1, "unattributed_lock_wait"),
             ("lost", "mixed", 1, "abstain")]
    for name, scenario, request, truth in cases:
        path = output / name
        path.mkdir()
        spec, workload, events = controlled_case(path, capacity=1 if name == "lost" else 128, scenario=scenario)
        if name in ("scheduler", "unowned"):
            raw = read_csv(path / "requests.csv", REQUEST_FIELDS)
            if name == "scheduler":
                for row in raw:
                    for key in ("submitted_ns", "started_ns", "completed_ns"):
                        row[key] = int(row[key]) + 1000000
                for event in events:
                    if 1000 <= event["timestamp_ns"] < 3001000:
                        event["timestamp_ns"] += 1000000
            else:
                raw[0]["completed_ns"] = 22
                times = {"region_end": 20, "lock_release": 21, "request_complete": 22}
                for event in events:
                    if event["request_id"] == 0 and event["kind"] in times:
                        event["timestamp_ns"] = 1000 + times[event["kind"]]
            write_csv(path / "requests.csv", REQUEST_FIELDS, raw)
            write_csv(path / "events.csv", EVENT_FIELDS, events)
        write_json(path / "spec.json", spec)
        report = diagnose(spec, path, analyze_run(spec, workload, path), selected_request=request)
        write_json(path / "diagnostics.json", report)
        actual = report["hypotheses"][0]["id"] if report["hypotheses"] else "abstain"
        stages = report["tail_requests"]
        if report["status"] == "insufficient_trace":
            region, delay = "abstain", "abstain"
        else:
            stage = next(item for item in stages if item["request_id"] == request)
            region = "digest_execution" if stage["digest_ns"] > 0 else "abstain"
            mapping = {"scheduler_ns": "workload_delivery_delay", "queue_ns": "queue_pressure",
                       "lock_wait_ns": "shared_lock_contention", "digest_ns": "digest_execution",
                       "other_service_ns": "unresolved_service_delay"}
            delay = mapping[max(mapping, key=lambda key: stage[key])]
        rows.append({"case": name, "selected_request": request, "known_mechanism": truth,
                     "dependency_analysis": actual, "region_only_baseline": region, "delay_only_baseline": delay,
                     "dependency_match": actual == truth, "region_only_match": region == truth,
                     "delay_only_match": delay == truth, "trace_complete": report["trace_quality"]["complete"]})
    result = {"schema_version": 1, "evidence_kind": "six hand-timed synthetic trace controls, not measured application performance",
              "cases": rows, "matches": {key: sum(row[key] for row in rows) for key in ("dependency_match", "region_only_match", "delay_only_match")},
              "limitations": ["Each case has three handcrafted requests; match counts are mechanism checks, not population accuracy.",
                              "Region-only ranks the observed execution region; delay-only ranks delay components and assumes a lock wait implies contention.",
                              "These transparent weak baselines illustrate ownership evidence and abstention; no comparison with a production profiler is claimed."]}
    write_json(output / "diagnostic-evaluation.json", result)
    seal_bundle(output, "complete")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.output), indent=2, sort_keys=True))
