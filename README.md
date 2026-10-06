# Jitter

C++ performance research for multithreaded, latency-sensitive software.

Phases 1 and 2 are implemented: reproducible baselines, correctness checks, bounded execution tracing, dependency evidence, bottleneck hypotheses, and paired instrumentation-overhead studies.

```sh
python3 tools/jitter.py run examples/contention.json --output results/observation
python3 tools/jitter.py diagnose results/observation
python3 tools/jitter.py overhead examples/contention.json --output results/overhead
```

Requires Linux/macOS, a C++20 compiler, CMake 3.20+, Git, and Python 3.9+.

[Observation and overhead](docs/phase2.md) · [Verification record](docs/phase2-validation.md) · [Baseline contracts](docs/phase1.md) · [Five-phase roadmap](docs/roadmap.md)
