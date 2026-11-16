#!/usr/bin/env python3
"""Run and retain paired local receiver measurements."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def capture(command):
    execution = subprocess.run(command, capture_output=True, text=True, timeout=120)
    row = {"argv": command, "exit_code": execution.returncode, "stderr": execution.stderr}
    try:
        row["result"] = json.loads(execution.stdout)
    except json.JSONDecodeError:
        row["result"] = None
        row["stdout"] = execution.stdout
    return row


def system(*command):
    return subprocess.check_output(command, cwd=ROOT, text=True).strip()


def study(args):
    binary = str(args.binary.resolve(strict=True))
    args.output.mkdir(parents=True, exist_ok=False)
    path = args.output / "study.json"
    report = {
        "schema": 1, "host": platform.uname()._asdict(), "python": platform.python_version(),
        "checkout_revision": system("git", "rev-parse", "HEAD"),
        "checkout_dirty": bool(system("git", "status", "--porcelain")),
        "binary_sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
        "configuration": {"rate": args.rate, "events": args.events, "repeats": args.repeats,
                          "steady_bursts": [1, 32], "stress_burst": 4096, "first_seed": 17},
        "faults": [], "steady_burst": [], "drain_comparison": [],
    }
    if args.compile_commands:
        commands = json.loads(args.compile_commands.read_text(encoding="utf-8"))
        report["compile_commands"] = [cmd for cmd in commands if Path(cmd["file"]).name in {"market.cpp", "incident.cpp"}]
    if platform.system() == "Darwin":
        report["cpu"] = system("sysctl", "-n", "machdep.cpu.brand_string")
        report["os_release"] = system("sw_vers", "-productVersion")
    elif platform.system() == "Linux":
        report["cpu"] = system("lscpu")
    def add(section, command):
        report[section].append(capture([binary, *command]))
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for i in range(args.repeats):
        seed = ["--seed", str(i + 17)]
        feeds = ([], ["--single-feed"]) if i % 2 == 0 else (["--single-feed"], [])
        for feed in feeds:
            add("faults", [*seed, *feed])
        common = ["--benchmark", "--events", str(args.events), "--rate", str(args.rate), *seed]
        for burst in (1, 32):
            for feed in feeds:
                add("steady_burst", [*common, "--burst", str(burst), *feed])
        intervals = ("4096", "32") if i % 2 == 0 else ("32", "4096")
        for interval in intervals:
            add("drain_comparison", [*common, "--burst", "4096", "--drain-every", interval])
    failures = sum(row["exit_code"] != 0 or not row["result"] or not row["result"]["book_verified"]
                   for key in ("faults", "steady_burst", "drain_comparison") for row in report[key])
    print(f"Retained {8 * args.repeats} runs ({failures} unverified) in {path}")
    return 2 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compile-commands", type=Path)
    parser.add_argument("--events", type=int, default=40000, metavar="20000..1000000")
    parser.add_argument("--rate", type=int, default=100000, metavar="1..1000000")
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 9))
    args = parser.parse_args()
    if not 20000 <= args.events <= 1000000 or not 1 <= args.rate <= 1000000:
        parser.error("events must be 20000..1000000 and rate 1..1000000")
    if args.events / args.rate > 90:
        parser.error("nominal run time must fit within 90 seconds")
    return study(args)


if __name__ == "__main__":
    raise SystemExit(main())
