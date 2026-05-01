#!/usr/bin/env python3
"""SUP-USD full param sweep + comprehensive portfolio report.

SUP was the B-tier surprise: 96.1% hit rate, $164/30d on $48.
Let's find the optimal params and build the unified portfolio.
"""
import json, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from strategy_library import momentum, rsi_mr

CACHE = os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache")

def load(coin, days="30d"):
    path = os.path.join(CACHE, f"{coin.replace('-USD', '_USD')}_FIVE_MINUTE_{days}.json")
    if not os.path.exists(path): return []
    with open(path) as f: data = json.load(f)
    return [{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
             "close": float(c["close"]), "start": int(c.get("start", c.get("time", 0))),
             "volume": float(c.get("volume", 0))} for c in data.get("candles", [])]

# SUP full param sweep
print("=" * 70)
print("SUP-USD FULL PARAM SWEEP — 30d")
print("=" * 70)
sup = load("SUP-USD")
if sup:
    print(f"SUP-USD: {len(sup)} candles")
    results = []
    for lb in [5, 8, 10, 12, 15, 20, 25, 30, 40, 50]:
        for tp in [2, 3, 5, 8, 10, 12, 15, 20, 25, 30]:
            for sl in [0, 1, 2, 3, 5, 8, 10]:
                for mh in [12, 24, 36, 48]:
                    r = momentum(sup, lookback=lb, tp_pct=tp, sl_pct=sl, max_hold=mh,
                                 starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                    results.append((r["net_pnl"], lb, tp, sl, mh, r))
    results.sort(reverse=True)
    profitable = [x for x in results if x[0] > 0]
    print(f"Combos: {len(results)}, Profitable: {len(profitable)} ({len(profitable)/len(results)*100:.1f}%)")
    print(f"\n{'Rank':<5} {'LB':<5} {'TP%':<5} {'SL%':<5} {'MH':<5} {'Net PnL':>10} {'WR':>7} {'Trades':>7} {'DD':>7}")
    print("-" * 70)
    for i, (pnl, lb, tp, sl, mh, r) in enumerate(results[:10], 1):
        print(f"{i:<5} {lb:<5} {tp:<5} {sl:<5} {mh:<5} ${pnl:>8.2f} {r['win_rate']:>6.1f}% {r['trades']:>7} {r['max_drawdown']:>6.1f}%")

# Also sweep SUP RSI MR (might work too)
print(f"\nSUP RSI MR sweep...")
sup_rsi = []
for rp in [2, 3, 4, 5, 7, 10]:
    for ot in [15, 20, 25, 30, 35, 40, 45]:
        for tp in [3, 5, 7.5, 10, 15, 20, 25]:
            for sl in [0, 0.5, 1, 2, 3, 5]:
                r = rsi_mr(sup, rsi_period=rp, os_thresh=ot, tp_pct=tp, sl_pct=sl, max_hold=48,
                           starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                sup_rsi.append((r["net_pnl"], rp, ot, tp, sl, r))
sup_rsi.sort(reverse=True)
profitable_rsi = [x for x in sup_rsi if x[0] > 0]
print(f"  RSI MR combos: {len(sup_rsi)}, Profitable: {len(profitable_rsi)} ({len(profitable_rsi)/len(sup_rsi)*100:.1f}%)")
if sup_rsi:
    pnl, rp, ot, tp, sl, r = sup_rsi[0]
    print(f"  Best: rsi={rp} os={ot} tp={tp}% sl={sl}% → PnL=${pnl:.2f} WR={r['win_rate']}% Trades={r['trades']}")

# Build comprehensive portfolio report
print(f"\n{'='*70}")
print("COMPREHENSIVE PORTFOLIO REPORT")
print(f"{'='*70}")

# All verified coins with ground-truth numbers
verified_coins = [
    # (coin, strategy, lb, tp, sl, pnl, wr, dd, trades, max_hold, notes)
    ("GHST-USD", "momentum", 20, 15, 3, 1225, 47.9, 32.6, 73, 24, "100% hit rate"),
    ("MOG-USD", "rsi_mr", 4, 7.5, 0.5, 1462, 36.0, 0, 239, 48, "33.1% hit rate, 283 signals"),
    ("TRU-USD", "momentum", 10, 10, 3, 576, 52.4, 29.1, 82, 24, "93.3% hit rate"),
    ("RAVE-USD", "momentum", 15, 10, 0, 501, 69.7, 0, 76, 48, "SL=0% — no stopouts"),
    ("SUP-USD", "momentum", 8, 8, 1, 188, 44.9, 12.3, 89, 24, "91.7% hit rate, optimized"),
    ("A8-USD", "momentum", 10, 15, 0, 118, 52.5, 27.7, 59, 48, ""),
    ("BAL-USD", "momentum", 50, 10, 3, 92, 56.7, 14.9, 30, 48, ""),
    ("PRL-USD", "momentum", 25, 10, 3, 69, 43.3, 31.0, 90, 48, ""),
    ("IOTX-USD", "momentum", 25, 5, 2, 46, 56.3, 21.3, 71, 24, "Optimized params"),
    ("BLUR-USD", "momentum", 15, 8, 5, 63, 54.1, 23.1, 37, 48, ""),
    ("ALEPH-USD", "momentum", 30, 15, 5, 47, 59.1, 12.9, 22, 48, "30d validated"),
    ("MDT-USD", "momentum", 25, 5, 2, 46, 61.5, 10.8, 52, 48, "75.3% hit rate"),
    ("TROLL-USD", "momentum", 30, 12, 8, 43, 54.3, 28.2, 35, 24, "57.2% hit rate"),
]

# Sort by PnL (index 5)
verified_coins.sort(key=lambda x: x[5], reverse=True)

total_pnl = sum(c[5] for c in verified_coins)
total_trades = sum(c[8] for c in verified_coins)
capital = len(verified_coins) * 48

print(f"\n{'#':<3} {'Coin':<15} {'Strat':<10} {'LB':<4} {'TP%':<4} {'SL%':<4} {'MH':<4} {'PnL/mo':>8} {'WR':>6} {'DD':>6} {'Trades':>7} {'Notes'}")
print("-" * 110)
for i, (coin, strat, lb, tp, sl, pnl, wr, dd, trades, mh, notes) in enumerate(verified_coins, 1):
    strat_short = strat[:4] if strat == "rsi_mr" else "mom"
    print(f"{i:<3} {coin:<15} {strat_short:<10} {lb:<4} {tp:<4} {sl:<4} {mh:<4} ${pnl:>7.0f} {wr:>5.1f}% {dd:>5.1f}% {trades:>7} {notes}")

print(f"\n{'='*70}")
print(f"PORTFOLIO TOTALS")
print(f"{'='*70}")
print(f"  Coins: {len(verified_coins)}")
print(f"  Total monthly PnL: ${total_pnl:,.0f}")
print(f"  Capital: ${capital:,} ({len(verified_coins)} × $48)")
print(f"  Monthly return: {total_pnl/capital*100:.0f}%")
print(f"  Total trades/month: {total_trades}")
print(f"  Daily avg trades: {total_trades/30:.0f}")

# Save
report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "coins": [
        {"coin": c[0], "strategy": c[1], "lookback": c[2], "tp_pct": c[3], "sl_pct": c[4],
         "net_pnl": c[5], "win_rate": c[6], "max_drawdown": c[7],
         "trades": c[8], "max_hold": c[9], "notes": c[10]}
        for c in verified_coins
    ],
    "portfolio_pnl": total_pnl,
    "capital": capital,
    "monthly_return_pct": round(total_pnl/capital*100, 1),
    "total_trades": total_trades,
}

report_path = os.path.join(os.path.dirname(__file__), "..", "reports", "comprehensive_portfolio.json")
os.makedirs(os.path.dirname(report_path), exist_ok=True)
with open(report_path, "w") as f:
    json.dump(report, f, indent=2)
print(f"\nReport saved: {report_path}")
