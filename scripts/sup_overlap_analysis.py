#!/usr/bin/env python3
"""SUP same-coin overlap: Momentum + Range Breakout combined."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import momentum
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

print("Fetching SUP-USD 30d...", flush=True)
candles = normalize_candles(fetch_candles_coinbase('SUP-USD', 30))
print(f"  {len(candles)} candles", flush=True)

# Strategy 1: Momentum (current runner params)
# Note: strategy library uses percentage scale (10 = 10%), not decimal
r1 = momentum(candles, lookback=10, tp_pct=10, sl_pct=5, max_hold=24,
              fee_rate=0.004, starting_cash=100.0, seed=42)
print(f"\nSUP Momentum (lb=10, TP=10%, SL=5%): Net=${r1['net_pnl']:+.2f} WR={r1['win_rate']}% T={r1['trades']} DD={r1['max_drawdown']}%", flush=True)

# Strategy 2: Momentum (optimized params from sweep)
r2 = momentum(candles, lookback=20, tp_pct=10, sl_pct=1, max_hold=48,
              fee_rate=0.004, starting_cash=100.0, seed=42)
print(f"SUP Momentum opt (lb=20, TP=10%, SL=1%): Net=${r2['net_pnl']:+.2f} WR={r2['win_rate']}% T={r2['trades']} DD={r2['max_drawdown']}%", flush=True)

# Strategy 3: Range Breakout-style (very short lookback, tight SL)
r3 = momentum(candles, lookback=8, tp_pct=8, sl_pct=1, max_hold=24,
              fee_rate=0.004, starting_cash=100.0, seed=42)
print(f"SUP Range-style (lb=8, TP=8%, SL=1%): Net=${r3['net_pnl']:+.2f} WR={r3['win_rate']}% T={r3['trades']} DD={r3['max_drawdown']}%", flush=True)

# Combined portfolio: 50% capital to each strategy (simulated)
# This approximates running both strategies with half capital each
combined_net = (r1['net_pnl'] + r3['net_pnl']) / 2
combined_trades = r1['trades'] + r3['trades']
print(f"\nSUP Combined (50/50 Momentum + Range): Net=${combined_net:+.2f} total_trades={combined_trades}", flush=True)
print(f"  vs best single: ${max(r1['net_pnl'], r3['net_pnl']):+.2f}", flush=True)
if combined_net > max(r1['net_pnl'], r3['net_pnl']):
    print(f"  ✅ ADDITIVE: +${combined_net - max(r1['net_pnl'], r3['net_pnl']):+.2f} from diversification", flush=True)
else:
    print(f"  ❌ NOT ADDITIVE: single strategy performs better", flush=True)
