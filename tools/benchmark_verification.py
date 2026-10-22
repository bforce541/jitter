#!/usr/bin/env python3
"""Measure full validation cost on identical evidence; never run an application."""

import argparse
import json
import statistics
import time
from pathlib import Path

from jitter_runner.investigation import verify_investigation
from jitter_runner.storage import sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    identity = sha256(args.bundle / "manifest.json")
    expected = verify_investigation(args.bundle)[1]
    samples = []
    for pair in range(3):
        for mode in (("uncached","cached") if pair % 2 == 0 else ("cached","uncached")):
            before, cpu = time.perf_counter(), time.process_time()
            result = verify_investigation(args.bundle, archive_cache=False if mode == "uncached" else None)[1]
            samples.append({"pair":pair+1,"mode":mode,"seconds":time.perf_counter()-before,
                            "cpu_seconds":time.process_time()-cpu})
            if result != expected or sha256(args.bundle / "manifest.json") != identity:
                raise RuntimeError("verification result or evidence changed during the cost comparison")
    summaries = {mode:statistics.median(row["seconds"] for row in samples if row["mode"]==mode) for mode in ("cached","uncached")}
    write_json(args.output, {"schema_version":1,"evidence_kind":"wall/CPU validation cost, not application timing",
        "manifest_sha256":identity,"samples":samples,"median_wall_seconds":summaries,
        "observed_speedup_ratio":summaries["uncached"]/summaries["cached"],
        "result_equal_in_every_run":True,
        "method":"One warmup validation, then three alternating pairs; same immutable raw evidence and semantic checks. Each archive path is hashed; only identical source decompression/inventory work is reused within one pass.",
        "limitations":["One uncontrolled host and three pairs; descriptive cost observations only.",
                       "Page cache and background activity are uncontrolled; no significance or universal speed guarantee is claimed."]})
    print(json.dumps(summaries,sort_keys=True))


if __name__ == "__main__":
    main()
