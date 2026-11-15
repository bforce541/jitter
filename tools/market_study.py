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


def measure(binary, mode, rate, burst, book, index, events, warmup, queue=None, trace=None):
    command = [str(binary), "--mode", mode, "--rate", str(rate), "--burst", str(burst),
               "--book", book, "--events", str(events), "--warmup", str(warmup),
               "--seed", str(17 + index), "--capacity", "4096"]
    if queue is not None:
        command.extend(("--queue", queue))
    if trace is not None:
        command.extend(("--trace", str(trace.resolve())))
    execution = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if execution.returncode:
        raise RuntimeError(f"{command}: {execution.stderr}")
    result = json.loads(execution.stdout)
    row = {"argv": command, "result": result}
    if trace is not None:
        row["trace_file"] = trace.name
    return row


def summarize(rows):
    qualified = all(each["result"]["full_coverage"] for pair in rows for each in pair)
    output = {"pairs": len(rows), "all_full_coverage": qualified}
    if not qualified:
        return output
    if all(each["result"].get("feed_phase_cpu_ns") is not None for pair in rows for each in pair):
        cpu = [[r["result"]["feed_phase_cpu_ns"] / r["result"]["elapsed_ns"] for r in pair] for pair in rows]
        output["feed_mean_cpu_cores"] = {
            "reference_median": median(a for a, _ in cpu),
            "candidate_median": median(b for _, b in cpu),
        }
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
    parser.add_argument("--reference", choices=("reference", "dense"), default="reference")
    parser.add_argument("--candidate", choices=("reference", "dense"), default="dense")
    parser.add_argument("--queue-study", action="store_true",
                        help="compare mutex and SPSC with the dense book fixed")
    parser.add_argument("--case", action="append", choices=OPTIONS)
    parser.add_argument("--seed-base", type=int, default=17,
                        help="first replay seed used by pairs and sweeps")
    parser.add_argument("--pair-events", type=int, default=100000)
    parser.add_argument("--skip-sweep", action="store_true")
    parser.add_argument("--sweep-only", action="store_true")
    parser.add_argument("--sweep-rate", type=int, action="append")
    parser.add_argument("--sweep-events", type=int, default=100000)
    parser.add_argument("--trace-sweep", action="store_true", help="diagnostic CSVs for sweep points; changes timing")
    parser.add_argument("--sweep-repeats", type=int, choices=range(1, 9), default=1)
    args = parser.parse_args()
    if args.queue_study and args.case and "direct" in args.case:
        parser.error("queue comparison requires feed mode")
    if args.reference == args.candidate and not args.queue_study:
        parser.error("choose distinct implementations")
    if args.trace_sweep and not args.sweep_only:
        parser.error("--trace-sweep requires --sweep-only")
    if args.skip_sweep and args.sweep_only:
        parser.error("--skip-sweep and --sweep-only are mutually exclusive")
    if not 0 <= args.seed_base <= (1 << 64) - 8:
        parser.error("--seed-base must leave room for eight uint64 seeds")
    if not 32 <= args.pair_events <= 2000000 or args.pair_events % 32:
        parser.error("--pair-events must be a multiple of 32 in [32, 2000000]")
    if not 32 <= args.sweep_events <= 2000000 or args.sweep_events % 32:
        parser.error("--sweep-events must be a multiple of 32 in [32, 2000000]")
    if any(not 1000 <= rate <= 10000000 for rate in args.sweep_rate or ()):
        parser.error("--sweep-rate must be in [1000, 10000000]")
    treatments = ("mutex", "spsc") if args.queue_study else (args.reference, args.candidate)
    def run(binary, mode, rate, burst, treatment, i, events, warmup, trace=None):
        return measure(binary, mode, rate, burst, "dense" if args.queue_study else treatment,
                       i + args.seed_base - 17, events, warmup, treatment if args.queue_study else None, trace)
    binary = args.binary.resolve(strict=True)
    args.output.mkdir(parents=True, exist_ok=False)
    evidence = {"schema": 1, "host": platform.uname()._asdict(), "python": platform.python_version(),
                "checkout_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "checkout_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"])),
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "comparison": {"reference": treatments[0], "candidate": treatments[1],
                               "axis": "queue" if args.queue_study else "book"},
                "cases": {}, "sweep": []}
    if args.trace_sweep:
        evidence["diagnostic_sweep"] = True
    def save():
        (args.output / "study.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    save()
    cases = args.case or [name for name in OPTIONS if not args.queue_study or name != "direct"]
    for name in (() if args.sweep_only else cases):
        mode, rate, burst = OPTIONS[name]
        case = evidence["cases"][name] = {"observations": []}
        for i in range(8):
            order = treatments if i % 2 == 0 else treatments[::-1]
            pair = {treatment: run(binary, mode, rate, burst, treatment, i, args.pair_events, 20000) for treatment in order}
            if pair[treatments[0]]["result"]["input_id"] != pair[treatments[1]]["result"]["input_id"]:
                raise RuntimeError("paired workloads differ")
            case["observations"].append([pair[treatments[0]], pair[treatments[1]]])
            save()
        case["summary"] = summarize(case["observations"])
        save()
        print(name, case["summary"]["all_full_coverage"], flush=True)
    for rate in (() if args.skip_sweep else (args.sweep_rate or SWEEP)):
        for burst in (1, 32):
            for i in range(args.sweep_repeats):
                order = treatments if i % 2 == 0 else treatments[::-1]
                for treatment in order:
                    trace = None
                    if args.trace_sweep:
                        trace = args.output / f"{treatment}-{rate}-burst{burst}-seed{args.seed_base + i}.csv"
                    evidence["sweep"].append(run(binary, "feed", rate, burst, treatment, i, args.sweep_events, 20000, trace))
                    save()
        print("sweep", rate, flush=True)


if __name__ == "__main__":
    main()
