"""Observed dependencies yield hypotheses, never automatic causal conclusions."""

from .dependencies import build_dependencies
from .trace_reader import read_trace


def diagnose(spec, run_dir, baseline, selected_request=None):
    trace = read_trace(spec, run_dir, baseline)
    quality = trace["quality"]
    if not quality["enabled"]:
        return {"schema_version": 1, "status": "disabled", "trace_quality": quality, "hypotheses": [],
                "limitations": ["Tracing is disabled. Baseline timing and correctness remain available."]}
    evidence = build_dependencies(trace, spec["application"]["workers"], selected_request)
    report = {"schema_version": 1, "status": "analyzed" if quality["complete"] else "insufficient_trace",
              "trace_quality": quality, **evidence, "hypotheses": [],
              "limitations": ["Lock ownership spans are sampled after acquisition and before release; overlaps are lower bounds.",
                              "Region and worker spans include preemption and instrumentation; they are not CPU-time attribution.",
                              "Parallel worker-capacity edges must not be summed into end-to-end delay.",
                              "Observation identifies testable mechanisms, not proof that a change improves performance."]}
    if not quality["complete"]:
        report["limitations"].append("Events were dropped; dependency edges and bottleneck hypotheses are withheld. Increase buffer capacity within the memory budget and rerun.")
    elif evidence["graph"]["edges_truncated"]:
        report["limitations"].append("The selected graph exceeded its edge budget. It is explicitly truncated and cannot represent every dependency.")
    return report
