#!/usr/bin/env python3
"""Jitter command-line interface."""

import argparse
import json
from pathlib import Path
import sys
import tarfile
import shutil
import subprocess
import platform

from .investigation import run_investigation, verify_investigation, markdown_report
from .investigation_spec import load_investigation
from .variants import REGISTRY
from .storage import write_json

from .overhead import run_overhead, verify_overhead
from .diagnostics import diagnose
from .metrics import analyze_run
from .workload import generate
from .spec import load_json

from .runner import run_experiment, verify_bundle
from .spec import ExperimentError, load_spec


def main(argv=None):
    parser = argparse.ArgumentParser(description="Investigate supported C++ latency with validated, reproducible evidence.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    investigation = subcommands.add_parser("investigate", help="adaptively search, compare, and freshly confirm supported changes")
    investigation.add_argument("spec", type=Path)
    investigation.add_argument("--output", type=Path)
    investigation.add_argument("--binary", type=Path, help="reuse a compatible engine; default builds isolated implementation binaries")
    investigation.add_argument("--dry-run", action="store_true", help="validate and print the search space and budget without executing")
    doctor = subcommands.add_parser("doctor", help="check platform and required local tools")
    doctor.add_argument("--json", action="store_true")
    subcommands.add_parser("variants", help="list bounded supported implementation alternatives")
    report = subcommands.add_parser("report", help="validate and print a saved investigation report")
    report.add_argument("bundle", type=Path)
    report.add_argument("--json", action="store_true")
    restart = subcommands.add_parser("restart", help="restart a saved investigation with a new independent confirmation block")
    restart.add_argument("bundle", type=Path)
    restart.add_argument("--output", type=Path)
    evaluate = subcommands.add_parser("evaluate", help="compare strategies and the hypothesis ablation using real investigations")
    evaluate.add_argument("plan", type=Path)
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument("--binary", type=Path)
    calibrate = subcommands.add_parser("calibrate", help="run synthetic statistical calibration and negative controls")
    calibrate.add_argument("--output", type=Path, required=True)
    calibrate.add_argument("--seed", type=int, default=42)
    calibrate.add_argument("--trials", type=int, default=1000)
    pack = subcommands.add_parser("pack", help="verify and deduplicate a portable evidence archive")
    pack.add_argument("bundle", type=Path)
    pack.add_argument("--output", type=Path, required=True)
    unpack = subcommands.add_parser("unpack", help="restore and verify portable evidence without executing it")
    unpack.add_argument("archive", type=Path)
    unpack.add_argument("--output", type=Path, required=True)
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
        if args.command in ("pack", "unpack"):
            from .transport import pack_result, unpack_result
            result = pack_result(args.bundle, args.output) if args.command == "pack" else unpack_result(args.archive, args.output)
            print(f"Verified portable evidence: {result}")
        elif args.command == "doctor":
            tools = {name: shutil.which(name) for name in ("cmake", "git", "cc", "c++")}
            source_ready = (Path(__file__).resolve().parents[2] / "CMakeLists.txt").is_file()
            result = {"system": platform.system(), "python": platform.python_version(), "source_root": str(Path(__file__).resolve().parents[2]), "tools": tools,
                      "source_checkout": source_ready,
                      "ready": source_ready and platform.system() in ("Linux", "Darwin") and all(tools.values()) and sys.version_info >= (3,9)}
            print(json.dumps(result, indent=2) if args.json else "\n".join([f"Jitter environment: {'ready' if result['ready'] else 'missing requirements'}", *[f"{name}: {path or 'not found; install this tool and retry'}" for name,path in tools.items()]]))
            return 0 if result["ready"] else 1
        elif args.command == "variants":
            print(json.dumps(REGISTRY, indent=2, sort_keys=True))
        elif args.command == "investigate":
            spec = load_investigation(args.spec)
            if args.dry_run:
                from .search import normalize_space
                print(json.dumps({"space": normalize_space(spec["experiment"], spec["search"]), "budget": spec["budget"], "sampling": spec["sampling"], "comparison": spec["comparison"]}, indent=2, sort_keys=True))
            else:
                path, report = run_investigation(spec, args.output, binary=args.binary)
                print(f"Investigation: {report['decision']} ({report['stop_reason']}); evidence: {path}; report: {path / 'report.md'}")
                return 1 if report["decision"] in ("invalid_investigation", "interrupted") else 0
        elif args.command in ("report", "restart"):
            spec, report, _ = verify_investigation(args.bundle)
            if args.command == "report":
                print(json.dumps(report, indent=2, sort_keys=True) if args.json else markdown_report(report))
            else:
                path, report = run_investigation(spec, args.output, reproduce_from=args.bundle)
                print(f"Restarted investigation: {report['decision']}; evidence: {path}")
        elif args.command == "evaluate":
            from .evaluation import run_evaluation
            path, report = run_evaluation(load_json(args.plan), args.output, binary=args.binary)
            print(f"Evaluation: {path}; {len(report.get('studies', []))} retained studies")
        elif args.command == "calibrate":
            from .calibration import run_calibration
            path, _ = run_calibration(args.output, seed=args.seed, trials=args.trials)
            print(f"Synthetic statistical calibration: {path}")
        elif args.command == "run":
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
            if (args.bundle / "evaluation-plan.json").exists():
                from .evaluation import run_evaluation, verify_evaluation
                spec, _, _ = verify_evaluation(args.bundle)
                path, _ = run_evaluation(spec, args.output, reproduce_from=args.bundle)
            elif (args.bundle / "investigation-spec.json").exists():
                spec, _, _ = verify_investigation(args.bundle)
                path, _ = run_investigation(spec, args.output, reproduce_from=args.bundle)
            elif (args.bundle / "overhead-spec.json").exists():
                spec, _ = verify_overhead(args.bundle)
                path, _ = run_overhead(spec, args.output, reproduce_from=args.bundle)
            else:
                spec, _, _ = verify_bundle(args.bundle)
                path, _ = run_experiment(spec, args.output, reproduce_from=args.bundle)
            print(f"Verified reproduction: {path}")
        elif args.command == "diagnose":
            spec, _, _ = verify_bundle(args.bundle)
            if spec["schema_version"] < 2 or not spec["instrumentation"]["enabled"]:
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
            if (args.bundle / "investigation-spec.json").exists():
                _, summary, _ = verify_investigation(args.bundle)
            elif (args.bundle / "evaluation-plan.json").exists():
                from .evaluation import verify_evaluation
                verified = verify_evaluation(args.bundle)
                summary = verified[1]
            elif (args.bundle / "calibration.json").exists():
                from .calibration import verify_calibration
                summary = verify_calibration(args.bundle)
            elif (args.bundle / "overhead-spec.json").exists():
                _, summary = verify_overhead(args.bundle)
            else:
                _, summary, _ = verify_bundle(args.bundle)
            if args.command == "summarize":
                print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
            else:
                print(f"Bundle integrity, request correctness, accounting, and summaries verified: {args.bundle.resolve()}")
        return 0
    except (ExperimentError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"jitter: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
