#!/usr/bin/env python3
"""Load simulated execution records, reconcile them, and export an operator CSV."""

import argparse
import json
from pathlib import Path
import sqlite3

from jitter_runner.reconcile import export_csv, read_records, reconcile


def validate_output(csv_path, database, inputs):
    ledger = database.resolve()
    protected = [ledger, *(Path(str(ledger) + suffix) for suffix in ("-journal", "-wal", "-shm")),
                 *(path.resolve() for path in inputs)]
    for path in protected:
        if csv_path == path or (csv_path.exists() and path.exists() and csv_path.samefile(path)):
            raise ValueError(f"CSV output would overwrite the ledger or an input: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--input", action="append", type=Path, default=[])
    parser.add_argument("--as-of-ns", required=True, type=int)
    parser.add_argument("--grace-ns", required=True, type=int)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    try:
        csv_path = args.csv.resolve()
        validate_output(csv_path, args.database, args.input)
        rows = [record for path in args.input for record in read_records(path)]
        result = reconcile(args.database, rows, as_of_ns=args.as_of_ns, grace_ns=args.grace_ns)
        export_csv(csv_path, result)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"desk reconcile: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
