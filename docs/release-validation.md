# Release validation

Validated during the October 21–22, 2026 release work. Performance evidence and functional checks are separate; the latter do not add performance hosts.

## Measured evidence

The [generated evaluation report](evaluation-report.md) and [machine-readable catalog](evidence/evaluation-catalog.json) retain all 180 original/revised controller investigations, their original policies, current-rule audits, budgets, outcomes and provenance. The revised cohort completed 105 studies: 75 fresh-confirmed recommendations, 30 inconclusive outcomes, zero failed wrappers. The original 75-study cohort remains available, including weaker controller behavior. No negative case was removed to improve reported outcomes.

Supporting artifacts contain 1,000 trials per synthetic calibration case, six known-timestamp diagnostic controls, a default isolated-build SQLite investigation, three alternating pairs per verification-cost trial, and twenty alternating instrumentation-overhead pairs across four configurations. These have distinct scopes and are not pooled as application performance measurements. The measured native engine is pinned independently of the Python controller revisions. See the report for uncontrolled-host and independence assumptions, unsupported claims and mixed baseline comparisons.

Portable raw evidence is published on the private [v1.0.0 release](https://github.com/bforce541/jitter/releases/tag/v1.0.0). The [asset inventory](evidence/release-assets.json) records sizes and SHA256 digests. `jitter pack` verified each input before publication; the main 105-study archive was restored and semantically verified in full before release. Archives include raw requests/responses, traces, preserved sources, engine bytes and manifests. Git contains the catalog and report, not duplicated multi-gigabyte result trees.

```sh
gh release download v1.0.0 --repo bforce541/jitter --pattern jitter-evaluation-v2.tar.gz
jitter unpack jitter-evaluation-v2.tar.gz --output results/released-evaluation
jitter verify results/released-evaluation
jitter ui results/released-evaluation
```

No performance cohort was rerun after building the visual interface. Its original measurements and provenance are preserved.

## Functional and platform checks

- Native C++ tests and 164 Python tests passed locally through CTest, including all five dashboard boundary/integrity tests with a real engine. The suite exercises correctness, accounting, tail support, statistics, search decisions, warmup coverage, timeouts, process-group cleanup, signals, failed builds, schema compatibility, malicious archives, preserved driver provenance and source bounds.
- [CI for the initial dashboard implementation](https://github.com/bforce541/jitter/actions/runs/39739341126) passed all four jobs: Ubuntu Python 3.9, Ubuntu Python 3.12, macOS Python 3.12, and Linux C/C++ address + undefined-behavior sanitizers. It also ran full investigation/replay/portable evidence flows. Subsequent release CI adds the installed dashboard smoke flow. The release is published only after its final commit's CI succeeds.
- Earlier full local address/undefined and thread-sanitizer suites passed after the native implementation was complete. Later changes were Python orchestration, storage, reporting and local inspection; sanitizer CI continues to cover the native engine. Sanitizers and tests provide evidence, not a proof of arbitrary transformation safety.
- A fresh private clone at `1f729ab` built Release and passed all 159 then-current Python tests plus native tests. An editable installation in a visible virtual environment passed `doctor`, baseline, dry-run, default isolated-build investigation, report, verification, saved-engine replay, pack, unpack and restored verification. A hidden-file `.pth` issue in this macOS environment was isolated and documented in [troubleshooting](troubleshooting.md). Source CLI and Linux/macOS CI installed flows remained usable.
- Replay of an older saved application with the newer driver preserved original native source identity separately from the current Python driver and verified successfully. Driver source bytes are retained rather than silently attributing old application sources to new orchestration.

## Visual interface checks

The dashboard was inspected in Chromium and the in-app browser using real saved studies. Desktop layout, a compact panel and a 390-pixel viewport were checked. The narrow layout had matching viewport/document widths (375 CSS pixels after scrollbar), with no page-wide horizontal overflow; tables and tabs scroll within their own regions. Screenshots accompany the release.

Browser checks covered filtering, an accepted study, inconclusive evidence, paired baseline/candidate plots, unsupported p99.9 withholding, search/fresh-confirmation separation, raw verification, process timeline/inspector, correctness/constraints, hypothesis relationships, dependency target selection and instrumentation overhead. Source assets use local URLs only; there are no network font/chart dependencies, accounts or external API calls. HTTP tests reject foreign origins/hosts, cross-site artifact access, traversal, symlinks, malformed/non-finite/oversized JSON, unknown measurements and execution endpoints. A changed displayed snapshot cannot acquire a successful verification badge. Large traces load one repetition at a time; log previews mark truncation.

The fresh checkout was then advanced to `f190694` and its existing visible environment passed the installed dashboard smoke flow. The installed CLI smoke check starts `jitter ui --port 0 --no-open`, reads all packaged assets, selects a real investigation and process, and verifies its raw evidence. The interface is an inspection aid. The CLI remains the reproducible automation surface.

## Limits

This release supports three explicit integration contracts and six registered implementation alternatives. It does not rewrite arbitrary programs, establish a global optimum, prove causal explanations, show universal superiority over tuning baselines, or validate an independently maintained production service. JSON and SQLite are independently maintained upstream engines used through Jitter-owned service drivers. Research claims remain limited to the saved workloads, hardware and observed resource envelope. The complete evaluated failure/inconclusive outcomes and statistical assumptions remain visible.
