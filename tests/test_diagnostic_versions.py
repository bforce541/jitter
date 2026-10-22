import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from evaluate_diagnostics import evaluate
from test_observation import controlled_case
from jitter_runner.adapters import get_adapter
from jitter_runner.diagnostics import diagnose
from jitter_runner.metrics import analyze_run
from jitter_runner.spec import ExperimentError
from jitter_runner.storage import write_json


class DiagnosticVersionTests(unittest.TestCase):
    def test_known_controls_show_abstention_and_the_unowned_wait_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate(Path(directory) / "controls")
            self.assertEqual(result["matches"], {"dependency_match":6,"region_only_match":2,"delay_only_match":5})
            unknown = next(row for row in result["cases"] if row["case"] == "unowned")
            self.assertEqual(unknown["dependency_analysis"], "unattributed_lock_wait")
            self.assertEqual(unknown["delay_only_baseline"], "shared_lock_contention")

    def test_archived_analysis_one_remains_semantically_verifiable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, _ = controlled_case(path)
            baseline = analyze_run(spec, workload, path)
            legacy = diagnose(spec, path, baseline, analysis_version=1)
            self.assertEqual(legacy["schema_version"], 1)
            write_json(path / "diagnostics.json", legacy)
            result = get_adapter("shared-index-v1").validate(spec, workload, path)
            self.assertEqual(result["observation"], legacy["trace_quality"])
            self.assertEqual(diagnose(spec, path, baseline)["schema_version"], 2)

    def test_unknown_analysis_schema_is_rejected_explicitly(self):
        for version in (True, 0, 3, "1", 2.0):
            with self.subTest(version=version), self.assertRaises(ExperimentError):
                diagnose({}, Path("unused"), {}, analysis_version=version)
