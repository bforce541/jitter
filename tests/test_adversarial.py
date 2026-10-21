"""Adversarial lifecycle, provenance, and registered-variant checks."""

import copy
import hashlib
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import jitter_runner.investigation as controller
from jitter_runner.runner import ROOT, run_experiment, verify_bundle
from jitter_runner.spec import ExperimentError, load_json
from jitter_runner.storage import seal_bundle, sha256, write_json
from jitter_runner.trace_reader import read_trace
from jitter_runner.variants import build_variant, describe_variant, freeze_source
from test_investigation import investigation_spec


def tiny_context(path):
    """Create a real validated source snapshot without touching the checkout."""
    archive = path / "original.tar.gz"
    contents = b"immutable fixture source\n"
    with tarfile.open(archive, "w:gz") as stream:
        member = tarfile.TarInfo("src/fixture.cpp")
        member.size = len(contents)
        stream.addfile(member, io.BytesIO(contents))
    source = {"revision": "fixture", "dirty": False,
              "files": {"src/fixture.cpp": hashlib.sha256(contents).hexdigest()}}
    return freeze_source(ROOT, path / "frozen", {"archive": archive, "source": source})


class VariantSafetyTests(unittest.TestCase):
    def test_frozen_source_is_a_separate_validated_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            context = tiny_context(path)
            before = sha256(context["archive"])
            original = path / "original.tar.gz"
            original.write_bytes(b"later checkout or input change")
            self.assertEqual((Path(context["root"]) / "src/fixture.cpp").read_bytes(), b"immutable fixture source\n")
            self.assertEqual(sha256(context["archive"]), before)
            replay = freeze_source(ROOT, path / "replay", {"archive": context["archive"], "source": context["source"]})
            self.assertEqual(replay["source"], context["source"])

    def test_failed_candidate_build_cannot_poison_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            context = tiny_context(path)
            adapter = mock.Mock()
            temporary_builds = []

            def missing_compiler(root, build, logs, **kwargs):
                temporary_builds.append(build)
                (logs / "configure.stderr.log").write_text("missing compiler fixture")
                raise FileNotFoundError("missing-cxx")

            adapter.build.side_effect = missing_compiler
            adapter.build_info.return_value = {"compiled_variant": "any"}
            with mock.patch("jitter_runner.variants.get_adapter", return_value=adapter):
                with self.assertRaises(FileNotFoundError):
                    build_variant("shared-index-v1", "narrow", context, path / "candidate")
                result = build_variant("shared-index-v1", "coarse", context, path / "baseline", supplied=Path(sys.executable))
            self.assertEqual(load_json(path / "candidate/build.json")["status"], "failed")
            self.assertIn("missing compiler", (path / "candidate/logs/configure.stderr.log").read_text())
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["engine_sha256"], sha256(sys.executable))
            self.assertTrue(all(not build.exists() for build in temporary_builds))
            self.assertEqual((Path(context["root"]) / "src/fixture.cpp").read_bytes(), b"immutable fixture source\n")

    def test_unregistered_transformations_and_caller_errors_are_explicit(self):
        for adapter, implementation in (("shared-index-v1", "ordered"), ("json-projection-v1", "narrow"),
                                        ("shared-index-v1", "../../escape")):
            with self.subTest(adapter=adapter, implementation=implementation), self.assertRaises(ExperimentError):
                describe_variant(adapter, implementation)
        self.assertEqual(controller.failure_kind(KeyboardInterrupt()), "interrupted")
        self.assertEqual(controller.failure_kind(subprocess.TimeoutExpired(["fixture"], 1)), "timeout")
        self.assertEqual(controller.failure_kind(FileNotFoundError("missing-cxx")), "environment_failure")

    def test_resealed_unsafe_journal_paths_are_rejected_before_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "interrupted"
            with mock.patch("jitter_runner.investigation.freeze_source", side_effect=KeyboardInterrupt):
                controller.run_investigation(investigation_spec(), root)
            original = load_json(root / "state.json")
            changes = []
            records = copy.deepcopy(original)
            records["records"] = {"../escape": {"status": "complete", "diagnostic": False, "stage": "exploration"}}
            records["cost"]["runs"] = 1
            changes.append(records)
            diagnostic = copy.deepcopy(original)
            diagnostic["diagnostic"] = "../../escape"
            changes.append(diagnostic)
            builds = copy.deepcopy(original)
            builds["builds"] = {"../../escape": {"status": "complete"}}
            builds["cost"]["builds"] = 1
            changes.append(builds)
            for state in changes:
                write_json(root / "state.json", state)
                seal_bundle(root, "complete")
                accessed = []

                def confined_load(path):
                    accessed.append(Path(path).resolve())
                    self.assertTrue(Path(path).resolve().is_relative_to(root.resolve()), "verifier attempted an unsafe read")
                    return load_json(path)

                with mock.patch("jitter_runner.investigation.load_json", side_effect=confined_load):
                    with self.assertRaises(ExperimentError):
                        controller.verify_investigation(root)
                self.assertTrue(accessed)


@unittest.skipUnless(os.environ.get("JITTER_TEST_BINARY"), "set JITTER_TEST_BINARY for adversarial engine tests")
class VariantIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.binary = Path(os.environ["JITTER_TEST_BINARY"]).resolve()
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.context = freeze_source(ROOT, self.path / "source")

    def tearDown(self):
        self.temporary.cleanup()

    def run_variant(self, implementation, adapter="shared-index-v1"):
        spec = investigation_spec()["experiment"]
        spec["application"]["implementation"] = implementation
        spec["instrumentation"]["enabled"] = True
        if adapter == "json-projection-v1":
            app = spec["application"]
            app["adapter"] = adapter
            app["key_count"] = 8
            del app["light_rounds"], app["heavy_rounds"]
            app.update(light_fields=3, heavy_fields=8)
            spec["workload"]["heavy_fraction"] = .5
        path, summary = run_experiment(spec, self.path / implementation, binary=self.binary, source_context=self.context)
        verify_bundle(path)
        trace = read_trace(spec, path / "run-001", summary["runs"][0])
        self.assertTrue(trace["quality"]["complete"])
        self.assertEqual(summary["runs"][0]["correctness"], "passed")
        self.assertEqual(summary["runs"][0]["phases"]["measurement"]["completed"], 4)
        for events in trace["groups"].values():
            self.assertLessEqual(events["lock_release"]["timestamp_ns"], events["region_begin"]["timestamp_ns"])
        return path

    def test_json_alternatives_match_oracle_and_release_before_region(self):
        first = self.run_variant("map", "json-projection-v1")
        second = self.run_variant("ordered", "json-projection-v1")
        import csv
        with (first / "run-001/requests.csv").open() as left, (second / "run-001/requests.csv").open() as right:
            self.assertEqual([row["value"] for row in csv.DictReader(left)], [row["value"] for row in csv.DictReader(right)])

    def test_narrow_trace_preserves_registered_grammar(self):
        self.run_variant("narrow")

    def test_partial_pair_interrupt_is_retained_and_cannot_recommend(self):
        original = controller.run_experiment
        calls = 0

        def interrupt_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                with mock.patch("jitter_runner.runner.run_command", side_effect=KeyboardInterrupt):
                    return original(*args, **kwargs)
            return original(*args, **kwargs)

        with mock.patch("jitter_runner.investigation.run_experiment", side_effect=interrupt_second):
            path, report = controller.run_investigation(investigation_spec(), self.path / "interrupted", binary=self.binary)
        self.assertEqual(report["decision"], "interrupted")
        self.assertIsNone(report["recommendation"])
        _, _, state = controller.verify_investigation(path)
        self.assertEqual(state["cost"]["runs"], 2)
        self.assertEqual(sum(record["status"] == "failed" for record in state["records"].values()), 1)
        comparison = state["trials"][0]["comparison"]
        self.assertEqual(comparison["pairs_requested"], 1)
        self.assertEqual(comparison["pairs_valid"], 0)
        self.assertFalse(comparison["accepted"])
        self.assertEqual(load_json(path / "measurements/run-00002/summary.json")["failure"]["type"], "KeyboardInterrupt")

    def test_budget_stop_in_partial_pair_preserves_unmatched_run(self):
        original = controller.run_experiment
        offset = 0
        clock = mock.Mock()
        clock.monotonic.side_effect = lambda: time.monotonic() + offset

        def expire_after_first(*args, **kwargs):
            nonlocal offset
            result = original(*args, **kwargs)
            offset = 100
            return result

        with mock.patch("jitter_runner.investigation.time", clock), \
             mock.patch("jitter_runner.investigation.run_experiment", side_effect=expire_after_first):
            path, report = controller.run_investigation(investigation_spec(), self.path / "budget", binary=self.binary)
        self.assertEqual(report["decision"], "budget_exhausted")
        self.assertIsNone(report["recommendation"])
        _, _, state = controller.verify_investigation(path)
        self.assertEqual(state["cost"]["runs"], 1)
        comparison = state["trials"][0]["comparison"]
        self.assertEqual(comparison["pairs_requested"], 1)
        self.assertEqual(comparison["pairs_valid"], 0)

    def test_diagnostic_interrupt_retains_failed_attempt(self):
        spec = investigation_spec()
        spec["diagnostic"] = True
        with mock.patch("jitter_runner.runner.run_command", side_effect=KeyboardInterrupt):
            path, report = controller.run_investigation(spec, self.path / "diagnostic-interrupt", binary=self.binary)
        self.assertEqual(report["decision"], "interrupted")
        self.assertIsNone(report["recommendation"])
        _, _, state = controller.verify_investigation(path)
        self.assertEqual(state["diagnostic"], "measurements/run-00001")
        self.assertEqual(state["records"][state["diagnostic"]]["status"], "failed")

    @unittest.skipUnless(os.name == "posix", "executable wrapper requires POSIX")
    def test_baseline_failure_invalidates_investigation_and_keeps_logs(self):
        broken = self.path / "baseline-failure"
        binary = shlex.quote(str(self.binary))
        broken.write_text("#!/bin/sh\nif [ \"$1\" = \"--build-info\" ]; then exec " + binary + " --build-info; fi\n"
                          "previous=''\nfor argument in \"$@\"; do\n"
                          "if [ \"$previous\" = '--workers' ] && [ \"$argument\" = '2' ]; then "
                          "echo 'baseline fixture failure' >&2; exit 9; fi\nprevious=$argument\ndone\n"
                          "exec " + binary + " \"$@\"\n")
        broken.chmod(0o755)
        path, report = controller.run_investigation(investigation_spec(), self.path / "baseline-failed", binary=broken)
        self.assertEqual(report["decision"], "invalid_investigation")
        self.assertEqual(report["stop_reason"], "baseline_failure")
        self.assertIsNone(report["recommendation"])
        _, _, state = controller.verify_investigation(path)
        failed = [(name, record) for name, record in state["records"].items() if record["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        name, record = failed[0]
        self.assertEqual(record["configuration"]["workers"], 2)
        self.assertIn("baseline fixture failure", (path / name / "run-001/stderr.log").read_text())


if __name__ == "__main__":
    unittest.main()
