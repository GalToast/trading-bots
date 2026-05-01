#!/usr/bin/env python3
"""
BTC M15 Step Sweet-Spot Sweep — uses the unified shadow engine for bar-level simulation.
This is the honest research bar path, not a crude custom simulation.
"""
from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5

mt5.initialize()

# Load M15 bars for BTCUSD (90 days)
bars15 = mt5.copy_rates_from_pos('BTCUSD', mt5.TIMEFRAME_M15, 0, 24*4*90)
if bars15 is None or len(bars15) == 0:
    print('NO M15 bars for BTCUSD')
    mt5.shutdown()
    exit()

bars = [{'time': int(r[0]), 'open': float(r[1]), 'high': float(r[2]), 'low': float(r[3]), 'close': float(r[4]), 'tick_volume': int(r[5])} for r in bars15]
print(f'Loaded {len(bars)} M15 bars ({len(bars)*15/60:.0f} hours)')
print()

# Sweep steps
steps = [15, 20, 30, 40, 50, 60, 75, 100, 125, 150, 200]
total_hours = len(bars) * 15 / 60

print(f"{'Step':>5} | {'Closes':>6} | {'$/close':>8} | {'Net $':>10} | {'Resets':>6} | {'$/hr':>8} | {'MaxOpen':>7}")
print("-" * 80)

results = []
for step in steps:
    cfg = {
        'step': float(step),
        'max_open_per_side': 60,
        'close_alpha': 1.0,
        'close_gap': 1,
        'momentum_gate': False,
        'rearm_variant': 'rearm_lvl2_exc1',
        'rearm_cooldown_bars': 0,
        'timeframe': 'M15',
    }
    state = init_symbol_state('BTCUSD', cfg, bars)
    state = process_symbol('BTCUSD', cfg, bars, state)
    
    resets = getattr(state, 'anchor_resets', 0)
    reset_ratio = resets / state.realized_closes * 100 if state.realized_closes > 0 else 0
    per_hour = state.realized_net_usd / total_hours
    
    results.append({
        'step': step,
        'closes': state.realized_closes,
        'net': state.realized_net_usd,
        'avg_per_close': state.realized_net_usd / state.realized_closes if state.realized_closes > 0 else 0,
        'resets': resets,
        'reset_ratio': reset_ratio,
        'per_hour': per_hour,
        'max_open': state.max_open_total,
    })
    print(f"${step:>4} | {state.realized_closes:>6} | ${state.realized_net_usd/state.realized_closes if state.realized_closes > 0 else 0:>7.2f} | ${state.realized_net_usd:>9.2f} | {resets:>6} | ${per_hour:>7.2f} | {state.max_open_total:>7}")

print("=" * 80)

# Find sweet spot: best $/hour with resets < 50% of closes
print("\nSweet-spot candidates (reset ratio < 50% of closes):")
viable = [r for r in results if r['closes'] > 0 and r['reset_ratio'] < 50]
if viable:
    best = max(viable, key=lambda r: r['net'])
    print(f"  *** ${best['step']}: ${best['per_hour']:.2f}/hr, ${best['avg_per_close']:.2f}/close, {best['closes']}c, {best['reset_ratio']:.1f}% resets ***")
else:
    best = max(results, key=lambda r: r['net'])
    print(f"  *** No viable step < 50% resets. Best net: ${best['step']}: ${best['per_hour']:.2f}/hr ***")

# Show all with reset ratios
print("\nAll steps with reset ratio:")
for r in results:
    flag = "OK" if r['reset_ratio'] < 50 else "HIGH"
    print(f"  ${r['step']}: {r['reset_ratio']:.1f}% resets [{flag}]")

mt5.shutdown()
