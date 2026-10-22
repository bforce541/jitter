"""Immutable result bundles with explicit content integrity checks."""

import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tarfile
import tempfile
import shutil

from .spec import ExperimentError, load_json


def link_immutable(source, destination):
    """Share private immutable inputs, retaining ordinary self-contained files.

    Only use for already frozen inputs inside one investigation, never for a
    supplied executable or live checkout file that another build can replace.
    """
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


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
        # Release catalogs are derived evidence, not application/build input.
        # Keep them in Git/release assets instead of recursively embedding a
        # campaign's reports in every subsequent process source snapshot.
        if Path(name).parts[:2] == ("docs", "evidence"):
            continue
        # Capture the supported source tree, not arbitrary personal files.
        if name not in ("CMakeLists.txt", ".gitignore", "README.md", "pyproject.toml", "LICENSE") and Path(name).parts[0] not in ("src", "include", "tools", "tests", "examples", "docs", ".github", "third_party"):
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
                 for path in sorted(root.rglob("*")) if path.is_file() and path != root / "manifest.json"}
    write_json(root / "manifest.json", {"schema_version": 1, "status": status, "artifacts": artifacts})


def verify_integrity(root, required=("spec.json", "workload.csv", "metadata.json", "summary.json", "engine/jitter", "source.tar.gz")):
    root = Path(root).resolve()
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ExperimentError("bundle must not contain symlinks")
    manifest = load_json(root / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("artifacts"), dict):
        raise ExperimentError("invalid bundle manifest")
    if manifest.get("status") != "complete":
        raise ExperimentError("bundle is incomplete or failed; inspect its logs and summary")
    for name, expected in manifest["artifacts"].items():
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ExperimentError(f"invalid artifact checksum: {name}")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ExperimentError("unsafe artifact path in manifest")
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ExperimentError(f"missing or unsafe artifact: {name}")
        if sha256(path) != expected:
            raise ExperimentError(f"artifact checksum mismatch: {name}")
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and path != root / "manifest.json"}
    if actual != set(manifest["artifacts"]):
        raise ExperimentError("bundle has unrecorded or missing files")
    for name in required:
        if name not in manifest["artifacts"]:
            raise ExperimentError(f"bundle missing required artifact: {name}")
    return manifest


def verify_source_archive(path, expected_files, verified=None):
    if not isinstance(expected_files, dict) or not expected_files:
        raise ExperimentError("invalid source inventory")
    if verified is False:
        verified = None
    identity = None
    if verified is not None:
        # Hash the bytes at EVERY path. Only decompression/inventory work may
        # be reused for an identical cryptographic content+inventory identity.
        identity = (sha256(path), hashlib.sha256(json.dumps(expected_files, sort_keys=True).encode()).hexdigest())
        if identity in verified:
            return
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or set(names) != set(expected_files):
            raise ExperimentError("source archive inventory differs from recorded source")
        for member in members:
            name = Path(member.name)
            if not member.isfile() or name.is_absolute() or ".." in name.parts:
                raise ExperimentError("unsafe source archive entry")
            with archive.extractfile(member) as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected_files[member.name]:
                raise ExperimentError(f"source archive content differs from recorded source: {member.name}")
    if verified is not None:
        verified.add(identity)
