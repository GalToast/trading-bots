#!/usr/bin/env python3
"""
ULTIMATE COMBINED $/HR — All winners, multi-timeframe on top symbols

BTC M5+M15+H1 + ETH M15 + GBPUSD M5+M15 + EURUSD M5+M15 + rest of FX
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_multi_tf_stacking import run_ema_controller_cascade

mt5.initialize()
days = 30

print("=== ULTIMATE COMBINED $/HR ===\n")

# Load all data
print("Loading data...", flush=True)
btc5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
        for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M5, 0, 24*12*days)]
btc15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
btc60 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_H1, 0, 24*days)]
eth15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("ETHUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]

# FX data
gbp5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
        for r in mt5.copy_rates_from_pos("GBPUSD", mt5.TIMEFRAME_M5, 0, 24*12*days)]
gbp15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("GBPUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
eur5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
        for r in mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_M5, 0, 24*12*days)]
eur15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
aud15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("AUDUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
usd15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("USDCAD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
nzd15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("NZDUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
jpy15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("USDJPY", mt5.TIMEFRAME_M15, 0, 24*4*days)]

total_hrs = len(btc15)*15/60
print(f"Total hours: {total_hrs:.0f}\n")

# BTC configs (from $100+ test)
cfg_btc5 = {"base_step":150,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_btc15 = {"base_step":50,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_btc60 = {"base_step":200,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_eth = {"base_step":3,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}

# FX configs (from all-symbol sweep winners)
cfg_gbp5 = {"base_step":0.00005,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_gbp15 = {"base_step":0.00005,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_eur5 = {"base_step":0.00005,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_eur15 = {"base_step":0.00005,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_aud = {"base_step":0.0001,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_usd = {"base_step":0.00005,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_nzd = {"base_step":0.0005,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_jpy = {"base_step":0.0005,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}

print("Running all lanes...", flush=True)

# BTC lanes
s_btc5 = run_ema_controller_cascade("BTCUSD", btc5, cfg_btc5)
s_btc15 = run_ema_controller_cascade("BTCUSD", btc15, cfg_btc15)
s_btc60 = run_ema_controller_cascade("BTCUSD", btc60, cfg_btc60)
s_eth = run_ema_controller_cascade("ETHUSD", eth15, cfg_eth)

# FX lanes
s_gbp5 = run_ema_controller_cascade("GBPUSD", gbp5, cfg_gbp5)
s_gbp15 = run_ema_controller_cascade("GBPUSD", gbp15, cfg_gbp15)
s_eur5 = run_ema_controller_cascade("EURUSD", eur5, cfg_eur5)
s_eur15 = run_ema_controller_cascade("EURUSD", eur15, cfg_eur15)
s_aud = run_ema_controller_cascade("AUDUSD", aud15, cfg_aud)
s_usd = run_ema_controller_cascade("USDCAD", usd15, cfg_usd)
s_nzd = run_ema_controller_cascade("NZDUSD", nzd15, cfg_nzd)
s_jpy = run_ema_controller_cascade("USDJPY", jpy15, cfg_jpy)

# Results
results = {
    "BTC M5": (s_btc5, "BTCUSD"),
    "BTC M15": (s_btc15, "BTCUSD"),
    "BTC H1": (s_btc60, "BTCUSD"),
    "ETH M15": (s_eth, "ETHUSD"),
    "GBP M5": (s_gbp5, "GBPUSD"),
    "GBP M15": (s_gbp15, "GBPUSD"),
    "EUR M5": (s_eur5, "EURUSD"),
    "EUR M15": (s_eur15, "EURUSD"),
    "AUD M15": (s_aud, "AUDUSD"),
    "USDCAD M15": (s_usd, "USDCAD"),
    "NZD M15": (s_nzd, "NZDUSD"),
    "USDJPY M15": (s_jpy, "USDJPY"),
}

total_net = 0
total_closes = 0

print(f"{'Lane':<15} {'$/hr':>9} {'Closes':>7} {'$/close':>9}")
print("-" * 45)
for name, (state, sym) in results.items():
    net = state.realized_net_usd
    closes = state.realized_closes
    per_hr = net / total_hrs
    avg = net / closes if closes > 0 else 0
    total_net += net
    total_closes += closes
    print(f"{name:<15} ${per_hr:>8.2f} {closes:>7} ${avg:>8.2f}")

combined_hr = total_net / total_hrs
combined_avg = total_net / total_closes if total_closes > 0 else 0

print("=" * 45)
print(f"{'COMBINED':<15} ${combined_hr:>8.2f} {total_closes:>7} ${combined_avg:>8.2f}")
print(f"\nPer day (24h):  ${combined_hr*24:.2f}")
print(f"Per month (720h): ${combined_hr*720:.2f}")
print(f"\nvs baseline cascade ($5.28/hr): {combined_hr/5.28:.0f}x improvement")

mt5.shutdown()
