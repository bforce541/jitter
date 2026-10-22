import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.calibration import run_calibration
from jitter_runner.spec import ExperimentError
from jitter_runner.storage import seal_bundle
from jitter_runner.transport import CHUNK, _index, pack_result, unpack_result


class TransportTests(unittest.TestCase):
    def test_real_bundle_roundtrip_deduplicates_and_preserves_executability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, _ = run_calibration(root / "input", trials=2)
            content = b"shared bytes\n" * 40000
            for name in ("first", "second"):
                (bundle / name).write_bytes(content)
            (bundle / "first").chmod(0o755)
            seal_bundle(bundle, "complete")
            packed = pack_result(bundle, root / "evidence.tar.gz")
            with tarfile.open(packed) as archive:
                index = json.load(archive.extractfile("index.json"))
                self.assertEqual(index["files"]["first"]["chunks"], index["files"]["second"]["chunks"])
                self.assertEqual(len(archive.getnames()), len(set(identity for row in index["files"].values() for identity in row["chunks"])) + 1)
            restored = unpack_result(packed, root / "restored")
            self.assertEqual((restored / "first").read_bytes(), content)
            self.assertTrue((restored / "first").stat().st_mode & 0o111)
            self.assertFalse((restored / "second").stat().st_mode & 0o111)
            self.assertEqual((restored / "manifest.json").read_bytes(), (bundle / "manifest.json").read_bytes())

    def test_existing_outputs_and_recursive_pack_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, _ = run_calibration(root / "input", trials=1)
            with self.assertRaises(ExperimentError):
                pack_result(bundle, bundle / "recursive.tar.gz")
            nested = root / "existing-parent"
            nested.mkdir()
            with self.assertRaises(ExperimentError):
                pack_result(bundle, nested / ".." / "input" / "recursive.tar.gz")
            output = root / "keep"
            output.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                pack_result(bundle, output)
            self.assertEqual(output.read_bytes(), b"keep")

    def test_traversal_symlinks_duplicate_entries_and_bombs_fail_before_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, kind in enumerate(("traversal", "symlink", "duplicate", "huge")):
                archive_path = root / (kind + ".tar.gz")
                with tarfile.open(archive_path, "w:gz") as archive:
                    member = tarfile.TarInfo("../outside" if kind == "traversal" else "index.json")
                    if kind == "symlink":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/tmp"
                    member.size = 33*1024**2 if kind == "huge" else 0
                    # Header-only oversized payload tests early bounds without
                    # allocating or decompressing a large fixture.
                    archive.fileobj.write(member.tobuf())
                    if kind == "duplicate":
                        archive.addfile(tarfile.TarInfo("index.json"))
                with self.assertRaises((ExperimentError, tarfile.TarError)):
                    unpack_result(archive_path, root / str(index))
                self.assertFalse((root / str(index)).exists())

    def test_index_rejects_conflicting_paths_and_inconsistent_chunk_counts(self):
        record = {"size": 0, "chunks": [], "sha256": "0"*64, "executable": False}
        for files in ({"../escape": record}, {"a": record,"a/b": record}, {"/absolute": record},
                      {"a": {**record, "size": CHUNK+1, "chunks": ["0"*64]}},
                      {"a": {**record, "executable": 1}}):
            with self.subTest(files=files), self.assertRaises(ExperimentError):
                _index({"schema_version":1,"chunk_bytes":CHUNK,"files":files})
