import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.storage import link_immutable, sha256
from jitter_runner.investigation import run_investigation, verify_investigation
from jitter_runner.spec import ExperimentError
from test_investigation import investigation_spec


class ImmutableInputTests(unittest.TestCase):
    def test_files_remain_self_contained_if_original_name_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, copied = root / "original", root / "copied"
            original.write_bytes(b"private immutable bytes")
            link_immutable(original, copied)
            original.unlink()
            self.assertEqual(copied.read_bytes(), b"private immutable bytes")
            self.assertFalse(copied.is_symlink())

    def test_unsupported_filesystem_falls_back_to_independent_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original, copied = root / "original", root / "copied"
            original.write_bytes(b"frozen")
            with patch("jitter_runner.storage.os.link", side_effect=OSError("different filesystem")):
                link_immutable(original, copied)
            original.write_bytes(b"changed")
            self.assertEqual(copied.read_bytes(), b"frozen")

    @unittest.skipUnless(os.environ.get("JITTER_TEST_BINARY"), "requires engine")
    def test_supplied_engine_is_independent_of_private_shared_measurement_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            supplied = Path(os.environ["JITTER_TEST_BINARY"]).resolve()
            identity = sha256(supplied)
            spec = investigation_spec()
            spec["budget"]["max_candidates"] = 1
            study, _ = run_investigation(spec, root / "study", binary=supplied)
            private = study / "engines/coarse/jitter"
            self.assertNotEqual((supplied.stat().st_dev, supplied.stat().st_ino), (private.stat().st_dev, private.stat().st_ino))
            _, _, state = verify_investigation(study)
            name = next(iter(state["records"]))
            copied = study / name / "engine/jitter"
            self.assertFalse(copied.is_symlink())
            with copied.open("r+b") as stream:
                stream.write(b"invalid")
            self.assertEqual(sha256(supplied), identity)
            with self.assertRaises(ExperimentError):
                verify_investigation(study)
