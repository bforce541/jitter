# Jitter

A C++ performance research system for multithreaded, latency-sensitive software.

Jitter's intended goal is to minimize p99 request latency while preserving application behavior and meeting throughput, memory, and CPU constraints. It will investigate supported applications, select experiments, validate candidates, and return reproducible evidence for a configuration or proposed patch.

## Current status

Phase 0: repository foundation only. The executable prints a setup message; measurement, tuning, instrumentation, and reports are not implemented yet.

## Build and run

Requires CMake 3.20 or newer and a C++20 compiler.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
./build/jitter
```

GitHub Actions builds and runs the foundation executable on Linux and macOS.

## Planned interface

A CLI will run investigations. A local HTML report will present measurements, experiment history, recommendations, uncertainty, and reproduction instructions.

## Scope

Optimization will operate through documented application adapters, configuration controls, implementation alternatives, and bounded transformations. Jitter will not claim to safely rewrite arbitrary C++ programs. Correctness checks provide evidence within their coverage.

See [the roadmap](docs/roadmap.md) for the five implementation phases.
