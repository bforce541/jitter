#!/usr/bin/env python3
"""Extract a small, verified comparison for the public historical replay."""

import argparse
import json
from pathlib import Path

from fetch_itch_sample import NAME, SHA256, SIZE, URL


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def select_run(study, case):
    for index, row in enumerate(study["runs"]):
        if row["case"] == case and row["verified"]:
            return index, row
    raise ValueError(f"no verified {case} run")


def build(session, study):
    if not session["complete_session"] or session["scope"] != "all-symbols":
        raise ValueError("showcase requires a complete all-symbol session")
    if session["reference_quote_checks"] != session["book_updates"] or not session["reference_state_checked"]:
        raise ValueError("session lacks full differential and canonical checks")
    _, clean = select_run(study, "clean")
    _, fault = select_run(study, "snapshot")
    reference, recovered = clean["receiver"], fault["receiver"]
    first = clean["sender"]["first"]
    if (fault["sender"]["first"] != first or clean["sender"]["messages"] != fault["sender"]["messages"] or
            first != session["windows"][study["window"]]["first_sequence"]):
        raise ValueError("comparison does not use one historical window")
    if (reference["through"] != recovered["through"] or
            reference["book_fingerprint"] != recovered["book_fingerprint"] or
            recovered["book_fingerprint"] != recovered["expected_fingerprint"]):
        raise ValueError("recovered final book does not match the uninterrupted run")
    marks = {}
    for row in recovered["timeline"]:
        marks.setdefault(row["event"], row)
    ordered = [marks[key] for key in ("initial", "gap", "snapshot-requested", "snapshot-applied")]
    if not ordered[1]["stale"] or not ordered[2]["stale"] or ordered[3]["stale"]:
        raise ValueError("missing recorded staleness and recovery")
    if (recovered["snapshot_covered"] != 32 or recovered["snapshot_buffered_suffix"] <= 0 or
            recovered["applied"] + recovered["snapshot_covered"] != fault["sender"]["messages"]):
        raise ValueError("unaccounted messages in historical recovery")
    return {
        "schema": 1,
        "tape": {"name": NAME, "date": "2019-12-30", "source": URL, "sha256": SHA256, "bytes": SIZE},
        "session": {key: session[key] for key in
                    ("messages", "directory_symbols", "updated_symbols", "book_updates",
                     "reference_quote_checks", "peak_active_orders", "checkpoints")},
        "window": session["windows"][study["window"]],
        "transport": {"environment": study["environment"], "revision": study["revision"],
                      "offered_messages_per_second": fault["sender"]["rate"], "messages_per_packet": fault["batch"],
                      "dropped_datagrams_a": fault["sender"]["omitted_datagrams_a"],
                      "dropped_datagrams_b": fault["sender"]["omitted_datagrams_b"],
                      "snapshot_covered": recovered["snapshot_covered"],
                      "snapshot_buffered_suffix": recovered["snapshot_buffered_suffix"],
                      "peak_buffered": recovered["peak_buffered"], "recovery_ns": recovered["recovery_ns"],
                      "snapshot_through": ordered[1]["sequence"] + recovered["snapshot_covered"],
                      "recovered_through": ordered[3]["sequence"], "final_sequence": recovered["through"],
                      "uninterrupted_fingerprint": reference["book_fingerprint"],
                      "recovered_fingerprint": recovered["book_fingerprint"], "timeline": ordered},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = build(read(args.session), read(args.study))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
