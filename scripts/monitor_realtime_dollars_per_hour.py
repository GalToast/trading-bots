#!/usr/bin/env python3
"""Real-time $/hour monitor for all live BTC lanes.

Polls state files every 5 seconds and shows:
- Current $/close, closes/hr, net USD
- DELTA since last check (new closes, new net, new $/hr)
- Running average since session start
- Alerts when metrics change significantly

Usage:
    python scripts/monitor_realtime_dollars_per_hour.py          # Poll every 5s
    python scripts/monitor_realtime_dollars_per_hour.py --poll 2 # Poll every 2s
    python scripts/monitor_realtime_dollars_per_hour.py --once   # Single snapshot
"""
import argparse
import json
import sys
import time
import io
from datetime import datetime, timezone
from pathlib import Path

# Force unbuffered output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

REPORTS = Path(__file__).resolve().parent.parent / "reports"

# Lanes to monitor: (display_name, state_filename, symbol)
LANES = [
    ("BTC M15 Live (alpha 1.0)", "penetration_lattice_live_btcusd_m15_warp_state.json", "BTCUSD"),
    ("BTC M15 $15 Shadow", "penetration_lattice_shadow_btcusd_m15_step15_state.json", "BTCUSD"),
    ("BTC M15 $20 Shadow", "penetration_lattice_shadow_btcusd_m15_step20_state.json", "BTCUSD"),
    ("BTC M15 Restore v1", "penetration_lattice_shadow_btcusd_m15_warp_restore_v1_state.json", "BTCUSD"),
]


def extract_metrics(state_filename: str, symbol: str) -> dict | None:
    """Extract current metrics from a state file."""
    p = REPORTS / state_filename
    if not p.exists():
        return None
    try:
        s = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    sym = s.get("symbols", {}).get(symbol, {})
    runner = s.get("runner", {})

    closes = sym.get("realized_closes") or sym.get("close_count") or 0
    net = sym.get("realized_net_usd") or sym.get("net_realized_usd") or 0.0
    
    # Check for pre-start carry (session-only vs total)
    carry = sym.get("pre_start_state_carry_realized_usd") or 0.0
    session_net = net - carry  # Net since this session started
    session_closes = closes - (sym.get("pre_start_state_carry_closes") or 0)
    
    opens = len(sym.get("open_tickets", []))
    resets = sym.get("anchor_resets") or 0

    started = runner.get("started_at", "")
    heartbeat = runner.get("heartbeat_at", "")

    hours = 0
    if started:
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours = max((now - start_dt).total_seconds() / 3600.0, 0.001)
        except:
            pass

    pc = net / closes if closes > 0 else 0
    session_pc = session_net / session_closes if session_closes > 0 else pc
    closes_hr = closes / hours if hours > 0 else 0
    session_closes_hr = session_closes / hours if hours > 0 else 0
    usd_hr = net / hours if hours > 0 else 0
    session_usd_hr = session_net / hours if hours > 0 else 0

    return {
        "closes": closes,
        "session_closes": session_closes if session_closes > 0 else closes,
        "net": net,
        "session_net": session_net if session_closes > 0 else net,
        "opens": opens,
        "resets": resets,
        "hours": hours,
        "$/close": pc,
        "session_$/close": session_pc,
        "closes/hr": closes_hr,
        "session_closes/hr": session_closes_hr if session_closes_hr > 0 else closes_hr,
        "$/hr": usd_hr,
        "session_$/hr": session_usd_hr if session_usd_hr != usd_hr else usd_hr,
        "heartbeat": heartbeat,
        "started": started,
    }


def snapshot() -> dict:
    """Take a snapshot of all lanes."""
    result = {}
    for name, state_path, symbol in LANES:
        m = extract_metrics(state_path, symbol)
        if m:
            result[name] = m
    return result


def compare_snapshots(prev: dict, curr: dict) -> dict:
    """Compare two snapshots and return deltas."""
    deltas = {}
    for name in curr:
        if name in prev:
            p = prev[name]
            c = curr[name]
            delta = {
                "new_closes": c["closes"] - p["closes"],
                "new_net": c["net"] - p["net"],
                "new_opens": c["opens"] - p["opens"],
                "new_resets": c["resets"] - p["resets"],
                "$/close_delta": c["$/close"] - p["$/close"],
                "closes/hr_delta": c["closes/hr"] - p["closes/hr"],
                "$/hr_delta": c["$/hr"] - p["$/hr"],
            }
            deltas[name] = delta
    return deltas


def print_snapshot(curr: dict, deltas: dict | None = None):
    """Print formatted snapshot with deltas."""
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"\n{'='*80}")
    print(f"  REAL-TIME $/HOUR MONITOR — {now} UTC")
    print(f"{'='*80}")

    for name, m in curr.items():
        status = "[OK]" if m["heartbeat"] else "[!!]"
        # Show session metrics if available, otherwise total
        use_net = m.get("session_net", m["net"])
        use_closes = m.get("session_closes", m["closes"])
        use_pc = m.get("session_$/close", m["$/close"])
        use_closes_hr = m.get("session_closes/hr", m["closes/hr"])
        use_usd_hr = m.get("session_$/hr", m["$/hr"])
        
        print(f"\n{name} {status}")
        print(f"  Session: {use_closes:5d} closes  |  Net: ${use_net:>10.2f}  |  $/close: ${use_pc:>7.2f}")
        print(f"  $/hr: ${use_usd_hr:>8.2f}  |  Closes/hr: {use_closes_hr:>6.2f}  |  Open: {m['opens']}  |  Resets: {m['resets']}")

        if deltas and name in deltas:
            d = deltas[name]
            if d["new_closes"] > 0 or abs(d["new_net"]) > 0.01:
                print(f"  >> DELTA: +{d['new_closes']}c, ${d['new_net']:+.2f}, $/close {d['$/close_delta']:+.2f}, $/hr {d['$/hr_delta']:+.2f}")

    print(f"\n{'='*80}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll", type=int, default=5, help="Poll interval in seconds (default: 5)")
    parser.add_argument("--once", action="store_true", help="Single snapshot then exit")
    args = parser.parse_args()

    if args.once:
        curr = snapshot()
        print_snapshot(curr)
        return 0

    print(f"Polling every {args.poll}s. Ctrl+C to stop.")
    prev = None
    try:
        while True:
            curr = snapshot()
            deltas = compare_snapshots(prev, curr) if prev else None
            print_snapshot(curr, deltas)
            prev = curr
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
