"""Observed dependencies yield hypotheses, never automatic causal conclusions."""

from .metrics import distribution
from .dependencies import build_dependencies
from .trace_reader import read_trace


def hypotheses(spec, baseline, evidence):
    tail = evidence["tail_requests"]
    total = sum(stage["latency_ns"] for stage in tail)
    components = {key: sum(stage[key] for stage in tail) for key in
                  ("scheduler_ns", "queue_ns", "lock_wait_ns", "digest_ns", "other_service_ns")}
    fraction = lambda key: components[key] / total if total else 0
    current = spec["application"]["workers"]
    result = []
    def add(name, mechanism, component, observations, experiment, caveat):
        result.append({"id": name, "mechanism": mechanism, "causal_status": "hypothesis",
                       "support_ns": components[component], "tail_component_fraction": fraction(component),
                       "evidence": observations, "suggested_experiment": experiment, "caveat": caveat})
    owners = sum(stage["observed_owner_overlap_ns"] for stage in tail)
    if owners > 0 and fraction("lock_wait_ns") >= .1:
        add("shared_lock_contention", "Slow requests wait while other requests own the shared-index mutex.",
            "lock_wait_ns", {"observed_owner_overlap_ns": owners, "blocking_holders": evidence["blocking_holders"],
                             "unattributed_lock_wait_ns": sum(stage["unattributed_lock_wait_ns"] for stage in tail)},
            {"kind": "configuration_comparison", "parameter": "application.workers", "values": sorted({max(1, current // 2), current}),
             "prediction": "Reducing concurrent contenders may reduce lock waiting; compare end-to-end latency and required throughput under identical inputs."},
            "Less lock waiting can shift delay into the queue. Observed hold time can include preemption; no configuration is recommended yet.")
    measurement = baseline["phases"]["measurement"]
    if fraction("queue_ns") >= .1 or measurement["rejected"]:
        max_workers = min(256, 268435456 // (spec["instrumentation"]["events_per_thread"] * 32) - 1)
        alternative = current + 1 if current < max_workers else max(1, current // 2)
        experiment = {"kind": "configuration_comparison", "parameter": "application.workers", "values": sorted({current, alternative}),
                      "prediction": "A worker-count change should alter queue delay if worker capacity is the limiting mechanism; preserve workload and correctness."}
        if measurement["rejected"]:
            capacity = spec["application"]["queue_capacity"]
            experiment = {"kind": "configuration_comparison", "parameter": "application.queue_capacity",
                          "values": sorted({capacity, min(1000000, capacity * 2) if capacity < 1000000 else capacity // 2}),
                          "prediction": "Changing waiting capacity should alter rejection and queueing; compare latency and throughput and respect the memory budget."}
        add("queue_pressure", "Requests accumulate in the bounded queue or are rejected at admission.", "queue_ns",
            {"rejected": measurement["rejected"], "all_workers_busy_queue_ns": sum(stage["all_workers_busy_queue_ns"] for stage in tail)},
            experiment, "Parallel worker overlaps are not additive delay. More workers can worsen shared-lock contention.")
    if fraction("scheduler_ns") >= .1 or not baseline["constraints"]["scheduler_lag"]:
        add("workload_delivery_delay", "Intended arrivals reach admission late, contributing to end-to-end latency.", "scheduler_ns",
            {"scheduler_lag_p99_ns": measurement["scheduler_lag_ns"]["p99"], "delivery_constraint_passed": baseline["constraints"]["scheduler_lag"]},
            {"kind": "repeat_control", "prediction": "Repeat identical inputs on a quieter host and check whether admission lag persists before attributing it to application work."},
            "Admission lag includes producer scheduling and the queue admission mutex; these events do not distinguish the two.")
    if fraction("digest_ns") >= .1:
        heavy = [stage["observed_hold_ns"] for stage in evidence["requests"] if stage["heavy"]]
        light = [stage["observed_hold_ns"] for stage in evidence["requests"] if not stage["heavy"]]
        add("digest_execution", "The selected digest region accounts for a substantial part of slow-request latency.", "digest_ns",
            {"heavy_hold_ns": distribution(heavy), "light_hold_ns": distribution(light),
             "heavy_blocking_holders": sum(holder["heavy"] for holder in evidence["blocking_holders"])},
            {"kind": "diagnostic_workload_comparison", "parameter": "workload.heavy_fraction", "values": [0, 1],
             "prediction": "Holding application behavior fixed, isolate request classes to test their region/ownership durations. This is a diagnostic test, not an optimization comparison."},
            "Wall time in this region includes preemption and tracing. Changing digest rounds changes the response contract and is not a proposed optimization.")
    if fraction("other_service_ns") >= .25 or (not result and components["other_service_ns"] > 0):
        add("unresolved_service_delay", "Existing regions leave service time unexplained by mutex waiting or digest execution.", "other_service_ns",
            {"unclassified_service_ns": components["other_service_ns"]},
            {"kind": "instrumentation_extension", "prediction": "Add a supported execution region or scheduling measurement around the residual interval, then measure its overhead."},
            "Residual time is not assigned to a guessed resource or function.")
    return sorted(result, key=lambda item: (-item["support_ns"], item["id"]))


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
    else:
        report["hypotheses"] = hypotheses(spec, baseline, evidence)
    if evidence["graph"]["edges_truncated"]:
        report["limitations"].append("The selected graph exceeded its edge budget. It is explicitly truncated and cannot represent every dependency.")
    return report
