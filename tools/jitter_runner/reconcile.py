"""Append-only simulated booking and drop-copy reconciliation using SQLite."""

import csv
from collections import defaultdict
import hashlib
import json
import re
import sqlite3


FIELDS = ("source", "record_id", "session", "execution_id", "revision", "instrument",
          "side", "quantity", "price_ticks", "executed_ns", "observed_ns")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
MAX_NS = 2**63 - 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    source TEXT NOT NULL, record_id TEXT NOT NULL, session TEXT NOT NULL,
    execution_id TEXT NOT NULL, revision INTEGER NOT NULL, instrument TEXT NOT NULL,
    side TEXT NOT NULL, quantity INTEGER NOT NULL, price_ticks INTEGER NOT NULL,
    executed_ns INTEGER NOT NULL, observed_ns INTEGER NOT NULL,
    PRIMARY KEY (source, record_id)
);
CREATE INDEX IF NOT EXISTS execution ON fills(session, execution_id, source, revision);
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY CHECK(id=1), grace_ns INTEGER NOT NULL, watermark_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS transitions (
    ordinal INTEGER PRIMARY KEY, as_of_ns INTEGER NOT NULL, session TEXT NOT NULL,
    execution_id TEXT NOT NULL, state TEXT NOT NULL, discrepancy TEXT NOT NULL,
    evidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS latest ON transitions(session, execution_id, ordinal DESC);
"""


def parse(row):
    if set(row) != set(FIELDS):
        raise ValueError("invalid execution CSV columns")
    row = dict(row)
    for key in ("record_id", "session", "execution_id", "instrument"):
        if not isinstance(row[key], str) or not NAME.fullmatch(row[key]):
            raise ValueError(f"invalid {key}")
    if row["source"] not in {"desk", "venue"} or row["side"] not in {"BUY", "SELL"}:
        raise ValueError("invalid source or side")
    for key, minimum, maximum in (("revision", 1, 10**9), ("quantity", 1, 10**9),
                                  ("price_ticks", 1, 10**12), ("executed_ns", 0, MAX_NS),
                                  ("observed_ns", 0, MAX_NS)):
        value = row[key]
        if type(value) is not int and (not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value)):
            raise ValueError(f"invalid integer {key}")
        row[key] = int(value)
        if not minimum <= row[key] <= maximum:
            raise ValueError(f"out of bounds {key}")
    if row["executed_ns"] > row["observed_ns"]:
        raise ValueError("observation precedes execution")
    return row


def read_records(path):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            raise ValueError("execution CSV header does not match the contract")
        return [parse(row) for row in reader]


def _signed(row):
    return row["quantity"] if row["side"] == "BUY" else -row["quantity"]


def _observations(db, as_of, grace):
    grouped = defaultdict(lambda: {"desk": [], "venue": []})
    positions = defaultdict(lambda: {"desk": 0, "venue": 0})
    for row in db.execute("""
        WITH ranked AS (
          SELECT *, dense_rank() OVER (PARTITION BY session,execution_id,source
                                      ORDER BY revision DESC) AS revision_rank
          FROM fills
        )
        SELECT source,record_id,session,execution_id,revision,instrument,side,quantity,
               price_ticks,executed_ns,observed_ns FROM ranked
        WHERE revision_rank=1 ORDER BY session,execution_id,source,record_id
    """):
        record = dict(row)
        grouped[row["session"], row["execution_id"]][row["source"]].append(record)
        positions[row["instrument"]][row["source"]] += _signed(row)
    for (session, execution), sources in sorted(grouped.items()):
        desk, venue = sources["desk"], sources["venue"]
        reasons = []
        if len(desk) > 1:
            reasons.append("duplicate-desk")
        if len(venue) > 1:
            reasons.append("duplicate-venue")
        if not desk:
            reasons.append("missing-desk")
        if not venue:
            reasons.append("missing-venue")
        if len(desk) == len(venue) == 1:
            for key in ("instrument", "side", "quantity", "price_ticks"):
                if desk[0][key] != venue[0][key]:
                    reasons.append("mismatched-" + key)
        first = min(record["observed_ns"] for record in desk + venue)
        discrepancy = ",".join(reasons)
        if not reasons:
            state = "resolved"
        elif len(reasons) == 1 and reasons[0].startswith("missing-") and as_of - first < grace:
            state = "pending"
        else:
            state = "confirmed"
        relevant = defaultdict(int)
        for record in desk + venue:
            relevant[record["instrument"]] += _signed(record) * (1 if record["source"] == "desk" else -1)
        detail = {"records": sources, "position_impact": dict(sorted(relevant.items())),
                  "first_observed_ns": first}
        evidence = json.dumps(detail, sort_keys=True, separators=(",", ":"))
        previous = db.execute("""
            SELECT state,discrepancy,evidence FROM transitions WHERE session=? AND execution_id=?
            ORDER BY ordinal DESC LIMIT 1
        """, (session, execution)).fetchone()
        if (previous is not None or reasons) and (previous is None or tuple(previous) != (state, discrepancy, evidence)):
            db.execute("""INSERT INTO transitions(as_of_ns,session,execution_id,state,discrepancy,evidence)
                          VALUES (?,?,?,?,?,?)""", (as_of, session, execution, state, discrepancy, evidence))
    return {instrument: {**sides, "desk_minus_venue": sides["desk"] - sides["venue"]}
            for instrument, sides in sorted(positions.items())}


def reconcile(database, records, *, as_of_ns, grace_ns):
    rows = [parse(record) for record in records]
    for value in (as_of_ns, grace_ns):
        if type(value) is not int or not 0 <= value <= MAX_NS:
            raise ValueError("invalid reconciliation clock")
    if any(row["observed_ns"] > as_of_ns for row in rows):
        raise ValueError("batch contains observations after as-of time")
    db = sqlite3.connect(database, check_same_thread=True)
    db.row_factory = sqlite3.Row
    try:
        db.executescript(SCHEMA)
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?, ?)", (grace_ns, as_of_ns))
            settings = db.execute("SELECT grace_ns,watermark_ns FROM settings WHERE id=1").fetchone()
            if settings["grace_ns"] != grace_ns:
                raise ValueError("grace period differs from existing database")
            watermark = max(as_of_ns, settings["watermark_ns"])
            db.execute("UPDATE settings SET watermark_ns=? WHERE id=1", (watermark,))
            for row in rows:
                values = tuple(row[k] for k in FIELDS)
                cursor = db.execute("INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?)", values)
                if cursor.rowcount == 0:
                    old = db.execute("SELECT * FROM fills WHERE source=? AND record_id=?", values[:2]).fetchone()
                    if tuple(old) != values:
                        raise ValueError(f"source record identity reused with different contents: {values[:2]}")
            positions = _observations(db, watermark, grace_ns)
            history = []
            open_by_id = {}
            for row in db.execute("SELECT * FROM transitions ORDER BY ordinal"):
                transition = dict(row)
                transition["evidence"] = json.loads(transition["evidence"])
                history.append(transition)
                open_by_id[row["session"], row["execution_id"]] = transition
            return {"as_of_ns": watermark, "records": db.execute("SELECT count(*) FROM fills").fetchone()[0],
                    "positions": positions, "history": history,
                    "open": [record for record in open_by_id.values() if record["state"] != "resolved"]}
    finally:
        db.close()


def export_csv(path, report):
    fields = ("session", "execution_id", "instrument", "discrepancy", "desk_minus_venue_quantity",
              "status", "as_of_ns", "desk_record_ids", "venue_record_ids", "previous_discrepancy")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        prior = {}
        for transition in report["history"]:
            details = transition["evidence"]
            identity = (transition["session"], transition["execution_id"])
            for instrument, impact in details["position_impact"].items():
                writer.writerow({"session": identity[0], "execution_id": identity[1], "instrument": instrument,
                                 "discrepancy": transition["discrepancy"], "desk_minus_venue_quantity": impact,
                                 "status": transition["state"], "as_of_ns": transition["as_of_ns"],
                                 "desk_record_ids": "|".join(r["record_id"] for r in details["records"]["desk"]),
                                 "venue_record_ids": "|".join(r["record_id"] for r in details["records"]["venue"]),
                                 "previous_discrepancy": prior.get(identity, "")})
            prior[identity] = transition["discrepancy"]
