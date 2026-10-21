"""Synthetic calibration is repeatable, honest, and cannot silently be altered."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.calibration import _pairs, _wilson, calibration_report, run_calibration, verify_calibration
from jitter_runner.spec import ExperimentError, load_json
from jitter_runner.storage import seal_bundle, write_json


class CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = calibration_report(seed=42, trials=128)

    def test_repeatable_seeded_report_has_no_timing_or_real_measurement_claims(self):
        report = calibration_report(seed=42, trials=128)
        self.assertEqual(report, self.report)
        self.assertIn("synthetic", report["evidence_kind"])
        self.assertIn("no application", report["evidence_kind"])
        self.assertNotIn("seconds", json.dumps(report))
        json.dumps(report, allow_nan=False)

    def test_analytical_practical_boundary_null_matches_exact_binomial_result(self):
        exact = self.report["analytical_boundary_null"]
        self.assertEqual(exact["one_sided_order_rank"], 2)
        self.assertEqual(exact["required_strict_margin_wins"], 7)
        self.assertEqual(exact["accepted_sign_patterns"], 9)
        self.assertEqual(exact["equiprobable_sign_patterns"], 256)
        self.assertEqual(exact["false_acceptance_probability"], 9 / 256)

    def test_independent_holdout_reduces_selection_bias_in_fixed_simulation(self):
        selection = self.report["winner_selection"]
        fresh = selection["fresh_holdout"]["accepted"]
        reused = selection["unsafe_reused_exploration"]["accepted"]
        self.assertGreater(selection["selected"], 100)
        self.assertGreater(reused, fresh)
        self.assertFalse(selection["unsafe_baseline_is_production_behavior"])
        self.assertEqual(selection["fresh_holdout"], self.report["scenarios"]["winner_curse_null"])

    def test_negative_controls_are_never_accepted(self):
        for scenario, expected in (("regression", "regression"),
                                   ("no_meaningful_difference", "no_meaningful_difference"),
                                   ("constraint_violation", "constraint_violation"),
                                   ("insufficient_tail", "inconclusive"), ("host_drift", "inconclusive")):
            result = self.report["scenarios"][scenario]
            self.assertEqual(result["accepted"], 0, scenario)
            self.assertEqual(result["outcomes"], {expected: 128}, scenario)
        self.assertEqual(self.report["scenarios"]["true_improvement"]["accepted"], 128)

    def test_holdout_random_stream_does_not_depend_on_exploratory_effort(self):
        before = _pairs(42, "winner_curse_null", 1, "confirmation")
        for candidate in range(20):
            _pairs(42, "winner_curse_null", 1, f"exploration-{candidate}")
        after = _pairs(42, "winner_curse_null", 1, "confirmation")
        self.assertEqual(before, after)
        self.assertNotEqual(before, _pairs(42, "winner_curse_null", 1, "exploration-0"))

    def test_wilson_interval_has_width_at_zero_and_one_observed_rate(self):
        zero, one = _wilson(0, 100), _wilson(100, 100)
        self.assertEqual(zero["lower"], 0)
        self.assertGreater(zero["upper"], 0)
        self.assertEqual(one["upper"], 1)
        self.assertLess(one["lower"], 1)
        self.assertAlmostEqual(zero["upper"], 1 - one["lower"])
        self.assertIn("Monte Carlo", zero["method"])
        self.assertIn("not an application", zero["scope"])

    def test_report_mutation_cannot_change_generator_parameters(self):
        first = calibration_report(1, 1)
        first["generator_parameters"]["boundary_null_effects"][0] = 100
        self.assertEqual(calibration_report(1, 1)["generator_parameters"]["boundary_null_effects"], [-.05, .15])

    def test_save_verify_and_resealed_semantic_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, original = run_calibration(Path(temporary) / "calibration", seed=123, trials=5)
            self.assertEqual(verify_calibration(path), original)
            saved = load_json(path / "calibration.json")
            saved["scenarios"]["boundary_null"]["accepted"] += 1
            write_json(path / "calibration.json", saved)
            seal_bundle(path, "complete")
            with self.assertRaisesRegex(ExperimentError, "deterministic synthetic evidence"):
                verify_calibration(path)

    def test_invalid_inputs_fail_before_output_is_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            for index, (seed, trials) in enumerate(((True, 5), (-1, 5), (1, 0), (1, 10001), (1, False))):
                path = Path(temporary) / str(index)
                with self.assertRaises(ExperimentError):
                    run_calibration(path, seed=seed, trials=trials)
                self.assertFalse(path.exists())

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "existing"
            path.mkdir()
            (path / "keep").write_text("preserved")
            with self.assertRaises(FileExistsError):
                run_calibration(path, trials=1)
            self.assertEqual((path / "keep").read_text(), "preserved")


if __name__ == "__main__":
    unittest.main()
