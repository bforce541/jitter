"""Preserve the Python driver separately from an application's archived source.

Capture bytes once at import, before a long evaluation can outlast edits.
Verification reads these sources; it never executes them.
"""

import copy
import hashlib
from pathlib import Path
import subprocess

from .spec import ExperimentError, object_keys
from .storage import sha256

ROOT = Path(__file__).resolve().parents[2]
_paths = [ROOT / "tools/jitter.py", *sorted(Path(__file__).resolve().parent.glob("*.py"))]
SOURCES = {path.relative_to(ROOT).as_posix():path.read_bytes() for path in _paths if path.is_file()}
try:
    _revision = subprocess.run(["git","-C",str(ROOT),"rev-parse","HEAD"],capture_output=True,text=True,timeout=5,check=True).stdout.strip()
    _dirty = bool(subprocess.run(["git","-C",str(ROOT),"status","--porcelain"],capture_output=True,text=True,timeout=5,check=True).stdout.strip())
except (OSError, subprocess.SubprocessError):
    _revision, _dirty = "unavailable", True
IDENTITY = {"schema_version":1,"revision":_revision,"dirty":_dirty,
            "files":{name:hashlib.sha256(content).hexdigest() for name,content in SOURCES.items()}}


def save_driver(root, application_files):
    if all(application_files.get(name) == digest for name,digest in IDENTITY["files"].items()):
        storage = "application_source_archive"
    else:
        storage = "driver-sources"
        for name,content in SOURCES.items():
            path = Path(root) / storage / name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
    return {"identity":copy.deepcopy(IDENTITY),"storage":storage}


def verify_driver(root, saved, application_files):
    object_keys(saved,("identity","storage"),"driver provenance")
    identity = saved["identity"]
    object_keys(identity,("schema_version","revision","dirty","files"),"driver identity")
    if (type(identity["schema_version"]) is not int or identity["schema_version"] != 1
            or type(identity["revision"]) is not str or type(identity["dirty"]) is not bool
            or not isinstance(identity["files"],dict) or not identity["files"]):
        raise ExperimentError("invalid recorded driver identity")
    for name,digest in identity["files"].items():
        path = Path(name)
        if (path.is_absolute() or ".." in path.parts or path.as_posix() != name
                or (name != "tools/jitter.py" and not (path.parts[:2] == ("tools","jitter_runner") and len(path.parts) == 3 and path.suffix == ".py"))):
            raise ExperimentError("unsafe driver source inventory")
        if not isinstance(digest,str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ExperimentError("invalid driver source checksum")
    if saved["storage"] == "application_source_archive":
        if any(application_files.get(name) != digest for name,digest in identity["files"].items()):
            raise ExperimentError("recorded driver differs from archived application source")
    elif saved["storage"] == "driver-sources":
        directory = Path(root) / "driver-sources"
        if directory.is_symlink() or any(path.is_symlink() for path in directory.rglob("*")):
            raise ExperimentError("driver sources must not contain symlinks")
        actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
        if actual != set(identity["files"]) or any(sha256(directory / name) != digest for name,digest in identity["files"].items()):
            raise ExperimentError("recorded driver differs from its preserved code")
    else:
        raise ExperimentError("unsupported driver source storage")
    return identity
