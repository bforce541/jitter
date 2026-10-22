import copy
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from jitter_runner.investigation import (derive_decision, experiment_for, failure_kind,
                                        pair_order, run_investigation, select_finalist,
                                        verify_investigation, strong_exploratory_gain,
                                        make_report, markdown_report)
from jitter_runner.investigation_spec import validate_investigation
from jitter_runner.runner import ROOT
from jitter_runner.search import choose_candidate, configuration, normalize_space
from jitter_runner.spec import ExperimentError, load_json, load_spec
from jitter_runner.storage import seal_bundle, write_json


def investigation_spec():
    experiment = load_spec(ROOT / "examples" / "baseline.json")
    experiment["schema_version"] = 3
    experiment["application"].update(workers=2, queue_capacity=8, implementation="coarse",
                                     light_rounds=2, heavy_rounds=4)
    experiment["instrumentation"] = {"enabled": False, "events_per_thread": 512}
    experiment["workload"].update(warmup_ms=0, measurement_ms=4, rate_per_second=1000,
                                  heavy_fraction=0)
    experiment["objective"].update(min_throughput_per_second=0, max_peak_rss_bytes=1073741824,
                                   max_cpu_cores=64, max_rejection_fraction=0,
                                   max_scheduler_lag_p99_ns=1000000000)
    experiment["repetitions"] = 1
    experiment["timeout_seconds"] = 5
    return {"schema_version": 1, "experiment": experiment,
            "search": {"strategy": "guided", "seed": 17,
                       "parameters": {"workers": [1, 2, 3]}, "exclude": []},
            "sampling": {"exploration_pairs": 2, "max_exploration_pairs": 2, "confirmation_pairs": 8},
            "comparison": {"metric": "p99", "min_effect": .05, "confidence": .95,
                           "max_throughput_loss": .5, "min_tail_observations": 20,
                           "max_baseline_spread": 100},
            "budget": {"max_candidates": 2, "max_runs": 40, "max_builds": 2,
                       "max_failures": 2, "wall_seconds": 60}, "diagnostic": False}


def decision_state(reason="no_promising_candidates", trials=None, confirmation=None):
    return {"stop_reason": reason, "trials": trials or [], "confirmation": confirmation}


def observed_trial(config, effect, status="promising"):
    return {"id": str(config["workers"]), "configuration": config,
            "comparison": {"status": status, "effect": {"median_fraction": effect}}}


class InvestigationContractTests(unittest.TestCase):
    def test_strong_gain_stops_exploration_without_authorizing_recommendation(self):
        spec = investigation_spec()
        spec.update(schema_version=2, stopping={"enabled": True, "strong_gain_fraction": .25})
        validate_investigation(spec)
        trial = observed_trial({"workers": 1}, .4)
        trial["comparison"]["effect"]["paired_fraction"] = [.4, .41]
        self.assertTrue(strong_exploratory_gain(spec, trial))
        self.assertEqual(derive_decision(decision_state(trials=[trial])), "inconclusive")
        for status in ("inconclusive", "constraint_violation", "invalid", "regression"):
            changed = copy.deepcopy(trial)
            changed["comparison"]["status"] = status
            self.assertFalse(strong_exploratory_gain(spec, changed))
        for effects in ([.4], [.4, .25], [.4, -.1]):
            changed = copy.deepcopy(trial)
            changed["comparison"]["effect"]["paired_fraction"] = effects
            self.assertFalse(strong_exploratory_gain(spec, changed))
        trial["failure"] = {"kind": "correctness_failure"}
        self.assertFalse(strong_exploratory_gain(spec, trial))
        trial.pop("failure")
        spec["stopping"]["enabled"] = False
        self.assertFalse(strong_exploratory_gain(spec, trial))
        for stopping in ({"enabled": 1, "strong_gain_fraction": .25},
                         {"enabled": True, "strong_gain_fraction": .01},
                         {"enabled": True, "strong_gain_fraction": float("nan")}):
            with self.assertRaises(ExperimentError):
                validate_investigation({**spec, "stopping": stopping})

    def test_valid_fixture_and_closed_contract(self):
        spec = investigation_spec()
        self.assertIs(validate_investigation(spec), spec)
        changes = [
            lambda value: value.update(unknown=True),
            lambda value: value.update(schema_version=2),
            lambda value: value.update(diagnostic=1),
            lambda value: value["experiment"].update(repetitions=2),
            lambda value: value["experiment"]["instrumentation"].update(enabled=True),
            lambda value: value["experiment"]["objective"].update(max_rejection_fraction=.01),
            lambda value: value["search"]["parameters"].update(light_rounds=[1, 2]),
            lambda value: value["sampling"].update(exploration_pairs=1),
            lambda value: value["sampling"].update(max_exploration_pairs=1),
            lambda value: value["sampling"].update(confirmation_pairs=7),
            lambda value: value["comparison"].update(min_tail_observations=1),
            lambda value: value["comparison"].update(confidence=float("nan")),
            lambda value: value["comparison"].update(metric="average"),
            lambda value: value["budget"].update(max_runs=True),
            lambda value: value["budget"].update(wall_seconds=0),
        ]
        for change in changes:
            changed = copy.deepcopy(spec)
            change(changed)
            with self.subTest(change=change), self.assertRaises(ExperimentError):
                validate_investigation(changed)

    def test_candidate_materialization_preserves_behavior_and_inputs(self):
        spec = investigation_spec()
        original = copy.deepcopy(spec)
        point = {"workers": 3, "queue_capacity": 16, "implementation": "narrow"}
        candidate = experiment_for(spec, point)
        self.assertEqual(candidate["workload"], spec["experiment"]["workload"])
        self.assertEqual(candidate["objective"], spec["experiment"]["objective"])
        for key in ("dataset_seed", "key_count", "light_rounds", "heavy_rounds"):
            self.assertEqual(candidate["application"][key], spec["experiment"]["application"][key])
        self.assertFalse(candidate["instrumentation"]["enabled"])
        self.assertTrue(experiment_for(spec, point, diagnostic=True)["instrumentation"]["enabled"])
        self.assertEqual(spec, original)

    def test_decisions_require_confirmation_and_respect_stop_reason(self):
        trial = observed_trial({"workers": 1}, .5)
        self.assertEqual(derive_decision(decision_state(trials=[trial])), "inconclusive")
        confirmation = {"comparison": {"accepted": True}}
        self.assertEqual(derive_decision(decision_state("confirmed_improvement", [trial], confirmation)),
                         "accepted_recommendation")
        self.assertEqual(derive_decision(decision_state("budget_exhausted", [trial], confirmation)),
                         "budget_exhausted")
        self.assertEqual(derive_decision(decision_state("confirmation_not_accepted", [trial],
                                                       {"comparison": {"accepted": False}})), "inconclusive")
        self.assertEqual(derive_decision(decision_state("interrupted")), "interrupted")
        for reason in ("baseline_failure", "diagnostic_failure", "internal_failure"):
            self.assertEqual(derive_decision(decision_state(reason)), "invalid_investigation")

    def test_negative_results_and_failed_trials_are_distinct(self):
        regression = observed_trial({"workers": 1}, -.5, "regression")
        unchanged = observed_trial({"workers": 3}, 0, "no_meaningful_difference")
        self.assertEqual(derive_decision(decision_state(trials=[regression, unchanged])), "no_improvement")
        self.assertEqual(derive_decision(decision_state(trials=[{"failure": {"kind": "timeout"}}])), "inconclusive")
        self.assertEqual(derive_decision(decision_state(trials=[observed_trial({"workers": 1}, -.1, "exploratory")])),
                         "inconclusive")

    def test_finalist_is_selected_only_from_promising_exploration(self):
        first = observed_trial({"workers": 1}, .1)
        best = observed_trial({"workers": 3}, .3)
        unsupported = observed_trial({"workers": 4}, .99, "inconclusive")
        self.assertIs(select_finalist([first, unsupported, best]), best)
        self.assertIsNone(select_finalist([unsupported, {"id": "failed", "failure": "timeout"}]))

    def test_pair_order_and_adaptive_choices_reproduce(self):
        orders = [pair_order(17, index) for index in range(20)]
        self.assertEqual(orders, [pair_order(17, index) for index in range(20)])
        self.assertEqual({tuple(order) for order in orders}, {("baseline", "candidate"), ("candidate", "baseline")})
        self.assertNotEqual(orders, [pair_order(18, index) for index in range(20)])
        spec = investigation_spec()
        baseline = configuration(spec["experiment"])
        space = normalize_space(spec["experiment"], spec["search"])
        first, first_reason = choose_candidate(space, baseline, [], [], "guided", 17)
        history = [{"configuration": first, "status": "inconclusive", "effect": None, "cost_seconds": 1}]
        second = choose_candidate(space, baseline, history, [], "guided", 17)
        self.assertEqual((first, first_reason), choose_candidate(space, baseline, [], [], "guided", 17))
        self.assertEqual(second, choose_candidate(space, baseline, history, [], "guided", 17))
        self.assertNotEqual(first, second[0])

    def test_failure_classification_is_explicit(self):
        for message, kind in (("incorrect response for request 0", "correctness_failure"),
                              ("trace event grammar mismatch", "instrumentation_failure"),
                              ("command timed out", "timeout"), ("compiler missing", "environment_failure")):
            self.assertEqual(failure_kind(ExperimentError(message)), kind)

    def test_interrupted_startup_retains_verifiable_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "interrupted"
            with mock.patch("jitter_runner.investigation.freeze_source", side_effect=KeyboardInterrupt):
                path, report = run_investigation(investigation_spec(), output)
            self.assertEqual(report["decision"], "interrupted")
            self.assertIsNone(report["recommendation"])
            self.assertTrue((path / "state.json").is_file())
            _, saved, state = verify_investigation(path)
            self.assertEqual(saved, report)
            self.assertEqual(state["cost"]["runs"], 0)


@unittest.skipUnless(os.environ.get("JITTER_TEST_BINARY"), "set JITTER_TEST_BINARY for engine investigation tests")
class InvestigationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.binary = Path(os.environ["JITTER_TEST_BINARY"]).resolve()
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def execute(self, spec=None, name="investigation", binary=None):
        return run_investigation(spec or investigation_spec(), self.path / name, binary=binary or self.binary)

    def test_short_investigation_is_inconclusive_and_report_tampering_fails(self):
        spec = investigation_spec()
        spec.update(schema_version=2, stopping={"enabled": True, "strong_gain_fraction": .25})
        path, report = self.execute(spec)
        self.assertEqual(report["decision"], "inconclusive")
        self.assertIsNone(report["recommendation"])
        self.assertIsNone(report["confirmation"])
        self.assertFalse((path / "recommendation.json").exists())
        self.assertEqual(len(report["trials"]), 2)
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["controller_version"], "adaptive-paired-v2")
        self.assertIsNone(report["search_stop_reason"])
        self.assertEqual(report["search_coverage"]["untried_candidates"], 0)
        self.assertFalse(report["search_coverage"]["global_optimum_established"])
        self.assertEqual(report["cost"]["runs"], 8)
        for trial in report["trials"]:
            self.assertEqual(len(trial["pairs"]), 2)
            self.assertFalse(trial["comparison"]["accepted"])
            self.assertFalse(trial["comparison"]["tail_support"]["sufficient"])
            self.assertTrue(any("completions" in reason for reason in trial["comparison"]["reasons"]))
        _, saved, state = verify_investigation(path)
        self.assertEqual(saved, report)
        completed = [name for name, record in state["records"].items() if record["status"] == "complete"]
        self.assertEqual(len(completed), 8)
        self.assertTrue(all(not load_json(path / name / "spec.json")["instrumentation"]["enabled"] for name in completed))
        for key in ("timed_application_cpu_seconds", "application_setup_seconds"):
            modified = copy.deepcopy(state)
            modified["cost"][key] += 1
            modified_report = make_report(spec, modified)
            write_json(path / "state.json", modified)
            write_json(path / "report.json", modified_report)
            (path / "report.md").write_text(markdown_report(modified_report))
            seal_bundle(path, "complete")
            with self.assertRaisesRegex(ExperimentError, "cost differs from validated raw measurements"):
                verify_investigation(path)
        write_json(path / "state.json", state)
        (path / "report.md").write_text(markdown_report(report))
        changed = copy.deepcopy(report)
        changed["decision"] = "accepted_recommendation"
        write_json(path / "report.json", changed)
        seal_bundle(path, "complete")
        with self.assertRaises(ExperimentError):
            verify_investigation(path)

    def test_single_run_budget_is_never_exceeded(self):
        spec = investigation_spec()
        spec["budget"]["max_runs"] = 1
        spec["diagnostic"] = True
        path, report = self.execute(spec)
        self.assertEqual(report["decision"], "budget_exhausted")
        self.assertEqual(report["cost"]["runs"], 1)
        self.assertIsNone(report["recommendation"])
        self.assertEqual(report["trials"], [])
        _, _, state = verify_investigation(path)
        self.assertEqual(len(state["records"]), 1)
        self.assertIsNotNone(state["diagnostic"])

    @unittest.skipUnless(os.name == "posix", "executable wrapper requires POSIX")
    def test_failed_application_retains_child_and_parent_evidence(self):
        broken = self.path / "broken-engine"
        broken.write_text("#!/bin/sh\nif [ \"$1\" = \"--build-info\" ]; then exec " +
                          shlex.quote(str(self.binary)) + " --build-info; fi\n" +
                          "echo 'deliberate application failure' >&2\nexit 9\n")
        broken.chmod(0o755)
        spec = investigation_spec()
        spec["diagnostic"] = True
        path, report = self.execute(spec, binary=broken)
        self.assertEqual(report["decision"], "invalid_investigation")
        self.assertIsNone(report["recommendation"])
        _, _, state = verify_investigation(path)
        failed = [(name, record) for name, record in state["records"].items() if record["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        child = path / failed[0][0]
        self.assertEqual(load_json(child / "summary.json")["status"], "failed")
        self.assertIn("deliberate application failure", (child / "run-001/stderr.log").read_text())
        self.assertEqual(load_json(child / "manifest.json")["status"], "failed")
        self.assertTrue((path / "report.md").is_file())

    def test_restart_recovers_failed_executor_with_fresh_measurements(self):
        broken = self.path / "broken-restart-engine"
        broken.write_text("#!/bin/sh\nif [ \"$1\" = \"--build-info\" ]; then exec " + shlex.quote(str(self.binary)) + " --build-info; fi\nexit 9\n")
        broken.chmod(0o755)
        spec = investigation_spec()
        spec["diagnostic"] = True
        original, report = self.execute(spec, name="restart-original", binary=broken)
        self.assertEqual(report["decision"], "invalid_investigation")
        restarted, fresh = run_investigation(spec, self.path / "restarted", binary=self.binary, restart_from=original)
        self.assertEqual(fresh["decision"], "inconclusive")
        self.assertEqual(fresh["restart_from"], str(original))
        self.assertEqual(verify_investigation(restarted)[2]["cost"]["failures"], 0)
        self.assertEqual(verify_investigation(original)[2]["cost"]["failures"], 1)


if __name__ == "__main__":
    unittest.main()
