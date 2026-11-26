#!/usr/bin/env python3
"""Fetch the pinned Nasdaq ITCH tape with bounded parallel ranges and verify its bytes."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


NAME = "12302019.NASDAQ_ITCH50.gz"
URL = "https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/" + NAME
SIZE = 3_524_013_057
SHA256 = "ef03df46a27e6bda4dead017f84c2e3979df7211f02c7868b51d53fceb99c689"
CHUNK = 1024 * 1024


def verify(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
    if path.stat().st_size != SIZE or digest.hexdigest() != SHA256:
        raise ValueError("download does not match pinned byte size and SHA-256")


def get_range(fd, start, stop):
    position = start
    for _ in range(4):
        command = [
            "curl", "--fail", "--silent", "--show-error", "--location",
            "--connect-timeout", "20", "--speed-limit", "4096", "--speed-time", "45",
            "--range", f"{position}-{stop - 1}", URL,
        ]
        with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
            try:
                while block := process.stdout.read(CHUNK):
                    if position + len(block) > stop:
                        raise ValueError("server returned bytes outside the requested range")
                    written = os.pwrite(fd, block, position)
                    if written != len(block):
                        raise OSError("short disk write")
                    position += written
                status = process.wait()
            finally:
                if process.poll() is None:
                    process.kill()
        if position == stop:
            if status:
                raise ValueError("server did not finish the range response cleanly")
            print(f"verified length of byte range {start}-{stop - 1}", flush=True)
            return
    raise OSError(f"incomplete range {start}-{stop - 1}, stopped at {position}")


def fetch(output, jobs):
    if output.exists():
        verify(output)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < SIZE + 256 * CHUNK:
        raise OSError("the output filesystem needs at least 3.8 GB available")
    fd, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".part", dir=output.parent)
    path = Path(temporary)
    try:
        os.ftruncate(fd, SIZE)
        stride = (SIZE + jobs - 1) // jobs
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(get_range, fd, start, min(start + stride, SIZE))
                       for start in range(0, SIZE, stride)]
            for future in futures:
                future.result()
        verify(path)
        os.link(path, output)
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--jobs", type=int, default=12, choices=range(1, 17), metavar="1..16")
    args = parser.parse_args()
    fetch(args.output, args.jobs)
    print(f"{SHA256}  {args.output}")


if __name__ == "__main__":
    main()
