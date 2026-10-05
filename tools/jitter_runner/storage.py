"""Immutable result bundles with explicit content integrity checks."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

from .spec import ExperimentError, load_json


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def git_output(root, args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return result.stdout


def source_files(root):
    names = git_output(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    files = []
    for name in sorted(set(filter(None, names))):
        path = root / name
        # Capture the supported source tree, not arbitrary personal files.
        if name not in ("CMakeLists.txt", ".gitignore", "README.md") and Path(name).parts[0] not in ("src", "include", "tools", "tests", "examples", "docs", ".github"):
            continue
        if path.is_symlink():
            raise ExperimentError(f"source snapshot does not support symlinks: {name}")
        if path.is_file():
            files.append(name)
    return files


def source_hashes(root):
    return {name: sha256(root / name) for name in source_files(root)}


def snapshot_source(root, destination):
    hashes = source_hashes(root)
    with tarfile.open(destination, "w:gz") as archive:
        for name in hashes:
            archive.add(root / name, arcname=name, recursive=False)
    return hashes


def seal_bundle(root, status):
    artifacts = {str(path.relative_to(root)): sha256(path)
                 for path in sorted(root.rglob("*")) if path.is_file() and path.name != "manifest.json"}
    write_json(root / "manifest.json", {"schema_version": 1, "status": status, "artifacts": artifacts})


def verify_integrity(root):
    root = Path(root).resolve()
    manifest = load_json(root / "manifest.json")
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("artifacts"), dict):
        raise ExperimentError("invalid bundle manifest")
    if manifest.get("status") != "complete":
        raise ExperimentError("bundle is incomplete or failed; inspect its logs and summary")
    for name, expected in manifest["artifacts"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ExperimentError("unsafe artifact path in manifest")
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ExperimentError(f"missing or unsafe artifact: {name}")
        if sha256(path) != expected:
            raise ExperimentError(f"artifact checksum mismatch: {name}")
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and path.name != "manifest.json"}
    if actual != set(manifest["artifacts"]):
        raise ExperimentError("bundle has unrecorded or missing files")
    for required in ("spec.json", "workload.csv", "metadata.json", "summary.json", "engine/jitter", "source.tar.gz"):
        if required not in manifest["artifacts"]:
            raise ExperimentError(f"bundle missing required artifact: {required}")
    return manifest
