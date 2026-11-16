#!/usr/bin/env python3
"""Load simulated execution records, reconcile them, and export an operator CSV."""

import argparse
import json
from pathlib import Path

from jitter_runner.reconcile import export_csv, read_records, reconcile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--input", action="append", type=Path, default=[])
    parser.add_argument("--as-of-ns", required=True, type=int)
    parser.add_argument("--grace-ns", required=True, type=int)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    rows = [record for path in args.input for record in read_records(path)]
    result = reconcile(args.database, rows, as_of_ns=args.as_of_ns, grace_ns=args.grace_ns)
    export_csv(args.csv, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
