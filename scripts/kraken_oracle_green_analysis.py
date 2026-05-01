#!/usr/bin/env python3
"""Analyze what separates oracle-green entries from losers in Kraken forward tape.

The autopsy shows 2/14 oracle-green entries (MOVR and ESP). Let's find what they share
that the 12 losers don't, so we can mutate the entry filter toward their characteristics.
"""
import json
from pathlib import Path

AUTOPSY_PATH = Path("reports/kraken_spot_forward_tape_autopsy.md")
TAPE_PATH = Path("reports/kraken_spot_forward_tape.jsonl")

def main():
    print("=" * 80)
    print("KRAKEN FORWARD TAPE — Oracle Green vs Loser Analysis")
    print("=" * 80)

    # Read the autopsy markdown to extract entry details
    # The "Recent Rows" table has all entries
    # Green entries: MOVR-USD (180s, +$0.6454), ESP-USD (600s, +$0.9456)

    green = [
        {"product": "MOVR-USD", "spread": 68.19, "edge": 125.10, "best_net": 0.6454, "best_horizon": 180},
        {"product": "ESP-USD", "spread": 18.40, "edge": 73.59, "best_net": 0.9456, "best_horizon": 600},
    ]

    losers = [
        {"product": "VFY-USD", "spread": 11.43, "edge": 287.65, "best_net": -0.7293},
        {"product": "SKR-USD", "spread": 27.46, "edge": 195.43, "best_net": -1.4256},
        {"product": "BADGER-USD", "spread": 55.54, "edge": 74.06, "best_net": -1.8402},
        {"product": "UNITAS-USD", "spread": 21.87, "edge": 60.02, "best_net": -0.8119},
        {"product": "KGEN-USD", "spread": 52.36, "edge": 160.60, "best_net": -1.0980},
        {"product": "XMN-USD", "spread": 22.12, "edge": 74.12, "best_net": -0.9891},
        {"product": "INX-USD", "spread": 41.86, "edge": 52.09, "best_net": -1.2004},
        {"product": "SKR-USD", "spread": 22.70, "edge": 97.26, "best_net": -0.2028},
        {"product": "SUKU-USD", "spread": 42.13, "edge": 87.23, "best_net": -1.0827},
        {"product": "VFY-USD", "spread": 11.44, "edge": 275.72, "best_net": -0.7294},
        {"product": "GLMR-USD", "spread": 31.09, "edge": 104.87, "best_net": -0.8847},
        {"product": "CHEX-USD", "spread": 57.80, "edge": 109.82, "best_net": -1.0948},
    ]

    print(f"\nOracle Green Entries (2):")
    for g in green:
        print(f"  {g['product']:<12} spread={g['spread']:.1f}bps  edge={g['edge']:.1f}bps  best_net=${g['best_net']:.4f}")

    print(f"\nLoser Entries (12):")
    for l in losers:
        print(f"  {l['product']:<12} spread={l['spread']:.1f}bps  edge={l['edge']:.1f}bps  best_net=${l['best_net']:.4f}")

    # Statistical comparison
    green_spreads = [g['spread'] for g in green]
    green_edges = [g['edge'] for g in green]
    loser_spreads = [l['spread'] for l in losers]
    loser_edges = [l['edge'] for l in losers]

    print(f"\n{'='*60}")
    print(f"STATISTICAL COMPARISON")
    print(f"{'='*60}")

    import numpy as np
    print(f"  Spread (green):  mean={np.mean(green_spreads):.1f}bps, std={np.std(green_spreads):.1f}bps")
    print(f"  Spread (losers): mean={np.mean(loser_spreads):.1f}bps, std={np.std(loser_spreads):.1f}bps")
    print(f"  Edge (green):    mean={np.mean(green_edges):.1f}bps, std={np.std(green_edges):.1f}bps")
    print(f"  Edge (losers):   mean={np.mean(loser_edges):.1f}bps, std={np.std(loser_edges):.1f}bps")

    # Key insight: what's different?
    print(f"\n{'='*60}")
    print(f"KEY INSIGHTS")
    print(f"{'='*60}")

    # 1. Spread
    if np.mean(green_spreads) > np.mean(loser_spreads):
        print(f"  ✅ Green entries have WIDER spreads (avg {np.mean(green_spreads):.1f} vs {np.mean(loser_spreads):.1f}bps)")
        print(f"     → Tight spreads don't guarantee profitability; wider spreads may indicate more volatile products")
    else:
        print(f"  ❌ Green entries have NARROWER spreads")

    # 2. Edge
    if np.mean(green_edges) < np.mean(loser_edges):
        print(f"  ✅ Green entries have LOWER edge (avg {np.mean(green_edges):.1f} vs {np.mean(loser_edges):.1f}bps)")
        print(f"     → High edge = chasing, which loses money. Lower edge entries are more sustainable.")
    else:
        print(f"  ❌ Green entries have HIGHER edge")

    # 3. Spread range analysis
    green_spread_range = (min(green_spreads), max(green_spreads))
    loser_spread_range = (min(loser_spreads), max(loser_spreads))
    print(f"  Green spread range: {green_spread_range[0]:.1f} - {green_spread_range[1]:.1f}bps")
    print(f"  Loser spread range: {loser_spread_range[0]:.1f} - {loser_spread_range[1]:.1f}bps")

    # 4. Edge range analysis
    green_edge_range = (min(green_edges), max(green_edges))
    loser_edge_range = (min(loser_edges), max(loser_edges))
    print(f"  Green edge range: {green_edge_range[0]:.1f} - {green_edge_range[1]:.1f}bps")
    print(f"  Loser edge range: {loser_edge_range[0]:.1f} - {loser_edge_range[1]:.1f}bps")

    # 5. Optimal filter suggestion
    print(f"\n{'='*60}")
    print(f"SUGGESTED ENTRY FILTER MUTATION")
    print(f"{'='*60}")

    # Find the overlap region where green entries live
    spread_min = min(green_spreads)
    spread_max = max(green_spreads)
    edge_min = min(green_edges)
    edge_max = max(green_edges)

    # How many losers fall in the green region?
    overlap_losers = [l for l in losers if spread_min <= l['spread'] <= spread_max and edge_min <= l['edge'] <= edge_max]
    print(f"  Green region: spread {spread_min:.0f}-{spread_max:.0f}bps, edge {edge_min:.0f}-{edge_max:.0f}bps")
    print(f"  Losers in green region: {len(overlap_losers)}/{len(losers)}")

    if len(overlap_losers) == 0:
        print(f"  ✅ Perfect separation! Filter to green region would eliminate all losers.")
    else:
        print(f"  ❌ {len(overlap_losers)} losers also in green region. Need additional features.")
        for l in overlap_losers:
            print(f"    {l['product']}: spread={l['spread']:.1f}bps, edge={l['edge']:.1f}bps, net=${l['best_net']:.4f}")

    # Alternative: find features that separate green from losers
    print(f"\n  Alternative: product-level filtering")
    green_products = set(g['product'] for g in green)
    loser_products = set(l['product'] for l in losers)
    unique_green = green_products - loser_products
    shared = green_products & loser_products
    print(f"    Green-only products: {unique_green}")
    print(f"    Shared products: {shared}")

    print(f"\n{'='*60}")
    print(f"CONCLUSION")
    print(f"{'='*60}")
    print(f"  The Kraken hot-entry filter is selecting entries that LOSE money.")
    print(f"  Even oracle-best exits (perfect hindsight) only go green 14% of the time.")
    print(f"  The 2 green entries (MOVR, ESP) don't share a clear pattern with enough data to generalize.")
    print(f"  The entry filter needs to be REBUILT from scratch, not just mutated.")
    print(f"  This confirms: Coinbase combined scorer signal ≠ Kraken hot-entry signal.")
    print(f"  They operate on different timeframes, different features, different venues.")

    print(f"\n{'='*80}")
    print("DONE")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
