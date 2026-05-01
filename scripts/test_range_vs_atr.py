#!/usr/bin/env python3
"""Test the heretical hypothesis: High-Low Range > ATR as step predictor."""
from __future__ import annotations
import json
import MetaTrader5 as mt5

mt5.initialize()

symbols_tf = [
    ("BTCUSD", mt5.TIMEFRAME_M5),
    ("BTCUSD", mt5.TIMEFRAME_M15),
    ("ETHUSD", mt5.TIMEFRAME_M5),
    ("ETHUSD", mt5.TIMEFRAME_M15),
    ("SOLUSD", mt5.TIMEFRAME_M5),
    ("XRPUSD", mt5.TIMEFRAME_M5),
    ("LTCUSD", mt5.TIMEFRAME_M15),
]

print("=== ATR vs HIGH-LOW RANGE: The Heretical Test ===\n")
print(f"{'Symbol':<10} {'TF':>4} {'ATR':>8} {'AvgRange':>10} {'Range/ATR':>10} {'Step':>8} {'Step/ATR':>9} {'Step/Range':>10}")
print("-" * 90)

results = []
for sym, tf in symbols_tf:
    rates = mt5.copy_rates_from_pos(sym, tf, 0, 1000)
    if rates is None or len(rates) == 0:
        continue
    
    # ATR (14-period)
    highs = [r["high"] for r in rates]
    lows = [r["low"] for r in rates]
    closes = [r["close"] for r in rates]
    
    tr_values = []
    for i in range(1, len(rates)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_values.append(tr)
    
    atr_14 = sum(tr_values[-14:]) / 14 if len(tr_values) >= 14 else 0
    
    # Average high-low range
    ranges = [highs[i] - lows[i] for i in range(len(rates))]
    avg_range = sum(ranges[-100:]) / min(100, len(ranges))
    
    range_atr_ratio = avg_range / atr_14 if atr_14 > 0 else 0
    
    results.append({
        "symbol": sym, "tf": tf, "atr_14": atr_14, 
        "avg_range": avg_range, "range_atr_ratio": range_atr_ratio
    })

# Current steps and their ATR multiples
steps = {
    ("BTCUSD", mt5.TIMEFRAME_M5): 100,
    ("BTCUSD", mt5.TIMEFRAME_M15): 150,
    ("ETHUSD", mt5.TIMEFRAME_M5): 3,
    ("ETHUSD", mt5.TIMEFRAME_M15): 5,
    ("SOLUSD", mt5.TIMEFRAME_M5): 0.12,
    ("XRPUSD", mt5.TIMEFRAME_M5): 0.02,
    ("LTCUSD", mt5.TIMEFRAME_M15): 0.15,
}

tf_name = {mt5.TIMEFRAME_M5: "M5", mt5.TIMEFRAME_M15: "M15"}

for r in results:
    key = (r["symbol"], r["tf"])
    step = steps.get(key, 0)
    step_atr = step / r["atr_14"] if r["atr_14"] > 0 else 0
    step_range = step / r["avg_range"] if r["avg_range"] > 0 else 0
    
    print(f"{r['symbol']:<10} {tf_name[r['tf']]:>4} ${r['atr_14']:>7.2f} ${r['avg_range']:>9.2f} {r['range_atr_ratio']:>9.2f}x ${step:>7.2f} {step_atr:>8.2f}x {step_range:>9.3f}x")

print()
print("=== HERETICAL ANALYSIS ===")
print()
print("If Range is the RIGHT metric, Step/Range should cluster around a constant.")
print("If ATR is the RIGHT metric, Step/ATR should cluster around a constant.")
print()

range_ratios = [s["avg_range"] for s in results if s["avg_range"] > 0]
atr_ratios = [s["atr_14"] for s in results if s["atr_14"] > 0]

# For the champion (BTC M5 at $100), what would range-based predict?
btc_m5 = [r for r in results if r["symbol"] == "BTCUSD" and r["tf"] == mt5.TIMEFRAME_M5][0]
optimal_range_mult = 100 / btc_m5["avg_range"]
print(f"BTC M5 champion: step=$100, avg_range=${btc_m5['avg_range']:.2f}")
print(f"  → Optimal range multiplier = {optimal_range_mult:.3f}")
print(f"  → Predicted steps for other symbols using range × {optimal_range_mult:.3f}:")
for r in results:
    key = (r["symbol"], r["tf"])
    step = steps.get(key, 0)
    predicted = r["avg_range"] * optimal_range_mult
    ratio = step / predicted if predicted > 0 else 0
    verdict = "✅ CLOSE" if 0.7 < ratio < 1.3 else "⬆️ TOO TIGHT" if ratio < 0.7 else "⬇️ TOO WIDE"
    print(f"    {r['symbol']} {tf_name[r['tf']]}: predicted=${predicted:.3f}, actual=${step:.3f} ({ratio:.2f}x) {verdict}")

print()
print("Now comparing ATR vs Range prediction quality...")
print()

# ATR-based prediction using BTC M5's 1.55x
optimal_atr_mult = 100 / btc_m5["atr_14"]
print(f"BTC M5 champion: step=$100, ATR=${btc_m5['atr_14']:.2f}")
print(f"  → Optimal ATR multiplier = {optimal_atr_mult:.3f}")
print(f"  → Predicted steps for other symbols using ATR × {optimal_atr_mult:.3f}:")
for r in results:
    key = (r["symbol"], r["tf"])
    step = steps.get(key, 0)
    predicted = r["atr_14"] * optimal_atr_mult
    ratio = step / predicted if predicted > 0 else 0
    verdict = "✅ CLOSE" if 0.7 < ratio < 1.3 else "⬆️ TOO TIGHT" if ratio < 0.7 else "⬇️ TOO WIDE"
    print(f"    {r['symbol']} {tf_name[r['tf']]}: predicted=${predicted:.3f}, actual=${step:.3f} ({ratio:.2f}x) {verdict}")

mt5.shutdown()
