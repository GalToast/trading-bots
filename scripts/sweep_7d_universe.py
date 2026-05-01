#!/usr/bin/env python3
"""
Validate ALEPH optimized params on 7d window (out-of-sample) + scan 7d coins for momentum edges.
"""
import json, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

from strategy_library import momentum, range_breakout

CACHE = os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache")

def load(coin, days):
    path = os.path.join(CACHE, f"{coin.replace('-USD', '_USD')}_FIVE_MINUTE_{days}d.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return [{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
             "close": float(c["close"]), "start": int(c.get("start", c.get("time", 0))),
             "volume": float(c.get("volume", 0))} for c in data.get("candles", [])]

# 1. ALEPH out-of-sample: 7d validation
print("=" * 70)
print("ALEPH OUT-OF-SAMPLE VALIDATION (7d window)")
print("=" * 70)
aleph_7d = load("ALEPH-USD", "7")
print(f"ALEPH 7d candles: {len(aleph_7d)}")
if len(aleph_7d) > 500:
    # Best params from 30d sweep
    best_mom = momentum(aleph_7d, lookback=30, tp_pct=15, sl_pct=5, max_hold=48,
                        starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    best_rb = range_breakout(aleph_7d, range_lookback=30, tp_pct=15, sl_pct=5, max_hold=48,
                             starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    print(f"  Momentum (lb=30, tp=15%, sl=5%): PnL=${best_mom['net_pnl']:.2f} WR={best_mom['win_rate']}% Trades={best_mom['trades']}")
    print(f"  Range Breakout (lb=30, tp=15%, sl=5%): PnL=${best_rb['net_pnl']:.2f} WR={best_rb['win_rate']}% Trades={best_rb['trades']}")
else:
    print(f"  Not enough 7d candles (need >500, got {len(aleph_7d)})")

# 2. 7d momentum sweep on all cached coins
print("\n" + "=" * 70)
print("7d MOMENTUM SWEEP — All cached coins (quick scan)")
print("=" * 70)

import glob as glob_module
cache_files = glob_module.glob(os.path.join(CACHE, "*_USD_FIVE_MINUTE_7d.json"))
print(f"Found {len(cache_files)} coins with 7d data")

results = []
t0 = time.time()
done = 0
for cf in cache_files:
    fname = os.path.basename(cf)
    coin = fname.replace("_USD_FIVE_MINUTE_7d.json", "-USD")
    candles = load(coin, "7")
    if len(candles) < 500:
        continue

    # Test 3 key momentum configs
    for lb, tp, sl in [(30, 15, 5), (15, 5, 0), (10, 10, 5)]:
        r = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl, max_hold=48,
                     starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
        results.append((r["net_pnl"], coin, lb, tp, sl, r))
    done += 1
    if done % 30 == 0:
        elapsed = time.time() - t0
        print(f"  Progress: {done}/{len(cache_files)} ({done/len(cache_files)*100:.0f}%) — {elapsed:.0f}s")

results.sort(reverse=True)
profitable = [x for x in results if x[0] > 0]
print(f"\nSweep complete: {len(results)} combos, {len(profitable)} profitable ({len(profitable)/max(len(results),1)*100:.1f}%)")

print(f"\n{'Rank':<5} {'Coin':<15} {'LB':<5} {'TP%':<5} {'SL%':<5} {'Net PnL':>10} {'WR':>7} {'Trades':>7} {'DD':>7}")
print("-" * 70)
for i, (pnl, coin, lb, tp, sl, r) in enumerate(results[:20], 1):
    print(f"{i:<5} {coin:<15} {lb:<5} {tp:<5} {sl:<5} ${pnl:>8.2f} {r['win_rate']:>6.1f}% {r['trades']:>7} {r['max_drawdown']:>6.1f}%")

# Also check top 7d coins for range breakout
print("\n" + "=" * 70)
print("7d RANGE BREAKOUT — Top 10 coins from momentum sweep")
print("=" * 70)
top_coins = list(set([r[1] for r in results[:10] if r[0] > 0]))[:10]
for coin in top_coins:
    candles = load(coin, "7")
    if len(candles) < 500:
        continue
    r = range_breakout(candles, range_lookback=30, tp_pct=15, sl_pct=5, max_hold=48,
                       starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    print(f"  {coin:<15} Range Breakout: PnL=${r['net_pnl']:8.2f} WR={r['win_rate']:5.1f}% Trades={r['trades']:4d}")
