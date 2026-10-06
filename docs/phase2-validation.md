# Phase 2 verification record

Verified on October 6, 2026. Phase 2 completes observation and bottleneck analysis for `shared-index-v1`. These checks establish supported behavior and evidence handling; they do not establish an optimization win or a negligible observation cost.

## Acceptance evidence

| Phase 2 requirement | Implemented evidence |
| --- | --- |
| Efficient recording | Fixed 32-byte events, preallocated single-writer thread buffers, export after measurement and worker shutdown. No observer allocation, mutex, atomic operation, or file writing on the recording path. |
| Observation quality | Exact per-thread/per-phase attempted, retained, and dropped counts; bounded memory; prefix retention; incomplete traces withhold hypotheses and dependency edges. |
| Supported execution regions | Request admission, queue transitions, worker activity, shared mutex waits/ownership, and digest regions use a common monotonic epoch. |
| Dependency evidence | Observed holder-to-waiter overlaps and occupied-worker-to-queued-request overlaps retain thread, request, resource, and timing identity. |
| Delay propagation | Per-request components sum to intended-arrival latency; parallel capacity overlaps remain separate from unique queue-saturation time. |
| Testable hypotheses | Ranked contention, queue, delivery, digest, and unresolved-service hypotheses include evidence, suggested controls, and attribution limits. |
| Observation overhead | Alternating paired on/off executions pin the binary, sources, workload, and application settings; preserve signed CPU, memory, throughput, and latency differences. |
| Reproduction and integrity | Ordinary and paired bundles verify hashes, request correctness, trace semantics, diagnostics, execution order, and comparisons; saved executables reproduce into new bundles. |
| Compatibility | Schema-version-1 experiments remain supported; the archived Phase 1 final baseline verifies with the current tooling. |

## Automated verification

The Release CTest suite passes the C++ core checks and all 38 Python tests. The same full suite passes local Debug builds with AddressSanitizer plus UndefinedBehaviorSanitizer, and a separate ThreadSanitizer build. Sanitizer runs are correctness checks, not performance measurements. Build commands are in [the Phase 1 verification record](phase1-validation.md#automated-verification).

Controlled event fixtures distinguish mixed lock/queue propagation, isolated digest execution, single-worker queue pressure, and workload-delivery delay. The mixed fixture checks an exact 80 ns observed holder overlap and 82 ns unique queue-saturation interval. Two parallel worker edges total 164 ns; the analyzer deliberately does not report that as 164 ns of queue delay. These are synthetic timestamps testing arithmetic, not a claim about hardware clock precision.

Additional checks cover buffer overflow, event grammar and accounting corruption, resource/worker identity, contradictory mutex ownership, FIFO/capacity/rejection semantics, equal-clock queue ordering, graph truncation, disabled observation, resealed diagnostic/report tampering, source/host comparison guards, and actual-engine ordinary/paired reproduction.

[GitHub Actions run 37518151496](https://github.com/bforce541/jitter/actions/runs/37518151496) passed on both Ubuntu and macOS for revision `15a0e0c95552164982cd41f3e28549c13fb688e4`. Each platform built and tested the project, ran and reproduced a Phase 1 baseline, captured and inspected Phase 2 evidence, reproduced the traced experiment, and verified a paired overhead study.

## Local observation and reproduction

Final evidence was captured from clean source revision `15a0e0c95552164982cd41f3e28549c13fb688e4` with a Release AppleClang executable. Its SHA-256 is `3ffa257a9238faadebe01a6aef390632e82327538fe4d48f5ce3efb271db799b`. Replay and paired study children use this same saved executable.

The contention example offers 50 warmup and 200 measurement requests per repetition. All responses were correct, zero requests were rejected, and all configured constraints passed in the final observation and replay bundles.

| Bundle | Per-repetition p99 latency (ms) | Evidence outcome |
| --- | --- | --- |
| `results/phase2-final-observation` | 13.032, 12.483, 13.786 | Each repetition retained all 3,018 events; diagnostics analyzed the measurement requests. |
| `results/phase2-final-replay` | 16.072, 14.573, 12.691 | Identical saved inputs and executable; timings and dependency graphs changed and were retained. |
| `results/phase2-final-overflow` | 17.811 | Capacity one per thread retained 5 events and dropped 3,013; correctness passed, diagnosis was `insufficient_trace`, hypotheses and edges were empty. |

The first observed repetition generated shared-lock contention, workload-delivery delay, and queue-pressure hypotheses. Its selected tail requests had 43,773,542 ns of observed holder overlap and 174,458 ns of unattributed lock waiting. Ownership overlap is a lower bound, and its sum across different waiters is not a single request's elapsed delay. The report retained 195 dependency edges for eight selected slow requests. Replay changed hypothesis ranking as well as timing; it did not invalidate the evidence.

## Local paired overhead observations

The dedicated `results/phase2-isolated-overhead` study ran after the project's sanitizer and replay jobs finished. Other host activity was uncontrolled. Earlier studies and their replay remain preserved under `results/phase2-final-overhead` and `results/phase2-final-overhead-replay`; these ran alongside verification work and are not used to estimate a stable observation cost.

All three dedicated pairs passed coverage, trace-loss, input/binary/source identity, host-metadata, and workload-delivery checks. Each traced process reserved 5,242,880 event bytes and retained 3,018 events with zero loss.

| Pair and execution order | Off p99 (ms) | On p99 (ms) | On-minus-off p99 (ms) | CPU ns/completion difference | Peak RSS difference (bytes) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1: off, on | 16.344 | 14.458 | -1.885 | -61,005 | 5,210,112 |
| 2: on, off | 16.380 | 16.452 | +0.072 | +3,900 | 5,263,360 |
| 3: off, on | 14.227 | 16.497 | +2.270 | +23,900 | 5,275,648 |

Throughput differences were -0.043, -0.129, and -1.214 completed requests/second. Both signs in latency/CPU observations remain in the report. Three pairs, alternating order, and identical executable/input identity do not control scheduling, thermal state, frequency, or background work. No significance, confidence interval, causal overhead estimate, or overhead guarantee is claimed. Peak RSS is a process high-water mark, not exact allocated-byte accounting.

## Recreate and inspect

The named bundles above are generated local files excluded from Git; a fresh checkout does not contain them. Recreate equivalent inputs and new timing evidence with:

```sh
python3 tools/jitter.py run examples/contention.json --output results/observation
python3 tools/jitter.py diagnose results/observation --run 1
python3 tools/jitter.py diagnose results/observation --run 1 --request 50
python3 tools/jitter.py verify results/observation
python3 tools/jitter.py reproduce results/observation --output results/observation-replay
python3 tools/jitter.py overhead examples/contention.json --output results/overhead
python3 tools/jitter.py verify results/overhead
python3 tools/jitter.py reproduce results/overhead --output results/overhead-replay
```

For overflow behavior, copy the contention specification, set `instrumentation.events_per_thread` to 1 and `repetitions` to 1, then run and diagnose it. Do not interpret verified artifact integrity as lossless observation or a performance win.

Phase 3 autonomous experiment selection, statistical decisions, configuration recommendations, and stopping rules remain the next milestone. See [Phase 2 contracts](phase2.md) and [the roadmap](roadmap.md).
