# Original Jitter baseline

Captured before measuring the new market workload. Source and executable build: clean
`d110fb2c902d34ca281aa19df783326f07cb1ec3`. Apple M4 Pro, arm64,
Darwin 25.6.0, Apple clang 21.0.0 (clang-2100.4.1.1), CMake 4.1.1, CMake Release. The executable built without an explicit architecture was
x86-64 under Rosetta, as confirmed by `file`; arm64 describes the host. Both
market variants were subsequently rebuilt explicitly for native ARM64.

```sh
cmake -S . -B results/market-baseline/build -DCMAKE_BUILD_TYPE=Release
cmake --build results/market-baseline/build --parallel 4
ctest --test-dir results/market-baseline/build --output-on-failure
python3 tools/jitter.py run examples/baseline.json --binary results/market-baseline/build/jitter --output results/market-baseline/measurement
python3 tools/jitter.py verify results/market-baseline/measurement
```

Both original CTest suites passed (90.55 s). All three repetitions verified, each
with 2,000 measured requests and zero rejections. P99 observations: 409,125 ns,
872,708 ns and 289,625 ns; throughput about 1,999.8 completed/s each.
See [raw summary](legacy-summary.json). This is the original shared-index service,
not the baseline for the market engine.
