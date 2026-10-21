"""Defined build, launch, and validation integration points."""

import json
import time
from pathlib import Path

from .metrics import analyze_run
from .spec import ExperimentError
from .storage import write_json
from .diagnostics import diagnose
from .spec import load_json
from .process import run_command


class SharedIndexAdapter:
    name = "shared-index-v1"

    def build(self, root, build_dir, logs, implementation="any", timeout=180):
        deadline = time.monotonic() + timeout
        commands = [
            ("configure", ["cmake", "-S", str(root), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release", f"-DJITTER_VARIANT={implementation}"]),
            ("build", ["cmake", "--build", str(build_dir), "--parallel", "2", "--target", "jitter"]),
        ]
        for name, command in commands:
            with (logs / f"{name}.stdout.log").open("w") as stdout, (logs / f"{name}.stderr.log").open("w") as stderr:
                result = run_command(command, stdout=stdout, stderr=stderr, timeout=max(.001, deadline - time.monotonic()))
            if result.returncode:
                raise ExperimentError(f"{name} failed; see {logs}")
        selected = {"CMAKE_BUILD_TYPE", "CMAKE_CXX_COMPILER", "CMAKE_CXX_FLAGS", "CMAKE_CXX_FLAGS_RELEASE",
                    "CMAKE_EXE_LINKER_FLAGS", "CMAKE_EXE_LINKER_FLAGS_RELEASE", "CMAKE_GENERATOR",
                    "CMAKE_MAKE_PROGRAM", "CMAKE_OSX_ARCHITECTURES", "CMAKE_OSX_DEPLOYMENT_TARGET",
                    "CMAKE_OSX_SYSROOT", "CMAKE_TOOLCHAIN_FILE", "CMAKE_CXX_COMPILER_TARGET"}
        cache = {}
        for line in (build_dir / "CMakeCache.txt").read_text().splitlines():
            if "=" in line and ":" in line and not line.startswith(("#", "//")):
                definition, value = line.split("=", 1)
                key = definition.split(":", 1)[0]
                if key in selected:
                    cache[key] = value
        write_json(logs / "build-config.json", cache)
        binary = build_dir / "jitter"
        return binary, [command for _, command in commands]

    def build_info(self, binary):
        result = run_command([str(binary), "--build-info"], capture_output=True, text=True, timeout=10, check=True)
        info = json.loads(result.stdout)
        required = {"version", "compiler_id", "compiler_version", "build_type", "cxx_flags",
                    "source_revision", "source_dirty", "system", "processor", "contract"}
        if not isinstance(info, dict) or not required.issubset(info) or self.name not in info.get("contracts", [info["contract"]]):
            raise ExperimentError(f"binary does not implement the {self.name} contract")
        return info

    def command(self, binary, spec, workload, output):
        app = spec["application"]
        command = [str(binary), "--workload", str(workload), "--output", str(output)]
        behavior = ("light_fields", "heavy_fields") if self.name == "json-projection-v1" else ("light_rounds", "heavy_rounds")
        for name in ("workers", "queue_capacity", "key_count", "dataset_seed", *behavior):
            command.extend(["--" + name.replace("_", "-"), str(app[name])])
        if spec["schema_version"] >= 3:
            command.extend(["--adapter", self.name, "--implementation", app["implementation"]])
        for name in ("warmup", "measurement"):
            command.extend([f"--{name}-ns", str(spec["workload"][f"{name}_ms"] * 1000000)])
        if spec["schema_version"] >= 2:
            tracing = spec["instrumentation"]
            command.extend(["--trace", str(int(tracing["enabled"])), "--trace-capacity", str(tracing["events_per_thread"])])
        return command

    def validate(self, spec, workload, output, persist=False):
        baseline = analyze_run(spec, workload, output)
        if spec["schema_version"] >= 2:
            report = diagnose(spec, output, baseline)
            baseline["observation"] = report["trace_quality"]
            if persist:
                write_json(Path(output) / "diagnostics.json", report)
            elif report != load_json(Path(output) / "diagnostics.json"):
                raise ExperimentError("saved diagnostics differ from raw trace evidence")
        return baseline


class JsonProjectionAdapter(SharedIndexAdapter):
    name = "json-projection-v1"


ADAPTERS = {adapter.name: adapter for adapter in (SharedIndexAdapter(), JsonProjectionAdapter())}


def get_adapter(name):
    try:
        return ADAPTERS[name]
    except KeyError as error:
        raise ExperimentError(f"unsupported application adapter: {name}") from error
