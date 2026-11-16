# Desk incident lab

Jitter runs a simulated market-data outage and an independent execution discrepancy. The C++20 market engine receives actual UDP datagrams on two loopback sockets. SQLite records what the desk booked and what the simulated venue reported. The offline report shows the decisions and their verification.

```sh
python3 tools/desk_incident.py --output results/desk-incident
```

Open `results/desk-incident/report.html`. The command builds a native Release executable in the new output directory when `--binary` is absent. A prebuilt `build/jitter_incident` can be supplied with `--binary build/jitter_incident`. Use `--single-feed` with a distinct output directory to run the A-only version. The simulator skips the redundant-only fault at sequence 2 in that version. Both modes exercise the gap, stale state, and snapshot recovery. All data is generated locally.

## Operator runbook

1. **Check sequence and channels.** A omits sequence 2 and B supplies it. The publication record attributes the update to B. Sequence 4 then arrives before 3. While waiting, the book is flagged `waiting-gap` and is unavailable to readers. When 3 arrives, 3 and 4 publish in order. Extra copies leave the publication count unchanged.
2. **Quarantine the book.** Both feeds omit 5. The next packet opens a 3 ms reorder window. At expiry the receiver marks the book stale at sequence 4 and holds later updates. The report exposes the last committed sequence and reason.
3. **Verify recovery.** Snapshot 4 leaves sequence 5 uncovered and is rejected. Sequence 7 arrives while stale. The authoritative snapshot through sequence 5 and buffered 6 and 7 are validated together. The lab compares every replayed quote and the full canonical book at recovery with the sparse reference, then verifies the rest of the replay through 12. Snapshot-covered sequence 5 is reflected in the state and has no delta publication.
4. **Check the execution audit.** At 40 ms on the separate simulation clock, EX-02 is pending with an ABC desk-minus-venue difference of −30. At 80 ms it is confirmed after the 50 ms grace window. At 120 ms the delayed drop copy resolves the difference. Final positions are **ABC +70 and XYZ +5** in both views. Importing the same six original records again produces the same positions and audit.

Inspect `market.json` for publication source, quote, state, and replay proof. The recovered and final book IDs identify canonical entries with the engine's reproducible fingerprint. The verifier compares those entries directly. `incident.json` contains the raw outcome and timings. `discrepancies.csv` carries every discrepancy transition and its source record IDs. The three input CSVs and the append-only raw records in `executions.sqlite` preserve the original evidence.

## Receiver contract

- The simulator uses connected IPv4 UDP sockets bound to loopback and two distinct receive ports. The 24-byte version-1 datagram encodes sequence, order ID, tick, quantity, kind and side in network byte order. Length, version, enum and identifier checks happen before sequencing. An authoritative snapshot arrives via a separate in-process control call from the simulator's reference state. The lab has one instrument and one session.
- At most 256 future sequences are buffered. A first observed future update starts a deadline. Progress through a partial prefix does not extend an outstanding deadline. Valid equal copies from either feed are suppressed. Conflicts detected in the pending buffer or the most recent 256 committed deltas quarantine the book and require a snapshot covering the conflict. Older committed sequences are treated as duplicates. Snapshot installation clears the recent-copy window and repopulates it from replayed deltas.
- Capacity exhaustion discards the incoming packet and raises a snapshot watermark. The book stays stale until the snapshot covers discarded or conflicting events and every retained delta after it is contiguous and valid. A malformed snapshot or invalid buffered event aborts the entire recovery and emits nothing. Late packets after expiry can enter the recovery buffer. Normal publication resumes after the staged candidate book and its buffered updates are accepted together.
- The deterministic incident advances the reorder clock explicitly after a requested wall-clock sleep. The send/receive driver waits for each scheduled local delivery with a bounded poll. The benchmark uses monotonic receiver time and batches real sends and drains on one thread. It captures any missing local datagrams separately from scheduled fault omissions. Feed ordering and local scheduling affect which valid copy wins. The model excludes network snapshot transfer and venue connectivity.

## Execution contract

The matching key is `(session, execution_id)`. An immutable raw record is identified by `(source, record_id)`. A conflicting reuse rejects the whole import transaction. Exact retries leave the raw count and audit unchanged. The highest revision on each source is effective. Multiple distinct records at the highest revision remain visible as duplicates and their signed quantities contribute to that source's reported position. Superseded records remain in the raw ledger. Match instrument, side, quantity and price ticks exactly. Quantity is an integer number of units, BUY is positive and SELL is negative. A price tick is 0.01 simulated quote units for both instruments.

`executed_ns` is the source's simulated execution timestamp. `observed_ns` is its immutable first-observation timestamp. The operator supplies `--as-of-ns`; the database watermark cannot run backwards. A sole missing side is pending until the grace period from the first observation expires. Mismatches and duplicates are confirmed immediately. Higher-revision reports can resolve discrepancies through later imports. Neither incoming source is silently amended. Position impact is desk minus venue per instrument, including disagreements that move quantity between instruments.

To rebuild the audit in a fresh SQLite database from a completed demo:

```sh
out=results/desk-incident
python3 tools/desk_reconcile.py --database "$out/rebuilt.sqlite" --csv "$out/rebuilt.csv" \
  --grace-ns 50000000 --as-of-ns 40000000 \
  --input "$out/desk-bookings.csv" --input "$out/venue-initial.csv"
python3 tools/desk_reconcile.py --database "$out/rebuilt.sqlite" --csv "$out/rebuilt.csv" \
  --grace-ns 50000000 --as-of-ns 80000000
python3 tools/desk_reconcile.py --database "$out/rebuilt.sqlite" --csv "$out/rebuilt.csv" \
  --grace-ns 50000000 --as-of-ns 120000000 --input "$out/venue-late.csv"
```

## Measure and reproduce

Use a clean native checkout. On Apple Silicon add `-DCMAKE_OSX_ARCHITECTURES=arm64` to the configure command. On Linux x86-64 omit it and save the native CPU, kernel, compiler version, allocation/affinity and governor before measuring. The same commands exercise the exact 100K steady and burst workloads, the paired stress comparison, and the deterministic fault.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
cmake --build build --parallel 4
ctest --test-dir build --output-on-failure
python3 tools/incident_study.py --binary build/jitter_incident \
  --output results/desk-study --compile-commands build/compile_commands.json \
  --events 40000 --rate 100000 --repeats 5
python3 tools/desk_incident.py --binary build/jitter_incident --output results/desk-repeat
```

The study alternates mode order over seeds 17–21, keeping each input the same across treatments. It preserves command lines, exit codes, environment, binary hash, compiler commands, per-run quantiles and stage witnesses. A stress campaign can exit 2 after preserving results if publications or final reference checks fail. Dedicated native Linux x86-64 timings are pending. The Linux CI matrix exercises correctness and artifact reproduction.

| Metric | Boundaries |
| --- | --- |
| Sender lateness | Scheduled group release → entry to the winning copy's send path |
| Local send → receipt | Entry to the send path → return of the receive call. Includes encoding, syscall, loopback and time before the receive loop drains the socket. |
| Receiver publication | Return of receive → append of its quote update. Per-event stages are decode, sequencing/buffer residence, and book update/publication. Recovery replay samples are excluded from these ordinary-path distributions. |
| Scheduled publication | Scheduled group release → append of its quote update. Includes the sender, local transport and receiver portions. |
| Peak sent-minus-received | Highest successful-send count minus user-space received count per feed. An outstanding datagram count that can include drops, independent of the receiver's peak reorder-buffer occupancy. |
| Incident detection | Start of the scheduled omission step → observed stale transition |
| Verified recovery | Stale transition → successful snapshot, buffered publications and sparse-reference checks |
| Reconciliation delay | First observation of the missing side's counterpart → confirmation in simulated time. SQLite analysis wall time is recorded separately. |

Nearest-rank quantiles are published at ≥40 observations for p50, ≥2,000 for p99 and ≥20,000 for p99.9. The attached study reports medians of per-run quantiles and preserves separate values for each seed. The experiment runs in one process with no wire timestamps. Read [the receiver results](desk-results.md) and the existing [in-process queue study](market-queue.md) for their individual latency boundaries.

The existing [4M events/s in-process investigation](market-tail.md) already traced a startup producer delay and preserved a rejected first-deadline spin experiment that cost publication coverage. That evidence did not point to a hybrid queue as a fix for the observed tail. The UDP experiment changes only when the local socket is serviced during a burst.
