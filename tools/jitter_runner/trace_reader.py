"""Validate the trace contract before attributing any delay."""

from collections import Counter, defaultdict
from pathlib import Path

from .metrics import REQUEST_FIELDS, numeric, read_csv
from .spec import ExperimentError, integer, load_json, object_keys

EVENT_FIELDS = ("thread_id", "sequence", "phase", "timestamp_ns", "request_id", "kind", "resource_id")
ORIGIN_FIELDS = ("phase", "start_ns", "end_ns")
PHASES = ("lifecycle", "warmup", "measurement")
NO_REQUEST = (1 << 64) - 1
RESOURCES = {"thread_start": 0, "thread_stop": 0, "worker_wait_begin": 2, "worker_wait_end": 2,
             "request_submit": 0, "request_reject": 2, "queue_enter": 2, "queue_exit": 2,
             "request_start": 0, "request_complete": 0, "lock_wait": 1, "lock_acquire": 1,
             "lock_release": 1, "region_begin": 3, "region_end": 3}
WORKER_STAGES = ("queue_exit", "request_start", "lock_wait", "lock_acquire", "region_begin", "region_end", "lock_release", "request_complete")


def require(condition, message):
    if not condition:
        raise ExperimentError("invalid trace: " + message)


def _worker_grammar(events, dropped):
    state, active = "thread_start", None
    for event in events:
        kind = event["kind"]
        if state == "after_wait":
            require(kind in ("queue_exit", "thread_stop"), "worker dispatch order")
            if kind == "thread_stop":
                state = "stopped"
                continue
            active = event["request_id"]
            state = "request_start"
        else:
            require(kind == state, f"worker expected {state}, got {kind}")
            if kind == "thread_start":
                state = "worker_wait_begin"
            elif kind == "worker_wait_begin":
                state = "worker_wait_end"
            elif kind == "worker_wait_end":
                state = "after_wait"
            else:
                require(event["request_id"] == active, "worker changed requests mid-operation")
                index = WORKER_STAGES.index(kind)
                state = WORKER_STAGES[index + 1] if index + 1 < len(WORKER_STAGES) else "worker_wait_begin"
    if not dropped:
        require(state == "stopped", "lossless worker trace is unfinished")


def read_trace(spec, run_dir, baseline):
    """baseline must already have passed the Phase 1 raw-response validator."""
    path = Path(run_dir)
    info = load_json(path / "trace-info.json")
    object_keys(info, ("schema_version", "enabled", "event_size_bytes", "capacity_per_thread", "thread_count",
                       "reserved_bytes", "clock", "retention", "buffers"), "trace metadata")
    tracing = spec["instrumentation"]
    threads = spec["application"]["workers"] + 1
    require(type(info["enabled"]) is bool and info["enabled"] == tracing["enabled"], "mode differs from specification")
    for key, expected in (("schema_version", 1), ("event_size_bytes", 32),
                          ("capacity_per_thread", tracing["events_per_thread"]), ("thread_count", threads)):
        integer(info[key], expected, expected, "trace metadata." + key)
    require(info["clock"] == "steady_clock_nanoseconds" and info["retention"] == "prefix_drop_new", "unsupported format")
    expected_bytes = tracing["events_per_thread"] * threads * 32 if info["enabled"] else 0
    integer(info["reserved_bytes"], expected_bytes, expected_bytes, "trace reserved bytes")
    require(isinstance(info["buffers"], list), "buffer inventory must be a list")
    if not info["enabled"]:
        require(not info["buffers"] and not (path / "events.csv").exists() and not (path / "trace-phases.csv").exists(), "disabled trace contains events")
        return {"quality": {"enabled": False, "complete": False, "attempted": 0, "written": 0, "dropped": 0,
                            "reserved_bytes": 0}, "requests": {}, "groups": {}, "threads": {}, "origins": {}, "holds": []}
    require(len(info["buffers"]) == threads, "buffer inventory does not match worker count")
    totals = {phase: Counter(attempted=0, written=0, dropped=0) for phase in PHASES}
    for thread, buffer in enumerate(info["buffers"]):
        object_keys(buffer, ("thread_id", "capacity", "phases"), "trace buffer")
        integer(buffer["thread_id"], thread, thread, "trace thread ID")
        integer(buffer["capacity"], tracing["events_per_thread"], tracing["events_per_thread"], "trace capacity")
        object_keys(buffer["phases"], PHASES, "trace phase counts")
        for phase, counts in buffer["phases"].items():
            object_keys(counts, ("attempted", "written", "dropped"), "trace counts")
            for key, value in counts.items():
                integer(value, 0, NO_REQUEST, "trace count." + key)
            require(counts["attempted"] == counts["written"] + counts["dropped"], "inconsistent loss accounting")
            totals[phase].update(counts)
        attempts = sum(counts["attempted"] for counts in buffer["phases"].values())
        written = sum(counts["written"] for counts in buffer["phases"].values())
        require(written == min(attempts, tracing["events_per_thread"]), "buffer does not preserve its full prefix")
    require(totals["lifecycle"]["attempted"] == 2 + 4 * (threads - 1) +
            2 * sum(phase["completed"] for phase in baseline["phases"].values()), "lifecycle attempt count")
    for phase in ("warmup", "measurement"):
        summary = baseline["phases"].get(phase, {"offered": 0, "completed": 0})
        require(totals[phase]["attempted"] == 2 * summary["offered"] + 8 * summary["completed"], "request attempt count")
    origins = {}
    origin_rows = read_csv(path / "trace-phases.csv", ORIGIN_FIELDS)
    require([row["phase"] for row in origin_rows] == list(baseline["phases"]), "phase origin inventory")
    previous_end = -1
    for row in origin_rows:
        start, end = numeric(row["start_ns"]), numeric(row["end_ns"])
        require(start >= previous_end and end - start == baseline["phases"][row["phase"]]["elapsed_ns"], "phase origin timing")
        origins[row["phase"]] = {"start_ns": start, "end_ns": end}
        previous_end = end
    requests = {}
    for row in read_csv(path / "requests.csv", REQUEST_FIELDS):
        request = {key: numeric(value, -1) if key not in ("phase", "status") else value for key, value in row.items()}
        requests[request["id"]] = request
    thread_events = {thread: [] for thread in range(threads)}
    groups = defaultdict(dict)
    observed_counts = {thread: Counter() for thread in range(threads)}
    previous_thread = 0
    for row in read_csv(path / "events.csv", EVENT_FIELDS):
        event = {key: numeric(value) if key not in ("phase", "kind") else value for key, value in row.items()}
        thread = event["thread_id"]
        require(thread in thread_events and thread >= previous_thread, "writer serialization order")
        previous_thread = thread
        events = thread_events[thread]
        require(event["sequence"] == len(events), "missing, duplicate, or non-prefix sequence")
        require(not events or event["timestamp_ns"] >= events[-1]["timestamp_ns"], "nonmonotonic writer clock")
        require(event["kind"] in RESOURCES and event["resource_id"] == RESOURCES[event["kind"]], "event kind or resource")
        if event["phase"] == "lifecycle":
            require(event["request_id"] == NO_REQUEST and event["kind"] in
                    ("thread_start", "thread_stop", "worker_wait_begin", "worker_wait_end"), "lifecycle identity")
            require(thread > 0 or event["kind"] in ("thread_start", "thread_stop"), "producer lifecycle kind")
        else:
            require(event["phase"] in origins and event["request_id"] in requests, "unknown phase or request")
            request = requests[event["request_id"]]
            require(event["phase"] == request["phase"], "request changed phase")
            origin = origins[request["phase"]]["start_ns"]
            time = event["timestamp_ns"]
            require(origin <= time <= origins[request["phase"]]["end_ns"], "event outside its drained phase")
            kind = event["kind"]
            require(kind not in groups[request["id"]], "duplicate request event")
            if thread == 0:
                require(kind in ("request_submit", "request_reject", "queue_enter"), "producer event kind")
                require(time >= origin + request["submitted_ns"], "admission before submission")
                if kind == "request_submit":
                    require(time == origin + request["submitted_ns"], "submission differs from raw record")
                elif kind == "request_reject":
                    require(request["status"] == "rejected", "rejected event for completed request")
                else:
                    require(request["status"] == "completed" and time <= origin + request["started_ns"], "queue admission after execution")
            else:
                require(request["status"] == "completed" and kind in WORKER_STAGES, "worker handled a rejected request or invalid stage")
                require(origin + request["submitted_ns"] <= time <= origin + request["completed_ns"], "worker stage outside response interval")
                if kind == "queue_exit":
                    require(time <= origin + request["started_ns"], "dequeue after start")
                elif kind in ("request_start", "request_complete"):
                    field = "started_ns" if kind == "request_start" else "completed_ns"
                    require(time == origin + request[field], "boundary differs from raw record")
                else:
                    require(time >= origin + request["started_ns"], "application stage before start")
            groups[request["id"]][kind] = event
        events.append(event)
        observed_counts[thread][event["phase"]] += 1
    producer_expected = [("thread_start", "lifecycle", NO_REQUEST)]
    for request in requests.values():
        producer_expected.extend([("request_submit", request["phase"], request["id"]),
                                  ("queue_enter" if request["status"] == "completed" else "request_reject", request["phase"], request["id"])])
    producer_expected.append(("thread_stop", "lifecycle", NO_REQUEST))
    producer_attempts = {phase: 0 for phase in PHASES}
    for _, phase, _ in producer_expected:
        producer_attempts[phase] += 1
    for phase in PHASES:
        require(info["buffers"][0]["phases"][phase]["attempted"] == producer_attempts[phase], "producer attempt count")
    observed_producer = [(event["kind"], event["phase"], event["request_id"]) for event in thread_events[0]]
    require(observed_producer == producer_expected[:len(observed_producer)], "producer event order")
    for thread, events in thread_events.items():
        buffer = info["buffers"][thread]
        for phase in PHASES:
            require(observed_counts[thread][phase] == buffer["phases"][phase]["written"], "written ledger differs from event rows")
        if thread:
            _worker_grammar(events, sum(counts["dropped"] for counts in buffer["phases"].values()))
        elif not sum(counts["dropped"] for counts in buffer["phases"].values()):
            require(len(events) == len(producer_expected), "lossless producer trace is unfinished")
    dropped = sum(counts["dropped"] for counts in totals.values())
    if dropped == 0:
        for request in requests.values():
            required = {"request_submit", "queue_enter", *WORKER_STAGES} if request["status"] == "completed" else {"request_submit", "request_reject"}
            require(set(groups[request["id"]]) == required, "lossless request has missing stages")
    holds = []
    for request_id, group in groups.items():
        worker_events = [event for kind, event in group.items() if kind in WORKER_STAGES]
        require(len({event["thread_id"] for event in worker_events}) <= 1, "request changed workers")
        if "queue_enter" in group and "queue_exit" in group:
            require(group["queue_exit"]["timestamp_ns"] >= group["queue_enter"]["timestamp_ns"], "dequeue preceded enqueue")
        if "lock_release" in group:
            acquire, release = group["lock_acquire"], group["lock_release"]
            holds.append({"request_id": request_id, "thread_id": acquire["thread_id"],
                          "phase": acquire["phase"], "start_ns": acquire["timestamp_ns"], "end_ns": release["timestamp_ns"]})
    holds.sort(key=lambda hold: (hold["start_ns"], hold["end_ns"], hold["request_id"]))
    for before, after in zip(holds, holds[1:]):
        require(before["end_ns"] <= after["start_ns"], "mutually exclusive ownership intervals overlap")
    quality = {"enabled": True, "complete": dropped == 0, "attempted": sum(counts["attempted"] for counts in totals.values()),
               "written": sum(counts["written"] for counts in totals.values()), "dropped": dropped,
               "reserved_bytes": info["reserved_bytes"], "phases": {phase: dict(counts) for phase, counts in totals.items()},
               "retention": "prefix_drop_new"}
    return {"quality": quality, "requests": requests, "groups": dict(groups), "threads": thread_events, "origins": origins, "holds": holds}
