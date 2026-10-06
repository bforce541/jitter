#!/usr/bin/env python3
"""Jitter's Phase 1 baseline interface."""

import argparse
import json
from pathlib import Path
import sys
import tarfile

from jitter_runner.overhead import run_overhead, verify_overhead
from jitter_runner.diagnostics import diagnose
from jitter_runner.metrics import analyze_run
from jitter_runner.workload import generate
from jitter_runner.spec import load_json

from jitter_runner.runner import run_experiment, verify_bundle
from jitter_runner.spec import ExperimentError, load_spec


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run and reproduce supported C++ baseline experiments.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="build and measure an experiment specification")
    run.add_argument("spec", type=Path)
    run.add_argument("--output", type=Path, help="new result directory; existing paths are never overwritten")
    run.add_argument("--build-dir", type=Path)
    run.add_argument("--binary", type=Path, help="use an existing compatible executable instead of building")
    overhead = subcommands.add_parser("overhead", help="alternate tracing off/on under identical inputs and executable")
    overhead.add_argument("spec", type=Path)
    overhead.add_argument("--output", type=Path)
    overhead.add_argument("--binary", type=Path)
    overhead.add_argument("--build-dir", type=Path)
    reproduce = subcommands.add_parser("reproduce", help="rerun saved inputs using the saved executable")
    reproduce.add_argument("bundle", type=Path)
    reproduce.add_argument("--output", type=Path)
    diagnostic = subcommands.add_parser("diagnose", help="inspect hypotheses or a measurement request's dependency evidence")
    diagnostic.add_argument("bundle", type=Path)
    diagnostic.add_argument("--run", type=int, default=1)
    diagnostic.add_argument("--request", type=int)
    for name in ("verify", "summarize"):
        command = subcommands.add_parser(name, help="check artifact integrity and validate raw evidence" if name == "verify" else "validate and print saved measurements")
        command.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            if args.binary and args.build_dir:
                raise ExperimentError("--binary and --build-dir are mutually exclusive")
            path, _ = run_experiment(load_spec(args.spec), args.output, args.binary, args.build_dir)
            print(f"Verified results: {path}")
        elif args.command == "overhead":
            if args.binary and args.build_dir:
                raise ExperimentError("--binary and --build-dir are mutually exclusive")
            path, report = run_overhead(load_spec(args.spec), args.output, args.binary, args.build_dir)
            print(f"Verified overhead study: {path}; all pairs comparable: {report['all_pairs_comparable']}")
        elif args.command == "reproduce":
            if (args.bundle / "overhead-spec.json").exists():
                spec, _ = verify_overhead(args.bundle)
                path, _ = run_overhead(spec, args.output, reproduce_from=args.bundle)
            else:
                spec, _, _ = verify_bundle(args.bundle)
                path, _ = run_experiment(spec, args.output, reproduce_from=args.bundle)
            print(f"Verified reproduction: {path}")
        elif args.command == "diagnose":
            spec, _, _ = verify_bundle(args.bundle)
            if spec["schema_version"] != 2 or not spec["instrumentation"]["enabled"]:
                raise ExperimentError("diagnosis requires an enabled schema-version-2 trace")
            if not 1 <= args.run <= spec["repetitions"]:
                raise ExperimentError("selected run is outside the bundle")
            run_dir = args.bundle / f"run-{args.run:03d}"
            if args.request is None:
                report = load_json(run_dir / "diagnostics.json")
                report = {key: report[key] for key in ("status", "trace_quality", "hypotheses", "limitations")}
            else:
                report = diagnose(spec, run_dir, analyze_run(spec, generate(spec), run_dir), args.request)
                report = {key: report[key] for key in ("status", "trace_quality", "tail_requests", "graph", "blocking_holders", "limitations")}
            print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        else:
            if (args.bundle / "overhead-spec.json").exists():
                _, summary = verify_overhead(args.bundle)
            else:
                _, summary, _ = verify_bundle(args.bundle)
            if args.command == "summarize":
                print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
            else:
                print(f"Bundle integrity, request correctness, accounting, and summaries verified: {args.bundle.resolve()}")
        return 0
    except (ExperimentError, OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"jitter: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
