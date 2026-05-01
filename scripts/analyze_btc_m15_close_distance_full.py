#!/usr/bin/env python3
"""Analyze close distances across full BTC M15 live history, bucketed by time."""
import json
from pathlib import Path

def analyze_events(path, label, step):
    events_path = Path(path)
    if not events_path.exists():
        print(f"{label}: FILE NOT FOUND")
        return
    lines = [l.strip() for l in events_path.read_text().strip().split("\n") if l.strip()]
    events = []
    for l in lines:
        try:
            events.append(json.loads(l))
        except json.JSONDecodeError:
            continue
    
    closes = [e for e in events if e.get("action") == "close_ticket" or e.get("event") == "close_ticket"]
    if not closes:
        print(f"{label}: 0 closes found")
        return
    
    print(f"\n=== {label} ({len(closes)} closes, step=${step}) ===")
    
    # Parse timestamps and bucket
    distances = []
    by_hour = {}
    for c in closes:
        entry = c.get("entry_fill_price", c.get("fill_price"))
        exit_px = c.get("exit_fill_price")
        pnl = c.get("realized_pnl", c.get("pnl"))
        ts = c.get("ts_utc", c.get("time_msc", ""))
        direction = c.get("direction", "?")
        
        if entry and exit_px:
            dist = abs(float(exit_px) - float(entry))
            dist_steps = dist / step
            distances.append((dist_steps, pnl if pnl else 0))
            
            # Bucket by hour
            hour_key = str(ts)[:13] if isinstance(ts, str) else "?"
            if hour_key not in by_hour:
                by_hour[hour_key] = []
            by_hour[hour_key].append((dist_steps, pnl if pnl else 0))
    
    if not distances:
        print("  No distances computed")
        return
    
    steps_list = [d[0] for d in distances]
    pnls = [d[1] for d in distances]
    
    print(f"  Avg distance: {sum(steps_list)/len(steps_list):.1f} steps")
    print(f"  Median distance: {sorted(steps_list)[len(steps_list)//2]:.1f} steps")
    print(f"  Min: {min(steps_list):.1f}, Max: {max(steps_list):.1f}")
    print(f"  Avg PnL: ${sum(pnls)/len(pnls):.2f}")
    print(f"  Total net: ${sum(pnls):.2f}")
    print(f"  $/close: ${sum(pnls)/len(pnls):.2f}")
    
    # Distribution buckets
    buckets = {"<1": 0, "1-2": 0, "2-3": 0, "3-5": 0, "5-10": 0, ">10": 0}
    for s in steps_list:
        if s < 1: buckets["<1"] += 1
        elif s < 2: buckets["1-2"] += 1
        elif s < 3: buckets["2-3"] += 1
        elif s < 5: buckets["3-5"] += 1
        elif s < 10: buckets["5-10"] += 1
        else: buckets[">10"] += 1
    
    print(f"\n  Distance distribution:")
    for b in sorted(buckets.keys()):
        print(f"    {b} steps: {buckets[b]}")
    
    # Show last few closes (most recent)
    print(f"\n  Last 5 closes (most recent):")
    for c in closes[-5:]:
        entry = c.get("entry_fill_price", c.get("fill_price"))
        exit_px = c.get("exit_fill_price")
        pnl = c.get("realized_pnl", c.get("pnl"))
        if entry and exit_px:
            dist = abs(float(exit_px) - float(entry))
            print(f"    entry={entry}, exit={exit_px}, dist=${dist:.1f} ({dist/step:.1f}x), pnl=${pnl}")

# Analyze live BTC M15
analyze_events(
    "reports/penetration_lattice_live_btcusd_m15_warp_events.jsonl",
    "LIVE $75",
    step=75.0
)

# Analyze $15 shadow
analyze_events(
    "reports/penetration_lattice_shadow_btcusd_m15_warp_events.jsonl",
    "SHADOW $15",
    step=15.0
)
