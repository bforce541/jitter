# Troubleshooting

**Missing compiler, CMake or Git:** run `jitter doctor`. Install the missing tool through your platform's normal development-tool installation, then retry. A C++20 compiler, C compiler, CMake 3.20+ and Python 3.9+ are required.

**Installed CLI cannot locate source:** the supported installation is `python3 -m pip install -e .` from a retained source checkout. A Python wheel alone does not contain the application and vendored sources.

**Output already exists:** choose a new output directory or archive filename. Jitter preserves completed and failed evidence.

**No recommendation:** inspect `decision`, `stop_reason`, each trial's `comparison.reasons`, and the confirmation block. A promising median alone is insufficient. Resource violations, rejected work, unstable delivery, unsupported tails, inadequate confidence or an incomplete holdout prevent acceptance.

**Insufficient p99/p99.9 support:** increase completed requests per process under the same declared workload contract, then start a new investigation. Default guards require at least 2,000/20,000 completions respectively. Adding more processes does not fix insufficient within-process tail support. Do not erase failed or rejected requests to increase apparent sample quality.

**Dropped trace events:** inspect `trace_quality`. Increase events per thread within the allocation and memory constraints and rerun. Warmup also consumes capacity. Lost traces withhold hypotheses and edges; timing comparisons use separate trace-disabled processes.

**Large unattributed lock wait:** the analyzer can observe waiting without a matching sampled owner. Diagnostic schema 2 reports this uncertainty explicitly. Extend supported scheduling/ownership observations or repeat a controlled experiment; do not assume every wait was caused by the busiest region.

**Application crash, deadlock or timeout:** inspect the retained child's stderr, execution record and failed summary. Process groups are terminated on timeout/interrupt. Failed candidate state cannot become a recommendation or poison a subsequent build.

**Interrupted investigation:** inspect the terminal journal, then use `jitter restart BUNDLE --output NEW`. Restart creates a new holdout. It does not combine a favorable part of an interrupted confirmation with new observations. A bundle that failed integrity/semantic verification needs diagnosis before reuse; a new run from the saved validated specification is safer than relabeling it complete.

**Saved executable fails on another host:** exact executable replay requires the original OS and architecture. You can still verify and inspect raw evidence. To collect new measurements, build from `BUNDLE/sources/tree` (or the standalone source archive) on the target host and run the saved specification with that compatible engine; retain that new build's provenance. Timing equivalence is never guaranteed.

**Portable archive fails restoration:** malformed paths, links, duplicates, missing content and checksum/size mismatches are rejected. Use a new restore directory; incomplete restored files remain for diagnosis. Obtain the intact archive rather than editing checksums.

**Different conclusions on repeat:** inspect all run-level effects, baseline spread, scheduling tolerance, constraints and confirmation bounds. The initial evaluation is from one uncontrolled machine. Fresh processes and recorded metadata cannot control thermal state, frequency, background load or all dependence between experiments.

**Shared CI runner produces no win:** this is acceptable. CI tests functionality and evidence handling, not performance superiority. Laboratory-grade performance gates are deliberately absent.
