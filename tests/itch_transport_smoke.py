"""Exercise historical-window capture and two-process UDP recovery."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from itch_smoke import add, be, directory, frame, message, reduce

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from itch_study import CASES, run


def synthetic_session(window_messages=640):
    yield message("S", 0, b"O", 1)
    yield directory(33, "AAA")
    yield directory(222, "BBB")
    for base, offset in [(34_200_000_000_000, 10), (43_200_000_000_000, 50_000)]:
        # Spec-compliant binary fields with interleaved symbols and independent order IDs.
        for i in range(window_messages):
            locate, symbol, side = (33, "AAA", "B") if i % 4 < 2 else (222, "BBB", "S")
            ident = offset + i // 2
            base_message = add(locate, symbol, ident, side, 10, 50000 + (i % 20)) if i % 2 == 0 else (
                reduce("E", locate, ident, 2, bytes(8)))
            yield base_message[:5] + be(base + 1000 * i, 6) + base_message[11:]
    yield message("S", 0, b"M", 57_600_000_000_000)
    yield message("S", 0, b"C", 72_000_000_000_000)


def main(replay_binary, mold_binary):
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        window_root = root / "windows"
        capture = subprocess.run([replay_binary, "--verify", "--capture", str(window_root),
                                  "--window-messages", "512"],
                                 input=b"".join(map(frame, synthetic_session())),
                                 check=True, capture_output=True, timeout=15)
        report = json.loads(capture.stdout)
        assert report["windows"]["open"]["first_market_ns"] == 34_200_000_000_000
        assert report["windows"]["quiet"]["first_market_ns"] == 43_200_000_000_000
        window = window_root / "open"
        for case in CASES:
            result = run(Path(mold_binary).resolve(), window, case, 8000, 8, root / case)
            assert result["verified"], (case, result)
            receiver = result["receiver"]
            if case == "snapshot":
                assert receiver["snapshot_covered"] == 32 and receiver["replayed"] > 0
                assert any(step["event"] == "snapshot-requested" and step["stale"] for step in receiver["timeline"])
                assert receiver["book_fingerprint"] == receiver["expected_fingerprint"]
            if case == "a-only":
                assert receiver["published_immediately_b"] + receiver["replayed"] >= 8


if __name__ == "__main__":
    main(*sys.argv[1:])
