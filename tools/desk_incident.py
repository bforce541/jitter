#!/usr/bin/env python3
"""Run the local desk incident and write a self-contained operator report."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from time import perf_counter_ns

from jitter_runner.desk_report import render
from jitter_runner.reconcile import FIELDS, export_csv, read_records, reconcile

ROOT = Path(__file__).resolve().parents[1]
MS = 1_000_000


def write_input(path, source, rows):
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(FIELDS)
        for ident, execution, instrument, side, quantity, ticks, executed, observed in rows:
            writer.writerow([source, ident, "SIM-01", execution, 1, instrument, side, quantity,
                             ticks, executed * MS, observed * MS])
    return read_records(path)


def reconcile_scenario(out):
    desk = write_input(out / "desk-bookings.csv", "desk", [
        ("B-101", "EX-01", "ABC", "BUY", 100, 12345, 10, 20),
        ("B-102", "EX-02", "ABC", "SELL", 30, 12349, 25, 30),
        ("B-103", "EX-03", "XYZ", "BUY", 5, 859, 31, 39),
    ])
    initial_venue = write_input(out / "venue-initial.csv", "venue", [
        ("V-810", "EX-01", "ABC", "BUY", 100, 12345, 10, 21),
        ("V-811", "EX-03", "XYZ", "BUY", 5, 859, 31, 40),
    ])
    late_venue = write_input(out / "venue-late.csv", "venue", [
        ("V-812", "EX-02", "ABC", "SELL", 30, 12349, 25, 120),
    ])
    database = out / "executions.sqlite"
    timeline, compute_ns = [], []
    for now, batch in ((40, desk + initial_venue), (80, []), (120, late_venue)):
        start = perf_counter_ns()
        result = reconcile(database, batch, as_of_ns=now * MS, grace_ns=50 * MS)
        compute_ns.append(perf_counter_ns() - start)
        timeline.append(result["history"][-1])
    expected_positions = {"ABC": {"desk": 70, "venue": 70, "desk_minus_venue": 0},
                          "XYZ": {"desk": 5, "venue": 5, "desk_minus_venue": 0}}
    if ([row["state"] for row in timeline] != ["pending", "confirmed", "resolved"] or
            any(row["execution_id"] != "EX-02" for row in timeline) or
            timeline[1]["evidence"]["position_impact"] != {"ABC": -30} or
            result["positions"] != expected_positions or result["open"] or result["records"] != 6):
        raise RuntimeError("simulated desk state differs from the independent scenario expectations")
    repeat = reconcile(database, desk + initial_venue + late_venue, as_of_ns=120 * MS, grace_ns=50 * MS)
    if repeat != result:
        raise RuntimeError("repeat import changed the reconciled state")
    export_csv(out / "discrepancies.csv", result)
    return {"verified": True, "idempotent": True, "grace_ns": 50 * MS,
            "logical_detection_ns": timeline[1]["as_of_ns"] - timeline[0]["evidence"]["first_observed_ns"],
            "logical_resolution_after_detection_ns": timeline[2]["as_of_ns"] - timeline[1]["as_of_ns"],
            "analysis_runtime_ns": compute_ns, "result": result}


def get_binary(out, supplied):
    if supplied:
        return supplied.resolve(strict=True)
    build = out / "build"
    configure = ["cmake", "-S", str(ROOT), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"]
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        configure.append("-DCMAKE_OSX_ARCHITECTURES=arm64")
    with (out / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(configure, stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(["cmake", "--build", str(build), "--target", "jitter_incident", "--parallel", "4"],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    return build / "jitter_incident"


def run(output, binary=None, single_feed=False):
    if binary is not None:
        binary = binary.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    binary = get_binary(output, binary)
    command = [str(binary)] + (["--single-feed"] if single_feed else [])
    run_market = subprocess.run(command, capture_output=True, text=True, timeout=10)
    (output / "market.json").write_text(run_market.stdout, encoding="utf-8")
    (output / "market.stderr.log").write_text(run_market.stderr, encoding="utf-8")
    run_market.check_returncode()
    market = json.loads(run_market.stdout)
    desk = reconcile_scenario(output)
    result = {"schema": 1, "market": market, "desk": desk, "command": command,
              "host": platform.platform(), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    (output / "incident.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (output / "report.html").write_text(render(result), encoding="utf-8")
    return output / "report.html"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--single-feed", action="store_true")
    args = parser.parse_args()
    try:
        path = run(args.output, args.binary, args.single_feed)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"desk incident: {error}\n")
    print(f"Book and positions verified. Operator report: {path}")


if __name__ == "__main__":
    main()
