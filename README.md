# Jitter

A C++20 market-data replay lab and a toolkit for reproducible tail-latency investigations. Jitter reconstructs a full historical Nasdaq session, checks the books against an independent reference, and measures simulated dual-feed recovery over MoldUDP64. It runs locally on Linux and macOS.

| Workflow | What you get |
| --- | --- |
| [Historical Nasdaq replay](#replay-a-full-nasdaq-session) | Full-session binary ITCH decoding, multi-symbol reconstruction, and paired two-process MoldUDP64 replay |
| [Trading-desk incident](#replay-the-desk-incident) | Redundant loopback UDP, verified snapshot recovery, and a SQLite audit of a delayed simulated fill |
| [Market replay](#benchmark-the-order-book) | A dense order book, a sparse reference, and open-loop benchmarks for sleeping mutex and polling SPSC queues |
| [Latency investigation](#investigate-tail-latency) | Paired experiments, request-level traces, fresh confirmation, and a read-only evidence dashboard |

## Replay a full Nasdaq session

On the official December 30, 2019 ITCH 5.0 sample, Jitter decoded **268,744,780 messages** for **8,906 directory symbols**, cross-checked **263,241,937** displayed order updates, and compared the complete canonical state at the open, close, and end of session. It held up to **1,924,078** live orders. [Full-session record](docs/evidence/itch/session.json)

The public replay shows the same historical opening window under clean delivery and a dual-feed outage. Both runs finish with the same independently verified order book. The recorded JSON includes the stale transition, snapshot coverage, and buffered suffix. [Interactive comparison](https://jitter-sandy.vercel.app/replay/itch/) · [Reproduction guide](docs/itch-replay.md)

An Ubuntu x86-64 VM reproduced the session counts and checkpoint fingerprints. All 20 matched transport runs verified their final books. Receive batching barely reduced syscall count at the tested rate and produced higher median scheduled p99.9, so single receive remains the default. [Linux study and profiling limits](docs/itch-replay.md#recorded-linux-x86-64-vm-result)

## Replay the desk incident

From the repository root, run this with Python 3.9+, CMake 3.20+, and C and C++20 compilers. The command builds the incident executable and writes everything to a new directory.

```sh
python3 tools/desk_incident.py --output results/desk-incident
```

Open `results/desk-incident/report.html`. Feed B supplies sequence **2** when A omits it. When both feeds omit sequence **5**, the book goes stale. Recovery verifies a snapshot through 5 and the buffered deltas through 7 against the sparse reference, then finishes at sequence 12. In a separate execution audit, a delayed drop copy takes the simulated ABC position difference from **−30 to 0**.

| Artifact | What to inspect |
| --- | --- |
| `report.html` | Feed and book timeline, recovery proof, execution discrepancy, and final positions |
| `market.json` and `incident.json` | Publication sequences, source feeds, book fingerprints, and timing boundaries |
| `executions.sqlite` and the input CSVs | Original bookings and venue records retained across retries |
| `discrepancies.csv` | Pending, confirmed, and resolved transitions with source record IDs |

Use `--single-feed` to run the A-only scenario or `--binary build/jitter_incident` to reuse an existing build. The snapshot is supplied in process, and all traffic and executions are simulated locally. The runbook includes the commands to reconstruct the audit in a fresh database. [Desk runbook](docs/desk-incident.md) · [Receiver measurements](docs/desk-results.md)

## Build and test

The full suite requires Git, Python 3.9+, CMake 3.20+, and C and C++20 compilers on Linux or macOS. SQLite and nlohmann/json are pinned and vendored. The build uses a single-configuration CMake generator.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
```

For native Apple Silicon measurements, configure with `-DCMAKE_OSX_ARCHITECTURES=arm64`. The tests cover book replay and recovery, queue FIFO behavior, one- and two-feed UDP, execution reconciliation, and the Python evidence pipeline. The [CI workflow](.github/workflows/build.yml) also runs sanitizer and end-to-end CLI checks.

## Benchmark the order book

Direct mode measures book mutation and top-of-book publication. Feed mode adds a paced producer and a bounded queue. The dense book uses fixed order and price-level arrays with bitmaps for best-quote lookup. Feed runs expose queue drops, stale outputs, and publication coverage alongside their percentiles. The sleeping mutex is the default, and the polling SPSC ring trades CPU for lower handoff latency in the measured steady case. [Book design and verification](docs/market-replay.md) · [Queue tradeoffs](docs/market-queue.md)

```sh
build/jitter_market --mode direct --book dense --events 100000 --warmup 20000 --seed 17
build/jitter_market --mode feed --book dense --queue spsc \
  --events 400000 --warmup 20000 --seed 1001 --rate 500000
```

### Recorded results on a native Apple M4 Pro

| Controlled experiment | Observed median | Evidence |
| --- | ---: | --- |
| Direct replay throughput, sparse reference → dense book | **14.51M → 36.83M events/s** | [Eight book pairs](docs/evidence/market/paired-arm64.json) |
| Steady 500K/s in-process feed, reference → dense with the same mutex queue | **27.333 → 17.771 µs scheduled p99** | [Eight book pairs](docs/evidence/market/paired-arm64.json) |
| Steady 500K/s dense feed, sleeping mutex → polling SPSC | **10.313 → 0.542 µs scheduled p99** and **1.36 → 2.00 approximate CPU cores** | [Eight queue confirmation pairs](docs/evidence/market/queue-confirmation-arm64.json) |
| Steady 100K/s redundant UDP, full publication in five runs | **0.084 µs receiver p99** and **16.125 µs scheduled p99** | [UDP confirmation](docs/evidence/desk/udp-confirmation-arm64.json) |
| Redundant UDP bursts of 4,096, drain at end → drain every 32 events | **18.554 → 0.147 ms local send-to-receipt p99** | [UDP confirmation](docs/evidence/desk/udp-confirmation-arm64.json) |

Each latency value is a median of per-run percentiles. Scheduled latency starts at the prescribed release and ends at publication. UDP receiver latency starts when the receive call returns. Book and queue comparisons used separate paired runs, and the bounded-drain result applies to local delivery under the tested burst shape. At 4M events/s, the original SPSC sweep breached the p99.9 budget, reruns showed variable tails, and a first-deadline pacing candidate was rejected after losing publication coverage. Dedicated Linux x86-64 performance remains unmeasured. [Load envelope](docs/market-queue.md#load-envelope) · [4M tail investigation](docs/market-tail.md) · [UDP timing definitions](docs/desk-incident.md#measure-and-reproduce)

## Investigate tail latency

Use the Python controller with the compiled `build/jitter` engine. It varies registered controls for a shared-index service, nlohmann/json projection, and SQLite aggregation. It checks response correctness, throughput, resource limits, and tail sample counts before accepting a candidate in a fresh confirmation block. Reports retain failed and inconclusive outcomes.

```sh
python3 tools/jitter.py investigate examples/investigation.json --dry-run
python3 tools/jitter.py investigate examples/investigation.json \
  --binary build/jitter --output results/investigation
python3 tools/jitter.py verify results/investigation
python3 tools/jitter.py ui results/investigation
```

Read `results/investigation/report.md` for the decision and `report.json` for structured evidence. The localhost dashboard shows measured runs, search decisions, confirmation, provenance, and request dependencies when a trace is available. The market benchmark and desk lab write their own evidence formats. [Dashboard guide](docs/dashboard.md) · [JSON example](examples/json-investigation.json) · [SQLite example](examples/sqlite-investigation.json)

Omit `--binary` to let an investigation build isolated implementation variants. The optional `python3 -m pip install --no-deps -e .` installs the `jitter` command and uses the source checkout. The tools run from source without the editable install.

```sh
python3 tools/jitter.py reproduce results/investigation --output results/replay
python3 tools/jitter.py pack results/investigation --output results/evidence.tar.gz
python3 tools/jitter.py unpack results/evidence.tar.gz --output results/restored
```

Use new output paths for each run. Portable archives preserve raw inputs, responses, traces, source snapshots, executables, and integrity manifests. Reproduction reruns the saved workload and may reach a different performance decision. [Architecture](docs/architecture.md) · [Evaluation and negative results](docs/evaluation-report.md) · [Release validation](docs/release-validation.md) · [Troubleshooting](docs/troubleshooting.md)
