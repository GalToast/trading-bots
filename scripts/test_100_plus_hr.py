#!/usr/bin/env python3
"""THE $100+ TEST: M5+M15+H1 on BTC + ETH at $3 — all combined"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_multi_tf_stacking import run_ema_controller_cascade

mt5.initialize()
days = 30

print("=== THE $100+ TEST: BTC(M5+M15+H1) + ETH ===\n")

# Load all data
print("Loading...", flush=True)
btc5 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
        for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M5, 0, 24*12*days)]
btc15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]
btc60 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_H1, 0, 24*days)]
eth15 = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} 
         for r in mt5.copy_rates_from_pos("ETHUSD", mt5.TIMEFRAME_M15, 0, 24*4*days)]

total_hrs = len(btc15)*15/60
print(f"BTC M5:{len(btc5)} M15:{len(btc15)} H1:{len(btc60)}, ETH M15:{len(eth15)}, Total:{total_hrs:.0f}hrs\n")

# Best configs from individual tests
cfg_btc5 = {"base_step":150,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_btc15 = {"base_step":50,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_btc60 = {"base_step":200,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}
cfg_eth = {"base_step":3,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True}

print("Running BTC M5...", flush=True)
s5 = run_ema_controller_cascade("BTCUSD", btc5, cfg_btc5)
print("Running BTC M15...", flush=True)
s15 = run_ema_controller_cascade("BTCUSD", btc15, cfg_btc15)
print("Running BTC H1...", flush=True)
s60 = run_ema_controller_cascade("BTCUSD", btc60, cfg_btc60)
print("Running ETH M15...", flush=True)
seth = run_ema_controller_cascade("ETHUSD", eth15, cfg_eth)

net5 = s5.realized_net_usd
net15 = s15.realized_net_usd
net60 = s60.realized_net_usd
net_eth = seth.realized_net_usd

hr5 = net5/total_hrs
hr15 = net15/total_hrs
hr60 = net60/total_hrs
hr_eth = net_eth/total_hrs

combined = net5 + net15 + net60 + net_eth
combined_hr = combined / total_hrs
total_closes = s5.realized_closes + s15.realized_closes + s60.realized_closes + seth.realized_closes
avg = combined / total_closes if total_closes > 0 else 0

print(f"\nBTC M5:  ${hr5:.2f}/hr ({s5.realized_closes} closes)")
print(f"BTC M15: ${hr15:.2f}/hr ({s15.realized_closes} closes)")
print(f"BTC H1:  ${hr60:.2f}/hr ({s60.realized_closes} closes)")
print(f"ETH M15: ${hr_eth:.2f}/hr ({seth.realized_closes} closes)")
print(f"-" * 40)
print(f"COMBINED: ${combined_hr:.2f}/hr ({total_closes} closes, ${avg:.2f}/close)")
print(f"\nvs baseline M15 cascade ($5.28/hr): {combined_hr/5.28:.0f}x improvement")

mt5.shutdown()
