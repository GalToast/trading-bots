#!/usr/bin/env python3
"""Remaining FX symbols M5+M15 cascade"""
import MetaTrader5 as mt5
import sys
sys.path.insert(0, 'scripts')
from test_multi_tf_stacking import run_ema_controller_cascade

mt5.initialize()
days = 30
symbols = ["AUDUSD", "USDCAD", "NZDUSD", "USDJPY"]

for sym in symbols:
    print(f"\n--- {sym} ---", flush=True)
    b5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)]
    b15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)]
    hrs = len(b15)*15/60
    info = mt5.symbol_info(sym)
    pip = info.point * 10 if info.digits in [3,5] else info.point
    
    best_hr = -999
    best_cfg = None
    for sp5 in [0.5, 1.0, 2.0, 3.0]:
        for sp15 in [0.5, 1.0, 2.0, 3.0]:
            for hf in [0, 1]:
                c5 = {"base_step":sp5*pip,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True}
                c15 = {"base_step":sp15*pip,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True}
                s5 = run_ema_controller_cascade(sym, b5, c5)
                s15 = run_ema_controller_cascade(sym, b15, c15)
                if s5 and s15:
                    hr = (s5.realized_net_usd + s15.realized_net_usd) / hrs
                    if hr > best_hr:
                        best_hr = hr
                        best_cfg = (sp5, sp15, hf, s5.realized_net_usd/hrs, s15.realized_net_usd/hrs, s5.realized_closes, s15.realized_closes)
    
    if best_cfg:
        sp5, sp15, hf, hr5, hr15, c5, c15 = best_cfg
        print(f"  BEST: M5={sp5}p + M15={sp15}p hf={hf} -> ${best_hr:.2f}/hr")
        print(f"    M5: ${hr5:.2f}/hr ({c5}c), M15: ${hr15:.2f}/hr ({c15}c)")

mt5.shutdown()
