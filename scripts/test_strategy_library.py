#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import bb_reversion, momentum
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

lines = []
for coin, name in [('IOTX-USD', 'IOTX'), ('RAVE-USD', 'RAVE'), ('BAL-USD', 'BAL'), ('BLUR-USD', 'BLUR')]:
    print(f"Fetching {coin}...", flush=True)
    candles = normalize_candles(fetch_candles_coinbase(coin, 30))
    print(f"  {len(candles)} candles", flush=True)
    
    r = bb_reversion(candles, bb_period=20, rsi_period=3, rsi_thresh=30,
                     proximity_pct=3.0, sl_pct=5.0, max_hold=24,
                     fee_rate=0.004, starting_cash=100.0, seed=42)
    m = momentum(candles, lookback=10, tp_pct=10, sl_pct=10, max_hold=48,
                 fee_rate=0.004, starting_cash=100.0, seed=42)
    
    lines.append(f"{name}: {len(candles)} candles")
    lines.append(f"  BB Rev: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")
    lines.append(f"  Momentum: Net=${m['net_pnl']:+.2f} WR={m['win_rate']}% T={m['trades']} DD={m['max_drawdown']}%")

result = "\n".join(lines)
with open("reports/strategy_library_test.txt", "w") as f:
    f.write(result)
print(result, flush=True)
