#!/usr/bin/env python3
"""Find the observed load envelope under latency, coverage, and CPU constraints."""

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path


def limit(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative finite limit")
    return parsed


def make_envelope(study, p99_ns, p999_ns=None, late_fraction=None, cpu_cores=None):
    axis = study.get("comparison", {}).get("axis", "book")
    if axis not in ("book", "queue"):
        raise ValueError("unrecognized study comparison axis")
    groups = defaultdict(list)
    paired = defaultdict(dict)
    comparison = study.get("comparison", {})
    expected = {comparison.get("reference"), comparison.get("candidate")} - {None}
    for row in study["sweep"]:
        run = row["result"]
        if run["mode"] != "feed":
            raise ValueError("envelope requires feed observations")
        rate, burst = run["offered_rate"], run["burst"]
        groups[(run[axis], burst, rate)].append(run)
        point = paired[(burst, rate, run["seed"])]
        if run[axis] in point:
            raise ValueError("duplicate treatment at the same rate, burst and seed")
        point[run[axis]] = (
            run["input_id"], run["events"], run.get("warmup_events"), run.get("queue_capacity"),
            run["book"] if axis == "queue" else run.get("queue", "mutex"),
        )
    if not groups:
        raise ValueError("the study has no load sweep")
    if not expected:
        expected = {treatment for treatment, _, _ in groups}
    for point in paired.values():
        if set(point) != expected:
            raise ValueError("missing or unexpected treatment at the same rate, burst and seed")
        if len(set(point.values())) != 1:
            raise ValueError("treatments at the same rate, burst and seed have different inputs or controls")
    shapes = defaultdict(dict)
    for (treatment, burst, rate), runs in sorted(groups.items()):
        reasons = defaultdict(int)
        for run in runs:
            count = run["events"]
            published = run["published"]
            metrics = run["scheduled_to_publication_ns"]
            if (not 0 < count or not 0 <= published <= run["processed"] <= count or
                    run["processed"] + run["dropped"] != count or
                    not 0 <= run["late_over_100us_processed"] <= run["processed"]):
                raise ValueError("inconsistent event accounting")
            if any(v is not None and (type(v) is not int or v < 0) for v in (metrics["p99"], metrics["p999"])):
                raise ValueError("invalid latency percentile")
            if metrics["count"] != published:
                raise ValueError("latency count must equal publications")
            if not run["full_coverage"] or published != count or run["warmup_dropped"]:
                reasons["coverage"] += 1
            if published < 2000 or metrics["p99"] is None or metrics["p99"] > p99_ns:
                reasons["p99"] += 1
            if p999_ns is not None and (published < 20000 or metrics["p999"] is None or metrics["p999"] > p999_ns):
                reasons["p999"] += 1
            if late_fraction is not None and run["late_over_100us_processed"] / count > late_fraction:
                reasons["late"] += 1
            if cpu_cores is not None:
                cost = run.get("feed_phase_cpu_ns")
                if cost is None or run["elapsed_ns"] <= 0 or cost / run["elapsed_ns"] > cpu_cores:
                    reasons["cpu"] += 1
        shapes[(treatment, burst)][rate] = {
            "runs": len(runs),
            "pass": not reasons,
            "failures_by_rule": dict(reasons),
            "worst_scheduled_p99_ns": max((run["scheduled_to_publication_ns"]["p99"]
                                           for run in runs if run["scheduled_to_publication_ns"]["p99"] is not None),
                                          default=None),
            "min_published_per_second": min(run["published_per_second"] for run in runs),
            "max_peak_queue_depth": max(run["peak_queue_depth"] for run in runs),
        }
    output = []
    for (treatment, burst), observations in sorted(shapes.items()):
        rates = sorted(observations.items())
        first_failed = next((rate for rate, point in rates if not point["pass"]), None)
        passing = [rate for rate, point in rates if point["pass"]]
        output.append({
            "treatment": treatment, "burst": burst,
            "passing_prefix_max_rate": max((r for r in passing if first_failed is None or r < first_failed), default=None),
            "highest_passing_rate": max(passing, default=None),
            "first_failed_rate": first_failed,
            "rates": [{"offered_rate": rate, **point} for rate, point in rates],
        })
    return {
        "schema": 1, "axis": axis, "p99_ns": p99_ns, "p999_ns": p999_ns,
        "max_late_fraction": late_fraction, "max_cpu_cores": cpu_cores,
        "note": "All recorded repeats at a rate must pass. Untested rates and future runs have no guarantee.",
        "series": output,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--p99-us", type=limit, required=True)
    parser.add_argument("--p999-us", type=limit)
    parser.add_argument("--max-late-fraction", type=limit)
    parser.add_argument("--max-cpu-cores", type=limit)
    args = parser.parse_args()
    if args.max_late_fraction is not None and args.max_late_fraction > 1:
        parser.error("--max-late-fraction must not exceed one")
    try:
        with args.study.open(encoding="utf-8") as stream:
            report = make_envelope(json.load(stream), args.p99_us * 1000,
                                   args.p999_us * 1000 if args.p999_us is not None else None,
                                   args.max_late_fraction, args.max_cpu_cores)
        print(json.dumps(report, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"market envelope: {error}\n")


if __name__ == "__main__":
    main()
