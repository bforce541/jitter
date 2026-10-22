import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.runner import ROOT, run_experiment, verify_bundle
from jitter_runner.spec import ExperimentError, load_spec, validate_spec
from jitter_runner.storage import load_json
from jitter_runner.workload import expected_value


def sqlite_spec():
    result = load_spec(ROOT / "examples/baseline.json")
    result["schema_version"] = 3
    result["repetitions"] = 1
    result["application"] = {"adapter": "sqlite-aggregate-v1", "workers": 2, "queue_capacity": 64,
                             "key_count": 8, "dataset_seed": 17, "light_rows": 8, "heavy_rows": 256,
                             "implementation": "scan"}
    result["workload"].update(warmup_ms=0, measurement_ms=4, rate_per_second=1000)
    result["objective"].update(min_throughput_per_second=0, max_cpu_cores=64,
                               max_scheduler_lag_p99_ns=1000000000)
    result["instrumentation"] = {"enabled": True, "events_per_thread": 1024}
    return result


class SqliteContractTests(unittest.TestCase):
    def test_oracle_fixed_golden_and_request_class(self):
        app = sqlite_spec()["application"]
        self.assertEqual(expected_value(app, 1, False), 13580604)
        self.assertEqual(expected_value(app, 0, True), 16310960696)
        app.update(heavy_rows=4096)
        self.assertEqual(expected_value(app, 7, True), 4252718751072)

    def test_closed_schema_and_preparation_limits(self):
        validate_spec(sqlite_spec())
        for key, value in (("light_rows", 0), ("heavy_rows", 4097), ("implementation", "map"), ("key_count", 65536)):
            with self.subTest(key=key):
                spec = sqlite_spec()
                spec["application"][key] = value
                with self.assertRaises(ExperimentError):
                    validate_spec(spec)
        invalid = sqlite_spec()
        invalid["schema_version"] = 2
        with self.assertRaises(ExperimentError):
            validate_spec(invalid)

    @unittest.skipUnless(os.environ.get("JITTER_TEST_BINARY"), "requires real engine")
    def test_scan_and_indexed_validate_trace_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            for variant in ("scan", "indexed"):
                spec = sqlite_spec()
                spec["application"]["implementation"] = variant
                path, summary = run_experiment(spec, Path(directory) / variant, binary=Path(os.environ["JITTER_TEST_BINARY"]))
                self.assertEqual(summary["runs"][0]["correctness"], "passed")
                self.assertTrue(summary["runs"][0]["observation"]["complete"])
                self.assertEqual(verify_bundle(path)[0], spec)
                self.assertEqual(load_json(path / "run-001/diagnostics.json")["status"], "analyzed")
                replay, _ = run_experiment(spec, Path(directory) / (variant + "-replay"), reproduce_from=path)
                self.assertEqual(verify_bundle(replay)[2]["engine_sha256"], verify_bundle(path)[2]["engine_sha256"])
