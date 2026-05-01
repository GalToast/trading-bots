#!/usr/bin/env python3
"""Test gap=0 in tick-native mode vs bar-level.
Bar-level gap=0 = $8,807/hr (27× current). But tick-native has spread.
Does gap=0 survive tick-native reality?
"""
import json
from pathlib import Path

# The bar engine cascades through all levels in one bar when price reverses.
# The tick engine processes one tick at a time.
# gap=0 in tick engine = close outermost as soon as ANY tick is favorable.
# This could mean: close at 1 pip profit (if spread allows) 
# OR: cascade through levels if price moves fast enough.

# The key question: in tick mode with gap=0, does the while loop
# cascade through levels within a single bar, or does it only close
# once per tick?

# Let me check what the tick engine does with gap=0.
# The close loop in tick_penetration_lattice_core.py:
# while len(sells) > sell_gap and ask <= sells[sell_gap].trigger_level:
#     ...close outermost...
#     sells = resorted list
#
# With gap=0: while len(sells) > 0 and ask <= sells[0].trigger_level:
# This means: close ALL SELLs as soon as ask <= outermost trigger.
# In practice: if price drops through 5 levels in one tick, ALL 5 close.
# That's the same cascade as bar-level.

# But: the tick engine only processes ticks, not bars. So if price
# drops through 5 levels across 5 different ticks, it closes one per tick.
# The bar engine sees the full bar range and closes all at once.

print("Tick engine gap=0 behavior analysis:")
print("  If price drops through 5 levels in 1 tick: ALL 5 close (cascade)")
print("  If price drops through 5 levels across 5 ticks: 1 per tick")
print("  With BTC volatility, most level-crossings happen in single ticks during spikes")
print("  So tick-native gap=0 should approximate bar-level cascade")
print()
print("The spread cost at gap=0:")
print("  BTC spread ~$177 median")
print("  At $15 step, spread = 11.8× step")
print("  Each close at 0 gap captures ~1 step = $15 gross, -$177 spread = NEGATIVE")
print("  BUT: penetration close doesn't close at 1 step — it closes when price")
print("  REVERSES to inner level, capturing the full penetration distance.")
print("  The spread is paid on open AND close, but penetration captures 5-10× step.")
print()
print("Need tick-native gap=0 test to verify. Bar-level is optimistic.")
