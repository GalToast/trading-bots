#!/usr/bin/env python3
"""
Event log indexer for penetration lattice lanes.

Scans .jsonl event logs and produces .summary.json files with:
- Total closes, wins, losses, net PnL, $/close
- Last N closes stats (total, avg, min, max)
- Reset count, watchdog restarts
- First/last event timestamps
- Recent activity window

Usage:
    python scripts/index_event_logs.py              # All logs, tail 500 lines
    python scripts/index_event_logs.py --full        # All logs, full scan
    python scripts/index_event_logs.py --file PATH   # Single file
    python scripts/index_event_logs.py --tail 1000   # Tail N lines
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

REPORTS = Path(__file__).resolve().parent.parent / "reports"


def tail_file(path: Path, n_lines: int) -> list[str]:
    """Read last N lines of a file efficiently."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            # Read in chunks from end
            chunk_size = min(65536, max(1024, size // 10))
            buf = bytearray()
            pos = size
            while pos > 0 and buf.count(b"\n") < n_lines:
                pos = max(0, pos - chunk_size)
                f.seek(pos)
                buf = f.read(size - pos) + buf
                if pos == 0:
                    break
            lines = buf.decode("utf-8", errors="replace").split("\n")
            # Remove trailing empty line from final newline
            if lines and lines[-1] == "":
                lines = lines[:-1]
            return lines[-n_lines:]
    except Exception:
        return []


def scan_events(lines: list[str]) -> dict[str, Any]:
    """Scan event lines and extract statistics."""
    closes = []
    resets = 0
    restarts = 0
    first_ts = None
    last_ts = None
    total_lines = 0
    parse_errors = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        total_lines += 1
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue

        action = evt.get("action", "")
        ts = evt.get("ts_utc", "")

        if first_ts is None:
            first_ts = ts
        last_ts = ts

        if action == "close_ticket":
            pnl = float(evt.get("realized_pnl", 0.0) or 0.0)
            closes.append({
                "ts_utc": str(ts)[:19],
                "direction": str(evt.get("direction", "")),
                "realized_pnl": round(pnl, 2),
                "symbol": evt.get("symbol", ""),
            })
        elif action == "reset_state":
            resets += 1
        elif action == "watchdog_restart":
            restarts += 1

    # Compute stats
    n_closes = len(closes)
    net_pnl = sum(c["realized_pnl"] for c in closes)
    wins = sum(1 for c in closes if c["realized_pnl"] > 0)
    losses = sum(1 for c in closes if c["realized_pnl"] < 0)
    pnl_per_close = round(net_pnl / n_closes, 2) if n_closes > 0 else 0.0

    # Last 20 closes
    last20 = closes[-20:]
    last20_net = sum(c["realized_pnl"] for c in last20)
    last20_avg = round(last20_net / len(last20), 2) if last20 else 0.0
    last20_min = min((c["realized_pnl"] for c in last20), default=0.0)
    last20_max = max((c["realized_pnl"] for c in last20), default=0.0)

    # Last 50 closes
    last50 = closes[-50:]
    last50_net = sum(c["realized_pnl"] for c in last50)
    last50_avg = round(last50_net / len(last50), 2) if last50 else 0.0

    return {
        "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "total_events_scanned": total_lines,
        "parse_errors": parse_errors,
        "first_event_ts": first_ts,
        "last_event_ts": last_ts,
        "closes": {
            "total": n_closes,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / n_closes, 3) if n_closes > 0 else 0.0,
            "net_usd": round(net_pnl, 2),
            "pnl_per_close": pnl_per_close,
        },
        "last_20_closes": {
            "count": len(last20),
            "net_usd": round(last20_net, 2),
            "avg_pnl": last20_avg,
            "min_pnl": round(last20_min, 2),
            "max_pnl": round(last20_max, 2),
        },
        "last_50_closes": {
            "count": len(last50),
            "net_usd": round(last50_net, 2),
            "avg_pnl": last50_avg,
        },
        "resets": resets,
        "watchdog_restarts": restarts,
    }


def index_file(path: Path, tail_lines: int, full: bool = False) -> dict[str, Any]:
    """Index a single event log file."""
    if full:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    else:
        lines = tail_file(path, tail_lines)

    summary = scan_events(lines)
    summary["source_file"] = str(path.name)
    summary["mode"] = "full" if full else f"tail_{tail_lines}"
    return summary


def main():
    parser = argparse.ArgumentParser(description="Index penetration lattice event logs")
    parser.add_argument("--file", type=str, help="Single event log file to index")
    parser.add_argument("--tail", type=int, default=500, help="Lines to read from end (default: 500)")
    parser.add_argument("--full", action="store_true", help="Scan entire file (slow for large logs)")
    parser.add_argument("--loop", action="store_true", help="Run in loop mode, re-index every 30s")
    parser.add_argument("--interval", type=int, default=30, help="Loop interval seconds (default: 30)")
    args = parser.parse_args()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"File not found: {path}")
            return
        summary = index_file(path, args.tail, args.full)
        out = path.with_suffix(".summary.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        c = summary["closes"]
        print(f"{path.name}: {c['total']} closes, ${c['net_usd']:.2f} net, ${c['pnl_per_close']:.2f}/close")
        print(f"  Resets: {summary['resets']}, Restarts: {summary['watchdog_restarts']}")
        print(f"  Last 20: ${summary['last_20_closes']['net_usd']:.2f} (${summary['last_20_closes']['avg_pnl']:.2f}/close)")
        print(f"  Summary: {out}")
        return

    # Batch mode: find all .jsonl event logs
    patterns = ["*_events.jsonl"]
    files = []
    for pattern in patterns:
        files.extend(REPORTS.glob(pattern))

    # Exclude blown-out / backup files
    files = [f for f in files if "blown_out" not in f.name and "backup" not in f.name]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    print(f"Indexing {len(files)} event logs (tail {args.tail} lines each)...\n")
    results = []
    for path in files:
        summary = index_file(path, args.tail, args.full)
        out = path.with_suffix(".summary.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        c = summary["closes"]
        if c["total"] > 0:
            line = f"  {path.name[:60]:60s} {c['total']:5d} closes  ${c['net_usd']:10.2f}  ${c['pnl_per_close']:8.2f}/close  resets={summary['resets']}"
        else:
            line = f"  {path.name[:60]:60s} {c['total']:5d} closes  (no data)"
        print(line)
        results.append(summary)

    # Summary board
    total_closes = sum(s["closes"]["total"] for s in results)
    total_net = sum(s["closes"]["net_usd"] for s in results)
    print(f"\n  {'TOTAL':60s} {total_closes:5d} closes  ${total_net:10.2f}")
    print(f"\n  Summaries written to reports/*.summary.json")

    if args.loop:
        print(f"\n  Loop mode: re-indexing every {args.interval}s (Ctrl+C to stop)")
        try:
            cycle = 0
            while True:
                time.sleep(args.interval)
                cycle += 1
                for path in files:
                    summary = index_file(path, args.tail, args.full)
                    out = path.with_suffix(".summary.json")
                    with open(out, "w", encoding="utf-8") as f:
                        json.dump(summary, f, indent=2)
                print(f"  [cycle {cycle}] indexed {len(files)} files at {time.strftime('%H:%M:%S')}")
        except KeyboardInterrupt:
            print("\n  Loop stopped.")


if __name__ == "__main__":
    main()
