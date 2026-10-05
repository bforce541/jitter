# Jitter

C++ performance research for multithreaded, latency-sensitive software.

Phase 1 is implemented: controlled baseline experiments, correctness validation, resource measurements, and reproducible result bundles.

```sh
python3 tools/jitter.py run examples/baseline.json --output results/baseline
python3 tools/jitter.py verify results/baseline
python3 tools/jitter.py reproduce results/baseline --output results/reproduction
```

Requires Linux/macOS, a C++20 compiler, CMake 3.20+, Git, and Python 3.9+.

[Usage and measurement contracts](docs/phase1.md) · [Five-phase roadmap](docs/roadmap.md)
