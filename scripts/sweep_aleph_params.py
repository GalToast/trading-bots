#!/usr/bin/env python3
"""Parameter sweep for ALEPH-USD — the only coin with positive edges in the opportunity sweep."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from strategy_library import range_breakout, momentum, rsi_mr, bb_reversion, vol_squeeze, ema_pullback

with open(os.path.join(os.path.dirname(__file__), "..", "reports", "candle_cache", "ALEPH_USD_FIVE_MINUTE_30d.json")) as f:
    data = json.load(f)

candles = [
    {"open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
     "close": float(c["close"]), "start": int(c.get("start", c.get("time", 0))),
     "volume": float(c.get("volume", 0))}
    for c in data["candles"]
]
print(f"ALEPH-USD: {len(candles)} candles")

# ---- RANGE BREAKOUT sweep ----
print("\n=== RANGE BREAKOUT ===")
all_rb = []
for lb in [5, 8, 10, 12, 15, 20, 30, 50]:
    for tp in [2, 3, 5, 8, 10, 12, 15, 20, 25, 30]:
        for sl in [0, 2, 3, 5, 8, 10]:
            for mh in [12, 24, 36, 48]:
                r = range_breakout(candles, range_lookback=lb, tp_pct=tp, sl_pct=sl, max_hold=mh,
                                   starting_cash=48.0, fee_rate=0.004)
                all_rb.append((r["net_pnl"], lb, tp, sl, mh, r))

all_rb.sort(reverse=True)
profitable = [x for x in all_rb if x[0] > 0]
print(f"Combos: {len(all_rb)}, Profitable: {len(profitable)}")
for i, (pnl, lb, tp, sl, mh, r) in enumerate(all_rb[:15], 1):
    print(f"  {i:2d}. lb={lb:3d} tp={tp:3d}% sl={sl:3d}% mh={mh:3d}  PnL=${pnl:8.2f}  WR={r['win_rate']:5.1f}%  Trades={r['trades']:4d}  DD={r['max_drawdown']:.1f}%")

# ---- MOMENTUM sweep ----
print("\n=== MOMENTUM ===")
all_mom = []
for lb in [5, 10, 15, 20, 25, 30, 50, 75, 100]:
    for tp in [2, 3, 5, 8, 10, 12, 15, 20, 25, 30]:
        for sl in [0, 2, 3, 5, 8, 10]:
            for mh in [12, 24, 36, 48]:
                r = momentum(candles, lookback=lb, tp_pct=tp, sl_pct=sl, max_hold=mh,
                             starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                all_mom.append((r["net_pnl"], lb, tp, sl, mh, r))

all_mom.sort(reverse=True)
profitable_m = [x for x in all_mom if x[0] > 0]
print(f"Combos: {len(all_mom)}, Profitable: {len(profitable_m)}")
for i, (pnl, lb, tp, sl, mh, r) in enumerate(all_mom[:15], 1):
    print(f"  {i:2d}. lb={lb:3d} tp={tp:3d}% sl={sl:3d}% mh={mh:3d}  PnL=${pnl:8.2f}  WR={r['win_rate']:5.1f}%  Trades={r['trades']:4d}  DD={r['max_drawdown']:.1f}%")

# ---- RSI MR sweep ----
print("\n=== RSI MEAN REVERSION ===")
all_rsi = []
for rp in [2, 3, 4, 5, 7, 10]:
    for ot in [15, 20, 25, 30, 35]:
        for tp in [5, 10, 15, 20, 25, 30, 40, 50]:
            for sl in [0, 3, 5, 8]:
                for mh in [12, 24, 36, 48]:
                    r = rsi_mr(candles, rsi_period=rp, os_thresh=ot, tp_pct=tp, sl_pct=sl, max_hold=mh,
                               starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                    all_rsi.append((r["net_pnl"], rp, ot, tp, sl, mh, r))

all_rsi.sort(reverse=True)
profitable_r = [x for x in all_rsi if x[0] > 0]
print(f"Combos: {len(all_rsi)}, Profitable: {len(profitable_r)}")
for i, (pnl, rp, ot, tp, sl, mh, r) in enumerate(all_rsi[:10], 1):
    print(f"  {i:2d}. rsi={rp} os={ot} tp={tp:3d}% sl={sl:3d}% mh={mh:3d}  PnL=${pnl:8.2f}  WR={r['win_rate']:5.1f}%  Trades={r['trades']:4d}")

# ---- BB REVERSION sweep ----
print("\n=== BB REVERSION ===")
all_bb = []
for bp in [14, 20, 30, 50]:
    for rp in [2, 3, 5]:
        for ot in [15, 20, 25, 30]:
            for px in [1, 2, 3, 5]:
                for sl in [3, 5, 8]:
                    for mh in [12, 24, 48]:
                        r = bb_reversion(candles, bb_period=bp, rsi_period=rp, rsi_thresh=ot,
                                         proximity_pct=px, sl_pct=sl, max_hold=mh,
                                         starting_cash=48.0, fee_rate=0.004, entry_slip=0.0, exit_slip=0.0, fill_prob=1.0)
                        all_bb.append((r["net_pnl"], bp, rp, ot, px, sl, mh, r))

all_bb.sort(reverse=True)
profitable_b = [x for x in all_bb if x[0] > 0]
print(f"Combos: {len(all_bb)}, Profitable: {len(profitable_b)}")
for i, (pnl, bp, rp, ot, px, sl, mh, r) in enumerate(all_bb[:10], 1):
    print(f"  {i:2d}. bb={bp:3d} rsi={rp} ot={ot:3d} px={px:3d}% sl={sl:3d}% mh={mh:3d}  PnL=${pnl:8.2f}  WR={r['win_rate']:5.1f}%  Trades={r['trades']:4d}")

print("\n=== SUMMARY ===")
print(f"Best Range Breakout:  ${all_rb[0][0]:8.2f} (lb={all_rb[0][1]}, tp={all_rb[0][2]}%, sl={all_rb[0][3]}%, mh={all_rb[0][4]})")
print(f"Best Momentum:        ${all_mom[0][0]:8.2f} (lb={all_mom[0][1]}, tp={all_mom[0][2]}%, sl={all_mom[0][3]}%, mh={all_mom[0][4]})")
if all_rsi:
    print(f"Best RSI MR:          ${all_rsi[0][0]:8.2f} (rsi={all_rsi[0][1]}, os={all_rsi[0][2]}, tp={all_rsi[0][3]}%)")
if all_bb:
    print(f"Best BB Reversion:    ${all_bb[0][0]:8.2f} (bb={all_bb[0][1]}, rsi={all_bb[0][2]}, px={all_bb[0][4]}%)")
