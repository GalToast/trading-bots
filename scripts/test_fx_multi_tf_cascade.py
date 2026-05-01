#!/usr/bin/env python3
"""
FX MULTI-TIMEFRAME CASCADE — M5 + M15 on ALL FX symbols

Since M1 cascade fails (spread death spiral), M5+M15 is the ceiling.
Testing all 6 FX symbols with step sweep.
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_multi_tf_stacking import run_ema_controller_cascade

mt5.initialize()
days = 30

fx_symbols = ["GBPUSD", "EURUSD", "AUDUSD", "USDCAD", "NZDUSD", "USDJPY"]

print(f"=== FX MULTI-TIMEFRAME CASCADE: {days} days ===\n")

all_results = []

for sym in fx_symbols:
    print(f"\n--- {sym} ---", flush=True)
    
    # Load M5 and M15
    bars5_raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)
    bars15_raw = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)
    
    if bars5_raw is None or bars15_raw is None:
        print(f"  NO DATA")
        continue
    
    bars5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars5_raw]
    bars15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars15_raw]
    total_hrs = len(bars15)*15/60
    
    info = mt5.symbol_info(sym)
    pip = info.point * 10 if info.digits in [3,5] else info.point
    
    # Step sizes for FX (in pips)
    steps_pips = [0.5, 1.0, 2.0, 3.0, 5.0]
    best_combo = None
    best_hr = -999
    
    for sp5 in steps_pips:
        for sp15 in steps_pips:
            for hf in [0, 1]:
                step5 = sp5 * pip
                step15 = sp15 * pip
                cfg5 = {"base_step":step5,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True}
                cfg15 = {"base_step":step15,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True}
                
                s5 = run_ema_controller_cascade(sym, bars5, cfg5)
                s15 = run_ema_controller_cascade(sym, bars15, cfg15)
                
                if s5 and s15:
                    net5 = s5.realized_net_usd
                    net15 = s15.realized_net_usd
                    combined_hr = (net5 + net15) / total_hrs
                    
                    if combined_hr > best_hr:
                        best_hr = combined_hr
                        best_combo = (sp5, sp15, hf, net5/total_hrs, net15/total_hrs, s5.realized_closes, s15.realized_closes)
    
    if best_combo:
        sp5, sp15, hf, hr5, hr15, c5, c15 = best_combo
        print(f"  BEST: M5={sp5}p + M15={sp15}p hf={hf} → ${best_hr:.2f}/hr")
        print(f"    M5: ${hr5:.2f}/hr ({c5}c), M15: ${hr15:.2f}/hr ({c15}c)")
        all_results.append((sym, best_hr, sp5, sp15, hf, hr5, hr15, c5+c15))

print(f"\n{'='*70}")
print(f"FX MULTI-TIMEFRAME SUMMARY (sorted by $/hr)")
print(f"{'='*70}")
print(f"{'Symbol':<10} {'M5':>6} {'M15':>6} {'hf':>3} {'M5 $/hr':>9} {'M15 $/hr':>9} {'Combined':>9} {'Closes':>7}")
print("-" * 70)
all_results.sort(key=lambda x: x[1], reverse=True)
for sym, hr, sp5, sp15, hf, hr5, hr15, closes in all_results:
    print(f"{sym:<10} {sp5:>5}p {sp15:>5}p {hf:>3} ${hr5:>8.2f} ${hr15:>8.2f} ${hr:>8.2f} {closes:>7}")

total_fx = sum(r[1] for r in all_results)
print(f"\nTotal FX combined: ${total_fx:.2f}/hr")
print(f"Per day: ${total_fx*24:.2f}")
print(f"Per month: ${total_fx*720:.2f}")

# Add BTC+ETH from earlier
crypto_total = 43.21 + 46.54 + 9.05 + 25.72  # BTC M5+M15+H1 + ETH M15
grand_total = total_fx + crypto_total
print(f"\n+ Crypto (BTC M5+M15+H1 + ETH): ${crypto_total:.2f}/hr")
print(f"GRAND TOTAL: ${grand_total:.2f}/hr")
print(f"Per month: ${grand_total*720:.2f}")

mt5.shutdown()
