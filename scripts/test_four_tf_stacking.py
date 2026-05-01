#!/usr/bin/env python3
"""FOUR-TIMEFRAME STACKING — M1 + M5 + M15 + H1 all on BTC"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_multi_tf_stacking import compute_ema, Ticket, run_ema_controller_cascade, SymbolState

mt5.initialize()
symbol = "BTCUSD"
days = 30

print(f"=== FOUR-TIMEFRAME STACKING: {symbol} {days} days ===\n")

# Load all timeframes
print("Loading data...", flush=True)
bars1_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 24*60*days)
bars5_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 24*12*days)
bars15_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24*4*days)
bars60_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 24*days)

def mk(bars): return [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in bars]
bars1 = mk(bars1_raw)
bars5 = mk(bars5_raw)
bars15 = mk(bars15_raw)
bars60 = mk(bars60_raw)

total_hrs = len(bars15) * 15 / 60
print(f"M1: {len(bars1)} bars, M5: {len(bars5)}, M15: {len(bars15)}, H1: {len(bars60)}")
print(f"Total hours: {total_hrs:.0f}\n")

# Configs to test
configs = [
    ("M5=$150 M15=$50 H1=$200 hf=1",
     {"base_step":150,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     {"base_step":50,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     {"base_step":200,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     None),
    ("M5=$150 M15=$50 hf=1 (no H1)",
     {"base_step":150,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     {"base_step":50,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     None, None),
    ("M1=$75 M5=$150 M15=$50 hf=1",
     {"base_step":75,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     {"base_step":150,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     {"base_step":50,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     None),
    ("M5=$150 M15=$75 H1=$300 hf=1",
     {"base_step":150,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     {"base_step":75,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     {"base_step":300,"controller_mode":"ema_ribbon","hold_frontier":1,"max_open_per_side":60,"rebase_on_flat":True},
     None),
]

for label, cfg5, cfg15, cfg60, cfg1 in configs:
    print(f"Running: {label}...", flush=True)
    s5 = run_ema_controller_cascade(symbol, bars5, cfg5)
    s15 = run_ema_controller_cascade(symbol, bars15, cfg15)
    
    net5 = s5.realized_net_usd
    net15 = s15.realized_net_usd
    net60 = 0
    net1 = 0
    closes5 = s5.realized_closes
    closes15 = s15.realized_closes
    closes60 = 0
    closes1 = 0

    if cfg60:
        s60 = run_ema_controller_cascade(symbol, bars60, cfg60)
        net60 = s60.realized_net_usd
        closes60 = s60.realized_closes
    
    if cfg1:
        s1 = run_ema_controller_cascade(symbol, bars1, cfg1)
        net1 = s1.realized_net_usd
        closes1 = s1.realized_closes

    combined = net5 + net15 + net60 + net1
    total_closes = closes5 + closes15 + closes60 + closes1
    combined_hr = combined / total_hrs
    avg = combined / total_closes if total_closes > 0 else 0
    
    parts = []
    if cfg1: parts.append(f"M1=${net1/total_hrs:.2f}")
    parts.append(f"M5=${net5/total_hrs:.2f}")
    parts.append(f"M15=${net15/total_hrs:.2f}")
    if cfg60: parts.append(f"H1=${net60/total_hrs:.2f}")
    
    print(f"  {' + '.join(parts)} = COMBINED ${combined_hr:.2f}/hr ({total_closes}c, ${avg:.2f}/close)\n")

mt5.shutdown()
