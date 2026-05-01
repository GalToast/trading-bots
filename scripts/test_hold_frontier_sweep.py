#!/usr/bin/env python3
"""HOLD FRONTIER SWEEP — testing hf=3,4,5,6 on BTC M15"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_multi_tf_stacking import run_ema_controller_cascade

mt5.initialize()
symbol = "BTCUSD"
days = 30
bars15_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24*4*days)
bars15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars15_raw]
total_hrs = len(bars15)*15/60

print(f"HOLD FRONTIER SWEEP: {symbol} M15, {days} days\n")

configs = []
for step in [25, 50, 75]:
    for hf in [0, 1, 2, 3, 4, 5, 6]:
        configs.append({
            "label": f"step={step} hf={hf}",
            "base_step": float(step), "controller_mode": "ema_ribbon",
            "hold_frontier": hf, "max_open_per_side": 60, "rebase_on_flat": True,
        })

results = []
for cfg in configs:
    s = run_ema_controller_cascade(symbol, bars15, cfg)
    net = s.realized_net_usd
    closes = s.realized_closes
    per_hr = net / total_hrs
    avg = net / closes if closes > 0 else 0
    results.append((cfg["label"], {"net": net, "closes": closes, "per_hr": per_hr, "avg": avg}))
    print(f"  {cfg['label']}: ${per_hr:.2f}/hr, {closes}c, ${avg:.2f}/close")

results.sort(key=lambda x: x[1]["per_hr"], reverse=True)
print(f"\n{'Config':<20} {'$/hr':>9} {'Closes':>7} {'$/close':>9}")
print("-" * 50)
for label, r in results[:10]:
    print(f"{label:<20} ${r['per_hr']:>8.2f} {r['closes']:>7} ${r['avg']:>8.2f}")

mt5.shutdown()
