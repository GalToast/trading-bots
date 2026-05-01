#!/usr/bin/env python3
"""Volume Analysis — What's the bottleneck in the money machine?

Analyzes:
1. How many products per hour get admitted by the tight gate?
2. What's the bottleneck: product availability, entry cooldowns, or position limits?
3. If we relaxed the gate, how many MORE products would cycle?
"""
import json
from pathlib import Path

EVENT_LOG = Path("reports/kraken_spot_maker_machinegun_shadow_events.jsonl")

def load_events():
    events = []
    with open(EVENT_LOG) as f:
        for line in f:
            try:
                events.append(json.loads(line.strip()))
            except:
                pass
    return events

def main():
    events = load_events()
    opens = [e for e in events if "open" in e.get("action", "")]
    closes = [e for e in events if "close" in e.get("action", "")]
    misses = [e for e in events if "miss" in e.get("action", "")]
    vetos = [e for e in events if "veto" in e.get("action", "").lower() or "block" in e.get("action", "").lower()]
    
    if not opens:
        print("No opens found!")
        return
    
    # Time range
    from datetime import datetime
    timestamps = []
    for e in events:
        ts = e.get("timestamp", "")
        if ts:
            try:
                timestamps.append(datetime.fromisoformat(ts))
            except:
                pass
    
    if timestamps:
        duration = (max(timestamps) - min(timestamps)).total_seconds() / 3600
    else:
        duration = 0
    
    print("=" * 80)
    print(f"VOLUME ANALYSIS — {len(opens)} opens, {len(closes)} closes over {duration:.1f} hours")
    print("=" * 80)
    
    # Products by frequency
    from collections import Counter
    open_products = Counter(e.get("product_id", "?") for e in opens)
    close_products = Counter(e.get("product_id", "?") for e in closes)
    
    print(f"\nProducts opened (by frequency):")
    for prod, count in open_products.most_common(10):
        closes_for_prod = close_products.get(prod, 0)
        print(f"  {prod:14s} {count} opens, {closes_for_prod} closes")
    
    # Throughput
    opens_per_hour = len(opens) / duration if duration > 0 else 0
    closes_per_hour = len(closes) / duration if duration > 0 else 0
    print(f"\nThroughput:")
    print(f"  Opens: {opens_per_hour:.2f}/hour")
    print(f"  Closes: {closes_per_hour:.2f}/hour")
    print(f"  Avg hold time: {duration / len(closes) * 60:.0f} minutes" if closes else "  No closes")
    
    # Miss rate
    print(f"\nEntry misses: {len(misses)}")
    for e in misses[:5]:
        print(f"  {e.get('product_id', '?')}: {e.get('reason', '?')}")
    
    # Block/veto rate
    print(f"\nBlocks/vetoes: {len(vetos)}")
    veto_reasons = Counter(e.get("reason", "?") for e in vetos)
    for reason, count in veto_reasons.most_common():
        print(f"  {reason}: {count}")
    
    # Wide spread product availability
    opp_path = Path("reports/kraken_maker_opportunity_board.json")
    if opp_path.exists():
        with open(opp_path) as f:
            board = json.load(f)
        
        rows = board.get("rows", [])
        tight_gate = [r for r in rows if r.get("spread_bps", 0) >= 100 and r.get("mer", 0) >= 3.5]
        loose_gate = [r for r in rows if r.get("spread_bps", 0) >= 50 and r.get("mer", 0) >= 2.0]
        
        print(f"\nCurrent board availability:")
        print(f"  Tight gate (100/3.5): {len(tight_gate)} products admitted")
        print(f"  Loose gate (50/2.0): {len(loose_gate)} products admitted")
        print(f"  Total products on board: {len(rows)}")
        
        print(f"\nTight gate products:")
        for r in sorted(tight_gate, key=lambda x: x.get("spread_bps", 0), reverse=True):
            print(f"  {r['product_id']:14s} spread={r.get('spread_bps', 0):>7.1f}bps  MER={r.get('mer', 0):>5.2f}")
        
        print(f"\nLoose gate ADDITIONAL products (not in tight):")
        tight_ids = {r["product_id"] for r in tight_gate}
        for r in sorted(loose_gate, key=lambda x: x.get("spread_bps", 0), reverse=True):
            if r["product_id"] not in tight_ids:
                print(f"  {r['product_id']:14s} spread={r.get('spread_bps', 0):>7.1f}bps  MER={r.get('mer', 0):>5.2f}")
    
    # Bottleneck analysis
    print(f"\n{'='*80}")
    print(f"BOTTLENECK ANALYSIS:")
    print(f"{'='*80}")
    print(f"Current: {closes_per_hour:.2f} closes/hour at $1.25 net")
    print(f"If we 2x closes (admit more products): ${1.25 * 2:.2f}/hour")
    print(f"If we 3x closes: ${1.25 * 3:.2f}/hour")
    print(f"If we 5x closes: ${1.25 * 5:.2f}/hour")
    print(f"\nPer day projections (assuming 12 active hours):")
    print(f"  Current: ${1.25 * closes_per_hour * 12:.2f}/day")
    print(f"  2x: ${1.25 * 2 * closes_per_hour * 12:.2f}/day")
    print(f"  5x: ${1.25 * 5 * closes_per_hour * 12:.2f}/day")

if __name__ == "__main__":
    main()
