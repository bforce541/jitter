"""Evaluation protocol and tamper tests with explicit synthetic child fixtures.

The fixtures test summary accounting and parent verification, not controller
performance; the checked-in evaluation plan executes the real engine separately.
"""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.evaluation import (child_spec, execution_order, make_evaluation_report,
                                      run_evaluation, validate_evaluation, verify_evaluation)
from jitter_runner.runner import ROOT
from jitter_runner.spec import ExperimentError, load_json
from jitter_runner.storage import seal_bundle, write_json


def plan():
    result = load_json(ROOT / "examples/evaluation.json")
    result["scenarios"] = result["scenarios"][:1]
    result["strategies"] = ["guided", "random"]
    return result


def child_report(effect=.2, accepted=False):
    comparison = {"status": "promising", "effect": {"median_fraction": effect},
                  "pairs_valid": 2, "pairs_requested": 2, "tail_support": {"sufficient": True}}
    configuration = {"workers": 4, "queue_capacity": 4096, "implementation": "narrow"}
    confirmation = {"comparison": {"effect": {"median_fraction": .1}, "accepted": True}}
    return {"decision": "accepted_recommendation" if accepted else "inconclusive",
            "stop_reason": "confirmed_improvement" if accepted else "confirmation_not_accepted",
            "recommendation": configuration if accepted else None,
            "confirmation": confirmation if accepted else None,
            "trials": [{"id": "fixture", "configuration": configuration, "comparison": comparison}],
            "cost": {"runs": 24, "builds": 2, "failures": 0, "seconds": .1, "build_seconds": .01}}


def fake_run(spec, output=None, binary=None, reproduce_from=None):
    """Fixture format intentionally cannot pass the real child verifier."""
    directory = Path(output)
    directory.mkdir(parents=True)
    write_json(directory / "investigation-spec.json", spec)
    report = child_report(accepted=spec["search"]["strategy"] == "guided")
    write_json(directory / "report.json", report)
    return directory, report


def fake_verify(path):
    directory = Path(path)
    return load_json(directory / "investigation-spec.json"), load_json(directory / "report.json"), {}


class EvaluationTests(unittest.TestCase):
    def test_checked_in_plan_has_real_adapter_scenarios_and_all_baselines(self):
        config = validate_evaluation(load_json(ROOT / "examples/evaluation.json"))
        self.assertEqual(config["strategies"], ["guided", "random", "grid", "local", "unguided"])
        self.assertEqual({row["name"] for row in config["scenarios"]}, {"shared-contention", "json-small", "json-large"})
        self.assertEqual(len(execution_order(config)), 15)
        for scenario in config["scenarios"]:
            exp = scenario["investigation"]["experiment"]
            self.assertEqual(exp["workload"]["rate_per_second"] * exp["workload"]["measurement_ms"] // 1000, 2000)

    def test_child_changes_only_search_controls_and_unguided_diagnostic(self):
        config = load_json(ROOT / "examples/evaluation.json")
        for scenario in config["scenarios"]:
            for strategy in config["strategies"]:
                expected = copy.deepcopy(scenario["investigation"])
                expected["search"].update(strategy="guided" if strategy == "unguided" else strategy, seed=37)
                expected["diagnostic"] = strategy != "unguided"
                actual = child_spec(scenario, 37, strategy)
                self.assertEqual(actual, expected)
                self.assertEqual(actual["budget"], scenario["investigation"]["budget"])
                self.assertEqual(actual["sampling"], scenario["investigation"]["sampling"])
        self.assertEqual(config, load_json(ROOT / "examples/evaluation.json"))

    def test_invalid_duplicate_unbounded_and_unsafe_plans_are_rejected(self):
        bad_plans = []
        for key, value in (("seeds", []), ("seeds", [True]), ("seeds", [1, 1]),
                           ("strategies", []), ("strategies", ["guided", "guided"]),
                           ("strategies", ["unknown"]), ("strategies", [["guided"]]),
                           ("scenarios", []), ("schema_version", True)):
            bad_plans.append({**plan(), key: value})
        for name in ("../escape", "Absolute", "has space", "a" * 49, ""):
            config = plan()
            config["scenarios"][0]["name"] = name
            bad_plans.append(config)
        config = plan()
        config["scenarios"] *= 2
        bad_plans.append(config)
        config = plan()
        config["scenarios"][0]["investigation"]["diagnostic"] = False
        bad_plans.append(config)
        for config in bad_plans:
            with self.assertRaises(ExperimentError, msg=str(config)):
                validate_evaluation(config)

    def test_order_is_reproducible_and_contains_each_planned_study_once(self):
        config = plan()
        config["seeds"] = [1, 2, 3]
        config["strategies"] = ["guided", "random", "grid", "local", "unguided"]
        ordered = execution_order(config)
        self.assertEqual(ordered, execution_order(copy.deepcopy(config)))
        self.assertEqual(len({row["path"] for row in ordered}), 15)
        self.assertEqual({(row["seed"], row["strategy"]) for row in ordered},
                         {(seed, strategy) for seed in config["seeds"] for strategy in config["strategies"]})

    def test_exploratory_minimum_never_becomes_a_confirmed_recommendation(self):
        config = plan()
        identities = execution_order(config)
        state = {"status": "complete", "records": [{**row, "status": "complete", "wall_seconds": 1} for row in identities]}
        reports = {row["path"]: child_report(effect=.8, accepted=False) for row in identities}
        summary = make_evaluation_report(config, state, reports, {row["path"]: 100 for row in identities})
        self.assertEqual(summary["totals"]["confirmed_recommendations"], 0)
        for row in summary["studies"]:
            self.assertIsNone(row["recommendation"])
            self.assertIsNone(row["confirmed_improvement_fraction"])
            self.assertEqual(row["best_exploratory"]["median_fraction"], .8)
            self.assertFalse(row["best_exploratory"]["is_recommendation"])

    def test_invalid_incomplete_and_negative_exploratory_points_are_not_selected(self):
        config = plan()
        identity = execution_order(config)[0]
        state = {"status": "complete", "records": [{**identity, "status": "complete", "wall_seconds": 1}]}
        for change in ({"status": "inconclusive"}, {"pairs_valid": 1}, {"tail_support": {"sufficient": False}},
                       {"effect": {"median_fraction": -.1}}):
            report = child_report()
            report["trials"][0]["comparison"].update(change)
            summary = make_evaluation_report(config, state, {identity["path"]: report}, {identity["path"]: 100})
            self.assertIsNone(summary["studies"][0]["best_exploratory"])

    @patch("jitter_runner.evaluation.verify_investigation", side_effect=fake_verify)
    @patch("jitter_runner.evaluation.run_investigation", side_effect=fake_run)
    def test_wrapper_preserves_costs_and_recomputes_after_resealing_tamper(self, run, verify):
        with tempfile.TemporaryDirectory() as temporary:
            root, report = run_evaluation(plan(), Path(temporary) / "evaluation")
            self.assertEqual(report["totals"]["planned_studies"], 2)
            self.assertEqual(report["totals"]["confirmed_recommendations"], 1)
            self.assertEqual(verify_evaluation(root)[1], report)
            self.assertEqual(sum(row["runs"] for row in report["groups"]), 48)
            changed = load_json(root / "evaluation-report.json")
            changed["totals"]["confirmed_recommendations"] = 2
            write_json(root / "evaluation-report.json", changed)
            seal_bundle(root, "complete")
            with self.assertRaisesRegex(ExperimentError, "report differs"):
                verify_evaluation(root)

    @patch("jitter_runner.evaluation.verify_investigation", side_effect=fake_verify)
    @patch("jitter_runner.evaluation.run_investigation", side_effect=fake_run)
    def test_child_cannot_change_budget_even_after_parent_is_resealed(self, run, verify):
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = run_evaluation(plan(), Path(temporary) / "evaluation")
            child = root / execution_order(plan())[0]["path"] / "investigation-spec.json"
            changed = load_json(child)
            changed["budget"]["max_runs"] += 1
            write_json(child, changed)
            seal_bundle(root, "complete")
            with self.assertRaisesRegex(ExperimentError, "expected investigation"):
                verify_evaluation(root)

    @patch("jitter_runner.evaluation.verify_investigation", side_effect=fake_verify)
    def test_wrapper_failure_remains_in_denominator_and_retains_partial_files(self, verify):
        def failing(spec, output=None, binary=None):
            if spec["search"]["strategy"] == "random":
                directory = Path(output)
                directory.mkdir(parents=True)
                write_json(directory / "investigation-spec.json", spec)
                (directory / "failure.log").write_text("fixture engine failed")
                raise RuntimeError("fixture engine failed")
            return fake_run(spec, output, binary)
        with tempfile.TemporaryDirectory() as temporary, patch("jitter_runner.evaluation.run_investigation", side_effect=failing):
            root, report = run_evaluation(plan(), Path(temporary) / "evaluation")
            self.assertEqual(report["totals"]["planned_studies"], 2)
            self.assertEqual(report["totals"]["failed_studies"], 1)
            failed = next(row for row in report["studies"] if row["status"] == "failed")
            self.assertIn("fixture engine failed", failed["failure"])
            self.assertGreater(failed["storage_bytes"], 0)
            self.assertIsNone(failed["cost"])
            self.assertEqual(next(row for row in report["groups"] if row["strategy"] == "random")["cost_unknown_studies"], 1)
            self.assertTrue((root / failed["path"] / "failure.log").exists())
            verify_evaluation(root)

    @patch("jitter_runner.evaluation.run_investigation", side_effect=KeyboardInterrupt)
    def test_interruption_retains_unexecuted_studies_without_claiming_completion(self, run):
        with tempfile.TemporaryDirectory() as temporary:
            root, report = run_evaluation(plan(), Path(temporary) / "evaluation")
            self.assertEqual(report["status"], "interrupted")
            self.assertEqual(report["totals"]["interrupted_studies"], 1)
            self.assertEqual(report["totals"]["not_run_studies"], 1)
            self.assertEqual(report["totals"]["confirmed_recommendations"], 0)
            verify_evaluation(root)

    @patch("jitter_runner.evaluation.verify_investigation", side_effect=fake_verify)
    @patch("jitter_runner.evaluation.run_investigation", side_effect=fake_run)
    def test_replay_requires_unchanged_plan_and_passes_saved_children(self, run, verify):
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = run_evaluation(plan(), Path(temporary) / "original")
            changed = plan()
            changed["seeds"] = [2]
            with self.assertRaisesRegex(ExperimentError, "changed its original plan"):
                run_evaluation(changed, Path(temporary) / "changed", reproduce_from=root)
            self.assertFalse((Path(temporary) / "changed").exists())
            with self.assertRaisesRegex(ExperimentError, "saved child executables"):
                run_evaluation(plan(), Path(temporary) / "override", binary="arbitrary", reproduce_from=root)
            run.reset_mock()
            replay, report = run_evaluation(plan(), Path(temporary) / "replay", reproduce_from=root)
            self.assertEqual(report["reproduce_from"], str(root))
            for call, identity in zip(run.call_args_list, execution_order(plan())):
                self.assertEqual(call.kwargs["reproduce_from"], root / identity["path"])
                self.assertIsNone(call.kwargs["binary"])
            verify_evaluation(replay)

    @patch("jitter_runner.evaluation.verify_investigation", side_effect=fake_verify)
    def test_replay_retains_failed_original_children_without_rebuilding(self, verify):
        def failing(spec, output=None, binary=None, reproduce_from=None):
            if spec["search"]["strategy"] == "random":
                raise RuntimeError("fixture missing binary")
            return fake_run(spec, output, binary, reproduce_from)
        with tempfile.TemporaryDirectory() as temporary, patch("jitter_runner.evaluation.run_investigation", side_effect=failing) as run:
            root, _ = run_evaluation(plan(), Path(temporary) / "original")
            run.reset_mock()
            replay, report = run_evaluation(plan(), Path(temporary) / "replay", reproduce_from=root)
            self.assertEqual(run.call_count, 1)
            failed = next(row for row in report["studies"] if row["status"] == "failed")
            self.assertIn("cannot silently rebuild", failed["failure"])
            self.assertEqual(report["totals"]["planned_studies"], 2)
            verify_evaluation(replay)


if __name__ == "__main__":
    unittest.main()
