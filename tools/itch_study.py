#!/usr/bin/env python3
"""Run matched MoldUDP64 experiments against an independently verified window."""

import argparse
import hashlib
import json
import math
import platform
import select
import struct
import subprocess
from pathlib import Path
from statistics import median


CASES = ("clean", "a-only", "re-request", "reorder", "snapshot", "overflow")
ROW = struct.Struct(">QQQ")


def distribution(values):
    ordered = sorted(values)
    out = {"samples": len(ordered)}
    for name, quantile, minimum in (("p50", .5, 40), ("p99", .99, 2000), ("p999", .999, 20000)):
        out[name] = ordered[math.ceil(quantile * len(ordered)) - 1] if len(ordered) >= minimum else None
    return out


def clock_metrics(path, sender):
    scheduled, receipt = [], []
    first, rate, batch, start_ns = (sender[key] for key in ("first", "rate", "batch", "start_ns"))
    previous = first - 1
    for sequence, received, published in ROW.iter_unpack(path.read_bytes()):
        if not previous < sequence < first + sender["messages"] or published < received:
            raise ValueError("invalid or duplicate timing observation")
        due = start_ns + ((sequence - first) // batch * batch) * 1_000_000_000 // rate
        if received < due:
            raise ValueError("sender and receiver timing bases disagree")
        previous = sequence
        scheduled.append(published - due)
        receipt.append(published - received)
    return {"scheduled_to_publication_ns": distribution(scheduled),
            "receipt_to_publication_ns": distribution(receipt)}


def evaluate(sender, receiver, case):
    accounted = receiver["applied"] + receiver["snapshot_covered"]
    immediate = receiver["published_immediately_a"] + receiver["published_immediately_b"]
    if receiver["applied"] != immediate + receiver["replayed"] or receiver["conflicts"]:
        return False
    if case == "overflow":
        return receiver["stale"] and not receiver["verified"] and receiver["discarded"] > 0
    if not receiver["verified"] or accounted != sender["messages"] or receiver["discarded"]:
        return False
    if case == "a-only":
        return sender["omitted_datagrams_a"] > 0 and sender["omitted_datagrams_b"] == 0
    if case == "re-request":
        return receiver["retransmission_requests"] > 0 and sender["retransmission_responses"] > 0
    if case == "snapshot":
        return receiver["snapshot_covered"] == 32 and receiver["replayed"] > 0
    if case == "reorder":
        return receiver["peak_buffered"] > 0 and not receiver["snapshot_covered"]
    return receiver["snapshot_covered"] == receiver["retransmission_requests"] == 0


def run(binary, window, case, rate, batch, out, sender_cpu=None, receiver_cpu=None, read_batch=1):
    out.mkdir()
    timings = out / "observations.bin"
    with (window / "initial.snapshot").open("rb") as stream:
        magic, through = struct.unpack(">8sQ", stream.read(16))
    with (window / "final.snapshot").open("rb") as stream:
        end_magic, end = struct.unpack(">8sQ", stream.read(16))
    if magic != b"JITSNAP1" or end_magic != magic or not 96 < end - through <= 1_000_000:
        raise ValueError("invalid study window")
    seconds = math.ceil((end - through) / rate) + 8
    receiver = [str(binary), "receive", str(window), case,
                "8" if case == "overflow" else "32768", str(seconds * 1000), str(timings), str(read_batch)]
    if receiver_cpu is not None:
        receiver = ["taskset", "-c", str(receiver_cpu), *receiver]
    with (out / "receiver.stderr").open("w") as log:
        with subprocess.Popen(receiver, text=True, stdout=subprocess.PIPE, stderr=log) as process:
            try:
                if not select.select([process.stdout], [], [], 30)[0]:
                    raise RuntimeError("receiver did not announce ports")
                ports = json.loads(process.stdout.readline())
                sender = [str(binary), "send", str(window), str(through + 1),
                          str(ports["port_a"]), str(ports["port_b"]), case, str(rate), str(batch)]
                if sender_cpu is not None:
                    sender = ["taskset", "-c", str(sender_cpu), *sender]
                sent = subprocess.run(sender, capture_output=True, text=True, timeout=seconds + 30)
                (out / "sender.stderr").write_text(sent.stderr)
                (out / "sender.json").write_text(sent.stdout)
                received, _ = process.communicate(timeout=seconds + 30)
                (out / "receiver.json").write_text(received)
                sent.check_returncode()
                if process.returncode:
                    raise RuntimeError(f"receiver failed; see {out / 'receiver.stderr'}")
            finally:
                if process.poll() is None:
                    process.kill()
    sender_stats = json.loads(sent.stdout)
    receiver_stats = json.loads(received)
    start_ns = sender_stats.pop("start_ns")
    stats = clock_metrics(timings, {**sender_stats, "start_ns": start_ns})
    if stats["receipt_to_publication_ns"]["samples"] != receiver_stats["published_immediately_a"] + receiver_stats["published_immediately_b"]:
        raise ValueError("timing ledger disagrees with immediate publication count")
    for step in receiver_stats["timeline"]:
        step["elapsed_ns"] = step.pop("time_ns") - start_ns
    return {"case": case, "batch": batch, "read_batch": read_batch,
            "verified": evaluate(sender_stats, receiver_stats, case),
            "sender": sender_stats, "receiver": receiver_stats, "timing": stats}


def summarize(rows):
    treatments = {}
    for case, batch, read_batch in sorted({(row["case"], row["batch"], row["read_batch"]) for row in rows}):
        matching = [row for row in rows if (row["case"], row["batch"], row["read_batch"]) == (case, batch, read_batch)]
        if not all(row["verified"] for row in matching):
            continue
        measures = {}
        for category in ("scheduled_to_publication_ns", "receipt_to_publication_ns"):
            measures[category] = {
                key: median(row["timing"][category][key] for row in matching)
                for key in ("p50", "p99", "p999") if all(row["timing"][category][key] is not None for row in matching)}
        for process in ("sender", "receiver"):
            measures[process + "_cpu_ns"] = median(row[process]["process"]["cpu_ns"] for row in matching)
        measures["recovery_ns"] = median(row["receiver"]["recovery_ns"] for row in matching)
        measures["nonempty_reads"] = median(row["receiver"]["nonempty_reads"] for row in matching)
        treatments[f"{case}/packet-{batch}/read-{read_batch}"] = measures
    return treatments


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--window", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--rate", type=int, default=50000)
    parser.add_argument("--batch", type=int, default=8, choices=(1, 2, 4, 8))
    parser.add_argument("--pair-batch", type=int, choices=(1, 2, 4, 8))
    parser.add_argument("--compare-receive-batching", action="store_true")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--sender-cpu", type=int)
    parser.add_argument("--receiver-cpu", type=int)
    parser.add_argument("--environment", default="unspecified")
    args = parser.parse_args()
    if not 1000 <= args.rate <= 5000000 or not 1 <= args.repeat <= 20:
        parser.error("rate must be 1000..5000000 and repeat 1..20")
    if args.pair_batch == args.batch:
        parser.error("--pair-batch must differ from --batch")
    if args.compare_receive_batching and (platform.system() != "Linux" or args.pair_batch is not None):
        parser.error("compare receive batching on Linux with a fixed packet batch size")
    args.output.mkdir(parents=True)
    binary, window = args.binary.resolve(strict=True), args.window.resolve(strict=True)
    rows = []
    report = {
        "schema": 1, "environment": args.environment,
        "system": platform.system(), "machine": platform.machine(), "kernel": platform.release(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "window": window.name,
        "window_files": {name: hashlib.sha256((window / name).read_bytes()).hexdigest()
                         for name in ("messages.itch", "initial.snapshot", "repair.snapshot", "final.snapshot")},
        "sender_cpu": args.sender_cpu, "receiver_cpu": args.receiver_cpu, "runs": rows,
    }
    for repetition in range(args.repeat):
        packets = (args.batch,) if args.pair_batch is None else (args.batch, args.pair_batch)
        reads = (1, 16) if args.compare_receive_batching else (1,)
        batches = [(packet, read) for packet in packets for read in reads]
        if repetition % 2:
            batches = batches[::-1]
        for case in args.case or CASES:
            for batch, read_batch in batches:
                row = run(binary, window, case, args.rate, batch,
                          args.output / f"{repetition + 1:02}-{case}-packet-{batch}-read-{read_batch}",
                          args.sender_cpu, args.receiver_cpu, read_batch)
                rows.append(row)
                print(f"{case} packet {batch} read {read_batch} repeat {repetition + 1}: verified={row['verified']}", flush=True)
                report["summary"] = summarize(rows)
                (args.output / "study.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if all(row["verified"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
