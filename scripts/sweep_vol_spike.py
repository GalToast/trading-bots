#!/usr/bin/env python3
"""Volume spike reversion param sweep on RAVE and TRU — the only coins with positive default."""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from strategy_library import volume_spike_reversion

CACHE = os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache")

def load(coin, days="30d"):
    path = os.path.join(CACHE, f"{coin.replace('-USD', '_USD')}_FIVE_MINUTE_{days}.json")
    if not os.path.exists(path): return []
    with open(path) as f: data = json.load(f)
    return [{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
             "close": float(c["close"]), "start": int(c.get("start", c.get("time", 0))),
             "volume": float(c.get("volume", 0))} for c in data.get("candles", [])]

for coin in ["RAVE-USD", "TRU-USD"]:
    candles = load(coin)
    if not candles: continue
    print(f"\n{'='*70}")
    print(f"{coin} — Volume Spike Reversion Param Sweep")
    print(f"{'='*70}")

    results = []
    for rp in [2, 3, 4, 5]:
        for ot in [20, 25, 30, 35, 40, 45]:
            for vm in [1.5, 2.0, 2.5, 3.0, 4.0]:
                for vl in [10, 20, 30, 50]:
                    for tp in [5, 10, 15, 20, 25]:
                        for sl in [0, 3, 5]:
                            for mh in [24, 48]:
                                r = volume_spike_reversion(candles, rsi_period=rp, os_thresh=ot, vol_mult=vm,
                                                           vol_lookback=vl, tp_pct=tp, sl_pct=sl, max_hold=mh,
                                                           starting_cash=48.0, fee_rate=0.004,
                                                           entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                                results.append((r['net_pnl'], rp, ot, vm, vl, tp, sl, mh, r))

    results.sort(reverse=True)
    profitable = [x for x in results if x[0] > 0]
    print(f"Combos: {len(results)}, Profitable: {len(profitable)} ({len(profitable)/len(results)*100:.1f}%)")

    if profitable:
        print(f"\n{'Rank':<5} {'RSI':<4} {'OS':<4} {'VM':<4} {'VL':<4} {'TP%':<4} {'SL%':<4} {'MH':<4} {'PnL':>9} {'WR':>6} {'Trades':>7}")
        print("-" * 65)
        for i, (pnl, rp, ot, vm, vl, tp, sl, mh, r) in enumerate(results[:10], 1):
            print(f"{i:<5} {rp:<4} {ot:<4} {vm:<4.1f} {vl:<4} {tp:<4} {sl:<4} {mh:<4} ${pnl:>7.2f} {r['win_rate']:>5.1f}% {r['trades']:>7}")

print("\nCOMPLETE")
