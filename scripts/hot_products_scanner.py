#!/usr/bin/env python3
"""Hot Products Scanner — Real-time tight gate eligibility.

Reads the live opportunity board and reports:
1. Which products currently pass the tight gate (spread>=100, MER>=3.5)
2. Which products are borderline (close to passing)
3. Historical cycling frequency per product
4. Estimated cycles per hour based on cooldown
"""
import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

OPPORTUNITY_PATH = Path("reports/kraken_maker_opportunity_board.json")
EVENT_LOG = Path("reports/kraken_spot_maker_machinegun_shadow_events.jsonl")

TIGHT_SPREAD = 100
TIGHT_MER = 3.5

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
    # Load opportunity board
    with open(OPPORTUNITY_PATH) as f:
        opp_board = json.load(f)
    
    rows = opp_board.get("rows", [])
    board = {r["product_id"]: r for r in rows}
    
    print("=" * 80)
    print(f"HOT PRODUCTS SCANNER — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
    print("=" * 80)
    
    # Classify products
    tight_gate = []
    borderline = []
    far_from_gate = []
    
    for r in sorted(rows, key=lambda x: -(x.get("spread_bps", 0) * x.get("mer", 0))):
        prod = r["product_id"]
        spread = r.get("spread_bps", 0)
        mer = r.get("mer", 0)
        
        if spread >= TIGHT_SPREAD and mer >= TIGHT_MER:
            tight_gate.append(r)
        elif spread >= TIGHT_SPREAD * 0.7 or mer >= TIGHT_MER * 0.7:
            borderline.append(r)
        else:
            far_from_gate.append(r)
    
    print(f"\n{'='*80}")
    print(f"🟢 TIGHT GATE ELIGIBLE ({len(tight_gate)} products — spread>={TIGHT_SPREAD}, MER>={TIGHT_MER}):")
    print(f"{'='*80}")
    print(f"{'Product':<14} {'Spread':>8} {'MER':>8} {'ATR':>10} {'Score':>8}")
    print("-" * 80)
    for r in tight_gate:
        score = r.get("spread_bps", 0) * r.get("mer", 0)
        print(f"{r['product_id']:<14} {r.get('spread_bps', 0):>7.1f}bps {r.get('mer', 0):>8.2f} "
              f"{r.get('atr_12_bps', 0):>9.1f}bps {score:>8.0f}")
    
    print(f"\n{'='*80}")
    print(f"🟡 BORDERLINE ({len(borderline)} products — approaching gate):")
    print(f"{'='*80}")
    print(f"{'Product':<14} {'Spread':>8} {'MER':>8} {'Gap to Spread':>14} {'Gap to MER':>11}")
    print("-" * 80)
    for r in sorted(borderline, key=lambda x: -(x.get("spread_bps", 0) + x.get("mer", 0) * 20)):
        spread = r.get("spread_bps", 0)
        mer = r.get("mer", 0)
        spread_gap = TIGHT_SPREAD - spread
        mer_gap = TIGHT_MER - mer
        print(f"{r['product_id']:<14} {spread:>7.1f}bps {mer:>8.2f} "
              f"{spread_gap:>+13.1f}bps {mer_gap:>+10.2f}")
    
    # Historical cycling analysis
    events = load_events()
    opens = [e for e in events if e.get("action") == "open_maker_shadow"]
    opens_by_product = Counter(e.get("product_id", "?") for e in opens)
    
    print(f"\n{'='*80}")
    print(f"HISTORICAL CYCLING FREQUENCY (by opens in event log):")
    print(f"{'='*80}")
    print(f"{'Product':<14} {'Opens':>6} {'Status':>12}")
    print("-" * 80)
    for prod, count in opens_by_product.most_common(15):
        r = board.get(prod, {})
        spread = r.get("spread_bps", 0)
        mer = r.get("mer", 0)
        if spread >= TIGHT_SPREAD and mer >= TIGHT_MER:
            status = "🟢 ADMITTED"
        elif spread >= TIGHT_SPREAD * 0.7 or mer >= TIGHT_MER * 0.7:
            status = "🟡 BORDERLINE"
        else:
            status = "🔴 BLOCKED"
        print(f"{prod:<14} {count:>6} {status:>12}")
    
    # Estimated cycles per hour
    # Assuming 60-poll cooldown (~5 minutes), max cycles per product = 12/hour
    # With 20-poll cooldown (~1.5 minutes), max cycles = 40/hour
    print(f"\n{'='*80}")
    print(f"ESTIMATED CYCLE POTENTIAL:")
    print(f"{'='*80}")
    print(f"At 60-poll cooldown (current baseline): ~12 cycles/hr per product")
    print(f"At 20-poll cooldown (A/B lane): ~40 cycles/hr per product")
    print(f"At 30-poll cooldown: ~24 cycles/hr per product")
    print(f"\nIf {len(tight_gate)} products are admitted:")
    for cooldown, max_cycles in [(60, 12), (30, 24), (20, 40)]:
        est_closes = len(tight_gate) * max_cycles
        print(f"  {cooldown}-poll cooldown: ~{est_closes} closes/hr ({len(tight_gate)} products × {max_cycles} cycles)")

if __name__ == "__main__":
    main()
