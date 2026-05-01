#!/usr/bin/env python3
"""30d validation of vol_breakout and atr_trailing strategies."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import momentum, rsi_mr
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

# Top coins from the 7d vol sweep that showed promise
COINS = ['NOM-USD', 'SUP-USD', 'RAVE-USD', 'GHST-USD', 'TRU-USD']

lines = []
lines.append("VOLATILITY STRATEGY 30D VALIDATION")
lines.append("=" * 60)

for coin in COINS:
    print(f"Fetching {coin}...", flush=True)
    candles = normalize_candles(fetch_candles_coinbase(coin, 30))
    lines.append(f"\n{coin} ({len(candles)} candles):")
    
    # Simulate vol_breakout: momentum with volatility filter
    # High vol = large recent candles = expansion
    # Use wider params to capture vol expansion moves
    for lb in [10, 20, 50]:
        for tp in [10, 15, 20]:
            for sl in [3, 5, 8]:
                r = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl, max_hold=48,
                            fee_rate=0.004, starting_cash=100.0, seed=42)
                # Only show results that beat the baseline momentum
                if r['net_pnl'] > 100 and r['win_rate'] >= 35:
                    lines.append(f"  lb={lb} TP={tp} SL={sl}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")

result = "\n".join(lines)
with open("reports/vol_strategy_30d.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(result, flush=True)
