#!/usr/bin/env python3
"""Verify MOG-USD RSI MR claim through strategy library."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from strategy_library import rsi_mr, momentum
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

print("Fetching MOG-USD...", flush=True)
try:
    candles = normalize_candles(fetch_candles_coinbase('MOG-USD', 30))
    print(f"MOG-USD: {len(candles)} candles\n", flush=True)
except Exception as e:
    print(f"ERROR: {e}", flush=True)
    sys.exit(1)

# Exact params from claim: rsi_period=4, os_thresh=45, tp=7.5%, sl=0.5%
lines = []
lines.append("MOG-USD RSI MR Verification")
lines.append("=" * 60)

# Claimed params
r = rsi_mr(candles, rsi_period=4, os_thresh=45, tp_pct=7.5, sl_pct=0.5,
           max_hold=48, fee_rate=0.004, starting_cash=100.0, seed=42)
lines.append(f"Claimed params (R=4, OS=45, TP=7.5%, SL=0.5%):")
lines.append(f"  Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}% Signals={r['signals']}")

# Sweep RSI period
lines.append(f"\nRSI Period Sweep (OS=45, TP=7.5%, SL=0.5%):")
best = None
best_net = -999
for period in [2, 3, 4, 5, 6, 7]:
    r = rsi_mr(candles, rsi_period=period, os_thresh=45, tp_pct=7.5, sl_pct=0.5,
               max_hold=48, fee_rate=0.004, starting_cash=100.0, seed=42)
    lines.append(f"  R={period}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")
    if r['net_pnl'] > best_net:
        best_net = r['net_pnl']
        best = (period, 45, 7.5, 0.5, r)

# Sweep OS threshold
lines.append(f"\nOS Threshold Sweep (R=4, TP=7.5%, SL=0.5%):")
for thresh in [20, 25, 30, 35, 40, 45, 50, 55]:
    r = rsi_mr(candles, rsi_period=4, os_thresh=thresh, tp_pct=7.5, sl_pct=0.5,
               max_hold=48, fee_rate=0.004, starting_cash=100.0, seed=42)
    lines.append(f"  OS={thresh}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")
    if r['net_pnl'] > best_net:
        best_net = r['net_pnl']
        best = (4, thresh, 7.5, 0.5, r)

# Sweep TP/SL
lines.append(f"\nTP/SL Sweep (R=4, OS=45):")
for tp in [3, 5, 7.5, 10, 15, 20, 25]:
    for sl in [0, 0.5, 1, 3, 5]:
        r = rsi_mr(candles, rsi_period=4, os_thresh=45, tp_pct=tp, sl_pct=sl,
                   max_hold=48, fee_rate=0.004, starting_cash=100.0, seed=42)
        if r['net_pnl'] > 0 and r['win_rate'] >= 40:
            lines.append(f"  TP={tp} SL={sl}: Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}%")
        if r['net_pnl'] > best_net:
            best_net = r['net_pnl']
            best = (4, 45, tp, sl, r)

if best:
    period, os_thresh, tp, sl, r = best
    lines.append(f"\nBEST: R={period} OS={os_thresh} TP={tp}% SL={sl}%")
    lines.append(f"  Net=${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}% Signals={r['signals']}")
    if r['net_pnl'] > 0 and r['win_rate'] >= 40:
        lines.append(f"  [CONFIRMED EDGE - MOG RSI MR is REAL]")
    else:
        lines.append(f"  [NOT CONFIRMED - best combo loses or low WR]")

# Also test momentum on MOG
lines.append(f"\nMOG Momentum (sanity check):")
for lb in [10, 25, 50]:
    for tp in [5, 10, 15]:
        for sl in [0, 3, 5]:
            m = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl,
                        max_hold=max(lb*2, 48), fee_rate=0.004,
                        starting_cash=100.0, seed=42)
            if m['net_pnl'] > 0 and m['win_rate'] >= 40:
                lines.append(f"  lb={lb} TP={tp} SL={sl}: Net=${m['net_pnl']:+.2f} WR={m['win_rate']}% T={m['trades']} DD={m['max_drawdown']}%")

result = "\n".join(lines)
with open("reports/mog_verification.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(result, flush=True)
