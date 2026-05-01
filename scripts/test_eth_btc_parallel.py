#!/usr/bin/env python3
"""ETH + BTC PARALLEL LATTICES — two symbols, same architecture"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_multi_tf_stacking import run_ema_controller_cascade

mt5.initialize()
days = 30

print(f"=== ETH + BTC PARALLEL LATTICES: {days} days ===\n")

# Load data
print("Loading data...", flush=True)
symbols_cfg = {
    "BTCUSD": {"tf": mt5.TIMEFRAME_M15, "step": 50.0},
    "ETHUSD": {"tf": mt5.TIMEFRAME_M15, "step": 5.0},  # ETH step smaller
}

all_bars = {}
for sym, cfg in symbols_cfg.items():
    raw = mt5.copy_rates_from_pos(sym, cfg["tf"], 0, 24*4*days)
    all_bars[sym] = [{"time":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4])} for r in raw]
    print(f"  {sym}: {len(all_bars[sym])} bars, step=${cfg['step']:.2f}")

total_hrs = len(all_bars["BTCUSD"])*15/60
print(f"Total hours: {total_hrs:.0f}\n")

# Test different ETH step sizes
eth_steps = [3.0, 5.0, 7.0, 10.0, 15.0]
btc_steps = [50.0, 75.0]
hf_values = [1, 2]

configs = []
for es in eth_steps:
    for bs in btc_steps:
        for hf in hf_values:
            label = f"ETH=${es:.0f} BTC=${bs:.0f} hf={hf}"
            cfg_eth = {"base_step":es,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True}
            cfg_btc = {"base_step":bs,"controller_mode":"ema_ribbon","hold_frontier":hf,"max_open_per_side":60,"rebase_on_flat":True}
            configs.append((label, cfg_eth, cfg_btc))

results = []
for label, cfg_eth, cfg_btc in configs:
    s_eth = run_ema_controller_cascade("ETHUSD", all_bars["ETHUSD"], cfg_eth)
    s_btc = run_ema_controller_cascade("BTCUSD", all_bars["BTCUSD"], cfg_btc)
    
    net_eth = s_eth.realized_net_usd
    net_btc = s_btc.realized_net_usd
    per_hr_eth = net_eth/total_hrs
    per_hr_btc = net_btc/total_hrs
    combined = net_eth + net_btc
    combined_hr = combined/total_hrs
    total_closes = s_eth.realized_closes + s_btc.realized_closes
    avg = combined/total_closes if total_closes>0 else 0
    
    results.append((label, {"eth_hr":per_hr_eth,"btc_hr":per_hr_btc,"combined_hr":combined_hr,
                            "eth_closes":s_eth.realized_closes,"btc_closes":s_btc.realized_closes,
                            "total_closes":total_closes,"avg":avg}))
    print(f"  {label}: ETH=${per_hr_eth:.2f}/hr, BTC=${per_hr_btc:.2f}/hr, COMBINED=${combined_hr:.2f}/hr")

results.sort(key=lambda x: x[1]["combined_hr"], reverse=True)

print(f"\n{'Config':<30} {'ETH $/hr':>9} {'BTC $/hr':>9} {'Combined':>9} {'Closes':>7}")
print("-" * 75)
for label, r in results[:10]:
    print(f"{label:<30} ${r['eth_hr']:>8.2f} ${r['btc_hr']:>8.2f} ${r['combined_hr']:>8.2f} {r['total_closes']:>7}")

if results:
    best = results[0]
    print(f"\nBEST: {best[0]} → ${best[1]['combined_hr']:.2f}/hr")

mt5.shutdown()
