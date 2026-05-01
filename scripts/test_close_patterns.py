#!/usr/bin/env python3
"""Test different closing patterns to find the REAL max $/hr.

Current pattern: gap=N waits for N inner levels, then closes ONLY the outermost position.
Problem: If 10 levels are stacked and price reverses through 9, we still only close 1.

Better patterns:
1. Close-all-profitable: Close every position that's in profit during reversal
2. Close-as-penetration: Close each position as price crosses back through its level
3. Current gap=N: Only close outermost when price reaches level N
"""
from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
import MetaTrader5 as mt5

mt5.initialize()
bars15 = mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24 * 4 * 90)
bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in bars15]
total_hrs = len(bars) * 15 / 60

print(f"Loaded {len(bars)} M15 bars ({total_hrs:.0f} hours)")
print()

# Test current gap patterns
print("=== CURRENT: Close outermost only when price reaches level N ===")
print(f"{'Step':>5} {'Gap':>4} | {'Closes':>6} | {'$/close':>8} | {'Net $':>10} | {'$/hr':>8} | {'MaxOpen':>7}")
print("-" * 80)

for step in [15, 20, 25, 30]:
    for gap in [0, 1, 2, 5]:
        cfg = {
            "step": float(step), "max_open_per_side": 60, "close_alpha": 1.0,
            "close_gap": gap, "momentum_gate": False, "rearm_variant": "rearm_lvl2_exc1",
            "rearm_cooldown_bars": 0, "timeframe": "M15",
        }
        state = init_symbol_state("BTCUSD", cfg, bars)
        state = process_symbol("BTCUSD", cfg, bars, state)
        closes = state.realized_closes
        net = state.realized_net_usd
        avg = net / closes if closes > 0 else 0
        per_hr = net / total_hrs
        print(f"${step:>4}   {gap:>2} | {closes:>6} | ${avg:>7.2f} | ${net:>9.2f} | ${per_hr:>7.2f} | {state.max_open_total:>7}")

print("=" * 80)

# The key question the user is asking:
# If we close ALL profitable positions during reversal instead of just the outermost,
# does $/hr increase dramatically?
#
# Example: 5 SELLs at 100, 115, 130, 145, 160. Price drops to all, then reverses.
# Current (gap=1): Only 160 closes when price reaches 145. Captures 15 steps.
# Better: Close 160 at 145, 145 at 130, 130 at 115, 115 at 100. Captures 60 steps.
# Or: Close ALL at once when reversal confirmed. Captures all profit at once.

print("\nKey insight:")
print("Current gap=N closes ONLY the outermost position when price reaches level N.")
print("If 10 positions are stacked, gap=1 still only closes 1 per reversal wave.")
print("A 'close-all-profitable' pattern could capture 10× more per reversal.")

mt5.shutdown()
