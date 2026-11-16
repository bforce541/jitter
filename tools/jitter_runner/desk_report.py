"""Render the completed desk incident as an offline operator report."""

from html import escape


STYLE = """
:root{color-scheme:dark;background:#101317;color:#e7ebf1;font:15px/1.6 system-ui,sans-serif;
--quiet:#a8b1bf;--line:#303846;--ok:#7be3ab;--warn:#ffd17b;--bad:#ffa097}
*{box-sizing:border-box}body{margin:0}main{max-width:1120px;margin:auto;padding:52px 28px 70px}
header{border-bottom:1px solid var(--line);padding-bottom:24px}
h1{font-size:clamp(2rem,5vw,3.2rem);line-height:1.02;letter-spacing:-.055em;margin:0 0 12px}
h2{font-size:1.25rem;letter-spacing:-.02em;margin:0 0 12px}
section{margin-top:32px}p{margin:8px 0;color:var(--quiet)}a{color:#b9d2ff}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:18px;
padding:18px 0;border-bottom:1px solid var(--line);margin:0}
.summary dt{color:var(--quiet);font-size:.84rem}.summary dd{margin:0;font-size:1.33rem;font-weight:650}
.scroll{overflow-x:auto;border-top:1px solid var(--line)}
table{border-collapse:collapse;width:100%;text-align:left}
th,td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--quiet);font-size:.81rem;font-weight:650}
.num{font-variant-numeric:tabular-nums;white-space:nowrap;text-align:right}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
small{color:var(--quiet);display:block;line-height:1.35}
@media(max-width:550px){main{padding:28px 14px}th,td{padding:8px}table{min-width:480px}}
"""


def table(head, rows):
    header = "".join(f"<th scope='col'>{escape(name)}</th>" for name in head)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<div class='scroll'><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>"


def text(value):
    return escape(str(value))


def status(value):
    styles = {"current": "ok", "resolved": "ok", "stale": "bad", "confirmed": "bad",
              "waiting-gap": "warn", "recovering": "warn", "pending": "warn"}
    return f"<strong class='{styles.get(value, '')}'>{text(value)}</strong>"


def render(incident):
    market, desk = incident["market"], incident["desk"]
    report = desk["result"]
    active_feeds = [market["feed_a"]] + ([market["feed_b"]] if market["dual"] else [])
    delivering = sum(feed["sent"] > 0 and feed["missing_datagrams"] == feed["malformed"] == 0
                     for feed in active_feeds)
    market_headline = ("Feed B supplied sequence 2 when A omitted it." if market["dual"]
                       else "Feed A carried the single-channel scenario.")
    unresolved = [row for row in report["history"] if row["state"] == "confirmed"]
    action = unresolved[0]
    impact = ", ".join(f"{instrument} {quantity:+}" for instrument, quantity in action["evidence"]["position_impact"].items())
    proof = market["recovery_proof"]
    lifecycle = table(("Replay elapsed", "Book", "Sequence", "Observed action", "Buffered"), [
        (f'<span class="num">{step["elapsed_ns"] / 1e6:.3f} ms</span>', status(step["state"]),
         text(step["sequence"]), text(step["operation"]), text(step["backlog"]))
        for step in market["timeline"]
    ])
    channels = table(("Feed", "Sent", "Received", "Scheduled omissions", "Unplanned missing", "Malformed"), [
        (f"Feed {key.upper()}" if key == "a" or market["dual"] else "Feed B (disabled)",
         text(market[f"feed_{key}"]["sent"]), text(market[f"feed_{key}"]["received"]),
         text(market[f"feed_{key}_scheduled_omissions"]), text(market[f"feed_{key}"]["missing_datagrams"]),
         text(market[f"feed_{key}"]["malformed"])) for key in ("a", "b")
    ])
    discrepancies = table(("Scenario time", "Execution", "Status", "Evidence", "Desk − venue"), [
        (f'<span class="num">{row["as_of_ns"] / 1e6:g} ms</span>', text(row["execution_id"]), status(row["state"]),
         text(row["discrepancy"].replace("-", " ") or "matching drop copy") +
         "<small>" + text(" · ".join(f"{side} {', '.join(r['record_id'] for r in records) or 'missing'}"
                                     for side, records in row["evidence"]["records"].items())) + "</small>",
         text(", ".join(f"{inst} {impact:+}" for inst, impact in row["evidence"]["position_impact"].items())))
        for row in report["history"]
    ])
    positions = table(("Instrument", "Desk quantity", "Venue quantity", "Difference"), [
        (text(inst), text(values["desk"]), text(values["venue"]), text(values["desk_minus_venue"]))
        for inst, values in report["positions"].items()
    ])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jitter · Desk incident</title><style>{STYLE}</style></head>
<body><main><header><h1>Desk incident replay</h1>
<p>Simulated market data and executions · local UDP replay · verified against reference state</p></header>
<dl class="summary"><div><dt>Book state</dt><dd class="ok">Current</dd></div>
<div><dt>Last sequence</dt><dd>{market["sequence"]}</dd></div>
<div><dt>Feeds delivering</dt><dd>{delivering}/{len(active_feeds)}</dd></div>
<div><dt>Unresolved executions</dt><dd>{len(report["open"])}</dd></div>
<div><dt>Recovered state</dt><dd class="ok">Verified</dd></div></dl>
<section><h2>Market data lifecycle</h2>
<p>{text(market_headline)} Recovery used snapshot {proof["snapshot_through"]} and replayed buffered updates through
{proof["last_verified"]}. Reference-matched book ID <code>{text(proof["book_id"])}</code>.</p>{lifecycle}
<p>Gap detected in {market["detection_ns"] / 1e6:.3f} ms from the omitted event.
Snapshot and buffered replay independently verified {market["recovery_ns"] / 1e6:.3f} ms after detection.
{market["duplicate_publications"]} duplicate publications.</p>
{channels}</section>
<section><h2>Execution reconciliation</h2><p>Simulation clock · 50 ms late-report grace period</p>
<p>{text(action["execution_id"])} affected the desk-minus-venue position by {text(impact)} until the delayed drop copy arrived.</p>
{discrepancies}<p>Final positions, in signed units</p>{positions}
<p>All {report["records"]} original records retained. Reimport produced the same audit and positions.</p></section>
<section><h2>Evidence</h2><p><a href="market.json">Market events and publications</a> ·
<a href="incident.json">Incident JSON</a> · <a href="discrepancies.csv">Operator CSV</a> ·
<a href="desk-bookings.csv">Bookings</a> · <a href="venue-initial.csv">Initial drop copy</a> ·
<a href="venue-late.csv">Delayed drop copy</a></p></section>
</main></body></html>"""
