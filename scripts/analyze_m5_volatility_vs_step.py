#!/usr/bin/env python3
"""
M5 Volatility vs Step Diagnostic
=================================
Compares average M5 bar range to configured step sizes across all
M5 Warp lanes (FX, crypto, indices) to determine if geometry is viable.

Usage:
    python scripts/analyze_m5_volatility_vs_step.py
"""
import MetaTrader5 as mt5
import json
import numpy as np
from pathlib import Path

mt5.initialize()

REPO = Path(__file__).resolve().parent.parent

LANES = {
    'GBPUSD': {'1.0x': 0.00028, '1.5x': 0.000337},
    'USDJPY': {'1.0x': 0.0338, '1.5x': 0.0519},
    'AUDUSD': {'1.0x': 0.00035, '1.5x': 0.00035},
    'EURUSD': {'1.0x': 0.00036, '1.5x': 0.00036},
    'NZDUSD': {'1.0x': 0.00029, '1.5x': 0.00029},
    'USDCAD': {'1.0x': 0.00041, '1.5x': 0.00041},
    'XAUUSD': {'1.5x': 1.13, '0.8x': 0.60},
    'NAS100': {'1.5x': 24.77, '0.8x': 5.36},
    'US30': {'1.5x': 40.69, '0.8x': 7.86},
}

tf = mt5.TIMEFRAME_M5
results = {}

print(f"{'Lane':25s} {'Step':>10s} {'Avg Range':>12s} {'Med Range':>12s} {'P25 Range':>12s} {'Ratio':>7s} {'Verdict'}")
print("-" * 95)

for sym, steps in sorted(LANES.items()):
    bars = mt5.copy_rates_from_pos(sym, tf, 0, 200)
    if bars is None or len(bars) == 0:
        print(f"{sym:25s} ERROR: no bars")
        continue
    ranges = [float(r['high'] - r['low']) for r in bars]
    avg_r = np.mean(ranges)
    med_r = np.median(ranges)
    p25_r = np.percentile(ranges, 25)

    for name, step in sorted(steps.items()):
        ratio = avg_r / step
        should_trigger = ratio > 1.0
        verdict = "✅" if should_trigger else "❌ TOO WIDE"
        key = f"{sym}_{name}"
        results[key] = {
            'symbol': sym,
            'coefficient': name,
            'step': step,
            'avg_range': float(avg_r),
            'median_range': float(med_r),
            'p25_range': float(p25_r),
            'range_step_ratio': round(ratio, 2),
            'should_trigger': should_trigger,
        }
        print(f"{key:25s} {step:10.5f} {avg_r:12.5f} {med_r:12.5f} {p25_r:12.5f} {ratio:7.2f}x {verdict}")

# Save report
output_path = REPO / 'reports' / 'fx_m5_volatility_diagnostic.json'
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(results, f, indent=2, default=str)

print(f"\nSaved to {output_path}")

# Summary
triggering = {k:v for k,v in results.items() if v['should_trigger']}
not_triggering = {k:v for k,v in results.items() if not v['should_trigger']}
print(f"\n✅ {len(triggering)} configs should trigger")
print(f"❌ {len(not_triggering)} configs step too wide for avg M5 bar")
if not_triggering:
    print("   Problem lanes: " + ", ".join(not_triggering.keys()))
