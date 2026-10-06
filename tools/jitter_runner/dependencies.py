"""Build bounded, temporal dependency evidence for slow requests."""

from bisect import bisect_right
import math

from .metrics import distribution
from .trace_reader import require

MAX_SELECTED = 64
MAX_EDGES = 8192


class Intervals:
    """Nonoverlapping intervals; indexed overlap queries avoid whole-trace scans."""
    def __init__(self, intervals):
        self.items = sorted(intervals, key=lambda span: (span["start_ns"], span["end_ns"]))
        self.ends = [span["end_ns"] for span in self.items]
        for before, after in zip(self.items, self.items[1:]):
            require(before["end_ns"] <= after["start_ns"], "overlapping intervals in exclusive timeline")

    def overlaps(self, start, end):
        index = bisect_right(self.ends, start)
        while index < len(self.items) and self.items[index]["start_ns"] < end:
            span = self.items[index]
            overlap = min(end, span["end_ns"]) - max(start, span["start_ns"])
            if overlap > 0:
                yield span, overlap
            index += 1


def saturation(services, workers):
    transitions = {}
    for span in services:
        transitions[span["start_ns"]] = transitions.get(span["start_ns"], 0) + 1
        transitions[span["end_ns"]] = transitions.get(span["end_ns"], 0) - 1
    active, previous, intervals = 0, None, []
    for time, delta in sorted(transitions.items()):
        if previous is not None and time > previous and active == workers:
            if intervals and intervals[-1]["end_ns"] == previous:
                intervals[-1]["end_ns"] = time
            else:
                intervals.append({"start_ns": previous, "end_ns": time})
        active += delta
        require(0 <= active <= workers, "worker occupancy exceeds configured capacity")
        previous = time
    require(active == 0, "worker occupancy is unfinished")
    return Intervals(intervals)


def build_dependencies(trace, workers, selected_request=None):
    requests, groups = trace["requests"], trace["groups"]
    measured = [request for request in requests.values() if request["phase"] == "measurement"]
    stages = []
    for request in measured:
        if request["status"] != "completed":
            continue
        group = groups.get(request["id"], {})
        def duration(begin, end):
            if begin not in group or end not in group:
                return None
            value = group[end]["timestamp_ns"] - group[begin]["timestamp_ns"]
            require(value >= 0, "negative observed span")
            return value
        waiting = duration("lock_wait", "lock_acquire")
        digest = duration("region_begin", "region_end")
        service = request["completed_ns"] - request["started_ns"]
        other = service - waiting - digest if waiting is not None and digest is not None else None
        require(other is None or other >= 0, "observed regions exceed response service time")
        stages.append({"request_id": request["id"], "heavy": bool(request["heavy"]),
                       "thread_id": group["request_start"]["thread_id"] if "request_start" in group else None,
                       "latency_ns": request["completed_ns"] - request["scheduled_ns"],
                       "scheduler_ns": request["submitted_ns"] - request["scheduled_ns"],
                       "queue_ns": request["started_ns"] - request["submitted_ns"],
                       "service_ns": service, "lock_wait_ns": waiting, "digest_ns": digest,
                       "observed_hold_ns": duration("lock_acquire", "lock_release"), "other_service_ns": other})
    tail_count = min(MAX_SELECTED, len(stages), max(8, math.ceil(len(stages) * .01)))
    selected = sorted(stages, key=lambda stage: (-stage["latency_ns"], stage["request_id"]))[:tail_count]
    if selected_request is not None:
        require(selected_request in requests and requests[selected_request]["phase"] == "measurement" and
                requests[selected_request]["status"] == "completed", "selected request must be a completed measurement request")
        selected = [stage for stage in stages if stage["request_id"] == selected_request]
    graph = {"selection": "slowest min(64, max(8, ceil(completions/100))) requests" if selected_request is None else "explicit request",
             "selected_request_ids": [stage["request_id"] for stage in selected], "edge_limit": MAX_EDGES,
             "edges_truncated": False, "edges": [], "nodes": [],
             "interpretation": "Temporal overlap evidence; request-level edges are not a critical-path sum or proof of causation."}
    services = []
    for request in requests.values():
        group = groups.get(request["id"], {})
        if "request_start" in group and "request_complete" in group:
            services.append({"request_id": request["id"], "thread_id": group["request_start"]["thread_id"],
                             "start_ns": group["request_start"]["timestamp_ns"], "end_ns": group["request_complete"]["timestamp_ns"]})
    by_worker = {thread: Intervals([span for span in services if span["thread_id"] == thread])
                 for thread in range(1, workers + 1)}
    hold_index = Intervals(trace["holds"])
    saturated = saturation(services, workers) if trace["quality"]["complete"] else Intervals([])
    holder_totals = {}
    node_ids = {stage["request_id"] for stage in selected}
    for stage in selected:
        group = groups.get(stage["request_id"], {})
        stage["observed_owner_overlap_ns"] = None
        stage["unattributed_lock_wait_ns"] = None
        stage["all_workers_busy_queue_ns"] = None
        if not trace["quality"]["complete"]:
            continue # No speculative edges when any recorded evidence was lost.
        def add_edge(kind, span, overlap, resource, start, end):
            if len(graph["edges"]) >= MAX_EDGES:
                graph["edges_truncated"] = True
                return
            node_ids.add(span["request_id"])
            graph["edges"].append({"source": f"request:{span['request_id']}", "target": f"request:{stage['request_id']}",
                                   "kind": kind, "resource_id": resource, "overlap_ns": overlap,
                                   "source_thread_id": span["thread_id"], "target_thread_id": stage["thread_id"],
                                   "start_ns": max(start, span["start_ns"]), "end_ns": min(end, span["end_ns"])})
        wait_start, wait_end = group["lock_wait"]["timestamp_ns"], group["lock_acquire"]["timestamp_ns"]
        overlap_total = 0
        for span, overlap in hold_index.overlaps(wait_start, wait_end):
            require(span["request_id"] != stage["request_id"], "request waits on itself")
            overlap_total += overlap
            holder_totals[span["request_id"]] = holder_totals.get(span["request_id"], 0) + overlap
            add_edge("observed_lock_owner", span, overlap, 1, wait_start, wait_end)
        stage["observed_owner_overlap_ns"] = overlap_total
        stage["unattributed_lock_wait_ns"] = stage["lock_wait_ns"] - overlap_total
        require(stage["unattributed_lock_wait_ns"] >= 0, "ownership exceeds waiting interval")
        queue_start, queue_end = group["queue_enter"]["timestamp_ns"], group["queue_exit"]["timestamp_ns"]
        busy = list(saturated.overlaps(queue_start, queue_end))
        stage["all_workers_busy_queue_ns"] = sum(overlap for _, overlap in busy)
        for interval, _ in busy:
            start, end = max(queue_start, interval["start_ns"]), min(queue_end, interval["end_ns"])
            for timeline in by_worker.values():
                for span, overlap in timeline.overlaps(start, end):
                    require(span["request_id"] != stage["request_id"], "request blocks its own queued admission")
                    add_edge("worker_capacity", span, overlap, 2, start, end)
    graph["nodes"] = [{"id": f"request:{request_id}", "kind": "request", "request_id": request_id,
                       "heavy": bool(requests[request_id]["heavy"]), "phase": requests[request_id]["phase"],
                       "thread_id": groups.get(request_id, {}).get("request_start", {}).get("thread_id")}
                      for request_id in sorted(node_ids)]
    for thread in range(1, workers + 1):
        graph["nodes"].append({"id": f"thread:{thread}", "kind": "worker", "thread_id": thread})
    graph["nodes"].extend([{"id": "resource:1", "kind": "mutex", "name": "shared_index"},
                           {"id": "resource:2", "kind": "queue", "name": "request_queue"}])
    holders = [{"request_id": request_id, "heavy": bool(requests[request_id]["heavy"]), "sum_waiter_overlap_ns": total}
               for request_id, total in sorted(holder_totals.items(), key=lambda item: (-item[1], item[0]))[:16]]
    activity = []
    measurement = trace["origins"]["measurement"]
    for thread in range(1, workers + 1):
        events = trace["threads"][thread]
        idle_start, queue_wait_ns, intervals = None, 0, 0
        for event in events:
            if event["kind"] == "worker_wait_begin":
                idle_start = event["timestamp_ns"]
            elif event["kind"] == "worker_wait_end":
                overlap = max(0, min(event["timestamp_ns"], measurement["end_ns"]) - max(idle_start, measurement["start_ns"]))
                queue_wait_ns += overlap
                intervals += int(overlap > 0)
                idle_start = None
        activity.append({"thread_id": thread, "observed_queue_wait_ns": queue_wait_ns,
                         "queue_wait_intervals": intervals, "open_wait_interval": idle_start is not None,
                         "interpretation": "Wall time inside queue wait, including scheduling/reacquisition; not CPU sleep-state telemetry."})
    return {"requests": stages, "tail_requests": selected, "graph": graph, "blocking_holders": holders,
            "worker_activity": activity,
            "stage_distributions_ns": {key: distribution([stage[key] for stage in stages if stage[key] is not None])
                                       for key in ("scheduler_ns", "queue_ns", "lock_wait_ns", "digest_ns", "observed_hold_ns", "other_service_ns")}}
