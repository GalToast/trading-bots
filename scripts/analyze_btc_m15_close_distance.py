#!/usr/bin/env python3
"""Analyze close distances in BTC M15 live events."""
import json
from pathlib import Path

events_path = Path("reports/penetration_lattice_live_btcusd_m15_warp_events.jsonl")
lines = [l.strip() for l in events_path.read_text().strip().split("\n") if l.strip()]
events = []
for l in lines:
    try:
        events.append(json.loads(l))
    except json.JSONDecodeError:
        continue

closes = [e for e in events if e.get("action") == "close_ticket" or e.get("event") == "close_ticket"]
opens = [e for e in events if e.get("action") == "open_ticket" or e.get("event") == "open_ticket"]

print(f"Total events: {len(events)}")
print(f"Open events: {len(opens)}")
print(f"Close events: {len(closes)}")

if closes:
    print("\n=== Sample Close Events ===")
    distances = []
    for c in closes[:10]:
        entry = c.get("entry_fill_price", c.get("fill_price"))
        exit_px = c.get("exit_fill_price")
        pnl = c.get("realized_pnl", c.get("pnl"))
        direction = c.get("direction", "?")
        step = 75.0
        if entry and exit_px:
            dist = abs(float(exit_px) - float(entry))
            dist_steps = dist / step
            distances.append((dist_steps, pnl))
            print(f"  {direction}: entry={entry}, exit={exit_px}, dist=${dist:.2f} ({dist_steps:.1f} steps), pnl=${pnl}")
        else:
            print(f"  {direction}: entry={entry}, exit={exit_px}, pnl={pnl}")
    
    if distances:
        print(f"\n=== Distance Distribution (n={len(distances)}) ===")
        steps_list = [d[0] for d in distances]
        pnls = [d[1] for d in distances if d[1] is not None]
        print(f"  Avg distance: {sum(steps_list)/len(steps_list):.1f} steps")
        print(f"  Min distance: {min(steps_list):.1f} steps")
        print(f"  Max distance: {max(steps_list):.1f} steps")
        if pnls:
            print(f"  Avg PnL: ${sum(pnls)/len(pnls):.2f}")
