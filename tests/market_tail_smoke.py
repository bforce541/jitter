"""Check evidence from the public feed benchmark without imposing timing limits."""

import csv
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from market_tail import analyze

with TemporaryDirectory() as tmp:
    trace = Path(tmp) / "trace.csv"
    cmd = [sys.argv[1], "--mode", "feed", "--book", "dense", "--queue", "spsc",
           "--events", "20000", "--warmup", "0", "--rate", "100000", "--trace", str(trace)]
    run = json.loads(subprocess.check_output(cmd, text=True))
    with trace.open(newline="") as stream:
        rows = csv.reader(stream)
        assert next(rows) == ["sequence", "status", "scheduled_ns", "arrival_ns", "dequeued_ns", "published_ns"]
        result = analyze(rows, run)
    worst = result["worst_publication"]
    if worst:
        assert worst["scheduled_latency_ns"] == worst["producer_lag_ns"] + worst["handoff_ns"] + worst["consumer_ns"]
    assert sum(result["status_counts"].values()) == 20000
    assert subprocess.run(cmd, capture_output=True).returncode == 1
