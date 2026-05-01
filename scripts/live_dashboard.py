#!/usr/bin/env python3
"""
Live Trading Dashboard — Zero dependencies, stdlib only.

Serves a simple HTML dashboard showing:
- Runner heartbeat and equity
- Per-coin positions and stats
- Recent events
- Strategy frontier table

Usage:
    python scripts/live_dashboard.py --port 8080

Open http://localhost:8080 in browser.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "multi_coin_momentum_state.json"
EVENT_PATH = ROOT / "reports" / "multi_coin_momentum_events.jsonl"
MEMORY_PATH = ROOT / "memory.md"

PORT = 8080


def read_state():
    if not STATE_PATH.exists():
        return None
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def read_events(n=20):
    if not EVENT_PATH.exists():
        return []
    try:
        with open(EVENT_PATH) as f:
            lines = f.readlines()
        events = []
        for line in lines[-n:]:
            try:
                events.append(json.loads(line.strip()))
            except Exception:
                pass
        return events
    except Exception:
        return []


def read_memory_snippet():
    if not MEMORY_PATH.exists():
        return ""
    try:
        with open(MEMORY_PATH, encoding="utf-8") as f:
            content = f.read()
        # Extract strategy frontier section
        start = content.find("## Strategy Frontier")
        end = content.find("## Critical Findings")
        if start >= 0 and end >= 0:
            return content[start:end].strip()
        return ""
    except Exception:
        return ""


def age_str(ts_str):
    if not ts_str:
        return "unknown"
    try:
        then = datetime.fromisoformat(ts_str)
        now = datetime.now(timezone.utc)
        delta = now - then
        total_sec = int(delta.total_seconds())
        if total_sec < 60:
            return f"{total_sec}s ago"
        elif total_sec < 3600:
            return f"{total_sec // 60}m ago"
        else:
            return f"{total_sec // 3600}h {total_sec % 3600 // 60}m ago"
    except Exception:
        return ts_str


HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="15">
    <title>Trading Bots Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ color: #58a6ff; margin-bottom: 20px; font-size: 24px; }}
        h2 {{ color: #58a6ff; margin: 20px 0 10px; font-size: 18px; border-bottom: 1px solid #30363d; padding-bottom: 5px; }}
        .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 15px; margin-bottom: 15px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 15px; }}
        .stat {{ text-align: center; padding: 15px; }}
        .stat .value {{ font-size: 32px; font-weight: bold; color: #58a6ff; }}
        .stat .label {{ font-size: 12px; color: #8b949e; margin-top: 5px; }}
        .green {{ color: #3fb950; }}
        .red {{ color: #f85149; }}
        .yellow {{ color: #d29922; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #30363d; font-size: 13px; }}
        th {{ color: #8b949e; font-weight: 600; }}
        .pos-active {{ background: #238636; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px; }}
        .pos-flat {{ background: #30363d; color: #8b949e; padding: 2px 8px; border-radius: 12px; font-size: 11px; }}
        pre {{ background: #0d1117; padding: 10px; border-radius: 6px; overflow-x: auto; font-size: 12px; }}
        .age {{ color: #8b949e; font-size: 11px; }}
    </style>
</head>
<body>
<div class="container">
    <h1>🤖 Trading Bots Dashboard</h1>
    <p class="age">Auto-refreshes every 15s | Last check: {check_time}</p>

    <h2>📊 Runner Status</h2>
    {runner_status}

    <h2>🪙 Coin Positions</h2>
    {coin_table}

    <h2>📈 Recent Events</h2>
    {events_table}

    <h2>📋 Strategy Frontier (30d Verified)</h2>
    {frontier_table}
</div>
</body>
</html>"""


def render():
    state = read_state()
    events = read_events(30)
    check_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Runner status
    if state:
        cycle = state.get("cycle", "?")
        equity = state.get("total_equity", 0)
        pnl = state.get("total_pnl", 0)
        updated = state.get("updated_at", "")
        age = age_str(updated)
        pnl_class = "green" if pnl >= 0 else "red"
        runner_status = f"""<div class="grid">
            <div class="card stat"><div class="value">{cycle}</div><div class="label">Cycle</div></div>
            <div class="card stat"><div class="value">${equity:.2f}</div><div class="label">Total Equity</div></div>
            <div class="card stat"><div class="value {pnl_class}">${pnl:+.2f}</div><div class="label">Total PnL</div></div>
            <div class="card stat"><div class="value">{age}</div><div class="label">Last Update</div></div>
        </div>"""
    else:
        runner_status = '<div class="card"><p class="red">No state file found. Runner may not be running.</p></div>'

    # Coin table
    if state and "coins" in state:
        rows = []
        for coin, info in sorted(state["coins"].items()):
            pos = info.get("position", "flat")
            pos_class = "pos-active" if pos == "active" else "pos-flat"
            entry = info.get("position_entry", "-")
            hold = info.get("position_hold", "-")
            signals = info.get("signals", 0)
            closes = info.get("closes", 0)
            wins = info.get("wins", 0)
            losses = info.get("losses", 0)
            wr = info.get("win_rate", 0)
            rows.append(f"<tr><td>{coin}</td><td><span class=\"{pos_class}\">{pos.upper()}</span></td>"
                        f"<td>{entry}</td><td>{hold}</td><td>{signals}</td><td>{closes}</td>"
                        f"<td>{wins}W / {losses}L</td><td>{wr}%</td></tr>")
        coin_table = f"""<table>
            <tr><th>Coin</th><th>Position</th><th>Entry</th><th>Hold</th><th>Signals</th><th>Closes</th><th>W/L</th><th>WR%</th></tr>
            {''.join(rows)}
        </table>"""
    else:
        coin_table = "<p>No coin data available.</p>"

    # Events table
    if events:
        rows = []
        for evt in reversed(events):
            action = evt.get("action", "?")
            coin = evt.get("coin", "-")
            ts = evt.get("ts_utc", "")
            details = ""
            if action == "open":
                details = f"Entry: {evt.get('entry_price', '?')}, TP: {evt.get('tp', '?')}, SL: {evt.get('sl', '?')}, Deploy: ${evt.get('deploy', '?')}"
            elif action == "close":
                pnl_val = evt.get("net", 0)
                pnl_class = "green" if pnl_val >= 0 else "red"
                details = f"Exit: {evt.get('exit_price', '?')}, Net: <span class=\"{pnl_class}\">${pnl_val:+.2f}</span>, Reason: {evt.get('reason', '?')}"
            elif action.startswith("runner"):
                details = f"Cash: ${evt.get('cash', '?')}"
            rows.append(f"<tr><td>{ts[:19] if ts else '?'}</td><td>{action}</td><td>{coin}</td><td>{details}</td></tr>")
        events_table = f"""<table>
            <tr><th>Time</th><th>Action</th><th>Coin</th><th>Details</th></tr>
            {''.join(rows)}
        </table>"""
    else:
        events_table = "<p>No events recorded.</p>"

    # Strategy frontier (from memory.md)
    frontier_html = read_memory_snippet()
    if frontier_html:
        frontier_table = f"<pre>{frontier_html}</pre>"
    else:
        frontier_table = "<p>No frontier data available.</p>"

    return HTML.format(
        check_time=check_time,
        runner_status=runner_status,
        coin_table=coin_table,
        events_table=events_table,
        frontier_table=frontier_table,
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(render().encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress logging


def main():
    port = PORT
    args = sys.argv[1:]
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            port = int(args[idx + 1])
    elif args and args[0].isdigit():
        port = int(args[0])

    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Dashboard running on http://0.0.0.0:{port}", flush=True)
    print(f"Open in browser: http://localhost:{port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
