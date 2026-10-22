#!/usr/bin/env python3
"""Source-checkout entry point; installed users can run `jitter`."""
from jitter_runner.cli import entrypoint

if __name__ == "__main__":
    raise SystemExit(entrypoint())
