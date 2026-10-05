"""Portable input generation and an independent logarithmic-time response oracle."""

import csv
import io

MASK = (1 << 64) - 1
GOLDEN = 0x9E3779B97F4A7C15
MULTIPLIER = 6364136223846793005
WORKLOAD_FIELDS = ("id", "phase", "scheduled_ns", "key", "heavy")


def mix64(value):
    value &= MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK
    return value ^ (value >> 31)


def generate(spec):
    workload = spec["workload"]
    rate = workload["rate_per_second"]
    burst = workload["burst_size"]
    threshold = int(workload["heavy_fraction"] * (1 << 64))
    request_id = 0
    rows = []
    for phase, duration_ms in (("warmup", workload["warmup_ms"]), ("measurement", workload["measurement_ms"])):
        count = (rate * duration_ms + 999) // 1000
        for index in range(count):
            scheduled_index = index if workload["arrival"] == "steady" else (index // burst) * burst
            random = mix64(workload["seed"] + GOLDEN * (request_id + 1))
            rows.append({"id": request_id, "phase": phase,
                         "scheduled_ns": scheduled_index * 1000000000 // rate,
                         "key": random % spec["application"]["key_count"],
                         "heavy": int(mix64(random + GOLDEN) < threshold)})
            request_id += 1
    return rows


def encode_workload(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=WORKLOAD_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("ascii")


def expected_value(app, key, heavy):
    """Exponentiate an affine map; intentionally different from the C++ loop."""
    value = mix64(app["dataset_seed"] + GOLDEN * (key + 1))
    rounds = app["heavy_rounds"] if heavy else app["light_rounds"]
    multiplier = MULTIPLIER
    increment = (key + 1) | 1
    accumulated_multiplier, accumulated_increment = 1, 0
    while rounds:
        if rounds & 1:
            accumulated_increment = (accumulated_increment * multiplier + increment) & MASK
            accumulated_multiplier = (accumulated_multiplier * multiplier) & MASK
        increment = (increment * (multiplier + 1)) & MASK
        multiplier = (multiplier * multiplier) & MASK
        rounds >>= 1
    return (accumulated_multiplier * value + accumulated_increment) & MASK
