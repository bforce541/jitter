#!/usr/bin/env python3
"""Locate scheduled tail samples in a traced market_study sweep."""

import argparse
from collections import Counter, deque
import csv
import hashlib
import heapq
import json
from pathlib import Path


def analyze(rows, expected):
    pending = deque()
    top = []
    statuses = Counter()
    first = None
    previous_arrival = previous_publication = None
    stages = ("producer_lag_ns", "handoff_ns", "consumer_ns")
    tail_size = expected["events"] // 1000 + 1

    for ordinal, (sequence, status, *timestamps) in enumerate(rows, start=1):
        sequence = int(sequence)
        if sequence != expected["warmup_events"] + ordinal:
            raise ValueError("noncontiguous trace sequence")
        statuses[status] += 1
        if status == "dropped":
            continue
        scheduled, arrival, dequeued, published = map(int, timestamps)
        if not scheduled <= arrival <= dequeued <= published:
            raise ValueError("timestamps out of order")
        while pending and pending[0] <= arrival:
            pending.popleft()
        latency = published - scheduled
        if first is None or status == "published" and (len(top) < tail_size or latency >= top[0][0]):
            record = {
                "sequence": sequence,
                "status": status,
                "scheduled_ns": scheduled,
                "arrival_ns": arrival,
                "dequeued_ns": dequeued,
                "published_ns": published,
                "scheduled_latency_ns": latency,
                "producer_lag_ns": arrival - scheduled,
                "handoff_ns": dequeued - arrival,
                "consumer_ns": published - dequeued,
                "prior_arrivals_pending_publication": len(pending),
                "preceding_arrival_gap_ns": arrival - previous_arrival if previous_arrival is not None else None,
                "preceding_publication_gap_ns": published - previous_publication if previous_publication is not None else None,
            }
            if first is None:
                first = record
            if status == "published":
                heapq.heappush(top, (latency, sequence, record))
                if len(top) > tail_size:
                    heapq.heappop(top)
        pending.append(published)
        previous_arrival, previous_publication = arrival, published

    count = statuses["published"]
    if (sum(statuses.values()) != expected["events"] or count != expected["published"] or
            statuses["dropped"] != expected["dropped"]):
        raise ValueError("trace counts do not match benchmark record")
    worst = sorted(top, reverse=True)
    pivot = count // 1000 if count >= 20000 else None
    witness = worst[pivot][2] if pivot is not None else None
    if witness is not None and witness["scheduled_latency_ns"] != expected["scheduled_to_publication_ns"]["p999"]:
        raise ValueError("trace p99.9 does not match benchmark record")
    tail = [row for _, _, row in worst[:pivot + 1]] if pivot is not None else []
    return {
        "status_counts": dict(statuses),
        "first_admitted": first,
        "worst_publication": worst[0][2] if worst else None,
        "p999_witness": witness,
        "p999_through_max_samples": len(tail),
        "p999_through_max_dominant_stage": dict(Counter(max(stages, key=row.__getitem__) for row in tail)),
        "p999_through_max_scheduled_before_1ms": sum(row["scheduled_ns"] < 1_000_000 for row in tail),
    }


def summarize(path):
    study = json.loads(path.read_text())
    if not study.get("diagnostic_sweep") or not study["sweep"]:
        raise ValueError("expected a nonempty diagnostic sweep")
    runs = []
    for row in study["sweep"]:
        trace = path.parent / row["trace_file"]
        digest = hashlib.sha256()
        with trace.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        with trace.open(newline="") as stream:
            rows = csv.reader(stream)
            if next(rows) != ["sequence", "status", "scheduled_ns", "arrival_ns", "dequeued_ns", "published_ns"]:
                raise ValueError(f"unexpected trace header in {trace}")
            tail = analyze(rows, row["result"])
        runs.append({**row, "trace_sha256": digest.hexdigest(), "tail": tail})
    return {**study, "sweep": runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.study), indent=2))


if __name__ == "__main__":
    main()
