"""Portable evidence archives with bounded, content-addressed deduplication."""

import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import tarfile

from .spec import ExperimentError, object_keys
from .storage import link_immutable

CHUNK = 262144
MAX_BYTES = 16 * 1024**3
MAX_FILES = 200000
HASH = re.compile(r"[0-9a-f]{64}")


def verify_result(root):
    root = Path(root)
    if (root / "investigation-spec.json").exists():
        from .investigation import verify_investigation
        return verify_investigation(root)
    if (root / "evaluation-plan.json").exists():
        from .evaluation import verify_evaluation
        return verify_evaluation(root)
    if (root / "calibration.json").exists():
        from .calibration import verify_calibration
        return verify_calibration(root)
    if (root / "overhead-spec.json").exists():
        from .overhead import verify_overhead
        return verify_overhead(root)
    from .runner import verify_bundle
    return verify_bundle(root)


def _add(archive, name, content):
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mode = 0o644
    archive.addfile(member, io.BytesIO(content))


def pack_result(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.is_relative_to(root):
        raise ExperimentError("portable archive must be outside its input bundle")
    verify_result(root)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if len(files) > MAX_FILES or sum(path.stat().st_size for path in files) > MAX_BYTES:
        raise ExperimentError("bundle exceeds the portable archive size limit")
    inventory, seen = {}, set()
    try:
        # Exclusive creation preserves an existing archive, including on races.
        with output.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for path in files:
                chunks, digest, size = [], hashlib.sha256(), 0
                with path.open("rb") as incoming:
                    while True:
                        content = incoming.read(CHUNK)
                        if not content:
                            break
                        digest.update(content)
                        size += len(content)
                        identity = hashlib.sha256(content).hexdigest()
                        chunks.append(identity)
                        if identity not in seen:
                            _add(archive, "blobs/" + identity, content)
                            seen.add(identity)
                inventory[path.relative_to(root).as_posix()] = {"size": size, "sha256": digest.hexdigest(),
                    "chunks": chunks, "executable": bool(path.stat().st_mode & 0o111)}
            index = {"schema_version": 1, "chunk_bytes": CHUNK, "files": inventory}
            _add(archive, "index.json", json.dumps(index, sort_keys=True, allow_nan=False).encode("utf-8"))
        verify_result(root)
    except (Exception, KeyboardInterrupt):
        # Preserve pre-existing paths: only this call's newly created file may
        # be cleaned up. FileExistsError occurs before entering the context.
        raise
    return output


def _index(value):
    object_keys(value, ("schema_version", "chunk_bytes", "files"), "portable archive index")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["chunk_bytes"] != CHUNK or not isinstance(value["files"], dict) or not 1 <= len(value["files"]) <= MAX_FILES:
        raise ExperimentError("unsupported or unbounded portable archive index")
    total, names = 0, set(value["files"])
    for name, record in value["files"].items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name or name in ("", ".") or any(str(parent) in names for parent in path.parents):
            raise ExperimentError("unsafe or conflicting portable archive path")
        object_keys(record, ("size", "sha256", "chunks", "executable"), "portable archive file")
        if type(record["size"]) is not int or not 0 <= record["size"] <= MAX_BYTES or type(record["executable"]) is not bool or not isinstance(record["sha256"], str) or HASH.fullmatch(record["sha256"]) is None:
            raise ExperimentError("malformed portable archive file metadata")
        if not isinstance(record["chunks"], list) or len(record["chunks"]) != math.ceil(record["size"] / CHUNK) or any(not isinstance(item, str) or HASH.fullmatch(item) is None for item in record["chunks"]):
            raise ExperimentError("invalid portable archive chunk inventory")
        total += record["size"]
    if total > MAX_BYTES:
        raise ExperimentError("portable archive exceeds the expanded size bound")
    return value["files"]


def unpack_result(source, output):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ExperimentError("restore output already exists; choose a new directory")
    with tarfile.open(source, "r:gz") as archive:
        members, total = {}, 0
        for member in archive:
            if len(members) >= MAX_FILES + 1 or member.name in members or not member.isfile() or (member.name != "index.json" and re.fullmatch(r"blobs/[0-9a-f]{64}", member.name) is None):
                raise ExperimentError("unsafe, duplicate, or excessive portable archive entries")
            bound = 32 * 1024**2 if member.name == "index.json" else CHUNK
            if not 0 <= member.size <= bound:
                raise ExperimentError("portable archive entry exceeds its size bound")
            total += member.size
            if total > MAX_BYTES:
                raise ExperimentError("portable archive payload exceeds its size bound")
            members[member.name] = member
        if "index.json" not in members:
            raise ExperimentError("portable archive is missing its index")
        from .spec import _unique_object, _invalid_constant
        with archive.extractfile(members["index.json"]) as stream:
            index = json.load(stream, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        files = _index(index)
        needed = {"blobs/" + identity for record in files.values() for identity in record["chunks"]}
        if needed != set(members) - {"index.json"}:
            raise ExperimentError("portable archive has missing or unreferenced content")
        output.mkdir(parents=True, exist_ok=False)
        restored = {}
        for name, record in files.items():
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            file_identity = (record["sha256"], record["size"], tuple(record["chunks"]), record["executable"])
            if file_identity in restored:
                link_immutable(restored[file_identity], path)
                continue
            digest, size = hashlib.sha256(), 0
            with path.open("xb") as destination:
                for identity in record["chunks"]:
                    with archive.extractfile(members["blobs/" + identity]) as stream:
                        content = stream.read(CHUNK + 1)
                    if hashlib.sha256(content).hexdigest() != identity:
                        raise ExperimentError(f"portable archive chunk checksum mismatch; incomplete restoration retained at {output}")
                    size += len(content)
                    digest.update(content)
                    destination.write(content)
            if size != record["size"] or digest.hexdigest() != record["sha256"]:
                raise ExperimentError(f"portable archive file identity mismatch: {name}; evidence retained at {output}")
            path.chmod(0o755 if record["executable"] else 0o644)
            restored[file_identity] = path
    verify_result(output)
    return output.resolve()
