"""Run-level inference controls, including enumerated finite null outcomes."""

from itertools import product
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.spec import ExperimentError
from jitter_runner.statistics import compare_pairs, median_bounds


def policy():
    return {"metric": "p99", "min_effect": .05, "confidence": .95,
            "max_throughput_loss": .05, "min_tail_observations": 20,
            "max_baseline_spread": 3.0}


def run(latency, completed=2000):
    return {"correctness": "passed", "feasible": True,
            "constraints": {"throughput": True, "cpu": True, "memory": True,
                            "rejections": True, "scheduler_lag": True, "latency_observed": True},
            "phases": {"measurement": {"offered": completed, "completed": completed, "rejected": 0, "errors": 0,
                "latency_ns": {"count": completed, "p50": latency / 2, "p95": latency * .9,
                               "p99": latency, "p999": latency * 1.1},
                "throughput_per_second": 1000, "cpu_cores": 1.0, "peak_rss_bytes": 1000000}}}


def pair(improvement=.2, completed=2000):
    return {"baseline": run(10000, completed), "candidate": run(10000 * (1 - improvement), completed),
            "baseline_host": {"system": "test", "machine": "example"},
            "candidate_host": {"system": "test", "machine": "example"}}


class PairedStatisticsTests(unittest.TestCase):
    def test_warmup_loss_cannot_confirm_a_measurement_only_win(self):
        data = [pair(.8) for _ in range(8)]
        data[0]["candidate"]["phases"]["warmup"] = {"offered":10,"completed":9,"rejected":1,"errors":0}
        current = compare_pairs(data, policy(), confirmation=True)
        self.assertEqual(current["schema_version"], 2)
        self.assertEqual(current["status"], "invalid")
        self.assertTrue(any("warmup" in reason for reason in current["reasons"]))
        legacy = compare_pairs(data, policy(), confirmation=True, version=1)
        self.assertEqual(legacy["schema_version"], 1)
        self.assertTrue(legacy["accepted"])

    def test_exact_one_sided_order_statistic_calculation(self):
        bounds = median_bounds(list(range(8)), .95)
        self.assertEqual(bounds["order_statistic_rank"], 2)
        self.assertEqual(bounds["lower_bound"], 1)
        self.assertEqual(bounds["upper_bound"], 6)
        self.assertEqual(bounds["achieved_one_sided_confidence"], 247 / 256)
        self.assertTrue(bounds["bounds_are_separately_one_sided"])
        bounds = median_bounds(list(range(12)), .95)
        self.assertEqual(bounds["order_statistic_rank"], 3)
        self.assertEqual(bounds["achieved_one_sided_confidence"], 4017 / 4096)

    def test_unsupported_bound_is_explicitly_unbounded(self):
        bounds = median_bounds([1, 2, 3, 4], .95)
        self.assertIsNone(bounds["lower_bound"])
        self.assertIsNone(bounds["upper_bound"])
        self.assertIsNone(bounds["achieved_one_sided_confidence"])
        self.assertIsNone(median_bounds([], .95)["lower_bound"])

    def test_confirmation_is_required_for_acceptance(self):
        data = [pair() for _ in range(8)]
        exploratory = compare_pairs(data, policy())
        self.assertEqual(exploratory["status"], "promising")
        self.assertFalse(exploratory["accepted"])
        confirmed = compare_pairs(data, policy(), confirmation=True)
        self.assertEqual(confirmed["status"], "accepted")
        self.assertAlmostEqual(confirmed["effect"]["median_fraction"], .2)
        self.assertAlmostEqual(confirmed["effect"]["median_absolute_ns"], 2000)
        self.assertEqual(confirmed["uncertainty"]["sign_test_p_at_margin"], 1 / 256)

    def test_lucky_single_run_and_seven_pairs_cannot_confirm(self):
        for count in (1, 7):
            result = compare_pairs([pair(.8) for _ in range(count)], policy(), confirmation=True)
            self.assertEqual(result["status"], "inconclusive")
            self.assertFalse(result["accepted"])
            self.assertTrue(result["tail_support"]["sufficient"])

    def test_tail_sufficiency_has_exact_integer_boundaries(self):
        for metric, required in (("p50", 40), ("p95", 400), ("p99", 2000), ("p999", 20000)):
            config = {**policy(), "metric": metric}
            small = compare_pairs([pair(completed=required - 1) for _ in range(8)], config, confirmation=True)
            enough = compare_pairs([pair(completed=required) for _ in range(8)], config, confirmation=True)
            self.assertEqual(small["status"], "inconclusive", metric)
            self.assertFalse(small["tail_support"]["sufficient"])
            self.assertEqual(enough["tail_support"]["required_completed_per_run"], required)
            self.assertTrue(enough["accepted"], metric)

    def test_population_median_practical_margin_null_false_positives(self):
        # Every sequence of eight independent equiprobable effects {0,.1}.
        # Their null population has median .05, exactly the practical margin.
        # Seven or eight successes pass: (8+1)/256 <= the declared .05 error.
        accepted = 0
        for sequence in product((0, .1), repeat=8):
            result = compare_pairs([pair(effect) for effect in sequence], policy(), confirmation=True)
            accepted += result["accepted"]
        self.assertEqual(accepted, 9)
        self.assertLessEqual(accepted / 256, 1 - policy()["confidence"])

    def test_noisy_favorable_median_cannot_become_accepted(self):
        result = compare_pairs([pair(effect) for effect in (.3, .2, .2, .1, .1, -.1, -.2, -.3)], policy(), confirmation=True)
        self.assertGreater(result["effect"]["median_fraction"], policy()["min_effect"])
        self.assertEqual(result["status"], "inconclusive")

    def test_flat_null_and_clear_regression_have_distinct_outcomes(self):
        same = compare_pairs([pair(0) for _ in range(8)], policy(), confirmation=True)
        worse = compare_pairs([pair(-.2) for _ in range(8)], policy(), confirmation=True)
        self.assertEqual(same["status"], "no_meaningful_difference")
        self.assertEqual(worse["status"], "regression")
        self.assertFalse(worse["accepted"])

    def test_exact_practical_margin_is_not_strictly_better_after_rounding(self):
        result = compare_pairs([pair(.05) for _ in range(8)], policy(), confirmation=True)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "no_meaningful_difference")
        self.assertEqual(result["uncertainty"]["sign_test_p_at_margin"], 1)

    def test_exact_throughput_tolerance_is_permitted_after_rounding(self):
        data = [pair() for _ in range(8)]
        for entry in data:
            entry["candidate"]["phases"]["measurement"]["throughput_per_second"] = 950
        result = compare_pairs(data, policy(), confirmation=True)
        self.assertTrue(result["accepted"])

    def test_many_requests_do_not_create_more_independent_replications(self):
        small = compare_pairs([pair() for _ in range(8)], policy(), confirmation=True)
        large = compare_pairs([pair(completed=1000000) for _ in range(8)], policy(), confirmation=True)
        self.assertEqual(small["uncertainty"], large["uncertainty"])

    def test_every_observed_constraint_dominates_the_latency_effect(self):
        for side in ("baseline", "candidate"):
            data = [pair() for _ in range(8)]
            data[-1][side]["feasible"] = False
            data[-1][side]["constraints"]["cpu"] = False
            result = compare_pairs(data, policy(), confirmation=True)
            self.assertEqual(result["status"], "constraint_violation")
            self.assertFalse(result["accepted"])

    def test_one_relative_throughput_regression_vetoes_acceptance(self):
        data = [pair() for _ in range(8)]
        data[0]["candidate"]["phases"]["measurement"]["throughput_per_second"] = 940
        result = compare_pairs(data, policy(), confirmation=True)
        self.assertEqual(result["status"], "constraint_violation")
        self.assertIn("relative throughput", " ".join(result["reasons"]))

    def test_correctness_failure_is_invalid_and_is_never_filtered_out(self):
        data = [pair() for _ in range(8)]
        data[-1]["candidate"]["correctness"] = "failed"
        result = compare_pairs(data, policy(), confirmation=True)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["pairs_requested"], 8)
        self.assertEqual(result["pairs_valid"], 7)
        self.assertEqual(len(result["pairs"]), 8)

    def test_rejected_work_errors_and_changed_coverage_are_invalid(self):
        for field, value in (("rejected", 1), ("errors", 1), ("offered", 2001)):
            data = [pair() for _ in range(8)]
            data[0]["candidate"]["phases"]["measurement"][field] = value
            result = compare_pairs(data, policy(), confirmation=True)
            self.assertEqual(result["status"], "invalid", field)

    def test_environment_drift_or_missing_identity_prevents_acceptance(self):
        data = [pair() for _ in range(8)]
        data[-1]["candidate_host"]["machine"] = "other"
        self.assertEqual(compare_pairs(data, policy(), confirmation=True)["status"], "inconclusive")
        data[-1]["candidate_host"] = {}
        self.assertEqual(compare_pairs(data, policy(), confirmation=True)["status"], "inconclusive")

    def test_lost_observations_or_changed_instrumentation_prevent_acceptance(self):
        data = [pair() for _ in range(8)]
        for entry in data:
            for side in ("baseline", "candidate"):
                entry[side]["observation"] = {"enabled": True, "complete": True, "dropped": 0}
        data[0]["candidate"]["observation"].update(complete=False, dropped=1)
        self.assertEqual(compare_pairs(data, policy(), confirmation=True)["status"], "inconclusive")
        data[0]["candidate"]["observation"].update(enabled=False, complete=True, dropped=0)
        self.assertEqual(compare_pairs(data, policy(), confirmation=True)["status"], "inconclusive")

    def test_unstable_baseline_prevents_acceptance(self):
        data = [pair() for _ in range(8)]
        data[-1]["baseline"] = run(40000)
        data[-1]["candidate"] = run(32000)
        result = compare_pairs(data, policy(), confirmation=True)
        self.assertEqual(result["baseline_spread"], 4)
        self.assertEqual(result["status"], "inconclusive")

    def test_empty_or_malformed_evidence_does_not_crash_or_accept(self):
        self.assertEqual(compare_pairs([], policy(), confirmation=True)["status"], "inconclusive")
        for malformed in ({}, None, {"baseline": "oops", "candidate": {}}):
            result = compare_pairs([malformed], policy(), confirmation=True)
            self.assertEqual(result["status"], "invalid")
            self.assertFalse(result["accepted"])

    def test_nonfinite_outputs_are_invalid_and_report_is_json_safe(self):
        for value in (float("nan"), float("inf"), -1, True, None, 10 ** 1000):
            data = [pair() for _ in range(8)]
            data[0]["candidate"]["phases"]["measurement"]["latency_ns"]["p99"] = value
            result = compare_pairs(data, policy(), confirmation=True)
            self.assertEqual(result["status"], "invalid")
            json.dumps(result, allow_nan=False)

    def test_nonprimary_metrics_and_dispersion_are_reported_descriptively(self):
        result = compare_pairs([pair() for _ in range(8)], policy())
        for metric in ("p50", "p95", "p99", "p999", "throughput_per_second", "cpu_cores", "peak_rss_bytes"):
            self.assertEqual(result["metrics"][metric]["baseline"]["count"], 8)
            self.assertIn("median_absolute_deviation", result["metrics"][metric]["baseline"])
        self.assertFalse(result["metrics"]["p99"]["descriptive_only"])
        self.assertTrue(result["metrics"]["p999"]["descriptive_only"])
        self.assertFalse(result["metrics"]["p999"]["tail_support"]["sufficient"])

    def test_invalid_policies_are_explicit_errors(self):
        for key, value in (("metric", "p100"), ("metric", []), ("confidence", 1), ("confidence", float("nan")),
                           ("min_effect", -.1), ("max_throughput_loss", True),
                           ("min_tail_observations", 0), ("max_baseline_spread", .5)):
            with self.assertRaises(ExperimentError, msg=key):
                compare_pairs([pair()], {**policy(), key: value})
        with self.assertRaises(ExperimentError):
            median_bounds([math.inf], .95)


if __name__ == "__main__":
    unittest.main()
