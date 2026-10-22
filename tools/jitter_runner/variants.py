"""Registered implementation alternatives and immutable candidate source builds."""

import shutil
import tarfile
import tempfile
import time
from pathlib import Path

from .adapters import get_adapter
from .spec import ExperimentError
from .storage import git_output, sha256, snapshot_source, verify_source_archive, write_json

REGISTRY = {
    "scan": {"adapter": "sqlite-aggregate-v1", "description": "Execute exact read-only aggregates using the upstream SQLite table scan.",
             "legality": "Generated immutable integer table only; automatic indexes disabled; no user SQL or schema observations."},
    "indexed": {"adapter": "sqlite-aggregate-v1", "description": "Create a (bucket,seq) index before arrivals and execute the same aggregate query.",
                "legality": "Only physical lookup organization changes; exact integer outputs and all request identities are checked; index memory remains constrained."},
    "coarse": {"adapter": "shared-index-v1", "description": "Serialize immutable lookup and digest under the shared mutex.",
               "legality": "Reference affine uint64 response contract; dataset is initialized before workers and never mutated."},
    "narrow": {"adapter": "shared-index-v1", "description": "Copy immutable input under the mutex; compute the same digest after release.",
               "legality": "Only applicable to the registered immutable dataset with request-local digest state; no writes, aliases, or lifetime extension."},
    "map": {"adapter": "json-projection-v1", "description": "Parse and project using pinned nlohmann::json ordered-map objects.",
            "legality": "Unique integer fields only; output excludes object order, reference lifetime, serialization and duplicate-key behavior."},
    "ordered": {"adapter": "json-projection-v1", "description": "Parse and project using pinned nlohmann::ordered_json vector objects.",
                "legality": "Same bounded projection contract as map; semantic equality is checked independently for every response."},
}


def describe_variant(adapter, implementation):
    if implementation not in REGISTRY or REGISTRY[implementation]["adapter"] != adapter:
        raise ExperimentError("unsupported implementation transformation for this adapter")
    return {"schema_version": 1, "identity": implementation, **REGISTRY[implementation],
            "transformation": "registered CMake JITTER_VARIANT selection; no arbitrary source rewriting",
            "validation": "independent per-response oracle, complete accounting, versioned trace grammar",
            "rollback": "baseline source is never edited; candidate build failure retains logs and discards only its temporary build directory"}


def freeze_source(root, destination, original=None):
    """Materialize only validated regular archive members, never extract paths blindly."""
    destination = Path(destination)
    destination.mkdir()
    archive = destination / "source.tar.gz"
    if original:
        shutil.copy2(original["archive"], archive)
        source = original["source"]
    else:
        files = snapshot_source(root, archive)
        source = {"revision": git_output(root, ["rev-parse", "HEAD"]).decode().strip(),
                  "dirty": bool(git_output(root, ["status", "--porcelain"]).strip()), "files": files}
    verify_source_archive(archive, source["files"])
    tree = destination / "tree"
    tree.mkdir()
    with tarfile.open(archive, "r:gz") as saved:
        for member in saved.getmembers():
            target = tree / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with saved.extractfile(member) as incoming, target.open("wb") as output:
                shutil.copyfileobj(incoming, output)
    write_json(destination / "source.json", source)
    return {"root": str(tree), "archive": str(archive), "source": source}


def build_variant(adapter, implementation, context, directory, timeout=180, supplied=None):
    directory = Path(directory)
    directory.mkdir()
    write_json(directory / "variant.json", describe_variant(adapter, implementation))
    logs = directory / "logs"
    logs.mkdir()
    start = time.monotonic()
    try:
        if supplied:
            binary, commands = Path(supplied), []
        else:
            # Build products are private and temporary; logs, settings, sources and
            # executable survive. No CMake cache can poison another candidate.
            with tempfile.TemporaryDirectory(prefix="jitter-build-") as temporary:
                binary, commands = get_adapter(adapter).build(Path(context["root"]), Path(temporary), logs,
                                                               implementation=implementation, timeout=timeout)
                shutil.copy2(binary, directory / "jitter")
            binary = directory / "jitter"
        if supplied:
            shutil.copy2(binary, directory / "jitter")
        info = get_adapter(adapter).build_info(directory / "jitter", timeout=max(.001, min(10, timeout - (time.monotonic() - start))))
        if info.get("compiled_variant", "any") not in ("any", implementation):
            raise ExperimentError("candidate executable implementation does not match its registration")
        result = {"status": "complete", "implementation": implementation, "build": info,
                  "commands": commands, "engine_sha256": sha256(directory / "jitter"),
                  "source_files": context["source"]["files"], "seconds": time.monotonic() - start,
                  "origin": "saved/supplied executable" if supplied else "isolated immutable source build"}
        write_json(directory / "build.json", result)
        return result
    except (Exception, KeyboardInterrupt) as error:
        write_json(directory / "build.json", {"status": "failed", "implementation": implementation,
                                             "seconds": time.monotonic() - start, "error": str(error)})
        raise
