import csv
import hashlib
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from jitter_runner.metrics import REQUEST_FIELDS, RESOURCE_FIELDS, analyze_run, distribution
from jitter_runner.runner import ROOT, run_experiment, verify_bundle
from jitter_runner.spec import ExperimentError, load_json, load_spec, validate_spec
from jitter_runner.storage import seal_bundle, verify_integrity, verify_source_archive, write_json
from jitter_runner.workload import MASK, MULTIPLIER, encode_workload, expected_value, generate, mix64, GOLDEN


def fixture_spec():
    spec = load_spec(ROOT / "examples" / "baseline.json")
    spec["workload"].update(warmup_ms=0, measurement_ms=3, rate_per_second=1000, heavy_fraction=0)
    spec["application"].update(light_rounds=2, heavy_rounds=4)
    spec["objective"].update(min_throughput_per_second=0, max_scheduler_lag_p99_ns=1000000000)
    spec["repetitions"] = 1
    return spec


def write_csv(path, fields, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def synthetic_run(path, spec):
    workload = generate(spec)
    rows = []
    for request in workload:
        rows.append({**request, "status": "completed", "submitted_ns": request["scheduled_ns"] + 10,
                     "started_ns": request["scheduled_ns"] + 20,
                     "completed_ns": request["scheduled_ns"] + 100,
                     "value": expected_value(spec["application"], request["key"], request["heavy"])})
    write_csv(path / "requests.csv", REQUEST_FIELDS, rows)
    resources = [{"phase": "measurement", "arrival_window_ns": 3000000, "elapsed_ns": 3000000,
                  "cpu_user_ns": 1000000, "cpu_system_ns": 500000, "peak_rss_bytes": 1000000}]
    write_csv(path / "resources.csv", RESOURCE_FIELDS, resources)
    return workload, rows, resources


class SpecificationTests(unittest.TestCase):
    def test_examples_validate(self):
        for path in (ROOT / "examples").glob("*.json"):
            from jitter_runner.spec import load_json
            value = load_json(path)
            if "scenarios" in value:
                from jitter_runner.evaluation import validate_evaluation
                validate_evaluation(value)
            elif "experiment" in value:
                from jitter_runner.investigation_spec import validate_investigation
                validate_investigation(value)
            else:
                load_spec(path)

    def test_unknown_fields_and_invalid_values_fail(self):
        for section, key, value in (("application", "workers", 0), ("application", "workers", True),
                                    ("workload", "heavy_fraction", float("nan")),
                                    ("workload", "rate_per_second", -1), ("workload", "arrival", "closed-loop"),
                                    ("objective", "max_cpu_cores", float("inf"))):
            spec = fixture_spec()
            spec[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ExperimentError):
                validate_spec(spec)
        spec = fixture_spec()
        spec["application"]["unknown"] = 1
        with self.assertRaises(ExperimentError):
            validate_spec(spec)

    def test_large_workload_and_duplicate_json_fail(self):
        spec = fixture_spec()
        spec["workload"].update(measurement_ms=2000, rate_per_second=1000000)
        with self.assertRaises(ExperimentError):
            validate_spec(spec)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.json"
            path.write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaises(ExperimentError):
                load_spec(path)


class WorkloadTests(unittest.TestCase):
    def test_golden_schedule_and_seed(self):
        spec = load_spec(ROOT / "examples" / "baseline.json")
        self.assertEqual(generate(spec)[:3], [
            {"id": 0, "phase": "warmup", "scheduled_ns": 0, "key": 149, "heavy": 0},
            {"id": 1, "phase": "warmup", "scheduled_ns": 500000, "key": 3, "heavy": 0},
            {"id": 2, "phase": "warmup", "scheduled_ns": 1000000, "key": 82, "heavy": 1}])
        self.assertEqual(encode_workload(generate(spec)), encode_workload(generate(spec)))
        spec["workload"]["seed"] += 1
        self.assertNotEqual(generate(spec)[0]["key"], 149)

    def test_fractional_counts_bursts_and_warmup(self):
        spec = fixture_spec()
        spec["workload"].update(rate_per_second=333, warmup_ms=5, measurement_ms=5,
                                arrival="burst", burst_size=2, heavy_fraction=1)
        rows = generate(spec)
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["id"] for row in rows], list(range(4)))
        self.assertEqual([row["scheduled_ns"] for row in rows], [0, 0, 0, 0])
        self.assertTrue(all(row["heavy"] == 1 for row in rows))
        spec["workload"].update(arrival="steady", burst_size=1, heavy_fraction=0)
        self.assertTrue(all(row["scheduled_ns"] < 5000000 and row["heavy"] == 0 for row in generate(spec)))

    def test_affine_oracle_against_iterative_reference(self):
        rng = random.Random(17)
        for _ in range(100):
            app = fixture_spec()["application"]
            app.update(dataset_seed=rng.randrange(1 << 64), light_rounds=rng.randrange(1, 500))
            key = rng.randrange(256)
            value = mix64(app["dataset_seed"] + GOLDEN * (key + 1))
            for _ in range(app["light_rounds"]):
                value = (value * MULTIPLIER + ((key + 1) | 1)) & MASK
            self.assertEqual(expected_value(app, key, False), value)
        app.update(dataset_seed=17, light_rounds=1, heavy_rounds=2)
        self.assertEqual(expected_value(app, 0, False), 15189048924701222248)
        self.assertEqual(expected_value(app, 0, True), 7502008099585157961)


class MeasurementTests(unittest.TestCase):
    def test_nearest_rank_quantiles(self):
        self.assertEqual(distribution(list(range(1, 101)))["p99"], 99)
        self.assertEqual(distribution([7])["p99"], 7)
        self.assertIsNone(distribution([])["p99"])

    def test_metrics_use_intended_arrival_and_drained_wall_time(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec = fixture_spec()
            workload, rows, _ = synthetic_run(path, spec)
            result = analyze_run(spec, workload, path)
            measured = result["phases"]["measurement"]
            self.assertEqual(measured["latency_ns"]["p99"], 100)
            self.assertEqual(measured["response_latency_ns"]["p99"], 90)
            self.assertEqual(measured["queue_delay_ns"]["p99"], 10)
            self.assertEqual(measured["scheduler_lag_ns"]["p99"], 10)
            self.assertEqual(measured["throughput_per_second"], 1000)
            self.assertEqual(measured["cpu_cores"], 0.5)

    def test_rejections_remain_in_accounting_and_fail_constraints(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec = fixture_spec()
            workload, rows, resources = synthetic_run(path, spec)
            rows[0].update(status="rejected", started_ns=-1, completed_ns=-1, value=0)
            resources[0]["elapsed_ns"] = 6000000
            write_csv(path / "requests.csv", REQUEST_FIELDS, rows)
            write_csv(path / "resources.csv", RESOURCE_FIELDS, resources)
            result = analyze_run(spec, workload, path)
            measured = result["phases"]["measurement"]
            self.assertEqual((measured["offered"], measured["completed"], measured["rejected"]), (3, 2, 1))
            self.assertAlmostEqual(measured["throughput_per_second"], 2 / .006)
            self.assertFalse(result["feasible"])
            self.assertEqual(result["correctness"], "passed")

    def test_empty_completion_sample_is_infeasible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec = fixture_spec()
            spec["objective"]["max_rejection_fraction"] = 1
            workload, rows, _ = synthetic_run(path, spec)
            for row in rows:
                row.update(status="rejected", started_ns=-1, completed_ns=-1, value=0)
            write_csv(path / "requests.csv", REQUEST_FIELDS, rows)
            result = analyze_run(spec, workload, path)
            self.assertIsNone(result["phases"]["measurement"]["latency_ns"]["p99"])
            self.assertFalse(result["constraints"]["latency_observed"])
            self.assertFalse(result["feasible"])

    def test_corrupt_outputs_fail(self):
        mutations = [lambda rows: rows.pop(), lambda rows: rows.append(rows[0]),
                     lambda rows: rows[1].update(id=0), lambda rows: rows[0].update(value=1),
                     lambda rows: rows[0].update(status="pending"), lambda rows: rows[0].update(status="error"),
                     lambda rows: rows[0].update(started_ns=0), lambda rows: rows[0].update(completed_ns=4000000)]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                spec = fixture_spec()
                workload, rows, _ = synthetic_run(path, spec)
                mutation(rows)
                write_csv(path / "requests.csv", REQUEST_FIELDS, rows)
                with self.assertRaises(ExperimentError):
                    analyze_run(spec, workload, path)

    def test_each_constraint_is_independent(self):
        cases = [("min_throughput_per_second", 1001, "throughput"), ("max_peak_rss_bytes", 1, "memory"),
                 ("max_cpu_cores", 0, "cpu"), ("max_scheduler_lag_p99_ns", 0, "scheduler_lag")]
        for name, value, check in cases:
            with self.subTest(check=check), tempfile.TemporaryDirectory() as directory:
                spec = fixture_spec()
                path = Path(directory)
                workload, _, _ = synthetic_run(path, spec)
                spec["objective"][name] = value
                result = analyze_run(spec, workload, path)
                self.assertFalse(result["constraints"][check])
                self.assertFalse(result["feasible"])



class StorageTests(unittest.TestCase):
    def test_source_inventory_retains_build_inputs_without_embedding_release_catalogs(self):
        from jitter_runner.storage import snapshot_source
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git","init","-q",str(root)],check=True)
            (root / "src").mkdir()
            (root / "src/application.cpp").write_text("registered application source\n")
            (root / "docs/evidence").mkdir(parents=True)
            (root / "docs/evidence/catalog.json").write_text('{"derived":"campaign reports"}\n')
            (root / "docs/method.md").write_text("measurement method\n")
            inventory = snapshot_source(root, root / "saved.tar.gz")
            self.assertEqual(set(inventory), {"src/application.cpp", "docs/method.md"})
            verify_source_archive(root / "saved.tar.gz", inventory)

    def test_source_archive_is_checked_against_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.tar.gz"
            contents = b"source snapshot\n"
            entry = tarfile.TarInfo("src/example.cpp")
            entry.size = len(contents)
            with tarfile.open(path, "w:gz") as archive:
                archive.addfile(entry, io.BytesIO(contents))
            expected = {entry.name: hashlib.sha256(contents).hexdigest()}
            validated = set()
            verify_source_archive(path, expected, verified=validated)
            verify_source_archive(path, expected, verified=validated)
            with self.assertRaises(ExperimentError):
                verify_source_archive(path, {entry.name: "0" * 64}, verified=validated)
            with self.assertRaises(ExperimentError):
                verify_source_archive(path, {"different.cpp": expected[entry.name]})
            corrupted = b"changed snapshot\n"
            entry.size = len(corrupted)
            with tarfile.open(path, "w:gz") as archive:
                archive.addfile(entry, io.BytesIO(corrupted))
            with self.assertRaises(ExperimentError):
                verify_source_archive(path, expected, verified=validated)
            entry = tarfile.TarInfo("src/link")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/tmp/elsewhere"
            with tarfile.open(path, "w:gz") as archive:
                archive.addfile(entry)
            with self.assertRaises(ExperimentError):
                verify_source_archive(path, {entry.name: hashlib.sha256(b"").hexdigest()}, verified=validated)

    def test_invalid_manifest_and_unsafe_paths_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_json(path / "manifest.json", [])
            with self.assertRaises(ExperimentError):
                verify_integrity(path)
            write_json(path / "manifest.json", {"schema_version": 1, "status": "complete", "artifacts": {"../outside": "0" * 64}})
            with self.assertRaises(ExperimentError):
                verify_integrity(path)


@unittest.skipUnless(os.environ.get("JITTER_TEST_BINARY"), "set JITTER_TEST_BINARY to run C++ integration tests")
class IntegrationTests(unittest.TestCase):
    def test_execution_arguments_cannot_be_changed_by_resealing(self):
        original, _ = self.execute()
        execution_path = original / "run-001/execution.json"
        execution = load_json(execution_path)
        position = execution["command"].index("--workers") + 1
        execution["command"][position] = str(int(execution["command"][position]) + 1)
        write_json(execution_path, execution)
        seal_bundle(original, "complete")
        with self.assertRaisesRegex(ExperimentError, "execution differs"):
            verify_bundle(original)

    def setUp(self):
        self.binary = Path(os.environ["JITTER_TEST_BINARY"]).resolve()
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def spec(self):
        spec = fixture_spec()
        spec["workload"].update(warmup_ms=10, measurement_ms=20, heavy_fraction=0.3)
        spec["repetitions"] = 2
        return spec

    def execute(self, spec=None, name="original"):
        return run_experiment(spec or self.spec(), self.path / name, binary=self.binary)

    def test_run_verify_reproduce_and_no_overwrite(self):
        original, summary = self.execute()
        self.assertEqual(summary["repetitions_valid"], 2)
        self.assertTrue(all(run["phases"]["measurement"]["completed"] == 20 for run in summary["runs"]))
        spec, _, original_metadata = verify_bundle(original)
        reproduction, repeated = run_experiment(spec, self.path / "reproduction", reproduce_from=original)
        self.assertEqual(repeated["repetitions_valid"], 2)
        _, _, reproduction_metadata = verify_bundle(reproduction)
        self.assertEqual(original_metadata["engine_sha256"], reproduction_metadata["engine_sha256"])
        self.assertEqual((original / "workload.csv").read_bytes(), (reproduction / "workload.csv").read_bytes())
        with self.assertRaises(FileExistsError):
            self.execute(name="original")

    def test_overload_is_valid_but_infeasible(self):
        spec = self.spec()
        spec["application"].update(queue_capacity=1, workers=1, heavy_rounds=1000000)
        spec["workload"].update(warmup_ms=0, measurement_ms=5, rate_per_second=50000,
                                arrival="burst", burst_size=250, heavy_fraction=1)
        spec["repetitions"] = 1
        path, summary = self.execute(spec)
        measured = summary["runs"][0]["phases"]["measurement"]
        self.assertEqual(measured["offered"], measured["completed"] + measured["rejected"])
        self.assertGreater(measured["rejected"], 0)
        self.assertFalse(summary["all_runs_feasible"])
        verify_bundle(path)

    def test_failed_and_timeout_runs_retain_evidence(self):
        spec = self.spec()
        spec["timeout_seconds"] = .001
        with self.assertRaises(ExperimentError):
            self.execute(spec)
        failure = json.loads((self.path / "original" / "summary.json").read_text())
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["failure"]["type"], "TimeoutExpired")
        with self.assertRaises(ExperimentError):
            verify_bundle(self.path / "original")
        spec["timeout_seconds"] = 5
        broken = self.path / "broken"
        broken.write_text('#!/bin/sh\nif [ "$1" = "--build-info" ]; then exec "' + str(self.binary) + '" --build-info; fi\nexit 9\n')
        broken.chmod(0o755)
        with self.assertRaises(ExperimentError):
            run_experiment(spec, self.path / "failed", binary=broken)
        self.assertIn("status 9", json.loads((self.path / "failed" / "summary.json").read_text())["failure"]["message"])

    def test_integrity_and_semantic_tampering_fail(self):
        original, _ = self.execute()
        raw = original / "run-001" / "requests.csv"
        before = raw.read_bytes()
        raw.write_bytes(before + b"bad\n")
        with self.assertRaises(ExperimentError):
            verify_bundle(original)
        raw.write_bytes(before)
        summary_path = original / "run-001" / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["phases"]["measurement"]["latency_ns"]["p99"] = 0
        write_json(summary_path, summary)
        seal_bundle(original, "complete")
        with self.assertRaises(ExperimentError):
            verify_bundle(original)

    def test_unrecorded_files_and_provenance_mismatches_fail(self):
        original, _ = self.execute()
        extra = original / "run-001" / "manifest.json"
        extra.write_text("{}")
        with self.assertRaises(ExperimentError):
            verify_bundle(original)
        extra.unlink()
        metadata_path = original / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["source"]["files"]["src/main.cpp"] = "0" * 64
        write_json(metadata_path, metadata)
        seal_bundle(original, "complete")
        with self.assertRaises(ExperimentError):
            verify_bundle(original)

    def test_cli_invalid_spec_creates_no_output(self):
        invalid = self.path / "invalid.json"
        spec = self.spec()
        spec["application"]["workers"] = 0
        invalid.write_text(json.dumps(spec))
        output = self.path / "output"
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "jitter.py"), "run", str(invalid), "--output", str(output)], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
