#!/usr/bin/env python3
"""Test 3 newly-fixed strategy types across portfolio coins.

Tests: VWAP reversion, volume spike reversion, multi-TF RSI.
These were broken (wrong function signatures) and are now fixed.
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from strategy_library import vwap_reversion, volume_spike_reversion, multi_tf_rsi, momentum

CACHE = os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache")

def load(coin, days="30d"):
    path = os.path.join(CACHE, f"{coin.replace('-USD', '_USD')}_FIVE_MINUTE_{days}.json")
    if not os.path.exists(path): return []
    with open(path) as f: data = json.load(f)
    return [{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
             "close": float(c["close"]), "start": int(c.get("start", c.get("time", 0))),
             "volume": float(c.get("volume", 0))} for c in data.get("candles", [])]

COINS = ["MOG-USD", "GHST-USD", "TRU-USD", "RAVE-USD", "SUP-USD", "A8-USD", "BAL-USD",
         "PRL-USD", "IOTX-USD", "BLUR-USD", "ALEPH-USD", "MDT-USD", "TROLL-USD"]

print("=" * 70)
print("NEW STRATEGY SWEEP — All 13 coins, default params")
print("=" * 70)
print(f"\n{'Coin':<15} {'VWAP Rev':>14} {'Vol Spike':>14} {'Multi-TF':>14} {'Momentum':>14}")

for coin in COINS:
    candles = load(coin)
    if not candles or len(candles) < 500:
        print(f"{coin:<15} {'N/A':>14} {'N/A':>14} {'N/A':>14}")
        continue

    rv = vwap_reversion(candles, vwap_window=48, vwap_dev_pct=2.0, tp_pct=5, sl_pct=3, max_hold=24,
                        starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    rs = volume_spike_reversion(candles, rsi_period=3, os_thresh=30, vol_mult=2.0, vol_lookback=20,
                                tp_pct=15, sl_pct=5, max_hold=36,
                                starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    rm = multi_tf_rsi(candles, rsi_period=3, os_thresh=30, tp_pct=20, sl_pct=5, max_hold=36,
                      starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    rmo = momentum(candles, lookback=20, tp_pct=10, sl_pct=5, max_hold=48,
                   starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)

    def fmt(r):
        if r['signals'] == 0: return "no signals"
        return f"${r['net_pnl']:7.2f} {r['win_rate']:4.1f}%"

    print(f"{coin:<15} {fmt(rv):>14} {fmt(rs):>14} {fmt(rm):>14} {fmt(rmo):>14}")

# VWAP param sweep on coins with positive default
print(f"\n{'='*70}")
print(f"VWAP REVERSION — Param sweep")
print(f"{'='*70}")

for coin in COINS:
    candles = load(coin)
    if not candles: continue
    r = vwap_reversion(candles, vwap_window=48, vwap_dev_pct=2.0, tp_pct=5, sl_pct=3, max_hold=24,
                       starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    if r['net_pnl'] > 0 and r['signals'] > 0:
        results = []
        for vw in [24, 36, 48, 72, 96]:
            for dp in [1.0, 1.5, 2.0, 2.5, 3.0]:
                for tp in [3, 5, 8, 10, 15]:
                    for sl in [0, 2, 3, 5]:
                        for mh in [12, 24, 36, 48]:
                            rr = vwap_reversion(candles, vwap_window=vw, vwap_dev_pct=dp, tp_pct=tp, sl_pct=sl, max_hold=mh,
                                                starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                            results.append((rr['net_pnl'], vw, dp, tp, sl, mh, rr))
        results.sort(reverse=True)
        profitable = [x for x in results if x[0] > 0]
        best = results[0] if results else None
        print(f"\n{coin}: {len(results)} combos, {len(profitable)} profitable ({len(profitable)/len(results)*100:.1f}%)")
        if best:
            print(f"  Best: vwap={best[1]}, dev={best[2]}%, tp={best[3]}%, sl={best[4]}%, mh={best[5]}")
            print(f"  PnL=${best[0]:.2f}, WR={best[6]['win_rate']}%, Trades={best[6]['trades']}")

print(f"\n{'='*70}")
print("COMPLETE")
