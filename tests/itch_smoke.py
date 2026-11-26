"""Independent wire fixtures and public CLI checks for ITCH 5.0."""

import json
import subprocess
import sys


def be(value, size):
    return value.to_bytes(size, "big")


def message(kind, locate, tail, time=34200000000000):
    return kind.encode() + be(locate, 2) + bytes(2) + be(time, 6) + tail


def frame(entry):
    return be(len(entry), 2) + entry


def directory(locate, name):
    return message("R", locate, name.encode().ljust(8) + bytes(20))


def add(locate, name, ident, side, size, price):
    return message("A", locate, be(ident, 8) + side.encode() + be(size, 4) +
                   name.encode().ljust(8) + be(price, 4))


def reduce(kind, locate, ident, size, tail=b""):
    return message(kind, locate, be(ident, 8) + be(size, 4) + tail)


def replace(locate, ident, new, size, price):
    return message("U", locate, be(ident, 8) + be(new, 8) + be(size, 4) + be(price, 4))


def session():
    ident = (1 << 44) + 1
    return [
        message("S", 0, b"O"), directory(76, "FIRST"), directory(9000, "SECOND"),
        message("H", 76, b"FIRST   T " + b"    "),
        add(76, "FIRST", ident, "B", 100, 1200040),
        add(9000, "SECOND", ident + 1, "S", 200, 900020),
        message("S", 0, b"Q"), reduce("E", 76, ident, 25, bytes(8)),
        reduce("C", 9000, ident + 1, 17, bytes(8) + b"Y" + be(900010, 4)),
        reduce("X", 76, ident, 15), replace(76, ident, ident + 2, 30, 1200035),
        message("B", 76, bytes(8)), message("P", 76, bytes(33)),
        message("Q", 76, bytes(29)), message("S", 0, b"M"),
        message("D", 9000, be(ident + 1, 8)), message("S", 0, b"C"),
    ]


def run(binary, tape, *args):
    return subprocess.run([binary, *args], input=tape, capture_output=True, timeout=15)


def book_fingerprint(orders):
    """Canonical row bytes specified independently of the C++ decoder."""
    result = 14695981039346656037
    for order_id, locate, side, price, size in sorted(orders):
        for value, width in ((order_id, 8), (locate, 2), (side, 1), (price, 4), (size, 4)):
            for byte in value.to_bytes(width, "little"):
                result = ((result ^ byte) * 1099511628211) % (1 << 64)
    return f"{result:016x}"


def main(binary):
    entries = session()
    tape = b"".join(map(frame, entries))
    result = json.loads(run(binary, tape, "--verify").stdout)
    assert result["complete_session"]
    assert result["messages"] == len(entries)
    assert result["directory_symbols"] == result["updated_symbols"] == 2
    assert result["book_updates"] == result["reference_quote_checks"] == 7
    assert [row["active_orders"] for row in result["checkpoints"]] == [2, 2, 1]
    ident = (1 << 44) + 1
    assert result["checkpoints"][-1]["book_fingerprint"] == book_fingerprint([
        (ident + 2, 76, 0, 1200035, 30)])
    # C carries a different execution price. The resting ask keeps its price
    # and loses only the executed shares. E and X remove shares on the bid.
    partial = json.loads(run(binary, tape, "--verify", "--limit", "10").stdout)
    assert not partial["complete_session"]
    assert partial["checkpoints"][-1]["book_fingerprint"] == book_fingerprint([
        (ident, 76, 0, 1200040, 60), (ident + 1, 9000, 1, 900020, 183)])
    filtered = json.loads(run(binary, tape, "--verify", "--symbol", "FIRST").stdout)
    assert filtered["updated_symbols"] == 1 and filtered["reference_quote_checks"] == 4
    assert filtered["checkpoints"][-1]["book_fingerprint"] == result["checkpoints"][-1]["book_fingerprint"]
    for data, error in [
        (tape[:-1], b"truncated"),
        (tape + frame(message("?", 0, b"")), b"unknown ITCH"),
        (tape + frame(message("S", 0, b"XX")), b"wrong ITCH length"),
        (tape + frame(reduce("X", 76, (1 << 44) + 3, 31)), b"invalid order lifecycle"),
    ]:
        bad = run(binary, data, "--verify")
        assert bad.returncode == 1 and error in bad.stderr, bad.stderr


if __name__ == "__main__":
    main(sys.argv[1])
