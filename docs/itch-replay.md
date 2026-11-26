# Historical Nasdaq replay and recovery

Jitter reconstructs displayed books from the Nasdaq TotalView-ITCH 5.0 binary file and uses captured windows from that reconstruction as inputs for its own two-process MoldUDP64 simulator. The public comparison aligns the uninterrupted reference and faulted runs by sequence. [Open the replay](https://jitter-sandy.vercel.app/replay/itch/).

## Download and reconstruct

Build the Release targets with the standard [repository instructions](../README.md#build-and-test). The sample is 3,524,013,057 compressed bytes. The downloader checks free space and validates the transfer against the project's pinned SHA-256. The pipeline streams decompressed records through standard input. It retains compact snapshot and tape files for just the selected windows.

```sh
out=results/itch-session
mkdir -p "$out"
python3 tools/fetch_itch_sample.py "$out/session.itch.gz"
set -o pipefail
gzip -cd "$out/session.itch.gz" |
  build/jitter_itch --verify --capture "$out/windows" > "$out/session.json"
```

The input is `12302019.NASDAQ_ITCH50.gz` from the [Nasdaq sample directory](https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/). Jitter calculates SHA-256 from the archived bytes and streams the entire gzip member through its integrity check. The link to Nasdaq's separate MD5 file returned HTTP 404 during the recorded download. The project pins the SHA-256 from the completed transfer. Use `--verify --symbol AAPL` to restrict book verification to one listed symbol while reading all records, or `--limit N` for a bounded prefix. The capture mode requires full-symbol verification.

The independent book uses per-symbol order identity maps and a combined price index. The primary replay uses a global sparse reference-number map and sparse price maps per side, indexed by the session's stock locate. Both use integer prices and aggregate displayed size at each price. The decoder checks big-endian binary field widths before reading, and requires a directory entry for every symbol receiving book updates. Replacement changes the order reference and retains the existing side. The executed-with-price message reduces displayed quantity at the resting price. Nasdaq separately reports trades, crosses, imbalances, and trade breaks. The replay counts those messages without using them to mutate displayed orders. The [Nasdaq specification](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification_5.0.pdf) governs these interpretations.

The reference compares both sides of the top of book for every order update. At the exchange's start of market hours, end of market hours, and end-of-messages markers it compares the sorted canonical order rows directly. The displayed 64-bit fingerprint is a compact identifier for those rows. The end-of-session empty-book fingerprint is expected. The opening and closing checkpoints compare nonempty books.

The archived [full-session record](evidence/itch/session.json) covers December 30, 2019. It reports **268,744,780 messages**, **8,906 directory symbols**, **8,892 symbols with displayed order updates**, and **263,241,937 differential quote comparisons**. Peak live orders were **1,924,078**. Peak process RSS includes the candidate and reference, snapshot generation, and result recording. Wall time in this verification record includes decompression through the pipe and checkpoint work.

## Capture windows and replay the fault

The capture starts at the first record in tape order whose market timestamp reaches 09:30 or 12:00 Eastern on that date. Each window contains 40,000 consecutive messages, a snapshot through the preceding file ordinal, a second snapshot through message 96 of the window, and an independent final snapshot. The transport study sends the captured payloads at a prescribed rate. The original market timestamps identify the source windows. Simulated send times come from the experiment schedule.

```sh
python3 tools/itch_study.py --binary build/jitter_mold \
  --window results/itch-session/windows/open \
  --output results/itch-faults --rate 15000 --repeat 1 \
  --environment macos-arm64
```

A separate producer and receiver exchange local IPv4 UDP datagrams. Each data packet carries up to eight ITCH messages with real [MoldUDP64 framing](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf). Both receive sockets bind to loopback. The simulator uses two unicast destinations, sends heartbeats and end-of-session markers, and services small re-requests from a bounded local tape range. File ordinals are used as simulated session sequences. The publisher can withhold, reorder, or resend datagrams. This experiment uses local sockets and a simulated snapshot file as its out-of-band recovery service.

The gap gates publication at the last contiguous sequence. For a small recoverable gap, the receiver requests the missing range as the publisher continues. The larger outage omits 32 messages across both channels. A recovery worker reads and builds the authoritative snapshot while the receiver buffers live input. The receiver installs it only when the watermark covers the missing range and the later buffered messages form a contiguous suffix. Buffer overflow leaves the receiver stale until a compatible snapshot covers the discarded sequences. The overflow test intentionally withholds that snapshot.

The fault report accounts for every input message as applied or covered by the snapshot. Duplicate arrivals are counted independently from publication. Both the clean and recovered processes compare their complete final order rows against the independent saved snapshot at the same sequence. In the public visual comparison, the reference sequence is aligned with the faulted receiver's observed input frontier. The two runs have independent wall clocks. [Recorded comparison](evidence/itch/showcase.json).

Five clean and five snapshot runs per window verified on the Apple Silicon host at 50,000 offered messages/s. The median stale-to-recovered interval was **175.7 ms** for the opening window and **903.0 ms** for midday. The midday initial snapshot held about four times as many orders. In both windows, every run ended with the correct final book. These timings describe that host and workload. [Opening runs](evidence/itch/macos-open.json) · [Midday runs](evidence/itch/macos-quiet.json).

## Linux measurement

The [dispatchable Linux workflow](../.github/workflows/itch-linux.yml) verifies the same full sample and runs the fixture tests before measuring the two captured windows. The standard runner is a GitHub-hosted Linux x86-64 VM. For a dedicated Intel or AMD Linux host, use the same commands on an otherwise idle machine, document the CPU and kernel, and select two available physical cores as appropriate.

```sh
python3 tools/itch_study.py --binary build/jitter_mold \
  --window results/itch-session/windows/open --case clean \
  --output results/itch-open-linux --rate 50000 --repeat 5 \
  --compare-receive-batching --sender-cpu 2 --receiver-cpu 4 \
  --environment linux-x86-64-bare-metal
```

Repeat for `windows/quiet`. The Linux comparison keeps eight messages per datagram, both feeds, the window bytes, and the sender's schedule fixed. It compares per-datagram `recvfrom` with nonblocking `recvmmsg` capped at 16 datagrams per call, alternating order across repeats. Read-call counts test whether the kernel actually returned multiple datagrams. Each receive timestamp is taken after the receive system call. A batched receive shares that timestamp across its returned datagrams.

Scheduled-to-publication starts at the planned release of the containing datagram. Receive-to-publication starts when the system call returns. Both end when the update advances the receiver's book and sequence. These ordinary-path distributions exclude buffered recovery work, which is counted and measured separately from stale detection through resumption. Each raw timing ledger row stores its sequence, receive time, and publication time as three big-endian 64-bit fields. Percentiles use nearest rank and require at least 40, 2,000, and 20,000 observations respectively.

Profile the baseline in separate runs with `perf stat` and `perf record` when available. Keep any PMU or kernel permission errors with the evidence. Jitter uses matching order rows and complete sequence accounting as gates for interpreting timing. Virtual-machine measurements and Apple Silicon measurements have distinct provenance. Dedicated Linux bare-metal measurements remain pending.
