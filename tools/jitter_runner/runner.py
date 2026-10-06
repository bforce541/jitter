"""Baseline orchestration, persistence, and reproduction."""

from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid

from .adapters import get_adapter
from .metrics import summarize_runs
from .spec import ExperimentError, load_json, load_spec, validate_spec
from .storage import git_output, seal_bundle, sha256, snapshot_source, source_hashes, verify_integrity, verify_source_archive, write_json
from .workload import encode_workload, generate

ROOT = Path(__file__).resolve().parents[2]


def host_info():
    info = {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "logical_cpus": os.cpu_count(), "python": platform.python_version()}
    if hasattr(os, "sched_getaffinity"):
        info["available_cpu_ids"] = sorted(os.sched_getaffinity(0))
    if platform.system() == "Darwin":
        result = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5)
        info["cpu_model"] = result.stdout.strip() if result.returncode == 0 else "unavailable"
    elif platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name") or line.startswith("Hardware"):
                    info["cpu_model"] = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    return info


def new_output(path):
    if path is None:
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        path = ROOT / "results" / name
    path = Path(path).absolute()
    path.mkdir(parents=True, exist_ok=False)
    return path.resolve()


def verify_bundle(path):
    path = Path(path).resolve()
    manifest = verify_integrity(path)
    spec = load_spec(path / "spec.json")
    workload = generate(spec)
    if (path / "workload.csv").read_bytes() != encode_workload(workload):
        raise ExperimentError("saved workload does not match its seed and specification")
    adapter = get_adapter(spec["application"]["adapter"])
    runs = []
    for index in range(spec["repetitions"]):
        run_dir = path / f"run-{index + 1:03d}"
        run = adapter.validate(spec, workload, run_dir)
        if run != load_json(run_dir / "summary.json"):
            raise ExperimentError(f"saved summary differs from raw evidence: {run_dir.name}")
        runs.append(run)
    summary = summarize_runs(spec, runs)
    if summary != load_json(path / "summary.json"):
        raise ExperimentError("aggregate summary differs from raw evidence")
    metadata = load_json(path / "metadata.json")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("source"), dict) or not isinstance(metadata.get("build"), dict):
        raise ExperimentError("invalid provenance metadata")
    if metadata.get("workload_sha256") != sha256(path / "workload.csv"):
        raise ExperimentError("recorded workload identity mismatch")
    if metadata.get("source_archive_sha256") != sha256(path / "source.tar.gz"):
        raise ExperimentError("recorded source archive identity mismatch")
    if metadata["build"].get("contract") != spec["application"]["adapter"]:
        raise ExperimentError("recorded build contract mismatch")
    verify_source_archive(path / "source.tar.gz", metadata["source"].get("files"))
    if metadata.get("engine_sha256") != sha256(path / "engine" / "jitter"):
        raise ExperimentError("recorded executable identity mismatch")
    if manifest["status"] != summary["status"]:
        raise ExperimentError("manifest and summary status disagree")
    return spec, summary, metadata


def run_experiment(spec, output=None, binary=None, build_dir=None, reproduce_from=None):
    validate_spec(spec)
    adapter = get_adapter(spec["application"]["adapter"])
    original = None
    if reproduce_from:
        original = Path(reproduce_from).resolve()
        original_spec, _, original_metadata = verify_bundle(original)
        if original_spec != spec:
            raise ExperimentError("reproduction specification differs from original")
        if original_metadata["host"]["system"] != platform.system() or original_metadata["host"]["machine"] != platform.machine():
            raise ExperimentError("saved executable requires the original OS and architecture; rebuild from the source snapshot on this host")
    path = new_output(output)
    write_json(path / "manifest.json", {"schema_version": 1, "status": "running", "artifacts": {}})
    write_json(path / "spec.json", spec)
    workload = generate(spec)
    (path / "workload.csv").write_bytes(encode_workload(workload))
    (path / "engine").mkdir()
    (path / "build-logs").mkdir()
    runs = []
    metadata = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "host": host_info(), "driver_command": sys.argv,
                "workload_sha256": sha256(path / "workload.csv"),
                "measurement_contract": "intended-arrival-to-completion-v1",
                "reproduce_from": str(original) if original else None,
                "environment_note": "Host state is recorded, not controlled. No equivalence or identical timing is guaranteed."}
    write_json(path / "metadata.json", metadata)
    try:
        if original:
            source_binary = original / "engine" / "jitter"
            shutil.copy2(original / "source.tar.gz", path / "source.tar.gz")
            metadata["source"] = original_metadata["source"]
            metadata["build_commands"] = original_metadata["build_commands"]
            metadata["binary_origin"] = "saved executable"
        else:
            hashes = snapshot_source(ROOT, path / "source.tar.gz")
            metadata["source"] = {"revision": git_output(ROOT, ["rev-parse", "HEAD"]).decode().strip(),
                                  "dirty": bool(git_output(ROOT, ["status", "--porcelain"]).strip()),
                                  "files": hashes}
            if binary:
                source_binary = Path(binary).resolve()
                metadata["build_commands"] = []
                metadata["binary_origin"] = "supplied executable; source correspondence is not guaranteed"
            else:
                source_binary, commands = adapter.build(ROOT, Path(build_dir).resolve() if build_dir else ROOT / "build", path / "build-logs")
                metadata["build_commands"] = commands
                metadata["binary_origin"] = "built from current checkout"
            if hashes != source_hashes(ROOT):
                raise ExperimentError("source changed during setup; rerun with a stable checkout")
        shutil.copy2(source_binary, path / "engine" / "jitter")
        saved_binary = path / "engine" / "jitter"
        metadata["build"] = adapter.build_info(saved_binary)
        if spec["schema_version"] == 2 and metadata["build"].get("trace_format") != 1:
            raise ExperimentError("schema version 2 requires a trace-format-1 engine")
        metadata["engine_sha256"] = sha256(saved_binary)
        metadata["source_archive_sha256"] = sha256(path / "source.tar.gz")
        if original:
            metadata["reproduction_host_changes"] = {
                key: {"original": original_metadata["host"].get(key), "current": value}
                for key, value in metadata["host"].items() if original_metadata["host"].get(key) != value}
        write_json(path / "metadata.json", metadata)
        for index in range(spec["repetitions"]):
            run_dir = path / f"run-{index + 1:03d}"
            run_dir.mkdir()
            command = adapter.command(saved_binary, spec, path / "workload.csv", run_dir)
            write_json(run_dir / "execution.json", {"command": command, "timeout_seconds": spec["timeout_seconds"]})
            print(f"Running repetition {index + 1}/{spec['repetitions']}...", flush=True)
            with (run_dir / "stdout.log").open("w") as stdout, (run_dir / "stderr.log").open("w") as stderr:
                result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=spec["timeout_seconds"])
            if result.returncode:
                raise ExperimentError(f"application exited with status {result.returncode}; see {run_dir / 'stderr.log'}")
            run = adapter.validate(spec, workload, run_dir, persist=True)
            write_json(run_dir / "summary.json", run)
            runs.append(run)
            measurement = run["phases"]["measurement"]
            p99 = measurement["latency_ns"]["p99"]
            latency = f"{p99 / 1e6:.3f} ms" if p99 is not None else "unavailable"
            print(f"  p99 {latency}, {measurement['throughput_per_second']:.1f} completed/s, "
                  f"{measurement['rejected']} rejected, constraints {'passed' if run['feasible'] else 'failed'}", flush=True)
        summary = summarize_runs(spec, runs)
        write_json(path / "summary.json", summary)
        seal_bundle(path, summary["status"])
        verify_bundle(path)
        return path, summary
    except (Exception, KeyboardInterrupt) as error:
        # Failed runs retain their inputs and logs, and cannot verify as valid evidence.
        summary = summarize_runs(spec, runs)
        summary["status"] = "failed"
        summary["failure"] = {"type": type(error).__name__, "message": str(error)}
        write_json(path / "metadata.json", metadata)
        write_json(path / "summary.json", summary)
        seal_bundle(path, "failed")
        raise ExperimentError(f"experiment failed: {error}; evidence retained at {path}") from error
