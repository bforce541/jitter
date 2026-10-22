import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.driver import IDENTITY, SOURCES, save_driver, verify_driver
from jitter_runner.investigation import run_investigation
from jitter_runner.spec import ExperimentError
from test_investigation import investigation_spec


class DriverProvenanceTests(unittest.TestCase):
    def test_current_driver_reuses_matching_application_archive_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = save_driver(directory, IDENTITY["files"])
            self.assertEqual(saved["storage"], "application_source_archive")
            self.assertEqual(verify_driver(directory,saved,IDENTITY["files"]), IDENTITY)
            self.assertFalse((Path(directory) / "driver-sources").exists())
            saved["identity"]["revision"] = "caller modification"
            self.assertNotEqual(saved["identity"],IDENTITY)

    def test_replay_preserves_current_tool_bytes_when_application_source_is_older(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = save_driver(directory,{})
            self.assertEqual(saved["storage"],"driver-sources")
            self.assertEqual(verify_driver(directory,saved,{}),IDENTITY)
            name = next(iter(SOURCES))
            target = Path(directory) / "driver-sources" / name
            self.assertEqual(target.read_bytes(),SOURCES[name])
            target.write_bytes(b"changed driver code")
            with self.assertRaisesRegex(ExperimentError,"preserved code"):
                verify_driver(directory,saved,{})

    def test_driver_inventory_cannot_escape_or_claim_missing_archived_code(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = save_driver(directory, IDENTITY["files"])
            with self.assertRaisesRegex(ExperimentError,"archived application source"):
                verify_driver(directory,saved,{})
            for name in ("../outside.py","/outside.py","tools/jitter_runner/../../outside.py"):
                changed = copy.deepcopy(saved)
                changed["identity"]["files"] = {name:"0"*64}
                with self.assertRaisesRegex(ExperimentError,"unsafe driver source"):
                    verify_driver(directory,changed,{})

    def test_exact_replay_cannot_override_its_saved_engine(self):
        with self.assertRaisesRegex(ExperimentError,"not an override binary"):
            run_investigation(investigation_spec(),binary=Path("override"),reproduce_from=Path("saved"))
