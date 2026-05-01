#!/usr/bin/env python3
"""30d reconciliation: TRU, GHST, RED, NOM through strategy library."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import momentum
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

coins = ['TRU-USD', 'GHST-USD', 'RED-USD', 'NOM-USD']
lines = []

for coin in coins:
    print(f"Fetching {coin}...", flush=True)
    try:
        candles = normalize_candles(fetch_candles_coinbase(coin, 30))
        lines.append(f"{coin}: {len(candles)} candles")
        best = None
        best_net = -999
        qualifying = 0
        total = 0
        for lb in [10, 25, 50]:
            for tp in [5, 10, 15]:
                for sl in [0, 3, 5]:
                    total += 1
                    r = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl,
                                max_hold=max(lb*2, 48), fee_rate=0.004,
                                starting_cash=100.0, seed=42)
                    if r['net_pnl'] > 0 and r['win_rate'] >= 40:
                        qualifying += 1
                        lines.append(f"  lb={lb} TP={tp} SL={sl}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")
                    if r['net_pnl'] > best_net:
                        best_net = r['net_pnl']
                        best = (lb, tp, sl, r)
        
        hit_rate = qualifying / max(total, 1) * 100
        if best:
            lb, tp, sl, r = best
            lines.append(f"  BEST: lb={lb} TP={tp} SL={sl}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")
            if r['net_pnl'] > 0 and r['win_rate'] >= 40:
                lines.append(f"  [CONFIRMED 30d EDGE - hit rate: {hit_rate:.1f}%]")
            else:
                lines.append(f"  [30d FAILED - best combo loses or low WR]")
        else:
            lines.append(f"  [NO DATA - all combos negative]")
    except Exception as e:
        lines.append(f"{coin}: ERROR - {e}")

result = "\n".join(lines)
with open("reports/reconciliation_tru_ghst_red_nom.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(result, flush=True)
