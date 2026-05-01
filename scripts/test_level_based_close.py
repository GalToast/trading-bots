#!/usr/bin/env python3
"""Test level-based penetration closing: close each position when price reverses through its OWN level.

Current: gap=1 closes outermost only when price reaches level 1.
Level-based: close each position when price crosses back through its own level.
  - Position at level 5 closes when ask <= level 4 trigger
  - Position at level 4 closes when ask <= level 3 trigger
  - Position at level 3 closes when ask <= level 2 trigger
  - etc.

This captures ALL the profit from the reversal, not just the outermost.
"""
import json
import MetaTrader5 as mt5
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def level_based_close_test(bars, step=15.0, max_open=60):
    """Simulate level-based penetration closing."""
    if len(bars) < 50:
        return {"closes": 0, "net": 0.0}
    
    anchor = bars[0]["close"]
    next_sell = anchor + step
    next_buy = anchor - step
    
    # Track positions with their level
    sells = []  # list of (level, entry_price)
    buys = []
    realized = 0.0
    closes = 0
    max_open_total = 0
    
    for bar in bars[1:]:
        bar_high = bar["high"]
        bar_low = bar["low"]
        
        # Open new positions
        while bar_high >= next_sell and len([s for s in sells if s[0] > 0]) < max_open:
            level = int(round((next_sell - anchor) / step))
            sells.append((level, next_sell))
            next_sell += step
            if len(sells) + len(buys) > max_open_total:
                max_open_total = len(sells) + len(buys)
        
        while bar_low <= next_buy and len([b for b in buys if b[0] > 0]) < max_open:
            level = int(round((anchor - next_buy) / step))
            buys.append((level, next_buy))
            next_buy -= step
            if len(sells) + len(buys) > max_open_total:
                max_open_total = len(sells) + len(buys)
        
        # Level-based close: close each position when price crosses back through its level
        # For SELLs: close when bar_low <= entry - step (1 step penetration)
        new_sells = []
        for level, entry in sells:
            if bar_low <= entry - step:
                # Position closed with 1 step penetration
                pnl = (entry - (entry - step)) * 0.01 * 1.0  # $1 per step × 0.01 lot = $0.01 per step
                # Actually for BTC, 0.01 lot at $1 move = $0.01
                # At $15 step penetration: $0.15 per close
                # But the real PnL is step × volume × contract_multiplier
                # For crypto shadow, volume=0.01 and contract=1, so pnl = step * 0.01
                realized += step * 0.01
                closes += 1
            else:
                new_sells.append((level, entry))
        sells = new_sells
        
        # For BUYs: close when bar_high >= entry + step
        new_buys = []
        for level, entry in buys:
            if bar_high >= entry + step:
                realized += step * 0.01
                closes += 1
            else:
                new_buys.append((level, entry))
        buys = new_buys
    
    return {
        "closes": closes,
        "net": round(realized, 2),
        "avg_per_close": round(realized / closes, 2) if closes > 0 else 0,
        "max_open": max_open_total,
    }


mt5.initialize()
bars15 = mt5.copy_rates_from_pos("BTCUSD", mt5.TIMEFRAME_M15, 0, 24 * 4 * 90)
bars = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])} for r in bars15]
total_hrs = len(bars) * 15 / 60

print(f"Loaded {len(bars)} M15 bars ({total_hrs:.0f} hours)")
print()

# Test level-based close
result = level_based_close_test(bars, step=15.0, max_open=12)
per_hr = result["net"] / total_hrs if total_hrs > 0 else 0

print("=== Level-based penetration closing (close each at own level) ===")
print(f"Step $15: {result['closes']} closes, ${result['net']:.2f} net, ${result['avg_per_close']:.2f}/close, ${per_hr:.2f}/hr")
print()

# Compare with bar-level sweep results
print("=== Comparison with current gap-based approach ===")
print(f"{'Method':<50} | {'$/hr':>8}")
print("-" * 70)
print(f"{'Level-based (close each at own level)':<50} | ${per_hr:>7.2f}")
print(f"{'Bar gap=0 (cascade all)':<50} | $8807.30")
print(f"{'Bar gap=1 (outermost only)':<50} | $  327.67")
print(f"{'Bar gap=2 (wait 2 levels)':<50} | $  349.26")

mt5.shutdown()
