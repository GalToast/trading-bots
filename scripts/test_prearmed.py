from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5
mt5.initialize()

bars_m5 = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_M5, 0, 24*12*90)
bars_h1 = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_H1, 0, 24*90)

if bars_m5 is not None:
    bars_m5 = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars_m5]
if bars_h1 is not None:
    bars_h1 = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars_h1]

print(f'Loaded: M5={len(bars_m5)}, H1={len(bars_h1)} bars')

# M5: $100, MO=60, mom=OFF
cfg_m5 = {'step': 100.0, 'max_open_per_side': 60, 'close_alpha': 1.0, 'close_gap': 1, 'momentum_gate': False, 'rearm_cooldown_bars': 0, 'timeframe': 'M5'}
state_m5 = init_symbol_state('BTCUSD', cfg_m5, bars_m5)
state_m5 = process_symbol('BTCUSD', cfg_m5, bars_m5, state_m5)

# H1: $25, MO=60, mom=OFF
cfg_h1 = {'step': 25.0, 'max_open_per_side': 60, 'close_alpha': 1.0, 'close_gap': 1, 'momentum_gate': False, 'rearm_cooldown_bars': 0, 'timeframe': 'H1'}
state_h1 = init_symbol_state('BTCUSD', cfg_h1, bars_h1)
state_h1 = process_symbol('BTCUSD', cfg_h1, bars_h1, state_h1)

print(f'\nM5 $100, MO=60, mom=OFF: ${state_m5.realized_net_usd:,.2f}, {state_m5.realized_closes} closes, {state_m5.rearm_opens} rearm, max_seen={state_m5.max_open_total}')
print(f'H1 $25, MO=60, mom=OFF: ${state_h1.realized_net_usd:,.2f}, {state_h1.realized_closes} closes, {state_h1.rearm_opens} rearm, max_seen={state_h1.max_open_total}')

total = state_m5.realized_net_usd + state_h1.realized_net_usd + 1824099.62  # M15 from before
print(f'\nTOTAL (M15+M5+H1): ${total:,.2f}')

mt5.shutdown()
