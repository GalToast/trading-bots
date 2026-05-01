#!/usr/bin/env python3
"""
Test: Per-level zero pattern — each filled level opens BOTH sides and closes independently.

Standard cascade (gap=0): 
  - Price trends UP 10 steps → stack 10 SELLs
  - Price reverses DOWN → close ALL 10 SELLs as price drops through each level
  - Only captures reversal profit

Per-level zero:
  - Price trends UP 10 steps → stack 10 SELLs
  - ALSO open BUY at each level (counter-position for oscillation)
  - Each SELL closes when price drops 1 step below its level
  - Each BUY closes when price rises 1 step above its level
  - Captures BOTH reversal AND oscillation profit
"""
import MetaTrader5 as mt5
from pathlib import Path

mt5.initialize()
bars15 = mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24 * 4 * 90)
bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in bars15]
total_hrs = len(bars) * 15 / 60

print(f"Loaded {len(bars)} M15 bars ({total_hrs:.0f} hours)")
print()

def simulate_per_level_zero(bars, step=15.0, max_open=12):
    """Simulate per-level zero: each filled level opens BOTH sides."""
    if len(bars) < 50:
        return {"closes": 0, "net": 0.0}
    
    anchor = bars[0]["close"]
    sells = {}  # level -> entry_price
    buys = {}   # level -> entry_price
    realized = 0.0
    closes = 0
    max_open_total = 0
    next_sell = anchor + step
    next_buy = anchor - step
    
    for bar in bars[1:]:
        bar_high = bar["high"]
        bar_low = bar["low"]
        
        # Open SELLs as price moves up
        while bar_high >= next_sell and len(sells) < max_open:
            level = int(round((next_sell - anchor) / step))
            sells[level] = next_sell
            next_sell += step
            # ALSO open BUY at this level (per-level zero)
            if level - 1 not in buys and len(buys) < max_open:
                buys[level - 1] = next_sell - step  # BUY 1 step below SELL
            if len(sells) + len(buys) > max_open_total:
                max_open_total = len(sells) + len(buys)
        
        # Open BUYs as price moves down
        while bar_low <= next_buy and len(buys) < max_open:
            level = int(round((anchor - next_buy) / step))
            buys[level] = next_buy
            next_buy -= step
            # ALSO open SELL at this level (per-level zero)
            if level + 1 not in sells and len(sells) < max_open:
                sells[level + 1] = next_buy + step  # SELL 1 step above BUY
            if len(sells) + len(buys) > max_open_total:
                max_open_total = len(sells) + len(buys)
        
        # Close SELLs when price drops 1 step below their level
        closes_to_remove = []
        for level, entry in sells.items():
            if bar_low <= entry - step:
                # SELL closed: profit = (entry - (entry - step)) * 0.01 = step * 0.01
                realized += step * 0.01
                closes += 1
                closes_to_remove.append(level)
        for level in closes_to_remove:
            del sells[level]
        
        # Close BUYs when price rises 1 step above their level
        closes_to_remove = []
        for level, entry in buys.items():
            if bar_high >= entry + step:
                # BUY closed: profit = ((entry + step) - entry) * 0.01 = step * 0.01
                realized += step * 0.01
                closes += 1
                closes_to_remove.append(level)
        for level in closes_to_remove:
            del buys[level]
    
    return {
        "closes": closes,
        "net": round(realized, 2),
        "avg_per_close": round(realized / closes, 2) if closes > 0 else 0,
        "max_open": max_open_total,
        "final_sells": len(sells),
        "final_buys": len(buys),
    }


# Test per-level zero
print("=== PER-LEVEL ZERO: Each filled level opens BOTH sides ===")
for step in [15, 20, 25]:
    result = simulate_per_level_zero(bars, step=step, max_open=12)
    per_hr = result["net"] / total_hrs if total_hrs > 0 else 0
    print(f"  ${step} step: {result['closes']}c, ${result['net']:.2f} net, ${result['avg_per_close']:.2f}/close, ${per_hr:.2f}/hr, max_open={result['max_open']}, final={result['final_sells']}S/{result['final_buys']}B")

print()
print("=== COMPARISON WITH CASCADE (gap=0) ===")
print(f"{'Pattern':<25} | {'$/hr':>8} | {'Closes':>6} | {'$/close':>8}")
print("-" * 55)

# Bar-level cascade results (from previous sweep)
cascade_results = {15: 8823.45, 20: 8138.14, 25: 7536.70}
cascade_closes = {15: 204017, 20: 175286, 25: 150918}
cascade_avg = {15: 86.55, 20: 92.91, 25: 99.94}

for step in [15, 20, 25]:
    result = simulate_per_level_zero(bars, step=step, max_open=12)
    per_hr = result["net"] / total_hrs if total_hrs > 0 else 0
    print(f"Per-level zero ${step:<7} | ${per_hr:>7.2f} | {result['closes']:>6} | ${result['avg_per_close']:>7.2f}")
    print(f"Cascade ${step:<11} | ${cascade_results[step]:>7.2f} | {cascade_closes[step]:>6} | ${cascade_avg[step]:>7.2f}")
    print()

mt5.shutdown()
