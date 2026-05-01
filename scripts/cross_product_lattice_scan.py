#!/usr/bin/env python3
"""Mad Scientist Cross-Product Lattice Scan.

Inspired by @mad-scientist's lattice idea from the chat.
The ONLY proven MT5 edge is the stopless penetration lattice.
For spot crypto, we can't short — but we can do cross-product relative strength:
  Long strong product + long weak product = spread trade

This is the stopless lattice concept applied to spot markets.
"""
import json
import pandas as pd
from pathlib import Path
from itertools import combinations

OPPORTUNITY_PATH = Path("reports/kraken_maker_opportunity_board.json")

def main():
    with open(OPPORTUNITY_PATH) as f:
        opp_board = json.load(f)
    
    rows = opp_board.get("rows", [])
    df = pd.DataFrame(rows)
    
    print("=" * 80)
    print("CROSS-PRODUCT LATTICE SCAN — Relative Strength Opportunities")
    print("=" * 80)
    
    # Filter to products with decent spread (for the maker angle)
    df = df[df["spread_bps"] >= 10]  # At least some spread to work with
    
    print(f"\nProducts with spread >= 10bps: {len(df)}")
    print(f"\nTop 10 by MER (most inefficient = most spread opportunity):")
    top_mer = df.nlargest(10, "mer")
    for _, r in top_mer.iterrows():
        print(f"  {r['product_id']:14s} MER={r['mer']:>6.2f}  spread={r['spread_bps']:>8.1f}bps  "
              f"ATR={r['atr_12_bps']:>8.1f}bps  vol_24h=${r['vol_24h_usd']:>10,.0f}")
    
    # Find pairs with HIGH spread differential
    # The lattice works when one product is trending and another is flat
    # This creates relative value opportunity
    
    print(f"\n{'='*80}")
    print(f"TOP 10 CROSS-PRODUCT PAIRS by spread differential:")
    print(f"{'='*80}")
    
    # Compute a "lattice score" for each pair:
    # = abs(MER_A - MER_B) * (spread_A + spread_B) / 2
    # High score = one product is inefficient (good for maker), 
    #              the other is efficient (hedge)
    #              AND both have wide spreads (room for profit)
    
    pairs = []
    for (i, r1), (j, r2) in combinations(df.iterrows(), 2):
        mer_diff = abs(r1["mer"] - r2["mer"])
        avg_spread = (r1["spread_bps"] + r2["spread_bps"]) / 2
        atr_diff = abs(r1["atr_12_bps"] - r2["atr_12_bps"])
        
        # Lattice score: high MER diff + high avg spread + high ATR diff = good pair
        lattice_score = mer_diff * avg_spread / 100 + atr_diff / 10
        
        pairs.append({
            "product_a": r1["product_id"],
            "product_b": r2["product_id"],
            "mer_a": r1["mer"],
            "mer_b": r2["mer"],
            "spread_a": r1["spread_bps"],
            "spread_b": r2["spread_bps"],
            "atr_a": r1["atr_12_bps"],
            "atr_b": r2["atr_12_bps"],
            "lattice_score": lattice_score,
        })
    
    pairs_df = pd.DataFrame(pairs).nlargest(10, "lattice_score")
    
    for _, p in pairs_df.iterrows():
        print(f"\n  {p['product_a']:14s} vs {p['product_b']:14s}  lattice_score={p['lattice_score']:.2f}")
        print(f"    MER: {p['mer_a']:.2f} vs {p['mer_b']:.2f} (diff={abs(p['mer_a']-p['mer_b']):.2f})")
        print(f"    Spread: {p['spread_a']:.1f}bps vs {p['spread_b']:.1f}bps (avg={p['spread_a']+p['spread_b']:.1f}bps)")
        print(f"    ATR: {p['atr_a']:.1f}bps vs {p['atr_b']:.1f}bps (diff={abs(p['atr_a']-p['atr_b']):.1f}bps)")
    
    print(f"\n{'='*80}")
    print(f"INTERPRETATION:")
    print(f"{'='*80}")
    print(f"The lattice works by exploiting RELATIVE inefficiency between products.")
    print(f"Long the high-MER product (inefficient, more alpha) + ")
    print(f"Long the low-MER product (efficient, hedge) = ")
    print(f"Net profit when the spread between them widens.")
    print(f"\nThis is the stopless lattice concept adapted for spot markets.")
    print(f"No shorting needed — just relative strength/weakness.")

if __name__ == "__main__":
    main()
