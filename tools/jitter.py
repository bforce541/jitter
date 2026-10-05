#!/usr/bin/env python3
"""Jitter's Phase 1 baseline interface."""

import argparse
import json
from pathlib import Path
import sys

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
    reproduce = subcommands.add_parser("reproduce", help="rerun saved inputs using the saved executable")
    reproduce.add_argument("bundle", type=Path)
    reproduce.add_argument("--output", type=Path)
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
        elif args.command == "reproduce":
            spec, _, _ = verify_bundle(args.bundle)
            path, _ = run_experiment(spec, args.output, reproduce_from=args.bundle)
            print(f"Verified reproduction: {path}")
        else:
            _, summary, _ = verify_bundle(args.bundle)
            if args.command == "summarize":
                print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
            else:
                print(f"Bundle integrity, request correctness, accounting, and summaries verified: {args.bundle.resolve()}")
        return 0
    except (ExperimentError, OSError, ValueError, KeyError) as error:
        print(f"jitter: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
