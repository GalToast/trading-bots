#!/usr/bin/env python3
"""BMB-USD Spread-Only Gate Analysis.

Tests the hypothesis: wide spread ALONE (regardless of MER) is a valid
admission criterion for maker trading.

BMB-USD currently has 520bps spread but MER=0.51 (blocked by tight gate).
If spread-only gate works, it opens a whole new class of products.
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
    closes = [e for e in events if "close" in e.get("action", "")]
    
    if not closes:
        print("No closes found!")
        return
    
    print("=" * 80)
    print("BMB-USD SPREAD-ONLY GATE ANALYSIS")
    print("=" * 80)
    
    # Check if BMB has any historical closes
    bmb_closes = [e for e in closes if e.get("product_id") == "BMB-USD"]
    print(f"\nBMB-USD historical closes: {len(bmb_closes)}")
    for e in bmb_closes:
        print(f"  net={e.get('net_pct', 0):+.4f}%  reason={e.get('reason', '?')}")
    
    if bmb_closes:
        total = sum(e.get("net_pct", 0) for e in bmb_closes)
        wins = sum(1 for e in bmb_closes if e.get("net_pct", 0) > 0)
        print(f"  Total: {total:+.4f}%, WR: {wins}/{len(bmb_closes)} ({wins/len(bmb_closes)*100:.0f}%)")
    
    # What if we admitted ALL products with spread >= 300bps?
    from pathlib import Path
    opp_path = Path("reports/kraken_maker_opportunity_board.json")
    if opp_path.exists():
        with open(opp_path) as f:
            board = {r["product_id"]: r for r in json.load(f).get("rows", [])}
    
    # Classify all closes by spread-at-time (approximated from current board)
    print(f"\n{'='*80}")
    print(f"ALL CLOSES BY SPREAD CATEGORY:")
    print(f"{'='*80}")
    
    categories = {
        "spread>=300 (MER-agnostic)": {"closes": [], "threshold": 300, "mer_min": 0},
        "spread>=100 + MER>=3.5 (tight gate)": {"closes": [], "threshold": 100, "mer_min": 3.5},
        "spread>=300 + MER<2.0 (spread-only, low MER)": {"closes": [], "threshold": 300, "mer_max": 2.0},
    }
    
    for e in closes:
        prod = e.get("product_id", "")
        net = e.get("net_pct", 0)
        r = board.get(prod, {})
        spread = r.get("spread_bps", 50)
        mer = r.get("mer", 3.0)
        
        if spread >= 300:
            categories["spread>=300 (MER-agnostic)"]["closes"].append(e)
            if mer < 2.0:
                categories["spread>=300 + MER<2.0 (spread-only, low MER)"]["closes"].append(e)
        
        if spread >= 100 and mer >= 3.5:
            categories["spread>=100 + MER>=3.5 (tight gate)"]["closes"].append(e)
    
    for name, data in categories.items():
        c = data["closes"]
        if c:
            total = sum(e.get("net_pct", 0) for e in c)
            wins = sum(1 for e in c if e.get("net_pct", 0) > 0)
            products = set(e.get("product_id", "?") for e in c)
            print(f"\n{name}:")
            print(f"  {len(c)} closes, {wins} wins ({wins/len(c)*100:.0f}%), total={total:+.4f}%")
            print(f"  Products: {', '.join(sorted(products))}")
            for e in c:
                prod = e.get("product_id", "?")
                net = e.get("net_pct", 0)
                spread = board.get(prod, {}).get("spread_bps", "?")
                mer = board.get(prod, {}).get("mer", "?")
                print(f"    {prod}: net={net:+.4f}%, spread={spread}bps, MER={mer}")
    
    print(f"\n{'='*80}")
    print(f"CONCLUSION:")
    print(f"{'='*80}")
    
    # Compare tight gate vs spread-only
    tight = categories["spread>=100 + MER>=3.5 (tight gate)"]["closes"]
    spread_only = categories["spread>=300 + MER<2.0 (spread-only, low MER)"]["closes"]
    
    if tight and spread_only:
        tight_total = sum(e.get("net_pct", 0) for e in tight)
        spread_only_total = sum(e.get("net_pct", 0) for e in spread_only)
        print(f"Tight gate: {len(tight)} closes, {tight_total:+.4f}%")
        print(f"Spread-only: {len(spread_only)} closes, {spread_only_total:+.4f}%")
        print(f"\nSpread-only adds {len(spread_only)} trades that tight gate misses.")
        print(f"If spread-only is profitable → new admission category")
        print(f"If spread-only loses → low-MER wide spreads are traps")
    elif tight and not spread_only:
        print(f"No historical closes for low-MER wide-spread products.")
        print(f"This is untested territory — shadow trial needed.")
        print(f"\nCurrent wide-spread low-MER products on board:")
        for prod, r in sorted(board.items(), key=lambda x: -x[1].get("spread_bps", 0)):
            if r.get("spread_bps", 0) >= 300 and r.get("mer", 3.0) < 2.0:
                print(f"  {prod}: spread={r['spread_bps']}bps, MER={r['mer']}")

if __name__ == "__main__":
    main()
