#!/usr/bin/env python3
"""The Chaos Formula: step = ATR × (0.8 + 0.5 × Range/ATR) — auto-adapts to ranging vs trending."""
from __future__ import annotations
import json
import MetaTrader5 as mt5

mt5.initialize()

symbols_tf = [
    ("BTCUSD", mt5.TIMEFRAME_M5, 100, "S+ CHAMPION"),
    ("BTCUSD", mt5.TIMEFRAME_M15, 150, "LIVE — resetting?"),
    ("ETHUSD", mt5.TIMEFRAME_M5, 3, "shadow — 12 open"),
    ("ETHUSD", mt5.TIMEFRAME_M15, 5, "shadow — $19.23/close"),
    ("SOLUSD", mt5.TIMEFRAME_M5, 0.12, "shadow — $1.70 first close"),
    ("XRPUSD", mt5.TIMEFRAME_M5, 0.02, "shadow — struggling"),
    ("LTCUSD", mt5.TIMEFRAME_M15, 0.15, "shadow — building"),
]

tf_name = {mt5.TIMEFRAME_M5: "M5", mt5.TIMEFRAME_M15: "M15"}

print("=" * 110)
print("  THE CHAOS FORMULA: step = ATR × (0.8 + 0.5 × Range/ATR_ratio)")
print("  Auto-adapts: ranging markets → wider, trending markets → tighter")
print("=" * 110)
print()
print(f"{'Symbol':<10} {'TF':>4} {'ATR':>8} {'Range':>8} {'R/A':>5} "
      f"{'Current':>8} {'Cur/ATR':>7} {'Chaos':>8} {'Chaos/ATR':>8} "
      f"{'Diff%':>7} {'Verdict':<20}")
print("-" * 110)

results = []
for sym, tf, current_step, note in symbols_tf:
    rates = mt5.copy_rates_from_pos(sym, tf, 0, 1000)
    if rates is None or len(rates) < 100:
        continue
    
    highs = [r["high"] for r in rates]
    lows = [r["low"] for r in rates]
    closes = [r["close"] for r in rates]
    
    # ATR (14-period)
    tr_values = []
    for i in range(1, len(rates)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_values.append(tr)
    atr_14 = sum(tr_values[-14:]) / 14 if len(tr_values) >= 14 else 0
    
    # Average high-low range
    ranges = [highs[i] - lows[i] for i in range(len(rates))]
    avg_range = sum(ranges[-100:]) / min(100, len(ranges))
    
    range_atr = avg_range / atr_14 if atr_14 > 0 else 0
    
    # Current step metrics
    cur_atr_mult = current_step / atr_14 if atr_14 > 0 else 0
    
    # CHAOS FORMULA
    chaos_step = atr_14 * (0.8 + 0.5 * range_atr)
    chaos_atr_mult = chaos_step / atr_14 if atr_14 > 0 else 0
    
    # Difference
    diff_pct = (chaos_step - current_step) / current_step * 100 if current_step > 0 else 0
    
    # Verdict
    if abs(diff_pct) < 15:
        verdict = "✅ ALREADY OPTIMAL"
    elif diff_pct > 0:
        verdict = f"⬆️ WIDEN by {diff_pct:.0f}%"
    else:
        verdict = f"⬇️ TIGHTEN by {abs(diff_pct):.0f}%"
    
    results.append({
        "symbol": sym, "tf": tf_name[tf], "atr": atr_14, "range": avg_range,
        "range_atr": range_atr, "current": current_step, "cur_atr": cur_atr_mult,
        "chaos": chaos_step, "chaos_atr": chaos_atr_mult, "diff": diff_pct,
        "note": note, "verdict": verdict
    })
    
    print(f"{sym:<10} {tf_name[tf]:>4} ${atr_14:>7.2f} ${avg_range:>7.2f} "
          f"{range_atr:>5.2f}x ${current_step:>7.3f} {cur_atr_mult:>6.2f}x "
          f"${chaos_step:>7.3f} {chaos_atr_mult:>7.2f}x "
          f"{diff_pct:>+6.0f}% {verdict:<20}")

print()
print("=" * 110)
print("  INTERPRETATION:")
print("  - Range/ATR > 1.5 = RANGING (lots of wicks, choppy) → WIDER steps needed")
print("  - Range/ATR < 1.3 = TRENDING (closes follow range) → TIGHTER steps work")
print("  - Chaos formula auto-adjusts: step scales with 'wickyness' of market")
print("=" * 110)
print()

# Save results
output = []
for r in results:
    output.append({
        **r,
        "tf": r["tf"],
        "recommended_step": round(r["chaos"], 6),
        "recommended_action": r["verdict"],
        "current_note": r["note"],
    })

with open("reports/chaos_formula_results.json", "w") as f:
    json.dump(output, f, indent=2)

print("Results saved to reports/chaos_formula_results.json")

mt5.shutdown()
