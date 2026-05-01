#!/usr/bin/env python3
"""Reentry Cooldown Analysis — How much money are we leaving on the table?

Analyzes block_maker_reentry events to quantify the cooldown bottleneck.
If we shortened the cooldown, how many more closes would we get?
"""
import json
from pathlib import Path
from collections import Counter, defaultdict

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
    
    # Find block_maker_reentry events
    blocks = [e for e in events if "block" in e.get("action", "").lower() and "reentry" in e.get("reason", "").lower()]
    closes = [e for e in events if "close" in e.get("action", "").lower()]
    opens = [e for e in events if "open" in e.get("action", "").lower()]
    
    print("=" * 80)
    print(f"REENTRY COOLDOWN ANALYSIS — {len(blocks)} blocks, {len(opens)} opens, {len(closes)} closes")
    print("=" * 80)
    
    # Count blocks by product
    block_by_product = Counter(e.get("product_id", "?") for e in blocks)
    print(f"\nBlocks by product:")
    for prod, count in block_by_product.most_common(10):
        print(f"  {prod:14s}: {count} blocks")
    
    # For each product: opens, closes, blocks
    product_stats = defaultdict(lambda: {"opens": 0, "closes": 0, "blocks": 0})
    for e in opens:
        product_stats[e.get("product_id", "?")]["opens"] += 1
    for e in closes:
        product_stats[e.get("product_id", "?")]["closes"] += 1
    for e in blocks:
        product_stats[e.get("product_id", "?")]["blocks"] += 1
    
    print(f"\n{'='*80}")
    print(f"{'Product':<14} {'Opens':>6} {'Closes':>7} {'Blocks':>7} {'Potential':>9}")
    print("-" * 80)
    
    total_blocks = 0
    for prod, stats in sorted(product_stats.items(), key=lambda x: -(x[1]["opens"] + x[1]["blocks"])):
        # If there were no blocks, potential = current closes
        # If there were blocks, potential = closes + blocks (assuming some would have succeeded)
        potential = stats["closes"] + stats["blocks"]
        total_blocks += stats["blocks"]
        print(f"{prod:<14} {stats['opens']:>6} {stats['closes']:>7} {stats['blocks']:>7} {potential:>9}")
    
    # Estimate additional closes if cooldown was shorter
    # Conservative: 50% of blocks would have resulted in successful trades
    # Aggressive: 100% of blocks would have resulted in successful trades
    avg_close_net = sum(e.get("net_pct", 0) for e in closes) / len(closes) if closes else 0
    
    print(f"\n{'='*80}")
    print(f"COOLDOWN IMPACT ESTIMATE:")
    print(f"{'='*80}")
    print(f"Total blocks: {total_blocks}")
    print(f"Avg net per close: {avg_close_net:+.4f}%")
    print(f"\nIf 50% of blocks resulted in closes: +{total_blocks * 0.5:.0f} trades")
    print(f"  Additional net: +{total_blocks * 0.5 * avg_close_net:+.4f}%")
    print(f"\nIf 100% of blocks resulted in closes: +{total_blocks} trades")
    print(f"  Additional net: +{total_blocks * avg_close_net:+.4f}%")
    
    # HOUSE specific analysis
    house_opens = sum(1 for e in opens if e.get("product_id") == "HOUSE-USD")
    house_closes = sum(1 for e in closes if e.get("product_id") == "HOUSE-USD")
    house_blocks = sum(1 for e in blocks if e.get("product_id") == "HOUSE-USD")
    
    print(f"\n{'='*80}")
    print(f"HOUSE-USD SPECIFIC:")
    print(f"{'='*80}")
    print(f"  Opens: {house_opens}")
    print(f"  Closes: {house_closes}")
    print(f"  Blocks: {house_blocks}")
    print(f"  If blocks were allowed: {house_closes + house_blocks} potential closes")
    house_total = sum(e.get("net_pct", 0) for e in closes if e.get("product_id") == "HOUSE-USD")
    house_avg = house_total / house_closes if house_closes else 0
    print(f"  Current total: {house_total:+.4f}%")
    print(f"  Potential total (if all blocks succeeded): {house_total + house_blocks * house_avg:+.4f}%")

if __name__ == "__main__":
    main()
