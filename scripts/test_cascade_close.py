#!/usr/bin/env python3
"""Test cascade close in the bar engine vs what the tick engine would do.

The bar engine with gap=0 cascades through ALL levels because it sees the full bar range.
The tick engine with gap=0 should cascade through all levels IF price moves through
multiple levels in a single tick.

Key question: does BTC tick size allow multi-level moves in single ticks?
BTC tick = ~$1-5 move. $15 step = 3-15 ticks per level.
So BTC needs 3-15 ticks to move through one level.
Cascade in tick mode would be limited to ~1 close per tick.

But the bar sweep showed gap=0 = $8,807/hr because in ONE BAR (15 min),
price can move through 10+ levels and cascade through all of them.

The fix: instead of gap-based closing, use LEVEL-BASED closing.
For each position, check if price has reversed through its level.
Close ALL positions that qualify, regardless of tick granularity.

This is what a REAL penetration lattice does:
- Position at level 5 closes when price reverses to level 4
- Position at level 4 closes when price reverses to level 3
- Position at level 3 closes when price reverses to level 2
- etc.

Each position closes independently when price crosses back through its level.
This is NOT the same as gap=0 (which closes at breakeven).
This is level-based penetration closing.
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

# Test what the current engine does at different gap values
print("=== Current engine: close outermost only when price reaches level N ===")
print(f"{'Step':>5} {'Gap':>4} | {'Closes':>6} | {'$/close':>8} | {'Net $':>10} | {'$/hr':>8} | {'MaxOpen':>7}")
print("-" * 80)

for step, gap in [(15,0), (15,1), (15,2), (20,0), (20,1), (20,2)]:
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

print("\n=== What we need: level-based penetration closing ===")
print("Each position closes when price reverses through its own level.")
print("Position at level 5 closes at level 4 (1 step penetration)")
print("Position at level 10 closes at level 9 (1 step penetration)")
print("This captures ALL the profit from the reversal, not just the outermost.")
print()
print("The current gap=0 in bar engine approximates this because the bar")
print("sees the full range and cascades. But gap=0 in tick engine closes")
print("at breakeven (level 0) which is too early.")
print()
print("Solution: modify close logic to close each position at its OWN level,")
print("not just the outermost. This is level-based, not gap-based.")

mt5.shutdown()
