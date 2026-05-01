#!/usr/bin/env python3
"""Filter master report to only REAL winners (WR% >= 20, trades >= 10)."""
import json
from pathlib import Path

REPORT_DIR = Path("reports")
with open(REPORT_DIR / "master_edge_scan_all_coins.json", encoding="utf-8") as f:
    data = json.load(f)

print(f"Total passing combos: {data['passing_combos']}")
print(f"Coins with edge: {data['coins_with_edge']}")

# Filter: win_rate >= 20% AND trades >= 10 AND net > 10
top_50 = data.get("top_50", [])
real_winners = [r for r in top_50 if r.get("win_rate", 0) >= 20 and r.get("trades", 0) >= 10]

print(f"\n  REAL WINNERS (WR% >= 20, trades >= 10, net > 0): {len(real_winners)} combos")

print(f"\n  {'Rank':>4} {'Coin':<18} {'RSI':>3} {'TP':>5} {'Hold':>5} {'Net':>9} {'Trades':>7} {'WR%':>5} {'DD%':>5}")
print(f"  {'----'} {'------------------'} {'---'} {'-----'} {'-----'} {'---------'} {'-------'} {'-----'} {'-----'}")
for i, r in enumerate(real_winners[:30]):
    net = r.get("net", 0)
    trades = r.get("trades", 0)
    wr = r.get("win_rate", 0)
    dd = r.get("max_drawdown", 0)
    print(f"  {i+1:>4} {r['coin']:<18} {r.get('rsi_period','?'):>3} {r.get('tp',0)*100:>4.0f}% {r.get('max_hold','?'):>5} "
          f"${net:+8.2f}  {trades:>6}  {wr:>4.1f}% {dd:>4.1f}%")

# Best coin by net with real WR
coin_best = {}
for r in top_50:
    coin = r.get("coin", "?")
    wr = r.get("win_rate", 0)
    trades = r.get("trades", 0)
    if wr >= 20 and trades >= 10:
        if coin not in coin_best or r.get("net", 0) > coin_best[coin].get("net", 0):
            coin_best[coin] = r

print(f"\n  Best coins with REAL edge (WR >= 20%, trades >= 10):")
for i, (coin, r) in enumerate(sorted(coin_best.items(), key=lambda x: x[1].get("net", 0), reverse=True)):
    print(f"  {i+1:>4}. {coin:<18} ${r.get('net',0):+8.2f}  {r.get('trades',0):>3}t  {r.get('win_rate',0):>4.1f}%WR  "
          f"RSI={r.get('rsi_period','?')}  TP={r.get('tp',0)*100:.0f}%  Hold={r.get('max_hold','?')}  "
          f"DD={r.get('max_drawdown',0):.1f}%")

# Save filtered
filtered = {
    "real_winners_count": len(real_winners),
    "top_real_winners": real_winners[:30],
    "best_coins": {c: r for c, r in sorted(coin_best.items(), key=lambda x: x[1].get("net", 0), reverse=True)},
}
output = REPORT_DIR / "real_edge_winners.json"
with open(output, "w", encoding="utf-8") as f:
    json.dump(filtered, f, indent=2, default=str)
print(f"\n  Saved: {output}")
