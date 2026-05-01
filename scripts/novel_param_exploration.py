#!/usr/bin/env python3
"""Test genuinely NEW strategy types that we haven't verified yet."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import momentum, rsi_mr
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

# These are the 2 VERIFIED strategies — test them with DIFFERENT params 
# to find genuinely new edges we haven't discovered yet

# NEW IDEA: Momentum with volume filter — only enter if volume > average
# NEW IDEA: RSI MR with tight SL and small TP — high frequency scalping
# NEW IDEA: Wide TP momentum — catch bigger moves
# NEW IDEA: Very short lookback momentum (5 bars) — ultra-fast entries

COINS = ['MOG-USD', 'NOM-USD', 'RAVE-USD', 'GHST-USD', 'TRU-USD', 'SUP-USD', 'A8-USD', 'BAL-USD', 'IOTX-USD']

lines = []
lines.append("Novel Param Exploration (strategy_library.py, 30d, $100 each)")
lines.append("=" * 80)

novel_configs = [
    # Ultra-fast momentum (5-bar lookback)
    ('ultra_fast_momentum', {'lookback': 5, 'tp_pct': 5, 'sl_pct': 3, 'max_hold': 24}),
    # Wide TP momentum (catch big moves)
    ('wide_tp_momentum', {'lookback': 20, 'tp_pct': 25, 'sl_pct': 0, 'max_hold': 96}),
    # Very tight SL momentum
    ('tight_sl_momentum', {'lookback': 20, 'tp_pct': 10, 'sl_pct': 1, 'max_hold': 48}),
    # High frequency RSI MR (RSI=2, tight TP)
    ('hf_rsi_mr', {'rsi_period': 2, 'os_thresh': 20, 'tp_pct': 3, 'sl_pct': 1, 'max_hold': 12}),
    # Wide RSI MR (RSI=7, OS=50)
    ('wide_rsi_mr', {'rsi_period': 7, 'os_thresh': 50, 'tp_pct': 15, 'sl_pct': 5, 'max_hold': 48}),
    # Slow momentum (100-bar lookback)
    ('slow_momentum', {'lookback': 100, 'tp_pct': 10, 'sl_pct': 3, 'max_hold': 96}),
]

for coin in COINS:
    print(f"Fetching {coin}...", flush=True)
    candles = normalize_candles(fetch_candles_coinbase(coin, 30))
    lines.append(f"\n{coin} ({len(candles)} candles):")
    
    for name, params in novel_configs:
        if 'rsi_period' in params:
            r = rsi_mr(candles, rsi_period=params['rsi_period'], os_thresh=params['os_thresh'],
                       tp_pct=params['tp_pct'], sl_pct=params['sl_pct'], max_hold=params['max_hold'],
                       fee_rate=0.004, starting_cash=100.0, seed=42)
        else:
            r = momentum(candles, lookback=params['lookback'], tp_pct=params['tp_pct'],
                         sl_pct=params['sl_pct'], max_hold=params['max_hold'],
                         fee_rate=0.004, starting_cash=100.0, seed=42)
        status = "✅" if r['net_pnl'] > 0 and r['win_rate'] >= 40 else ""
        if r['net_pnl'] > 50:  # Only show meaningful results
            lines.append(f"  {name:25s}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']:>5.1f}% T={r['trades']:>4} DD={r['max_drawdown']:>5.1f}% {status}")

result = "\n".join(lines)
with open("reports/novel_param_exploration.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(result, flush=True)
