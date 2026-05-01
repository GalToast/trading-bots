#!/usr/bin/env python3
"""Full param sweep on the top 4 new discovery coins: TRU, GHST, RED, NOM."""
import json, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from strategy_library import momentum

CACHE = os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache")

def load(coin, days):
    path = os.path.join(CACHE, f"{coin.replace('-USD', '_USD')}_FIVE_MINUTE_{days}d.json")
    if not os.path.exists(path): return []
    with open(path) as f: data = json.load(f)
    return [{"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
             "close": float(c["close"]), "start": int(c.get("start", c.get("time", 0))),
             "volume": float(c.get("volume", 0))} for c in data.get("candles", [])]

COINS = ["TRU-USD", "GHST-USD", "RED-USD", "NOM-USD"]
t0 = time.time()

for coin in COINS:
    candles = load(coin, "7")
    if len(candles) < 500:
        print(f"\n{coin}: only {len(candles)} candles, skipping")
        continue

    print(f"\n{'='*70}")
    print(f"{coin} MOMENTUM PARAM SWEEP (7d, {len(candles)} candles)")
    print(f"{'='*70}")

    results = []
    for lb in [5, 8, 10, 12, 15, 20, 25, 30, 40, 50]:
        for tp in [3, 5, 8, 10, 12, 15, 20, 25, 30]:
            for sl in [0, 2, 3, 5, 8, 10]:
                for mh in [12, 24, 36, 48]:
                    r = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl, max_hold=mh,
                                 starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                    results.append((r["net_pnl"], lb, tp, sl, mh, r))

    results.sort(reverse=True)
    profitable = [x for x in results if x[0] > 0]
    print(f"Combos: {len(results)}, Profitable: {len(profitable)} ({len(profitable)/len(results)*100:.1f}%)")
    print(f"{'Rank':<5} {'LB':<5} {'TP%':<5} {'SL%':<5} {'MH':<5} {'Net PnL':>10} {'WR':>7} {'Trades':>7} {'DD':>7}")
    print("-" * 70)
    for i, (pnl, lb, tp, sl, mh, r) in enumerate(results[:10], 1):
        print(f"{i:<5} {lb:<5} {tp:<5} {sl:<5} {mh:<5} ${pnl:>8.2f} {r['win_rate']:>6.1f}% {r['trades']:>7} {r['max_drawdown']:>6.1f}%")

    best = results[0]
    print(f"\n  BEST: lb={best[1]}, tp={best[2]}%, sl={best[3]}%, mh={best[4]}")
    print(f"  PnL=${best[0]:.2f}, WR={best[5]['win_rate']}%, Trades={best[5]['trades']}, DD={best[5]['max_drawdown']}%")

print(f"\n{'='*70}")
print(f"ALL SWEEPS COMPLETE in {time.time()-t0:.0f}s")
