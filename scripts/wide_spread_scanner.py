#!/usr/bin/env python3
"""Wide Spread Scanner — What's tradeable RIGHT NOW?

Scans the current opportunity board for products with spreads wide
enough to survive maker fees (25bps × 2 = 50bps round-trip).

This tells us what the money machine COULD be trading if the gates
are set correctly.
"""
import json
from pathlib import Path

OPPORTUNITY_PATH = Path("reports/kraken_maker_opportunity_board.json")

def main():
    with open(OPPORTUNITY_PATH) as f:
        opp_board = json.load(f)
    
    rows = opp_board.get("rows", [])
    
    print("=" * 80)
    print("WIDE SPREAD SCANNER — What's Tradeable RIGHT NOW")
    print("=" * 80)
    
    # Fee math: 25bps maker entry + 25bps maker exit = 50bps round-trip
    # Need spread > 50bps to have ANY profit room
    # Need spread > 100bps to have MEANINGFUL profit
    
    FEE_RT = 50.0  # 25bps × 2 round-trip
    
    profitable = []
    breakeven = []
    unprofitable = []
    
    for r in rows:
        spread = r.get("spread_bps", 0)
        mer = r.get("mer", 0)
        atr = r.get("atr_12_bps", 0)
        prod = r["product_id"]
        
        net_room = spread - FEE_RT
        
        if net_room > 50:  # Very profitable
            profitable.append((prod, spread, mer, atr, net_room))
        elif net_room > 0:  # Barely profitable
            breakeven.append((prod, spread, mer, atr, net_room))
        else:  # Unprofitable
            unprofitable.append((prod, spread, mer, atr, net_room))
    
    print(f"\nTotal products scanned: {len(rows)}")
    print(f"Fee round-trip: {FEE_RT}bps (25bps × 2)")
    
    print(f"\n{'='*80}")
    print(f"VERY PROFITABLE (spread > 100bps, net room > 50bps): {len(profitable)}")
    print(f"{'='*80}")
    print(f"{'Product':<14} {'Spread':>8} {'Net Room':>10} {'MER':>6} {'ATR':>8}")
    print("-" * 80)
    for prod, spread, mer, atr, room in sorted(profitable, key=lambda x: x[1], reverse=True):
        print(f"{prod:<14} {spread:>7.1f}bps {room:>9.1f}bps {mer:>6.2f} {atr:>7.1f}bps")
    
    print(f"\n{'='*80}")
    print(f"BARELY PROFITABLE (spread 50-100bps, net room 0-50bps): {len(breakeven)}")
    print(f"{'='*80}")
    for prod, spread, mer, atr, room in sorted(breakeven, key=lambda x: x[1], reverse=True)[:15]:
        print(f"{prod:<14} {spread:>7.1f}bps {room:>9.1f}bps {mer:>6.2f} {atr:>7.1f}bps")
    if len(breakeven) > 15:
        print(f"... and {len(breakeven) - 15} more")
    
    print(f"\n{'='*80}")
    print(f"UNPROFITABLE (spread < 50bps, fees eat everything): {len(unprofitable)}")
    print(f"{'='*80}")
    print(f"These products have spreads too tight to survive maker fees.")
    print(f"Tightest: {min(unprofitable, key=lambda x: x[1])[0]} at {min(unprofitable, key=lambda x: x[1])[1]:.1f}bps")
    print(f"Widest of the losers: {max(unprofitable, key=lambda x: x[1])[0]} at {max(unprofitable, key=lambda x: x[1])[1]:.1f}bps")

if __name__ == "__main__":
    main()
