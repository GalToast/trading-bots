#!/usr/bin/env python3
"""Audit spread distribution in BTC M15 live event log."""
import json
from pathlib import Path
from collections import defaultdict

events_path = Path("reports/penetration_lattice_live_btcusd_m15_warp_events.jsonl")
if not events_path.exists():
    print("ERROR: events file not found")
    exit(1)

lines = events_path.read_text().strip().split("\n")
lines = [l.strip() for l in lines if l.strip()]
events = []
for line in lines:
    try:
        events.append(json.loads(line))
    except json.JSONDecodeError:
        continue

if not events:
    print("ERROR: no valid events found")
    # Show first few lines for debugging
    for i, l in enumerate(lines[:5]):
        print(f"  Line {i}: {l[:120]}")
    exit(1)

spreads_at_open = []
spread_px_values = []
step = 75.0

prev_count = 0
for e in events:
    evt_type = e.get("event") or e.get("action") or ""
    if "open_ticket" in evt_type or "open_guarded" in evt_type:
        bid, ask = e.get("bid"), e.get("ask")
        if bid and ask:
            spreads_at_open.append(float(ask) - float(bid))
    bid, ask = e.get("bid"), e.get("ask")
    if bid and ask:
        spread_px = float(ask) - float(bid)
        spread_px_values.append(spread_px)

print(f"=== Spread Distribution Audit: BTC M15 Live ===")
print(f"Total events: {len(events)}")
print(f"Open tickets with spread data: {len(spreads_at_open)}")
print(f"Tick bid/ask events: {len(spread_px_values)}")

# Debug: show first few open-like events
if spreads_at_open == []:
    print("\nDEBUG: Looking for open-like events...")
    open_events = [e for e in events if "open" in str(e.get("event", "")) + str(e.get("action", ""))]
    if open_events:
        print(f"Found {len(open_events)} open-like events")
        print(f"Sample event keys: {list(open_events[0].keys())[:20]}")
        print(f"Sample event: {json.dumps(open_events[0], indent=2)[:500]}")
print()

if spreads_at_open:
    spreads_at_open.sort()
    n = len(spreads_at_open)
    print(f"=== Spreads at Open (n={n}) ===")
    print(f"Min: ${spreads_at_open[0]:.2f} ({spreads_at_open[0]/step:.2f}x step)")
    print(f"P25: ${spreads_at_open[n//4]:.2f} ({spreads_at_open[n//4]/step:.2f}x step)")
    print(f"Median: ${spreads_at_open[n//2]:.2f} ({spreads_at_open[n//2]/step:.2f}x step)")
    print(f"P75: ${spreads_at_open[3*n//4]:.2f} ({spreads_at_open[3*n//4]/step:.2f}x step)")
    print(f"Max: ${spreads_at_open[-1]:.2f} ({spreads_at_open[-1]/step:.2f}x step)")
    print(f"Mean: ${sum(spreads_at_open)/len(spreads_at_open):.2f}")
    print()

    buckets = defaultdict(int)
    for s in spreads_at_open:
        ratio = s / step
        if ratio <= 0.3:
            buckets["0.0-0.3x"] += 1
        elif ratio <= 0.5:
            buckets["0.3-0.5x"] += 1
        elif ratio <= 1.0:
            buckets["0.5-1.0x"] += 1
        elif ratio <= 2.0:
            buckets["1.0-2.0x"] += 1
        else:
            buckets[">2.0x"] += 1

    print("=== Distribution Buckets ===")
    for bucket in sorted(buckets.keys()):
        count = buckets[bucket]
        pct = count / n * 100
        bar = "#" * max(1, count // max(1, n // 50))
        print(f"{bucket}: {count:4d} ({pct:5.1f}%) {bar}")

    print()
    print(f"=== What would various thresholds block? ===")
    for threshold in [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 2.5, 3.0]:
        blocked = sum(1 for s in spreads_at_open if s / step > threshold)
        pct = blocked / n * 100
        print(f"  ratio>{threshold:.1f} (${threshold*step:.0f}): blocks {blocked}/{n} = {pct:.1f}% of opens")

if spread_px_values:
    spread_px_values.sort()
    n2 = len(spread_px_values)
    print(f"\n=== Tick Spreads (n={n2}) ===")
    print(f"Min: ${spread_px_values[0]:.2f}")
    print(f"Median: ${spread_px_values[n2//2]:.2f}")
    print(f"Max: ${spread_px_values[-1]:.2f}")
    print(f"Mean: ${sum(spread_px_values)/len(spread_px_values):.2f}")
