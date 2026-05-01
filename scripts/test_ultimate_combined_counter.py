#!/usr/bin/env python3
"""
THE ULTIMATE COMBINED TEST — Everything we've discovered

Crypto (hf=1):
  BTC M5 ($150) + M15 ($50) + H1 ($200)
  ETH M15 ($3)

FX with Counter-Trend Cascade (hf=0):
  GBPUSD M5 (0.5p) + M15 (0.5p) + COUNTER
  EURUSD M5 (0.5p) + M15 (0.5p) + COUNTER
  USDJPY M5 (1.0p) + M15 (0.5p) + COUNTER
  AUDUSD M5 (0.5p) + M15 (0.5p) + COUNTER
  NZDUSD M5 (0.5p) + M15 (0.5p) + COUNTER
  USDCAD M5 (0.5p) + M15 (0.5p) + COUNTER

Plus H1 for top 3 FX.

This is the CEILING-SHATTERING configuration.
"""
import MetaTrader5 as mt5
import sys
sys.path.insert(0, 'scripts')
from test_multi_tf_stacking import run_ema_controller_cascade
from test_fx_counter_trend import run_counter_cascade_fx

mt5.initialize()
days = 30
print("="*70)
print("THE ULTIMATE COMBINED $/HR — ALL SYMBOLS, ALL SEAMS")
print("="*70)

# Load all data
print("\nLoading...", flush=True)
btc5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M5, 0, 24*12*days)]
btc15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
btc60 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_H1, 0, 24*days)]
eth15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos("ETHUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]

fx_m5 = {}; fx_m15 = {}; fx_h1 = {}
for sym in ["GBPUSD","EURUSD","USDJPY","AUDUSD","NZDUSD","USDCAD"]:
    fx_m5[sym] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 24*12*days)]
    fx_m15[sym] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 24*4*days)]
    fx_h1[sym] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 24*days)]

total_hrs = len(btc15)*15/60
print(f"Total hours: {total_hrs:.0f}")
print(f"Data: BTC({len(btc5)}M5,{len(btc15)}M15,{len(btc60)}H1), ETH({len(eth15)}M15), 6 FX(M5+M15+H1)")

# Run crypto
print("\n--- CRYPTO ---", flush=True)
s_btc5 = run_ema_controller_cascade("BTCUSD", btc5, {"base_step":150,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True})
s_btc15 = run_ema_controller_cascade("BTCUSD", btc15, {"base_step":50,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True})
s_btc60 = run_ema_controller_cascade("BTCUSD", btc60, {"base_step":200,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True})
s_eth = run_ema_controller_cascade("ETHUSD", eth15, {"base_step":3,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True})

crypto_total = 0
for name, s in [("BTC M5", s_btc5), ("BTC M15", s_btc15), ("BTC H1", s_btc60), ("ETH M15", s_eth)]:
    hr = s.realized_net_usd/total_hrs
    crypto_total += hr
    print(f"  {name:<12} ${hr:>8.2f}/hr ({s.realized_closes}c)")

# Run FX with counter-trend cascade
print("\n--- FX (COUNTER-TREND CASCADE) ---", flush=True)
fx_configs = [
    ("GBPUSD", 0.00005, 0.00005, 0.0001),  # M5 step, M15 step, H1 step
    ("EURUSD", 0.00005, 0.00005, 0.0001),
    ("USDJPY", 0.001, 0.0005, 0.0005),
    ("AUDUSD", 0.00005, 0.00005, 0.0001),
    ("NZDUSD", 0.00005, 0.00005, 0.0003),
    ("USDCAD", 0.00005, 0.00005, 0.0001),
]

fx_total = 0
for sym, sp5, sp15, sp60 in fx_configs:
    # M5 with counter
    c5 = {"base_step":sp5,"controller_mode":"ema_ribbon","hold_frontier":0,"max_open_per_side":60,
          "counter_on_cascade":True,"counter_close_step":1,"tf_seconds":300}
    r5 = run_counter_cascade_fx(sym, fx_m5[sym], c5)
    
    # M15 with counter
    c15 = {"base_step":sp15,"controller_mode":"ema_ribbon","hold_frontier":0,"max_open_per_side":60,
           "counter_on_cascade":True,"counter_close_step":1,"tf_seconds":900}
    r15 = run_counter_cascade_fx(sym, fx_m15[sym], c15)
    
    # H1 without counter (counter less effective on longer TFs)
    c60 = {"base_step":sp60,"controller_mode":"ema_ribbon","hold_frontier":0,"max_open_per_side":60,"rebase_on_flat":True}
    s60 = run_ema_controller_cascade(sym, fx_h1[sym], c60)
    
    hr5 = r5["per_hr"] if r5 else 0
    hr15 = r15["per_hr"] if r15 else 0
    hr60 = s60.realized_net_usd/total_hrs if s60 else 0
    
    combined = hr5 + hr15 + hr60
    fx_total += combined
    c5_count = r5["closes"] if r5 else 0
    c15_count = r15["closes"] if r15 else 0
    c60_count = s60.realized_closes if s60 else 0
    print(f"  {sym:<8} M5=${hr5:>7.2f} M15=${hr15:>7.2f} H1=${hr60:>7.2f} = ${combined:>8.2f}/hr ({c5_count+c15_count+c60_count}c)")

grand_total = crypto_total + fx_total
print(f"\n{'='*70}")
print(f"CRYPTO SUBTOTAL: ${crypto_total:.2f}/hr")
print(f"FX SUBTOTAL:     ${fx_total:.2f}/hr")
print(f"{'='*70}")
print(f"GRAND TOTAL:     ${grand_total:.2f}/hr")
print(f"Per day (24h):   ${grand_total*24:,.2f}")
print(f"Per month (720h): ${grand_total*720:,.2f}")
print(f"\nvs baseline cascade ($5.28/hr): {grand_total/5.28:.0f}x improvement")

mt5.shutdown()
