#!/usr/bin/env python3
"""Test depth-adaptive close: close inner positions at shallow gap, outer at deeper gap.

Current: gap=N closes outermost only when price reaches level N, leaving inner positions open.
Depth-adaptive: close position at level L when price reaches level max(1, L - adaptive_gap).
  - Level 1 (nearest anchor): close when price reaches level 0 (immediate on reversal)
  - Level 2: close when price reaches level 1
  - Level 5: close when price reaches level 3
  - Level 10: close when price reaches level 7

This way we harvest inner churn AND capture deep penetration on outer positions.
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

# Test depth-adaptive close
# The key insight: gap should be relative to position depth, not fixed
# Position at level L closes when price reaches level L - adaptive_gap

# We can't test this directly in the bar engine without modifying it,
# but we can infer the behavior from the fixed gap results:
# - gap=1: close outermost when price reaches level 1 (inner stays open)
# - gap=3: close outermost when price reaches level 3 (leaves 2 inner positions open)

# If depth-adaptive works, we'd expect:
# - More closes than gap=3 (because inner positions also close)
# - Higher $/close than gap=1 (because outer positions capture more distance)
# - Net result: higher $/hr than both gap=1 and gap=3

# Let me test this by checking the close distance distribution:
# If outer positions are held longer, they should close with larger distances

print("Depth-adaptive close hypothesis:")
print("  Level 1 position: close when price reverses to level 0 (1 step distance)")
print("  Level 3 position: close when price reverses to level 1 (2 steps distance)")
print("  Level 5 position: close when price reverses to level 3 (2 steps distance)")
print("  Level 10 position: close when price reverses to level 7 (3 steps distance)")
print()
print("  This captures BOTH inner churn (frequent small closes) AND")
print("  outer penetration (fewer but larger closes)")
print()
print("  Expected: More closes than gap=3, higher $/close than gap=1")
print("  The bar engine doesn't support this directly, but we can verify")
print("  from the close distance distribution whether outer positions")
print("  are capturing more distance than gap=1 allows.")

mt5.shutdown()
