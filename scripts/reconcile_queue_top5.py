#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import momentum
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

coins = ['IOTX-USD', 'CFG-USD', 'PRL-USD', 'DASH-USD', 'ALEPH-USD']
lines = []

for coin in coins:
    print(f"Fetching {coin}...", flush=True)
    try:
        candles = normalize_candles(fetch_candles_coinbase(coin, 30))
        lines.append(f"{coin}: {len(candles)} candles")
        for lb in [10, 25, 50]:
            for tp in [5, 10, 15]:
                for sl in [0, 3, 5]:
                    r = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl,
                                max_hold=max(lb*2, 48), fee_rate=0.004,
                                starting_cash=100.0, seed=42)
                    if r['net_pnl'] > 0 and r['win_rate'] >= 40:
                        lines.append(f"  lb={lb} TP={tp} SL={sl}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")
    except Exception as e:
        lines.append(f"{coin}: ERROR - {e}")

result = "\n".join(lines)
with open("reports/reconciliation_queue_results.txt", "w") as f:
    f.write(result)
print(result, flush=True)
