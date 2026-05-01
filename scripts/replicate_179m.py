from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5
mt5.initialize()

# Load M15 bars for BTCUSD
bars15 = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_M15, 0, 24*4*90)
if bars15 is None or len(bars15) == 0:
    print('NO M15 bars for BTCUSD')
    mt5.shutdown()
    exit()

bars = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars15]
print(f'Loaded {len(bars)} M15 bars')

# Test M15 $15, MO=80, mom=OFF (qwen-main's $1.79M config)
cfg = {'step': 15.0, 'max_open_per_side': 80, 'close_alpha': 1.0, 'close_gap': 1, 'momentum_gate': False, 'rearm_cooldown_bars': 0, 'timeframe': 'M15'}
state = init_symbol_state('BTCUSD', cfg, bars)
state = process_symbol('BTCUSD', cfg, bars, state)
print(f'M15 $15, MO=80, mom=OFF: ${state.realized_net_usd:,.2f}, {state.realized_closes} closes, {state.rearm_opens} rearm, max_seen={state.max_open_total}')

# Test M15 $20, MO=80, mom=ON (my previous best with MO=80)
cfg2 = {'step': 20.0, 'max_open_per_side': 80, 'close_alpha': 1.0, 'close_gap': 1, 'momentum_gate': True, 'rearm_cooldown_bars': 0, 'timeframe': 'M15'}
state2 = init_symbol_state('BTCUSD', cfg2, bars)
state2 = process_symbol('BTCUSD', cfg2, bars, state2)
print(f'M15 $20, MO=80, mom=ON: ${state2.realized_net_usd:,.2f}, {state2.realized_closes} closes, {state2.rearm_opens} rearm, max_seen={state2.max_open_total}')

# Test M15 $15, MO=80, mom=ON (hybrid)
cfg3 = {'step': 15.0, 'max_open_per_side': 80, 'close_alpha': 1.0, 'close_gap': 1, 'momentum_gate': True, 'rearm_cooldown_bars': 0, 'timeframe': 'M15'}
state3 = init_symbol_state('BTCUSD', cfg3, bars)
state3 = process_symbol('BTCUSD', cfg3, bars, state3)
print(f'M15 $15, MO=80, mom=ON: ${state3.realized_net_usd:,.2f}, {state3.realized_closes} closes, {state3.rearm_opens} rearm, max_seen={state3.max_open_total}')

mt5.shutdown()
