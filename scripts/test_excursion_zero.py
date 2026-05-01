from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5
mt5.initialize()

# Load M15 bars
bars15 = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_M15, 0, 24*4*90)
bars15 = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars15]
print(f'Loaded {len(bars15)} M15 bars')

# Test with excursion_levels=0 (qwen-main's config)
cfg = {'step': 15.0, 'max_open_per_side': 80, 'close_alpha': 1.0, 'close_gap': 1, 
       'momentum_gate': True, 'rearm_cooldown_bars': 0, 'rearm_excursion_levels': 0, 'timeframe': 'M15'}
state = init_symbol_state('BTCUSD', cfg, bars15)
state = process_symbol('BTCUSD', cfg, bars15, state)
print(f'M15 $15, MO=80, mom=ON, excursion=0: ${state.realized_net_usd:,.2f}, {state.realized_closes} closes, {state.rearm_opens} rearm, max_seen={state.max_open_total}')

# Test with excursion_levels=1 (my previous config)
cfg2 = {'step': 15.0, 'max_open_per_side': 80, 'close_alpha': 1.0, 'close_gap': 1,
        'momentum_gate': True, 'rearm_cooldown_bars': 0, 'rearm_excursion_levels': 1, 'timeframe': 'M15'}
state2 = init_symbol_state('BTCUSD', cfg2, bars15)
state2 = process_symbol('BTCUSD', cfg2, bars15, state2)
print(f'M15 $15, MO=80, mom=ON, excursion=1: ${state2.realized_net_usd:,.2f}, {state2.realized_closes} closes, {state2.rearm_opens} rearm, max_seen={state2.max_open_total}')

mt5.shutdown()
