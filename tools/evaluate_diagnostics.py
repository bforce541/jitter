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
from jitter_runner.diagnostic_evaluation import CASES, control_row, control_report, verify_diagnostic_evaluation


def evaluate(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for name, scenario, request, truth in CASES:
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
        rows.append(control_row(name, request, truth, report))
    result = control_report(rows)
    write_json(output / "diagnostic-evaluation.json", result)
    seal_bundle(output, "complete")
    verify_diagnostic_evaluation(output)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.output), indent=2, sort_keys=True))
