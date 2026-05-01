#!/usr/bin/env python3
"""
Test: Oscillation lattice — each level opens BOTH sides and captures FULL bar range.

The issue with previous test: it only captured 1 step per close ($0.15).
In reality, cascade captures the FULL bar range because it closes at bar extreme.

Oscillation lattice should also capture full bar range:
- SELL at level 10 closes at bar_low (not level 9)
- BUY at level 5 closes at bar_high (not level 6)

This way, each position captures the full bar movement, not just 1 step.
"""
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state

mt5.initialize()
bars15 = mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24 * 4 * 90)
bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in bars15]
total_hrs = len(bars) * 15 / 60

print(f"Loaded {len(bars)} M15 bars ({total_hrs:.0f} hours)")
print()

# Standard cascade (proven champion)
print("=== CASCADE (gap=0) — proven champion ===")
for step in [15, 20]:
    cfg = {"step": float(step), "max_open_per_side": 60, "close_alpha": 1.0,
           "close_gap": 0, "momentum_gate": False, "rearm_variant": "rearm_lvl2_exc1",
           "rearm_cooldown_bars": 0, "timeframe": "M15"}
    state = init_symbol_state("BTCUSD", cfg, bars)
    state = process_symbol("BTCUSD", cfg, bars, state)
    closes = state.realized_closes
    net = state.realized_net_usd
    avg = net / closes if closes > 0 else 0
    per_hr = net / total_hrs
    print(f"  ${step}: {closes}c, ${net:.2f} net, ${avg:.2f}/close, ${per_hr:.2f}/hr")

# Oscillation lattice — FIXED to capture full bar range
print("\n=== OSCILLATION LATTICE (per-level zero, captures FULL bar range) ===")
print("Each level opens SELL on uptrend, BUY on downtrend.")
print("SELL closes at bar_low (full range), BUY closes at bar_high (full range).")
print()

def simulate_oscillation_lattice_full(bars, step=15.0, max_open_per_side=12):
    """Oscillation lattice: each level opens both sides, closes at bar extremes."""
    if len(bars) < 50:
        return {"closes": 0, "net": 0.0, "max_open": 0}
    
    anchor = bars[0]["close"]
    
    # Track positions: {level: {"sell": entry_price or None, "buy": entry_price or None}}
    levels = {}
    realized = 0.0
    closes = 0
    max_open_total = 0
    max_open_per_level = max_open_per_side
    
    # Track next levels to open
    next_sell_level = anchor + step
    next_buy_level = anchor - step
    
    for bar in bars[1:]:
        bar_high = bar["high"]
        bar_low = bar["low"]
        
        # Open SELLs as price moves UP through levels
        while bar_high >= next_sell_level:
            level = int(round((next_sell_level - anchor) / step))
            
            # Initialize level if not exists
            if level not in levels:
                levels[level] = {"sell": None, "buy": None}
            
            # Open SELL if not already open
            if levels[level]["sell"] is None:
                # Check max open limit
                total_sells = sum(1 for l in levels.values() if l["sell"] is not None)
                if total_sells < max_open_per_level:
                    levels[level]["sell"] = next_sell_level
            
            next_sell_level += step
        
        # Open BUYs as price moves DOWN through levels
        while bar_low <= next_buy_level:
            level = int(round((anchor - next_buy_level) / step))
            
            # Initialize level if not exists
            if level not in levels:
                levels[level] = {"sell": None, "buy": None}
            
            # Open BUY if not already open
            if levels[level]["buy"] is None:
                # Check max open limit
                total_buys = sum(1 for l in levels.values() if l["buy"] is not None)
                if total_buys < max_open_per_level:
                    levels[level]["buy"] = next_buy_level
            
            next_buy_level -= step
        
        # Close SELLs at bar_low (full range capture)
        for level, pos in list(levels.items()):
            if pos["sell"] is not None and bar_low <= pos["sell"]:
                # SELL closed at bar_low: profit = (entry - bar_low) * 0.01
                pnl = (pos["sell"] - bar_low) * 0.01
                realized += pnl
                closes += 1
                pos["sell"] = None
        
        # Close BUYs at bar_high (full range capture)
        for level, pos in list(levels.items()):
            if pos["buy"] is not None and bar_high >= pos["buy"]:
                # BUY closed at bar_high: profit = (bar_high - entry) * 0.01
                pnl = (bar_high - pos["buy"]) * 0.01
                realized += pnl
                closes += 1
                pos["buy"] = None
        
        # Track max open
        total_open = sum(1 for l in levels.values() for v in l.values() if v is not None)
        if total_open > max_open_total:
            max_open_total = total_open
    
    return {
        "closes": closes,
        "net": round(realized, 2),
        "avg": round(realized / closes, 2) if closes > 0 else 0,
        "max_open": max_open_total,
    }


for step in [15, 20]:
    result = simulate_oscillation_lattice_full(bars, step=step, max_open_per_side=12)
    per_hr = result["net"] / total_hrs if total_hrs > 0 else 0
    print(f"  ${step}: {result['closes']}c, ${result['net']:.2f} net, ${result['avg']:.2f}/close, ${per_hr:.2f}/hr, max_open={result['max_open']}")

# Compare
print("\n=== COMPARISON ===")
print(f"{'Pattern':<30} | {'Step':>5} | {'$/hr':>8} | {'Closes':>6} | {'$/close':>8}")
print("-" * 70)

cascade_15 = {"per_hr": 8863.12, "closes": 204017, "avg": 86.95}
cascade_20 = {"per_hr": 8161.64, "closes": 175286, "avg": 93.19}

for step in [15, 20]:
    result = simulate_oscillation_lattice_full(bars, step=step, max_open_per_side=12)
    per_hr = result["net"] / total_hrs if total_hrs > 0 else 0
    cr = cascade_15 if step == 15 else cascade_20
    print(f"Oscillation lattice          | ${step:>4} | ${per_hr:>7.2f} | {result['closes']:>6} | ${result['avg']:>7.2f}")
    print(f"Cascade (gap=0)              | ${step:>4} | ${cr['per_hr']:>7.2f} | {cr['closes']:>6} | ${cr['avg']:>7.2f}")
    ratio = cr['per_hr'] / per_hr if per_hr > 0 else 0
    print(f"  → Cascade is {ratio:.1f}× better")
    print()

print("CONCLUSION:")
print("  If oscillation lattice >> cascade: opening BOTH sides at each level")
print("    creates more profit than just closing on full reversal.")
print("  If cascade >> oscillation lattice: waiting for full reversal and")
print("    capturing the full bar range is better than capturing both sides.")

mt5.shutdown()
