#!/usr/bin/env python3
"""Exercise the installed/source CLI end-to-end without timing-win assertions."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from jitter_runner.spec import load_json
from jitter_runner.storage import write_json
from jitter_runner.process import run_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=False)
    spec = load_json(root / "examples/investigation.json")
    spec["search"]["parameters"] = {"workers":[2,4],"implementation":["coarse","narrow"]}
    spec["sampling"]["max_exploration_pairs"] = 2
    spec["budget"].update(max_candidates=1,max_runs=21)
    write_json(args.output / "input.json", spec)
    cli = [sys.executable, str(root / "tools/jitter.py")]
    bundle = args.output / "study"
    commands = [["doctor","--json"], ["investigate",str(args.output / "input.json"),"--dry-run"],
                ["investigate",str(args.output / "input.json"),"--binary",str(args.binary.resolve()),"--output",str(bundle)],
                ["verify",str(bundle)], ["report",str(bundle),"--json"],
                ["reproduce",str(bundle),"--output",str(args.output / "replay")],
                ["pack",str(bundle),"--output",str(args.output / "evidence.tar.gz")],
                ["unpack",str(args.output / "evidence.tar.gz"),"--output",str(args.output / "restored")]]
    for command in commands:
        run_command([*cli,*command],check=True,timeout=180)
    report = load_json(bundle / "report.json")
    if report["decision"] in ("invalid_investigation","interrupted"):
        raise RuntimeError("smoke investigation did not produce valid completed evidence")
    print("Complete CLI flow verified; timing outcomes were not used as CI gates.")


if __name__ == "__main__":
    main()
