#!/usr/bin/env python3
"""Verify 6-coin unified runner through strategy library."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import momentum, rsi_mr
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

configs = [
    ('GHST-USD', 'momentum', {'lookback': 20, 'tp_pct': 15, 'sl_pct': 3, 'max_hold': 48}),
    ('NOM-USD', 'momentum', {'lookback': 30, 'tp_pct': 8, 'sl_pct': 8, 'max_hold': 12}),
    ('RAVE-USD', 'momentum', {'lookback': 15, 'tp_pct': 10, 'sl_pct': 0, 'max_hold': 48}),
    ('TRU-USD', 'momentum', {'lookback': 10, 'tp_pct': 10, 'sl_pct': 3, 'max_hold': 48}),
    ('A8-USD', 'momentum', {'lookback': 10, 'tp_pct': 15, 'sl_pct': 0, 'max_hold': 48}),
]

lines = []
lines.append('6-Coin Unified Runner Verification (strategy_library, 30d, $100 each)')
lines.append('=' * 75)
total = 0

for coin, stype, params in configs:
    print(f"Fetching {coin}...", flush=True)
    candles = normalize_candles(fetch_candles_coinbase(coin, 30))
    r = momentum(candles, lookback=params['lookback'], tp_pct=params['tp_pct'],
                 sl_pct=params['sl_pct'], max_hold=params['max_hold'],
                 fee_rate=0.004, starting_cash=100.0, seed=42)
    lines.append(f'{coin}: Net=${r["net_pnl"]:+.2f} WR={r["win_rate"]}% T={r["trades"]} DD={r["max_drawdown"]}%')
    total += r['net_pnl']

print("Fetching MOG-USD...", flush=True)
candles = normalize_candles(fetch_candles_coinbase('MOG-USD', 30))
r = rsi_mr(candles, rsi_period=4, os_thresh=45, tp_pct=7.5, sl_pct=0.5,
           max_hold=48, fee_rate=0.004, starting_cash=100.0, seed=42)
lines.append(f'MOG-USD (RSI MR): Net=${r["net_pnl"]:+.2f} WR={r["win_rate"]}% T={r["trades"]} DD={r["max_drawdown"]}%')
total += r['net_pnl']

lines.append(f'')
lines.append(f'TOTAL: ${total:+.2f} on $600')

result = '\n'.join(lines)
with open("reports/unified_runner_verification.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(result, flush=True)
