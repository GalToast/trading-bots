#!/usr/bin/env python3
"""Independent verification of headline claims through ground-truth engine.

Verifying:
1. RAVE-USD momentum lb=15, tp=10%, sl=0% — claimed $994, 80.2% WR
2. MOG-USD RSI MR RSI=4, OS=45, TP=7.5%, SL=0.5% — claimed $668, 33.9% WR
3. TROLL, SUP, MDT 30d momentum sweep (3 coins from my 7d B-tier list)
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

print("=" * 70)
print("HEADLINE CLAIM VERIFICATION — Ground Truth Engine")
print("=" * 70)

# 1. RAVE momentum lb=15/tp=10/sl=0
rave = load("RAVE-USD")
if rave:
    print(f"\nRAVE-USD: {len(rave)} candles")
    r = momentum(rave, lookback=15, tp_pct=10, sl_pct=0, max_hold=48,
                 starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    print(f"  lb=15, tp=10%, sl=0%, mh=48: PnL=${r['net_pnl']:.2f}  WR={r['win_rate']}%  Trades={r['trades']}  Signals={r['signals']}")
    claimed_pnl, claimed_wr, claimed_trades = 994, 80.2, 86
    print(f"  Claimed:          PnL=${claimed_pnl:.2f}  WR={claimed_wr}%  Trades={claimed_trades}")
    print(f"  Gap:              PnL=${r['net_pnl']-claimed_pnl:.2f}  WR={r['win_rate']-claimed_wr:.1f}%  Trades={r['trades']-claimed_trades}")
    if abs(r['net_pnl'] - claimed_pnl) < 50:
        print(f"  ✅ VERIFIED (within tolerance)")
    else:
        print(f"  ⚠️ DISCREPANCY — significant gap")
    
    # Also test my earlier best (lb=10/sl=5)
    r2 = momentum(rave, lookback=10, tp_pct=10, sl_pct=5, max_hold=48,
                  starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    print(f"  My earlier best (lb=10/tp=10/sl=5): PnL=${r2['net_pnl']:.2f}  WR={r2['win_rate']}%  Trades={r2['trades']}")

# 2. MOG RSI MR
mog = load("MOG-USD")
if mog:
    print(f"\nMOG-USD: {len(mog)} candles")
    r = rsi_mr(mog, rsi_period=4, os_thresh=45, tp_pct=7.5, sl_pct=0.5, max_hold=48,
               starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
    print(f"  RSI=4, OS=45, TP=7.5%, SL=0.5%: PnL=${r['net_pnl']:.2f}  WR={r['win_rate']}%  Trades={r['trades']}  Signals={r['signals']}")
    claimed_pnl, claimed_wr, claimed_trades = 668, 33.9, 127
    print(f"  Claimed:          PnL=${claimed_pnl:.2f}  WR={claimed_wr}%  Trades={claimed_trades}")
    print(f"  Gap:              PnL=${r['net_pnl']-claimed_pnl:.2f}  WR={r['win_rate']-claimed_wr:.1f}%  Trades={r['trades']-claimed_trades}")
    if abs(r['net_pnl'] - claimed_pnl) < 100:
        print(f"  ✅ VERIFIED (within tolerance)")
    else:
        print(f"  ⚠️ DISCREPANCY — significant gap")

    # Sweep MOG RSI MR params
    print(f"\n  MOG RSI MR param sweep...")
    mog_results = []
    for rp in [2, 3, 4, 5, 6, 7, 8, 10]:
        for ot in [20, 25, 30, 35, 40, 45, 50]:
            for tp in [3, 5, 7.5, 10, 12, 15, 20, 25]:
                for sl in [0, 0.5, 1, 2, 3, 5]:
                    for mh in [12, 24, 36, 48]:
                        mr = rsi_mr(mog, rsi_period=rp, os_thresh=ot, tp_pct=tp, sl_pct=sl, max_hold=mh,
                                    starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                        mog_results.append((mr["net_pnl"], rp, ot, tp, sl, mh, mr))
    mog_results.sort(reverse=True)
    profitable = [x for x in mog_results if x[0] > 0]
    print(f"  Combos: {len(mog_results)}, Profitable: {len(profitable)} ({len(profitable)/len(mog_results)*100:.1f}%)")
    for i, (pnl, rp, ot, tp, sl, mh, mr) in enumerate(mog_results[:5], 1):
        print(f"  {i}. rsi={rp} os={ot} tp={tp}% sl={sl}% mh={mh}  PnL=${pnl:.2f}  WR={mr['win_rate']}%  Trades={mr['trades']}")
else:
    print(f"\nMOG-USD: NO 30d candles found")

# 3. TROLL, SUP, MDT 30d momentum sweep
print(f"\n{'='*70}")
print("TROLL / SUP / MDT — 30d Momentum Sweep")
print(f"{'='*70}")
for coin in ["TROLL-USD", "SUP-USD", "MDT-USD"]:
    candles = load(coin)
    if not candles:
        print(f"\n{coin}: NO 30d candles")
        continue
    print(f"\n{coin}: {len(candles)} candles")
    results = []
    for lb in [5, 10, 15, 20, 25, 30, 50]:
        for tp in [3, 5, 8, 10, 12, 15, 20]:
            for sl in [0, 2, 3, 5, 8]:
                for mh in [12, 24, 36, 48]:
                    r = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl, max_hold=mh,
                                 starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                    results.append((r["net_pnl"], lb, tp, sl, mh, r))
    results.sort(reverse=True)
    profitable = [x for x in results if x[0] > 0]
    print(f"  Combos: {len(results)}, Profitable: {len(profitable)} ({len(profitable)/len(results)*100:.1f}%)")
    if results:
        best = results[0]
        print(f"  Best: lb={best[1]}, tp={best[2]}%, sl={best[3]}%, mh={best[4]}")
        print(f"  PnL=${best[0]:.2f}, WR={best[5]['win_rate']}%, Trades={best[5]['trades']}, DD={best[5]['max_drawdown']}%")
        # Also show worst
        print(f"  Worst: ${results[-1][0]:.2f}")

print(f"\n{'='*70}")
print("VERIFICATION COMPLETE")
