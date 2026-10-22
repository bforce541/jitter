"""Diagnostic baseline counts must be regenerated from the retained traces."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from evaluate_diagnostics import evaluate
from jitter_runner.diagnostic_evaluation import verify_diagnostic_evaluation
from jitter_runner.spec import ExperimentError, load_json
from jitter_runner.storage import seal_bundle, write_json
from jitter_runner.transport import pack_result, unpack_result


class DiagnosticEvaluationTests(unittest.TestCase):
    def test_raw_controls_and_portable_roundtrip_preserve_all_baselines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = evaluate(root / "input")
            self.assertEqual(expected["matches"], {"dependency_match": 6, "region_only_match": 2, "delay_only_match": 5})
            self.assertEqual(verify_diagnostic_evaluation(root / "input"), expected)
            archive = pack_result(root / "input", root / "controls.tar.gz")
            restored = unpack_result(archive, root / "restored")
            self.assertEqual(verify_diagnostic_evaluation(restored), expected)

    def test_resealed_favorable_counts_and_changed_truth_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "controls"
            original = evaluate(root)
            for change in (lambda value: value["matches"].update(region_only_match=6),
                           lambda value: value["cases"][0].update(known_mechanism="digest_execution")):
                value = load_json(root / "diagnostic-evaluation.json")
                change(value)
                write_json(root / "diagnostic-evaluation.json", value)
                seal_bundle(root, "complete")
                with self.assertRaisesRegex(ExperimentError, "differs from its declared controls"):
                    verify_diagnostic_evaluation(root)
                write_json(root / "diagnostic-evaluation.json", original)

    def test_resealed_trace_analysis_cannot_change_a_saved_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "controls"
            evaluate(root)
            path = root / "owner/diagnostics.json"
            report = load_json(path)
            report["hypotheses"] = []
            write_json(path, report)
            seal_bundle(root, "complete")
            with self.assertRaisesRegex(ExperimentError, "differs from raw trace evidence"):
                verify_diagnostic_evaluation(root)
