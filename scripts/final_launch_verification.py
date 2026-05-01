#!/usr/bin/env python3
"""Final 10-coin pre-launch verification through strategy_library.py."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import momentum, rsi_mr
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

configs = [
    ('RAVE-USD', 'momentum', {'lookback': 15, 'tp_pct': 10, 'sl_pct': 0, 'max_hold': 36}),
    ('MOG-USD', 'rsi_mr', {'rsi_period': 4, 'os_thresh': 45, 'tp_pct': 7.5, 'sl_pct': 0.5, 'max_hold': 48}),
    ('NOM-USD', 'momentum', {'lookback': 30, 'tp_pct': 8, 'sl_pct': 8, 'max_hold': 12}),
    ('GHST-USD', 'momentum', {'lookback': 20, 'tp_pct': 15, 'sl_pct': 3, 'max_hold': 24}),
    ('TRU-USD', 'momentum', {'lookback': 10, 'tp_pct': 10, 'sl_pct': 3, 'max_hold': 24}),
    ('A8-USD', 'momentum', {'lookback': 10, 'tp_pct': 15, 'sl_pct': 0, 'max_hold': 48}),
    ('SUP-USD', 'momentum', {'lookback': 10, 'tp_pct': 10, 'sl_pct': 5, 'max_hold': 24}),
    ('SUP-USD-opt', 'momentum', {'lookback': 8, 'tp_pct': 8, 'sl_pct': 1, 'max_hold': 24}),
    ('IOTX-USD', 'momentum', {'lookback': 20, 'tp_pct': 5, 'sl_pct': 3, 'max_hold': 24}),
    ('IOTX-USD-opt', 'momentum', {'lookback': 25, 'tp_pct': 5, 'sl_pct': 2, 'max_hold': 24}),
    ('CFG-USD', 'momentum', {'lookback': 50, 'tp_pct': 15, 'sl_pct': 0, 'max_hold': 48}),
    ('BAL-USD', 'momentum', {'lookback': 50, 'tp_pct': 10, 'sl_pct': 3, 'max_hold': 36}),
]

lines = []
lines.append("FINAL PRE-LAUNCH VERIFICATION (strategy_library.py, 30d, $100 each)")
lines.append("=" * 80)
coins_seen = set()
total = 0

for label, stype, params in configs:
    coin = label.split('-opt')[0]
    if coin not in coins_seen:
        coins_seen.add(coin)
        print(f"Fetching {coin}...", flush=True)
        candles = normalize_candles(fetch_candles_coinbase(coin, 30))
    else:
        continue
    
    if stype == 'rsi_mr':
        r = rsi_mr(candles, rsi_period=params['rsi_period'], os_thresh=params['os_thresh'],
                   tp_pct=params['tp_pct'], sl_pct=params['sl_pct'], max_hold=params['max_hold'],
                   fee_rate=0.004, starting_cash=100.0, seed=42)
    else:
        r = momentum(candles, lookback=params['lookback'], tp_pct=params['tp_pct'],
                     sl_pct=params['sl_pct'], max_hold=params['max_hold'],
                     fee_rate=0.004, starting_cash=100.0, seed=42)
    
    opt_tag = " (opt)" if '-opt' in label else ""
    lines.append(f'{label}: Net=${r["net_pnl"]:+.2f} WR={r["win_rate"]}% T={r["trades"]} DD={r["max_drawdown"]}%{opt_tag}')
    if '-opt' not in label:
        total += r['net_pnl']

lines.append(f'\n10-COIN TOTAL: ${total:+.2f} on $1,000')
result = '\n'.join(lines)
with open("reports/final_launch_verification.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(result, flush=True)
