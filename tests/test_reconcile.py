import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.reconcile import export_csv, reconcile


def row(source, record_id, **changes):
    return {"source": source, "record_id": record_id, "session": "S", "execution_id": "E",
            "revision": 1, "instrument": "ABC", "side": "SELL", "quantity": 30,
            "price_ticks": 1201, "executed_ns": 1, "observed_ns": 2, **changes}


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = pathlib.Path(directory.name)
        self.db = self.path / "test.sqlite"

    def run_batch(self, rows, time):
        return reconcile(self.db, rows, as_of_ns=time, grace_ns=10)

    def test_late_report_restarts_and_repeat(self):
        desk = row("desk", "booking")
        self.assertEqual(self.run_batch([desk], 2)["open"][0]["state"], "pending")
        self.assertEqual(self.run_batch([], 11)["open"][0]["state"], "pending")
        before = self.run_batch([], 12)
        self.assertEqual(before["open"][0]["state"], "confirmed")
        self.assertEqual(before["positions"]["ABC"]["desk_minus_venue"], -30)
        venue = row("venue", "drop", observed_ns=25)
        done = self.run_batch([venue], 25)
        self.assertEqual([x["state"] for x in done["history"]], ["pending", "confirmed", "resolved"])
        self.assertEqual(done["positions"], {"ABC": {"desk": -30, "venue": -30, "desk_minus_venue": 0}})
        self.assertEqual(self.run_batch([desk, venue], 25), done)
        self.assertEqual(self.run_batch([desk], 2), done)
        export_csv(self.path / "operator.csv", done)
        self.assertIn("resolved", (self.path / "operator.csv").read_text())

    def test_revision_duplicate_and_position_by_instrument(self):
        desk = row("desk", "booking")
        venue = row("venue", "drop")
        duplicate = row("venue", "other", observed_ns=3)
        first = self.run_batch([desk, venue, duplicate], 3)
        self.assertEqual(first["open"][0]["discrepancy"], "duplicate-venue")
        self.assertEqual(first["positions"]["ABC"]["desk_minus_venue"], 30)
        correction = row("venue", "fix", observed_ns=4, revision=2, instrument="XYZ", price_ticks=1300)
        second = self.run_batch([correction], 4)
        self.assertEqual(second["open"][0]["discrepancy"], "mismatched-instrument,mismatched-price_ticks")
        self.assertEqual(second["open"][0]["evidence"]["position_impact"], {"ABC": -30, "XYZ": 30})
        restored = self.run_batch([row("venue", "restore", observed_ns=5, revision=3)], 5)
        self.assertEqual(restored["open"], [])
        self.assertEqual(restored["records"], 5)
        self.assertEqual(restored["positions"]["ABC"]["desk_minus_venue"], 0)

    def test_identity_conflict_rolls_back_batch_and_clock(self):
        desk = row("desk", "booking")
        original = self.run_batch([desk], 2)
        with self.assertRaises(ValueError):
            self.run_batch([row("venue", "drop"), {**desk, "quantity": 31}], 15)
        self.assertEqual(self.run_batch([], 2), original)
        with self.assertRaises(ValueError):
            self.run_batch([row("venue", "drop", observed_ns=50)], 49)
        self.assertEqual(self.run_batch([], 2), original)
