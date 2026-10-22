"""Exact controlled trace cases plus real C++ integration."""

from collections import Counter
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.dependencies import Intervals, build_dependencies
from jitter_runner.diagnostics import diagnose
from jitter_runner.metrics import REQUEST_FIELDS, RESOURCE_FIELDS, analyze_run
from jitter_runner.overhead import compare_pairs, plan, run_overhead, verify_overhead
from jitter_runner.runner import run_experiment, verify_bundle
from jitter_runner.spec import ExperimentError, load_spec, validate_spec
from jitter_runner.storage import seal_bundle, write_json
from jitter_runner.trace_reader import EVENT_FIELDS, ORIGIN_FIELDS, NO_REQUEST, PHASES, RESOURCES, read_trace, validate_queue
from jitter_runner.workload import expected_value, generate
from test_foundation import fixture_spec, write_csv


def observation_spec():
    spec = fixture_spec()
    spec.update(schema_version=2, instrumentation={"enabled": True, "events_per_thread": 128})
    spec["application"]["workers"] = 2
    spec["workload"].update(arrival="burst", burst_size=3, heavy_fraction=1)
    return spec


def controlled_case(path, capacity=128, scenario="mixed"):
    """Three known responses: lock overlap=80 ns, saturation queue time=82 ns."""
    spec = observation_spec()
    spec["instrumentation"]["events_per_thread"] = capacity
    if scenario != "mixed":
        spec["application"]["workers"] = 1
    if scenario == "region":
        spec["workload"].update(arrival="steady", burst_size=1)
    workload = generate(spec)
    times = [(0, 10, 102), (2, 20, 112), (4, 116, 126)]
    if scenario == "region":
        times = [(i * 1000000, i * 1000000 + 10, i * 1000000 + 102) for i in range(3)]
    elif scenario == "queue":
        times = [(0, 10, 102), (2, 114, 206), (4, 218, 310)]
    responses = []
    for request, (submit, start, complete) in zip(workload, times):
        responses.append({**request, "status": "completed", "submitted_ns": submit, "started_ns": start,
                          "completed_ns": complete, "value": expected_value(spec["application"], request["key"], request["heavy"])})
    write_csv(path / "requests.csv", REQUEST_FIELDS, responses)
    write_csv(path / "resources.csv", RESOURCE_FIELDS, [{"phase": "measurement", "arrival_window_ns": 3000000,
              "elapsed_ns": 3000000, "cpu_user_ns": 100000, "cpu_system_ns": 0, "peak_rss_bytes": 1000000}])
    write_csv(path / "trace-phases.csv", ORIGIN_FIELDS, [{"phase": "measurement", "start_ns": 1000, "end_ns": 3001000}])
    thread_count = spec["application"]["workers"] + 1
    streams = [[] for _ in range(thread_count)]
    def emit(thread, kind, time, request=None):
        streams[thread].append({"thread_id": thread, "sequence": len(streams[thread]),
                               "phase": "measurement" if request is not None else "lifecycle",
                               "timestamp_ns": time + 1000 if request is not None else time,
                               "request_id": request if request is not None else NO_REQUEST,
                               "kind": kind, "resource_id": RESOURCES[kind]})
    emit(0, "thread_start", 0)
    for request in range(3):
        emit(0, "request_submit", times[request][0], request)
        emit(0, "queue_enter", times[request][0] + 1, request)
    emit(0, "thread_stop", 3001005)
    for thread in range(1, thread_count):
        emit(thread, "thread_start", 500)
        emit(thread, "worker_wait_begin", 501)
    def operation(thread, request, exit_time, wait_end, wait, acquire, begin, end, release, complete):
        emit(thread, "worker_wait_end", 1000 + wait_end)
        emit(thread, "queue_exit", exit_time, request)
        emit(thread, "request_start", times[request][1], request)
        for kind, time in (("lock_wait", wait), ("lock_acquire", acquire), ("region_begin", begin),
                           ("region_end", end), ("lock_release", release), ("request_complete", complete)):
            emit(thread, kind, time, request)
        emit(thread, "worker_wait_begin", 1000 + complete + 1)
    if scenario == "mixed":
        operation(1, 0, 9, 8, 11, 12, 13, 100, 101, 102)
        operation(2, 1, 19, 18, 21, 103, 104, 110, 111, 112)
        operation(1, 2, 115, 114, 117, 118, 119, 124, 125, 126)
    else:
        for request, (_, start, complete) in enumerate(times):
            operation(1, request, start - 1, start - 2, start + 1, start + 2, start + 3, complete - 2, complete - 1, complete)
    for thread in range(1, thread_count):
        emit(thread, "worker_wait_end", 3001000 + thread)
        emit(thread, "thread_stop", 3001002 + thread)
    buffers, retained = [], []
    for thread, stream in enumerate(streams):
        counts = {}
        for phase in PHASES:
            attempted = sum(event["phase"] == phase for event in stream)
            written = sum(event["phase"] == phase for event in stream[:capacity])
            counts[phase] = {"attempted": attempted, "written": written, "dropped": attempted - written}
        buffers.append({"thread_id": thread, "capacity": capacity, "phases": counts})
        retained.extend(stream[:capacity])
    write_csv(path / "events.csv", EVENT_FIELDS, retained)
    write_json(path / "trace-info.json", {"schema_version": 1, "enabled": True, "event_size_bytes": 32,
               "capacity_per_thread": capacity, "thread_count": thread_count, "reserved_bytes": thread_count * capacity * 32,
               "clock": "steady_clock_nanoseconds", "retention": "prefix_drop_new", "buffers": buffers})
    return spec, workload, retained


class TraceContractTests(unittest.TestCase):
    def test_known_holder_and_queue_propagation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, _ = controlled_case(path)
            baseline = analyze_run(spec, workload, path)
            trace = read_trace(spec, path, baseline)
            self.assertTrue(trace["quality"]["complete"])
            report = diagnose(spec, path, baseline)
            target = next(stage for stage in report["tail_requests"] if stage["request_id"] == 1)
            self.assertEqual(target["lock_wait_ns"], 82)
            self.assertEqual(target["observed_owner_overlap_ns"], 80)
            self.assertEqual(target["unattributed_lock_wait_ns"], 2)
            queued = next(stage for stage in report["tail_requests"] if stage["request_id"] == 2)
            self.assertEqual(queued["all_workers_busy_queue_ns"], 82)
            edges = report["graph"]["edges"]
            self.assertTrue(any(edge["source"] == "request:0" and edge["target"] == "request:1" and edge["overlap_ns"] == 80 for edge in edges))
            queue_edges = [edge for edge in edges if edge["kind"] == "worker_capacity" and edge["target"] == "request:2"]
            self.assertEqual(sum(edge["overlap_ns"] for edge in queue_edges), 164) # Parallel, not 164 ns of queue delay.
            names = {hypothesis["id"] for hypothesis in report["hypotheses"]}
            self.assertIn("shared_lock_contention", names)
            self.assertIn("queue_pressure", names)
            self.assertTrue(all(hypothesis["causal_status"] == "hypothesis" for hypothesis in report["hypotheses"]))
            for stage in report["requests"]:
                self.assertEqual(stage["latency_ns"], sum(stage[key] for key in ("scheduler_ns", "queue_ns", "lock_wait_ns", "digest_ns", "other_service_ns")))

    def test_isolated_region_and_queue_controls_do_not_invent_contention(self):
        for scenario, expected in (("region", "digest_execution"), ("queue", "queue_pressure")):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                spec, workload, _ = controlled_case(path, scenario=scenario)
                report = diagnose(spec, path, analyze_run(spec, workload, path))
                names = {hypothesis["id"] for hypothesis in report["hypotheses"]}
                self.assertIn(expected, names)
                self.assertNotIn("shared_lock_contention", names)
                self.assertFalse(any(edge["kind"] == "observed_lock_owner" for edge in report["graph"]["edges"]))

    def test_scheduler_delay_control_is_ranked_without_blaming_application(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, events = controlled_case(path)
            from jitter_runner.metrics import read_csv
            responses = read_csv(path / "requests.csv", REQUEST_FIELDS)
            for row in responses:
                for key in ("submitted_ns", "started_ns", "completed_ns"):
                    row[key] = int(row[key]) + 1000000
            for event in events:
                if 1000 <= event["timestamp_ns"] < 3001000:
                    event["timestamp_ns"] += 1000000
            write_csv(path / "requests.csv", REQUEST_FIELDS, responses)
            write_csv(path / "events.csv", EVENT_FIELDS, events)
            report = diagnose(spec, path, analyze_run(spec, workload, path))
            self.assertEqual(report["hypotheses"][0]["id"], "workload_delivery_delay")
            self.assertGreater(report["hypotheses"][0]["tail_component_fraction"], .99)

    def test_graph_budget_reports_truncation(self):
        with tempfile.TemporaryDirectory() as directory, patch("jitter_runner.dependencies.MAX_EDGES", 1):
            path = Path(directory)
            spec, workload, _ = controlled_case(path)
            report = diagnose(spec, path, analyze_run(spec, workload, path))
            self.assertTrue(report["graph"]["edges_truncated"])
            self.assertEqual(len(report["graph"]["edges"]), 1)
            self.assertTrue(any("truncated" in limitation for limitation in report["limitations"]))

    def test_report_is_deterministic_and_request_selection_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, _ = controlled_case(path)
            baseline = analyze_run(spec, workload, path)
            self.assertEqual(diagnose(spec, path, baseline), diagnose(spec, path, baseline))
            selected = diagnose(spec, path, baseline, 1)
            self.assertEqual(selected["graph"]["selected_request_ids"], [1])
            with self.assertRaises(ExperimentError):
                diagnose(spec, path, baseline, 999)

    def test_loss_withholds_attribution_without_invalidating_correctness(self):
        for capacity in (1, 5, 15):
            with self.subTest(capacity=capacity), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                spec, workload, _ = controlled_case(path, capacity)
                baseline = analyze_run(spec, workload, path)
                report = diagnose(spec, path, baseline)
                self.assertEqual(baseline["correctness"], "passed")
                self.assertEqual(report["status"], "insufficient_trace")
                self.assertGreater(report["trace_quality"]["dropped"], 0)
                self.assertFalse(report["graph"]["edges"])
                self.assertFalse(report["hypotheses"])

    def test_malformed_or_contradictory_events_fail(self):
        mutations = [lambda rows: rows.pop(), lambda rows: rows[1].update(sequence=99),
                     lambda rows: rows[1].update(timestamp_ns=0), lambda rows: rows[1].update(kind="unknown"),
                     lambda rows: rows[1].update(resource_id=9), lambda rows: rows[1].update(request_id=999),
                     lambda rows: rows[1].update(phase="warmup"), lambda rows: rows[1].update(thread_id=1)]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                spec, workload, rows = controlled_case(path)
                baseline = analyze_run(spec, workload, path)
                mutation(rows)
                write_csv(path / "events.csv", EVENT_FIELDS, rows)
                with self.assertRaises(ExperimentError):
                    diagnose(spec, path, baseline)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, rows = controlled_case(path)
            event = next(event for event in rows if event["kind"] == "lock_acquire" and event["request_id"] == 1)
            event["timestamp_ns"] = 1090
            write_csv(path / "events.csv", EVENT_FIELDS, rows)
            with self.assertRaisesRegex(ExperimentError, "ownership"):
                diagnose(spec, path, analyze_run(spec, workload, path))

    def test_loss_and_phase_ledgers_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, _ = controlled_case(path)
            info = json.loads((path / "trace-info.json").read_text())
            info["buffers"][1]["phases"]["measurement"]["attempted"] += 1
            write_json(path / "trace-info.json", info)
            with self.assertRaises(ExperimentError):
                diagnose(spec, path, analyze_run(spec, workload, path))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, _ = controlled_case(path)
            write_csv(path / "trace-phases.csv", ORIGIN_FIELDS, [{"phase": "measurement", "start_ns": 1010, "end_ns": 3001010}])
            with self.assertRaises(ExperimentError):
                diagnose(spec, path, analyze_run(spec, workload, path))

    def test_region_execution_and_unowned_wait_are_not_busiest_function_guesses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, _ = controlled_case(path)
            report = diagnose(spec, path, analyze_run(spec, workload, path))
            self.assertIn("digest_execution", {h["id"] for h in report["hypotheses"]})
            digest = next(h for h in report["hypotheses"] if h["id"] == "digest_execution")
            self.assertEqual(digest["suggested_experiment"]["kind"], "diagnostic_workload_comparison")
            self.assertIn("not a proposed optimization", digest["caveat"])

    def test_memory_bounds_and_boolean_mode(self):
        spec = observation_spec()
        spec["instrumentation"]["enabled"] = 1
        with self.assertRaises(ExperimentError):
            validate_spec(spec)
        spec["instrumentation"].update(enabled=True, events_per_thread=1000000)
        spec["application"]["workers"] = 256
        with self.assertRaises(ExperimentError):
            validate_spec(spec)

    def test_queue_capacity_fifo_and_equal_clock_order(self):
        def event(kind, request, time, sequence, thread):
            return {"kind": kind, "request_id": request, "timestamp_ns": time, "sequence": sequence, "thread_id": thread}
        simultaneous = [event("queue_enter", 0, 1, 0, 0), event("queue_enter", 1, 1, 1, 0),
                        event("request_reject", 2, 1, 2, 0), event("queue_exit", 0, 1, 0, 1), event("queue_exit", 1, 1, 0, 2)]
        validate_queue(simultaneous, 1)
        validate_queue(simultaneous, 2)
        with self.assertRaises(ExperimentError):
            validate_queue(simultaneous, 3)
        invalid = [event("queue_enter", 0, 1, 0, 0), event("queue_enter", 1, 2, 1, 0),
                   event("queue_exit", 1, 3, 0, 2), event("queue_exit", 0, 4, 0, 1)]
        with self.assertRaises(ExperimentError):
            validate_queue(invalid, 2)
        reversed_worker = [event("queue_enter", 0, 1, 0, 0), event("queue_enter", 1, 1, 1, 0),
                           event("queue_exit", 1, 1, 0, 1), event("queue_exit", 0, 1, 1, 1)]
        with self.assertRaises(ExperimentError):
            validate_queue(reversed_worker, 2)

    def test_interval_index_handles_touching_and_zero_spans(self):
        intervals = Intervals([{"start_ns": 0, "end_ns": 10}, {"start_ns": 10, "end_ns": 20}])
        self.assertEqual(sum(overlap for _, overlap in intervals.overlaps(5, 15)), 10)
        self.assertFalse(list(intervals.overlaps(10, 10)))

    def test_alternating_plan(self):
        spec = observation_spec()
        spec["repetitions"] = 3
        self.assertEqual([entry["mode"] for entry in plan(spec)], ["off", "on", "on", "off", "off", "on"])

    def test_overhead_preserves_signed_noise_and_withholds_bad_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            spec, workload, _ = controlled_case(path)
            baseline = analyze_run(spec, workload, path)
            off, on = copy.deepcopy(baseline), copy.deepcopy(baseline)
            off["observation"] = {"enabled": False, "complete": False, "reserved_bytes": 0}
            on["observation"] = read_trace(spec, path, baseline)["quality"]
            on["phases"]["measurement"]["latency_ns"]["p99"] -= 10
            report = compare_pairs([{"off": off, "on": on}])
            self.assertEqual(report["pairs"][0]["p99_latency_ns"]["delta"], -10)
            self.assertTrue(report["all_pairs_comparable"])
            on["observation"]["complete"] = False
            report = compare_pairs([{"off": off, "on": on}])
            self.assertFalse(report["all_pairs_comparable"])
            self.assertIsNone(report["paired_deltas"])
            self.assertEqual(len(report["pairs"]), 1)
            on["observation"]["complete"] = True
            report = compare_pairs([{"off": off, "on": on, "off_host": {"logical_cpus": 2}, "on_host": {"logical_cpus": 4}}])
            self.assertFalse(report["all_pairs_comparable"])
            self.assertTrue(any("host" in reason for reason in report["pairs"][0]["limitations"]))


@unittest.skipUnless(os.environ.get("JITTER_TEST_BINARY"), "set JITTER_TEST_BINARY for real-engine observation tests")
class ObservationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.binary = Path(os.environ["JITTER_TEST_BINARY"]).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def spec(self):
        spec = observation_spec()
        spec["instrumentation"]["events_per_thread"] = 4096
        spec["workload"].update(warmup_ms=10, measurement_ms=40, burst_size=8)
        spec["application"].update(queue_capacity=64, heavy_rounds=10000)
        return spec

    def test_capture_verify_diagnose_and_saved_executable_replay(self):
        path, summary = run_experiment(self.spec(), self.path / "original", binary=self.binary)
        self.assertTrue(summary["runs"][0]["observation"]["complete"])
        report = json.loads((path / "run-001" / "diagnostics.json").read_text())
        self.assertEqual(len(report["requests"]), 40)
        for stage in report["requests"]:
            self.assertEqual(stage["latency_ns"], sum(stage[key] for key in ("scheduler_ns", "queue_ns", "lock_wait_ns", "digest_ns", "other_service_ns")))
        spec, _, metadata = verify_bundle(path)
        replay, _ = run_experiment(spec, self.path / "replay", reproduce_from=path)
        self.assertEqual(metadata["engine_sha256"], verify_bundle(replay)[2]["engine_sha256"])

    def test_real_overflow_is_explicit_and_cannot_claim_complete_attribution(self):
        spec = self.spec()
        spec["instrumentation"]["events_per_thread"] = 1
        path, summary = run_experiment(spec, self.path / "overflow", binary=self.binary)
        self.assertFalse(summary["runs"][0]["observation"]["complete"])
        self.assertGreater(summary["runs"][0]["observation"]["dropped"], 0)
        self.assertEqual(json.loads((path / "run-001" / "diagnostics.json").read_text())["hypotheses"], [])
        verify_bundle(path)

    def test_trace_report_tampering_fails_even_after_resealing(self):
        path, _ = run_experiment(self.spec(), self.path / "original", binary=self.binary)
        report_path = path / "run-001" / "diagnostics.json"
        report = json.loads(report_path.read_text())
        report["trace_quality"]["written"] += 1
        write_json(report_path, report)
        seal_bundle(path, "complete")
        with self.assertRaises(ExperimentError):
            verify_bundle(path)

    def test_overhead_study_and_reproduction_verify(self):
        spec = self.spec()
        spec["repetitions"] = 2
        path, report = run_overhead(spec, self.path / "study", binary=self.binary)
        self.assertEqual(len(report["pairs"]), 2)
        verify_overhead(path)
        replay, _ = run_overhead(spec, self.path / "replay", reproduce_from=path)
        verify_overhead(replay)
        report_path = path / "comparison.json"
        changed = json.loads(report_path.read_text())
        changed["pairs"][0]["p99_latency_ns"]["delta"] = 0
        write_json(report_path, changed)
        seal_bundle(path, "complete")
        with self.assertRaises(ExperimentError):
            verify_overhead(path)

    def test_registered_schema_three_overhead_is_supported(self):
        spec = self.spec()
        spec["schema_version"] = 3
        spec["application"]["implementation"] = "narrow"
        spec["repetitions"] = 1
        path, report = run_overhead(spec, self.path / "registered-overhead", binary=self.binary)
        self.assertEqual(len(report["pairs"]), 1)
        self.assertEqual(verify_overhead(path)[0]["schema_version"], 3)


if __name__ == "__main__":
    unittest.main()
