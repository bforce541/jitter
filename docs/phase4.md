# Phase 4: variants, CLI and reproducible evidence

Jitter implements explicit alternatives inside supported integrations. It does not attach transparently to arbitrary C++ processes or rewrite unknown programs.

## Supported integrations

| Adapter | Alternatives | Preserved behavior |
| --- | --- | --- |
| `shared-index-v1` | `coarse`, `narrow` | Identical affine uint64 responses from an immutable seeded dataset. Narrow copies the input under the mutex and computes the request-local digest after release. |
| `json-projection-v1` | `map`, `ordered` | Parse every generated JSON document and look up every required integer field, returning the same projection. |
| `sqlite-aggregate-v1` | `scan`, `indexed` | Execute the same aggregate SQL against identical immutable generated rows. Only the physical index differs. |

The JSON and SQLite engines are independently maintained upstream software; Jitter owns their controlled service drivers and workloads. These integrations do not establish success in an independently maintained production service. Upstream files are vendored unchanged, versioned, licensed/dedicated appropriately, checked by hash during configuration, and included in source snapshots. Builds need no dependency download.

[JSON contract](json-contract.md) and [SQLite contract](sqlite-contract.md) define the exact data, oracle, limits and excluded semantics.

`variants` lists registered descriptions and legality conditions. Transformations select a registered CMake `JITTER_VARIANT`; they cannot insert commands, rewrite arbitrary source, or change behavior parameters. Both scope-changing C++ code and physical SQL indexing are explicit, reviewed alternatives. Correctness tests provide evidence within those contracts, not a proof for arbitrary applications.

## Candidate isolation

An investigation freezes the supported source inventory and archive before its first trial. Each implementation builds in a fresh temporary directory from that immutable tree. Compiler configuration, command argument arrays, logs, executable hash, implementation identity, source inventory and elapsed build time survive in `engines/IMPLEMENTATION/`. Temporary object files are discarded; an interrupted or failed build cannot poison a baseline cache.

The default `any` engine can execute every registered implementation. A restricted engine rejects runtime requests for a different implementation. A supplied executable is recorded as supplied; changing its filename cannot change its hash or compiled identity.

No source modification is needed for rollback. An unsuccessful candidate simply fails to become a recommendation. Every admitted response is checked by an independent Python oracle; duplicate, extra, missing, errored or incorrect responses invalidate the experiment. Latency comparisons require all offered work to complete.

Private immutable archives and executables can share ordinary file hardlinks within an investigation. Supplied binaries and live checkout files remain independent. This reduces repeated evidence storage; a copy fallback supports filesystems without hardlinks. Restored archives can likewise share identical private files while preserving executable modes. Raw evidence is immutable input to verification, not an editable workspace.

## Commands

| Command | Purpose |
| --- | --- |
| `doctor [--json]` | Check platform, source checkout and local C/C++/build tools. |
| `run SPEC --output DIR` | Capture a repeated standalone experiment and validate every response. |
| `diagnose BUNDLE [--run N] [--request ID]` | Inspect supported dependency evidence and hypotheses. |
| `overhead SPEC --output DIR` | Alternate tracing off/on under pinned inputs and executable. |
| `investigate SPEC [--dry-run] --output DIR` | Search adaptively and freshly confirm one frozen finalist. |
| `variants` | List supported implementation alternatives. |
| `report BUNDLE [--json]` | Validate and render an investigation's evidence and decision. |
| `verify BUNDLE`, `summarize BUNDLE` | Validate supported saved evidence; optionally print its structured report. |
| `reproduce BUNDLE --output NEW` | Replay ordinary, overhead, investigation or evaluation evidence using saved executables. |
| `restart BUNDLE --output NEW` | Start a new investigation from saved evidence with a fresh holdout. |
| `evaluate PLAN --output DIR` | Execute equal-budget strategies, ablation and retained negative/inconclusive cases. |
| `calibrate --output DIR` | Run explicitly synthetic statistical controls. |
| `pack BUNDLE --output FILE` | Validate and export a deduplicated portable evidence archive. |
| `unpack FILE --output DIR` | Restore and validate evidence without executing archived programs. |

All output locations must be new. Existing results are never overwritten. Commands also run as `python3 tools/jitter.py ...`; installing the editable source checkout exposes `jitter`.

Zero exit status means the requested workflow produced verified evidence, including legitimate inconclusive or budget-limited outcomes. It does not mean a performance win. Invalid investigations, malformed inputs, unavailable executables and failed verification return nonzero. Inspect `decision`, `stop_reason`, constraints and trace-quality fields.

## Bundle formats and compatibility

Standalone schema versions 1 and 2 preserve earlier contracts; schema 3 adds explicit implementations, external adapters and p99.9. New engines also write `setup.csv`; schema-3 summaries include it when present. Older bundles without this additional preparation evidence remain readable and clearly lack that measurement.

An investigation adds `investigation-spec.json`, `state.json`, `sources/`, `engines/`, `measurements/`, `report.json`, `report.md`, and a hash manifest. `recommendation.json` exists only for a fully confirmed recommendation. Each measurement is a complete independently verifiable standalone bundle, including exact input bytes, engine, raw requests, resources, trace quality, source archive and logs. Failed children retain evidence but cannot verify as complete experiments.

Evaluation artifacts retain their plan, ordered state, every child investigation, generated reports and wrapper costs. The source tree is frozen once for the entire evaluation. Synthetic calibration artifacts identify their generator, seed, assumptions and uncertainty and are never labeled application measurements.

Portable archives deduplicate 256 KiB content chunks while preserving exact file hashes and executable modes. Restoration validates inventory, size bounds, regular-file types, confined paths, blob hashes and the reconstructed result. It never extracts tar paths directly or runs saved executables. Missing/corrupt data leaves an explicitly incomplete restoration. Packing is useful for the large repeated source/binary inventories produced by evaluation.

## Developing an adapter

Define observable responses, bounded inputs, setup and timed work, valid controls and implementation legality before changing measurement code. Add independent oracle checks, build/launch metadata, strict schema and command validation, and trace grammar/resource semantics. Validate success and malformed output, full accounting, overload, timeout, interruption, implementation mismatch and reproduction. A new adapter must join the real-engine integration suite and an evaluation scenario before its behavior is claimed supported.

Workloads and compilers run as local trusted programs. Argument vectors avoid shell interpolation; timeouts terminate inherited POSIX process groups. This is lifecycle management, not a sandbox for hostile programs that detach into their own sessions.

[Controller and statistics](phase3.md) · [Fresh-user workflow](../README.md) · [Troubleshooting](troubleshooting.md)
