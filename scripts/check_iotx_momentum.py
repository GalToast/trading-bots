#!/usr/bin/env python3
"""IOTX Momentum reconciliation — ground truth check.

@qwen-trading-bots has IOTX momentum at $67/mo with lb=25, tp=10%, sl=0%.
@main proved IOTX BB Rev is -$35/30d.
Question: Does momentum work on IOTX, or is the coin structurally untradeable?
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from strategy_library import momentum

# Load reconciliation candles
recon_path = os.path.join(os.path.dirname(__file__), "..", "reports", "reconciliation_candles.json")
with open(recon_path) as f:
    data = json.load(f)

raw = data["coins"]["IOTX-USD"]["candles"]
candles = [
    {"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
     "close": float(c["close"]), "start": int(c["start"]),
     "volume": float(c["volume"])}
    for c in raw
]
print(f"IOTX-USD: {len(candles)} candles (30d reconciliation window)")

# Test the params @qwen-trading-bots claims: lb=25, tp=10%, sl=0%
print("\n=== IOTX MOMENTUM — qwen-trading-bots params ===")
r = momentum(candles, lookback=25, tp_pct=10, sl_pct=0, max_hold=48,
             starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
print(f"  lb=25, tp=10%, sl=0%, mh=48: PnL=${r['net_pnl']:.2f}  WR={r['win_rate']}%  Trades={r['trades']}  Signals={r['signals']}  DD={r['max_drawdown']}%")

# Also test with slippage (more realistic)
r_slip = momentum(candles, lookback=25, tp_pct=10, sl_pct=0, max_hold=48,
                  starting_cash=48.0, fee_rate=0.004, entry_slip=0.0008, exit_slip=0.0, fill_prob=1.0)
print(f"  + 0.08% entry slippage: PnL=${r_slip['net_pnl']:.2f}  WR={r_slip['win_rate']}%  Trades={r_slip['trades']}")

# Full param sweep on IOTX momentum
print("\n=== IOTX MOMENTUM — Full Param Sweep ===")
results = []
for lb in [5, 10, 15, 20, 25, 30, 50, 75, 100]:
    for tp in [3, 5, 8, 10, 12, 15, 20, 25, 30]:
        for sl in [0, 2, 3, 5, 8, 10]:
            for mh in [12, 24, 36, 48]:
                r = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl, max_hold=mh,
                             starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                results.append((r["net_pnl"], lb, tp, sl, mh, r))

results.sort(reverse=True)
profitable = [x for x in results if x[0] > 0]
print(f"Combos: {len(results)}, Profitable: {len(profitable)} ({len(profitable)/len(results)*100:.1f}%)")

print(f"\n{'Rank':<5} {'LB':<5} {'TP%':<5} {'SL%':<5} {'MH':<5} {'Net PnL':>10} {'WR':>7} {'Trades':>7} {'DD':>7}")
print("-" * 70)
for i, (pnl, lb, tp, sl, mh, r) in enumerate(results[:10], 1):
    print(f"{i:<5} {lb:<5} {tp:<5} {sl:<5} {mh:<5} ${pnl:>8.2f} {r['win_rate']:>6.1f}% {r['trades']:>7} {r['max_drawdown']:>6.1f}%")

print(f"\nWorst combo: ${results[-1][0]:.2f} (lb={results[-1][1]}, tp={results[-1][2]}%, sl={results[-1][3]}%)")

if profitable:
    best = results[0]
    print(f"\n=== VERDICT ===")
    if results[0][0] > 20:
        print(f"IOTX MOMENTUM: GENUINE EDGE ✅")
        print(f"  Best: lb={best[1]}, tp={best[2]}%, sl={best[3]}%, mh={best[4]}")
        print(f"  PnL=${best[0]:.2f}/30d, WR={best[5]['win_rate']}%, {best[5]['trades']} trades")
        print(f"  {len(profitable)}/{len(results)} combos profitable ({len(profitable)/len(results)*100:.1f}%)")
    elif results[0][0] > 0:
        print(f"IOTX MOMENTUM: MARGINAL ⚠️")
        print(f"  Best: ${best[0]:.2f}/30d — technically positive but tiny")
        print(f"  {len(profitable)}/{len(results)} combos profitable")
    else:
        print(f"IOTX MOMENTUM: LOSING ❌")
        print(f"  Even best combo is negative: ${best[0]:.2f}")
        print(f"  Only {len(profitable)}/{len(results)} combos profitable")
else:
    print(f"\n=== VERDICT ===")
    print(f"IOTX MOMENTUM: ALL COMBOS LOSING ❌")
    print(f"  Even the best configuration loses money on IOTX.")
    print(f"  IOTX appears structurally untradeable with momentum.")
