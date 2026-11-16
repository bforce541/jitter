"""Exercise the operator command and replay its original records in a new database."""

import csv
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

tools = Path(__file__).resolve().parents[1] / "tools"
with TemporaryDirectory() as root:
    output = Path(root) / "incident"
    command = [sys.executable, str(tools / "desk_incident.py"), "--binary", sys.argv[1],
               "--output", str(output)]
    subprocess.run(command, check=True)
    report = json.loads((output / "incident.json").read_text())
    market, desk = report["market"], report["desk"]
    assert market["book_verified"] and desk["verified"] and desk["idempotent"]
    proof = market["recovery_proof"]
    assert (proof["snapshot_through"], proof["last_verified"]) == (5, 7)
    assert proof["book_id"] == proof["reference_book_id"]
    assert market["final_book_id"] == market["reference_book_id"]
    assert market["feed_b_publications"] == 1 and market["duplicate_publications"] == 0
    assert [r["sequence"] for r in market["publications"]] == [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12]
    assert [r["sequence"] for r in market["publications"] if r["replayed"]] == [6, 7]
    timeline = market["timeline"]
    assert [r["elapsed_ns"] for r in timeline] == sorted(r["elapsed_ns"] for r in timeline)
    assert {"stale", "waiting-gap", "recovering", "current"} == {r["state"] for r in timeline}
    with (output / "discrepancies.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["status"] for row in rows] == ["pending", "confirmed", "resolved"]
    assert [row["desk_minus_venue_quantity"] for row in rows] == ["-30", "-30", "0"]
    assert "Desk incident replay" in (output / "report.html").read_text()
    assert subprocess.run(command, capture_output=True).returncode == 1

    fresh = Path(root) / "rebuilt.sqlite"
    reconcile = [sys.executable, str(tools / "desk_reconcile.py"), "--database", str(fresh),
                 "--grace-ns", "50000000", "--csv", str(Path(root) / "rebuilt.csv")]
    def apply(now, *names):
        args = [item for name in names for item in ("--input", str(output / name))]
        return json.loads(subprocess.check_output([*reconcile, "--as-of-ns", str(now), *args]))
    apply(40_000_000, "desk-bookings.csv", "venue-initial.csv")
    apply(80_000_000)
    result = apply(120_000_000, "venue-late.csv")
    assert result == desk["result"]
