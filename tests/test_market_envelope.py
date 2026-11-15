import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from market_envelope import make_envelope


def observation(rate, p99, *, published=100000, seed=1, input_id="equal", cpu=1000):
    return {"result": {
        "mode": "feed", "book": "dense", "queue": "mutex", "burst": 1, "offered_rate": rate,
        "events": 100000, "published": published, "processed": published,
        "dropped": 100000 - published, "warmup_dropped": 0, "full_coverage": published == 100000,
        "scheduled_to_publication_ns": {"count": published, "p99": p99, "p999": None},
        "seed": seed, "input_id": input_id, "elapsed_ns": 1000, "feed_phase_cpu_ns": cpu,
        "published_per_second": rate * published / 100000,
        "late_over_100us_processed": 0, "peak_queue_depth": 17,
    }}


class MarketEnvelopeTests(unittest.TestCase):
    def test_one_failed_repeat_breaks_prefix_but_preserves_later_passing_points(self):
        study = {"comparison": {"axis": "queue"}, "sweep": [
            observation(1, 20), observation(2, 20), observation(2, 500, seed=2), observation(3, 20)]}
        shape = make_envelope(study, 100)["series"][0]
        self.assertEqual((shape["passing_prefix_max_rate"], shape["first_failed_rate"],
                          shape["highest_passing_rate"]), (1, 2, 3))
        self.assertEqual(shape["rates"][1]["failures_by_rule"], {"p99": 1})

    def test_drops_missing_tail_and_unknown_cpu_never_pass(self):
        study = {"comparison": {"axis": "queue"}, "sweep": [observation(1, 20, published=50000, cpu=None)]}
        point = make_envelope(study, 100, 1000, .01, 2)["series"][0]["rates"][0]
        self.assertFalse(point["pass"])
        self.assertEqual(point["failures_by_rule"], {"coverage": 1, "p999": 1, "cpu": 1})

    def test_mismatched_treatment_inputs_rejected(self):
        a, b = observation(1, 20), observation(1, 20, input_id="other")
        b["result"]["queue"] = "spsc"
        with self.assertRaisesRegex(ValueError, "different inputs"):
            make_envelope({"comparison": {"axis": "queue"}, "sweep": [a, b]}, 100)

    def test_missing_comparator_does_not_inflate_the_envelope(self):
        study = {"comparison": {"axis": "queue", "reference": "mutex", "candidate": "spsc"},
                 "sweep": [observation(1, 20)]}
        with self.assertRaisesRegex(ValueError, "missing"):
            make_envelope(study, 100)
