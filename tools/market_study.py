#!/usr/bin/env python3
"""Run the registered market experiments and retain every observation."""
import argparse
import hashlib
import json
import platform
from pathlib import Path
from statistics import median
import subprocess

from jitter_runner.statistics import median_bounds

OPTIONS = {
    "direct": ("direct", 100000, 1),
    "steady-100k": ("feed", 100000, 1),
    "burst32-100k": ("feed", 100000, 32),
    "steady-500k": ("feed", 500000, 1),
    "burst32-500k": ("feed", 500000, 32),
}
SWEEP = (100000, 250000, 500000, 1000000, 2000000, 4000000)


def measure(binary, mode, rate, burst, book, index, events, warmup):
    command = [str(binary), "--mode", mode, "--rate", str(rate), "--burst", str(burst),
               "--book", book, "--events", str(events), "--warmup", str(warmup),
               "--seed", str(17 + index), "--capacity", "4096"]
    execution = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if execution.returncode:
        raise RuntimeError(f"{command}: {execution.stderr}")
    result = json.loads(execution.stdout)
    return {"argv": command, "result": result}


def summarize(rows):
    qualified = all(each["result"]["full_coverage"] for pair in rows for each in pair)
    output = {"pairs": len(rows), "all_full_coverage": qualified}
    if not qualified:
        return output
    for metric in ("arrival_to_publication_ns", "scheduled_to_publication_ns"):
        output[metric] = {}
        for q in ("p50", "p99", "p999"):
            reference = [pair[0]["result"][metric][q] for pair in rows]
            dense = [pair[1]["result"][metric][q] for pair in rows]
            effects = [1 - b / a for a, b in zip(reference, dense) if a and b]
            if len(effects) != len(rows):
                continue
            output[metric][q] = {"reference_median_ns": median(reference), "candidate_median_ns": median(dense),
                                 "median_paired_improvement": median(effects),
                                 "bounds": median_bounds(effects, .95)}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", choices=("reference", "dense", "cached"), default="reference")
    parser.add_argument("--candidate", choices=("reference", "dense", "cached"), default="dense")
    parser.add_argument("--case", action="append", choices=OPTIONS)
    parser.add_argument("--skip-sweep", action="store_true")
    args = parser.parse_args()
    if args.reference == args.candidate:
        parser.error("choose distinct implementations")
    binary = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"schema": 1, "host": platform.uname()._asdict(), "python": platform.python_version(),
                "checkout_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "checkout_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"])),
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "comparison": {"reference": args.reference, "candidate": args.candidate},
                "cases": {}, "sweep": []}
    def save():
        (args.output / "study.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    save()
    for name in args.case or OPTIONS:
        mode, rate, burst = OPTIONS[name]
        case = evidence["cases"][name] = {"observations": []}
        for i in range(8):
            order = (args.reference, args.candidate) if i % 2 == 0 else (args.candidate, args.reference)
            pair = {book: measure(binary, mode, rate, burst, book, i, 100000, 20000) for book in order}
            if pair[args.reference]["result"]["input_id"] != pair[args.candidate]["result"]["input_id"]:
                raise RuntimeError("paired workloads differ")
            case["observations"].append([pair[args.reference], pair[args.candidate]])
            save()
        case["summary"] = summarize(case["observations"])
        save()
        print(name, case["summary"]["all_full_coverage"], flush=True)
    for rate in (() if args.skip_sweep else SWEEP):
        for burst in (1, 32):
            for book in (args.reference, args.candidate):
                evidence["sweep"].append(measure(binary, "feed", rate, burst, book, 0, 100000, 20000))
                save()
        print("sweep", rate, flush=True)


if __name__ == "__main__":
    main()
