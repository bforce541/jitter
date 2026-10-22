"""Revalidate hand-timed diagnostic controls without executing an application."""

from pathlib import Path

from .diagnostics import diagnose
from .metrics import analyze_run
from .spec import ExperimentError, load_json, load_spec
from .storage import verify_integrity
from .workload import generate


CASES = (("owner", "mixed", 1, "shared_lock_contention"),
         ("region", "region", 0, "digest_execution"),
         ("queue", "queue", 2, "queue_pressure"),
         ("scheduler", "mixed", 1, "workload_delivery_delay"),
         ("unowned", "mixed", 1, "unattributed_lock_wait"),
         ("lost", "mixed", 1, "abstain"))


def control_row(name, request, truth, report):
    actual = report["hypotheses"][0]["id"] if report["hypotheses"] else "abstain"
    if report["status"] == "insufficient_trace":
        region, delay = "abstain", "abstain"
    else:
        stage = next(item for item in report["tail_requests"] if item["request_id"] == request)
        region = "digest_execution" if stage["digest_ns"] > 0 else "abstain"
        mapping = {"scheduler_ns": "workload_delivery_delay", "queue_ns": "queue_pressure",
                   "lock_wait_ns": "shared_lock_contention", "digest_ns": "digest_execution",
                   "other_service_ns": "unresolved_service_delay"}
        delay = mapping[max(mapping, key=lambda key: stage[key])]
    return {"case": name, "selected_request": request, "known_mechanism": truth,
            "dependency_analysis": actual, "region_only_baseline": region, "delay_only_baseline": delay,
            "dependency_match": actual == truth, "region_only_match": region == truth,
            "delay_only_match": delay == truth, "trace_complete": report["trace_quality"]["complete"]}


def control_report(rows):
    return {"schema_version": 1, "evidence_kind": "six hand-timed synthetic trace controls, not measured application performance",
            "cases": rows, "matches": {key: sum(row[key] for row in rows) for key in ("dependency_match", "region_only_match", "delay_only_match")},
            "limitations": ["Each case has three handcrafted requests; match counts are mechanism checks, not population accuracy.",
                            "Region-only ranks the observed execution region; delay-only ranks delay components and assumes a lock wait implies contention.",
                            "These transparent weak baselines illustrate ownership evidence and abstention; no comparison with a production profiler is claimed."]}


def verify_diagnostic_evaluation(path):
    root = Path(path).resolve()
    verify_integrity(root, required=("diagnostic-evaluation.json",))
    rows = []
    for name, _, request, truth in CASES:
        case = root / name
        spec = load_spec(case / "spec.json")
        saved = load_json(case / "diagnostics.json")
        report = diagnose(spec, case, analyze_run(spec, generate(spec), case), selected_request=request,
                          analysis_version=saved["schema_version"])
        if report != saved:
            raise ExperimentError(f"diagnostic control differs from raw trace evidence: {name}")
        rows.append(control_row(name, request, truth, report))
    expected = control_report(rows)
    if load_json(root / "diagnostic-evaluation.json") != expected:
        raise ExperimentError("diagnostic evaluation differs from its declared controls and raw evidence")
    return expected
