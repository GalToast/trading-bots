#!/usr/bin/env python3
"""FX H1 EXPANSION — Add H1 to GBPUSD, EURUSD, USDJPY cascade"""
import MetaTrader5 as mt5
import sys
sys.path.insert(0, 'scripts')
from test_multi_tf_stacking import run_ema_controller_cascade

mt5.initialize()
days = 30

print("FX H1 EXPANSION TEST\n")

top_fx = ["GBPUSD", "EURUSD", "USDJPY"]
all_results = []

for sym in top_fx:
    print(f"--- {sym} ---", flush=True)
    b5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)]
    b15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)]
    b60 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 24*days)]
    
    info = mt5.symbol_info(sym)
    pip = info.point * 10 if info.digits in [3,5] else info.point
    total_hrs = len(b15)*15/60
    
    best_2tf = None
    best_3tf = None
    best_2hr = -999
    best_3hr = -999
    
    # 2-TF sweep (M5+M15)
    for sp5 in [0.5, 1.0]:
        for sp15 in [0.5, 1.0]:
            for hf in [0, 1]:
                s5 = run_ema_controller_cascade(sym, b5, {"base_step":sp5*pip,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True})
                s15 = run_ema_controller_cascade(sym, b15, {"base_step":sp15*pip,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True})
                if s5 and s15:
                    hr = (s5.realized_net_usd + s15.realized_net_usd) / total_hrs
                    if hr > best_2hr:
                        best_2hr = hr
                        best_2tf = (sp5, sp15, hf, s5.realized_net_usd/total_hrs, s15.realized_net_usd/total_hrs, s5.realized_closes, s15.realized_closes)
    
    # 3-TF sweep (M5+M15+H1)
    for sp5 in [0.5, 1.0]:
        for sp15 in [0.5, 1.0]:
            for sp60 in [1.0, 2.0, 5.0]:
                for hf in [0, 1]:
                    s5 = run_ema_controller_cascade(sym, b5, {"base_step":sp5*pip,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True})
                    s15 = run_ema_controller_cascade(sym, b15, {"base_step":sp15*pip,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True})
                    s60 = run_ema_controller_cascade(sym, b60, {"base_step":sp60*pip,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True})
                    if s5 and s15 and s60:
                        hr = (s5.realized_net_usd + s15.realized_net_usd + s60.realized_net_usd) / total_hrs
                        if hr > best_3hr:
                            best_3hr = hr
                            best_3tf = (sp5, sp15, sp60, hf, s5.realized_net_usd/total_hrs, s15.realized_net_usd/total_hrs, s60.realized_net_usd/total_hrs, s5.realized_closes+s15.realized_closes+s60.realized_closes)
    
    if best_2tf:
        sp5, sp15, hf, hr5, hr15, c5, c15 = best_2tf
        print(f"  2-TF: M5={sp5}p + M15={sp15}p hf={hf} = ${best_2hr:.2f}/hr")
        print(f"        M5=${hr5:.2f}, M15=${hr15:.2f}")
    
    if best_3tf:
        sp5, sp15, sp60, hf, hr5, hr15, hr60, tc = best_3tf
        print(f"  3-TF: M5={sp5}p + M15={sp15}p + H1={sp60}p hf={hf} = ${best_3hr:.2f}/hr")
        print(f"        M5=${hr5:.2f}, M15=${hr15:.2f}, H1=${hr60:.2f}, closes={tc}")
        
        gain = best_3hr - best_2hr if best_2tf else 0
        print(f"  H1 adds: ${gain:+.2f}/hr ({gain/max(best_2hr,0.01)*100:+.1f}%)")
    
    all_results.append((sym, best_3hr if best_3tf else best_2hr))
    print()

print("="*60)
print("FX H1 EXPANSION SUMMARY")
print("="*60)
total = sum(r[1] for r in all_results)
for sym, hr in all_results:
    print(f"  {sym:<10} ${hr:>8.2f}/hr")
print(f"  {'='*20}")
print(f"  {'TOTAL':<10} ${total:>8.2f}/hr")

mt5.shutdown()
