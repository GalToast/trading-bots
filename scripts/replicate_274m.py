#!/usr/bin/env python3
"""
Replicate $2.74M Multi-TF Stacking: M15+M5+H1 on BTCUSD
"""
from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5
mt5.initialize()

symbol = 'BTCUSD'

# Load bars for each timeframe
bars_m15_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24*4*90)
bars_m5_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 24*12*90)
bars_h1_raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 24*90)

bars_m15 = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars_m15_raw]
bars_m5 = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars_m5_raw]
bars_h1 = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars_h1_raw]

print(f'Loaded: M15={len(bars_m15)}, M5={len(bars_m5)}, H1={len(bars_h1)} bars')

# M15: $15, MO=80, mom=ON
cfg_m15 = {'step': 15.0, 'max_open_per_side': 80, 'close_alpha': 1.0, 'close_gap': 1, 'momentum_gate': True, 'rearm_cooldown_bars': 0, 'timeframe': 'M15'}
state_m15 = init_symbol_state(symbol, cfg_m15, bars_m15)
state_m15 = process_symbol(symbol, cfg_m15, bars_m15, state_m15)

# M5: $100, MO=60, mom=OFF
cfg_m5 = {'step': 100.0, 'max_open_per_side': 60, 'close_alpha': 1.0, 'close_gap': 1, 'momentum_gate': False, 'rearm_cooldown_bars': 0, 'timeframe': 'M5'}
state_m5 = init_symbol_state(symbol, cfg_m5, bars_m5)
state_m5 = process_symbol(symbol, cfg_m5, bars_m5, state_m5)

# H1: $25, MO=60, mom=OFF
cfg_h1 = {'step': 25.0, 'max_open_per_side': 60, 'close_alpha': 1.0, 'close_gap': 1, 'momentum_gate': False, 'rearm_cooldown_bars': 0, 'timeframe': 'H1'}
state_h1 = init_symbol_state(symbol, cfg_h1, bars_h1)
state_h1 = process_symbol(symbol, cfg_h1, bars_h1, state_h1)

print(f'\nM15 $15, MO=80, mom=ON: ${state_m15.realized_net_usd:,.2f}, {state_m15.realized_closes} closes, max_seen={state_m15.max_open_total}')
print(f'M5 $100, MO=60, mom=OFF: ${state_m5.realized_net_usd:,.2f}, {state_m5.realized_closes} closes, max_seen={state_m5.max_open_total}')
print(f'H1 $25, MO=60, mom=OFF: ${state_h1.realized_net_usd:,.2f}, {state_h1.realized_closes} closes, max_seen={state_h1.max_open_total}')

total = state_m15.realized_net_usd + state_m5.realized_net_usd + state_h1.realized_net_usd
total_closes = state_m15.realized_closes + state_m5.realized_closes + state_h1.realized_closes
print(f'\nTOTAL: ${total:,.2f}, {total_closes} closes')
print(f'Daily: ${total/90:,.2f}/day')

mt5.shutdown()
