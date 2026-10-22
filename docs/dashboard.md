# Local investigation dashboard

```sh
jitter ui results
# Same viewer without installing the command:
python3 tools/jitter.py ui results
# A single investigation or an evaluation directory also works:
jitter ui results/investigation --port 0 --no-open
```

The command serves a read-only browser interface on `127.0.0.1:8765` and opens the local URL. `--port 0` chooses a free port; `--no-open` prints it without opening a browser. Stop with Ctrl-C. It requires no JavaScript build, downloaded fonts, CDN, account, API key or external backend. After Python build dependencies are installed, the interface works offline with zero per-use API cost. The CLI remains the automation, execution and reproduction interface.

The library discovers investigations, standalone measurements and alternating instrumentation-overhead studies under the selected directory. Evaluation child investigations are discovered automatically. Filter by path, application or strategy, or select an outcome filter. Refresh reloads the selected result and clears its verification badge. Running/failed journals can be inspected; missing evidence stays missing. Discovery stops at 2,000 bundles / 30,000 directories / nine nested levels; choose a narrower root if the limit is reached. Nested source snapshots, engines and measurement directories are excluded from discovery.

The overview shows baseline and candidate quantiles, throughput, CPU, memory, paired effects, one-sided confidence bounds, search progression, current/best configuration, consumed/remaining process/build/failure/time budgets, runtime and application CPU/setup cost. Tail quantiles are withheld when recorded per-run sample guards fail. Other metrics remain descriptive. Separately one-sided bounds are not presented as a joint confidence interval. Recorded host equality does not establish independent runs or a quiet machine.

Search & decisions connects each configuration to its selection rationale, comparison status, constraint/validity reasons and uncertainty. Exploratory estimates are visibly distinct from a fixed fresh confirmation block. A promising exploratory estimate never becomes an accepted recommendation in the viewer. Hypotheses shows mechanism → selected test → observed comparison, including attribution caveats. An accepted change is not proof of the proposed cause.

Experiments provides an ordered process timeline and inspector for the specification, environment, raw summary, correctness, constraints, selection/decision reasons, execution provenance and stdout/stderr. Failed records retain their errors. Select a repetition for standalone multi-run measurements; trace details load one repetition at a time. Log previews are capped at 128 KiB and explicitly marked when truncated. JSON artifacts larger than 16 MiB are rejected by the viewer; the CLI is available for deeper inspection.

Dependencies displays actual recorded request/resource/thread paths, ranked by overlap for a selected target request, alongside trace coverage, suspected bottlenecks and limitations. It shows at most twelve edges for that request and reports the omitted count. Overlapping worker/owner spans are lower-bound observations and must not be summed as additive causal delay. Trace loss withholds diagnosis; processes without a trace display an explicit absence state.

Instrumentation overhead appears as its own result in the library. It shows real alternating on/off pairs, trace drops, reserved memory and signed median deltas. Negative deltas are retained as noisy observations, not claimed as instrumentation making the application faster. No unmeasured dollar/electricity cost is invented.

Provenance retains source/engine/driver distinctions, scientific limits, the exact CLI verification command and a shell-quoted reproduction command. The proposed output directory must be new. A copied replay command reruns adaptive decisions and may produce different timings or conclusions.

## Evidence trust and local access

The interface initially labels decisions as **stored, unverified**. “Verify evidence” invokes the same raw integrity and semantic validators as the CLI. It executes no saved application code. A successful badge has a timestamp, applies to that displayed snapshot at that instant, and is cleared on refresh/selection. A changed displayed report must be reloaded before verification. Evidence can change afterward; reverify before relying on it. Verification is serialized to avoid concurrent expensive checks. The original CLI remains authoritative.

The server binds only to IPv4 loopback. It rejects foreign Host/Origin and cross-site browser requests, has no execution or file-writing endpoints, never lists arbitrary files and confines reads to discovered bundles. Symlinks and traversal paths are rejected. Content is escaped and served with a same-origin content security policy. Like other local developer tools, this is intended for a trusted local machine; do not expose its port through a proxy or tunnel. The viewer does not change immutable artifact manifests or repair incomplete evidence.
