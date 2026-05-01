#!/usr/bin/env python3
"""Filter foundry pockets by spread and net-after-fees."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "reports" / "coinbase_spot_foundry_pocket_board.json"

with open(BOARD) as f:
    d = json.load(f)

rows = d.get("rows", [])
print(f"Total pockets: {len(rows)}")

nets = [r.get("avg_net_pct", 0) for r in rows]
spreads = [r.get("spread_bps_proxy", 0) for r in rows]
signals = [r.get("signals", 0) for r in rows]

print(f"Net range: {min(nets):.2f}% to {max(nets):.2f}% (avg {sum(nets)/len(nets):.2f}%)")
print(f"Spread range: {min(spreads):.1f} to {max(spreads):.1f} bps (avg {sum(spreads)/len(spreads):.1f})")
print(f"Signal range: {min(signals)} to {max(signals)} (avg {sum(signals)/len(signals):.1f})")
print()

# Filter: net >= 0, spread <= 20bps, signals >= 3
survivors = [r for r in rows if r.get("avg_net_pct", 0) >= 0 and r.get("spread_bps_proxy", 999) <= 20 and r.get("signals", 0) >= 3]
survivors.sort(key=lambda x: x["avg_net_pct"], reverse=True)

print(f"Survivors (net>=0, spread<=20bps, signals>=3): {len(survivors)}")
print(f"Eliminated: {len(rows) - len(survivors)} ({(1-len(survivors)/len(rows))*100:.0f}%)")
print()

print("Top 20 survivors:")
print(f"  {'#':>3} {'Product':>15} {'Net%':>7} {'Spread':>8} {'Signals':>8} {'Win%':>6}")
print(f"  {'---':>3} {'-'*15:>15} {'-'*7:>7} {'-'*8:>8} {'-'*8:>8} {'-'*6:>6}")
for i, s in enumerate(survivors[:20]):
    print(f"  {i+1:>3} {s['product_id']:>15} {s['avg_net_pct']:>6.2f}% {s['spread_bps_proxy']:>5.1f}bps {s['signals']:>8} {s['win_rate_pct']:>5.0f}%")

# TREE analysis
print()
print("=== TREE POCKETS ===")
tree = [r for r in rows if "TREE" in r.get("product_id", "")]
for t in tree:
    print(f"  {t['product_id']} v{t['variant_id']} net={t['avg_net_pct']:.2f}% spread={t['spread_bps_proxy']:.1f}bps signals={t['signals']} win={t['win_rate_pct']:.0f}%")
