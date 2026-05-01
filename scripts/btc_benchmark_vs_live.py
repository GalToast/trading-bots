#!/usr/bin/env python3
"""
BTC Tick-Native vs Live — Why is live still bleeding when benchmark was +$148?

Compare:
1. Tick-native benchmark window (1-day replay, what price range?)
2. Live trading window (what price range since launch?)
3. Key difference: replay uses historical ticks, live uses real-time broker fills
"""
import json
from pathlib import Path
from datetime import datetime, timezone

REPORTS = Path(__file__).parent.parent / "reports"

# Read the tick-native benchmark results
tick_csv = REPORTS / "tick_native_live_configs.csv"
tick_data = []
for line in tick_csv.read_text().splitlines()[1:]:  # skip header
    parts = line.split(",")
    if parts[1] == "BTCUSD":
        tick_data.append({
            "lane": parts[0],
            "symbol": parts[1],
            "engine": parts[2],
            "tf": parts[3],
            "days": int(parts[4]),
            "ticks": int(parts[5]),
            "realized": float(parts[6]),
            "closes": int(parts[7]),
            "rearm_opens": int(parts[8]),
            "open_count": int(parts[9]),
            "max_open": int(parts[10]),
            "next_buy": float(parts[11]),
            "next_sell": float(parts[12]),
        })

print("=== Tick-Native Benchmark (1-day replay) ===")
for d in tick_data:
    avg_pnl = d["realized"] / d["closes"] if d["closes"] > 0 else 0
    print(f"  Lane: {d['lane']}")
    print(f"  Timeframe: {d['tf']}, Days: {d['days']}, Ticks: {d['ticks']}")
    print(f"  Realized: ${d['realized']:+.2f} on {d['closes']} closes (avg ${avg_pnl:+.2f}/close)")
    print(f"  Rearm opens: {d['rearm_opens']}, Open: {d['open_count']}, Max open: {d['max_open']}")
    print(f"  Next levels: BUY={d['next_buy']:.2f}, SELL={d['next_sell']:.2f}")
    print()

# Read the live exec events to understand live trading pattern
exec_events = REPORTS / "penetration_lattice_live_btcusd_exc2_tight_exec_events.jsonl"
events = [json.loads(l) for l in exec_events.read_text().splitlines() if l.strip()]

opens = [e for e in events if "open" in e.get("action", "") and "close" not in e.get("action", "")]
closes = [e for e in events if "close" in e.get("action", "")]
reconciles = [e for e in events if "reconcile" in e.get("action", "")]

print("=== Live Exec Events ===")
print(f"  Open events: {len(opens)}")
print(f"  Close events: {len(closes)}")
print(f"  Reconcile events: {len(reconciles)}")
print()

# Analyze close types
close_types = {}
for c in closes:
    action = c.get("action", "")
    close_types[action] = close_types.get(action, 0) + 1

print("  Close event types:")
for k, v in sorted(close_types.items()):
    print(f"    {k}: {v}")

# Look at timestamps
if events:
    first_ts = events[0].get("ts_utc", events[0].get("event", {}).get("ts_utc", "?"))
    last_ts = events[-1].get("ts_utc", events[-1].get("event", {}).get("ts_utc", "?"))
    print(f"\n  First event: {first_ts}")
    print(f"  Last event:  {last_ts}")
