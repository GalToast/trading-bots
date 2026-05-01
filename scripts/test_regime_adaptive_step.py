#!/usr/bin/env python3
"""
Regime-Adaptive Step Formula: step = range × (1.6 - 0.6 × Range/ATR)

Every N bars, recomputes the optimal step based on recent market regime.
Auto-adapts between frequency mode (trending) and survivability mode (ranging).

Saves results to reports/regime_adaptive_steps.json for analysis.
"""
from __future__ import annotations
import json
from pathlib import Path
import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
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

tf_name = {mt5.TIMEFRAME_M5: "M5", mt5.TIMEFRAME_M15: "M15"}
WINDOW = 100  # bars for regime calculation
RECOMPUTE_EVERY = 20  # bars between step adjustments

print("=" * 120)
print("  REGIME-ADAPTIVE STEP FORMULA")
print("  step = range × (1.6 - 0.6 × Range/ATR)")
print("  Recomputes every %d bars over a %d-bar window" % (RECOMPUTE_EVERY, WINDOW))
print("=" * 120)
print()

all_results = []
for sym, tf in symbols_tf:
    rates = mt5.copy_rates_from_pos(sym, tf, 0, 1000)
    if rates is None or len(rates) < WINDOW + 100:
        continue

    highs = [r["high"] for r in rates]
    lows = [r["low"] for r in rates]
    closes = [r["close"] for r in rates]

    # Compute ATR (14-period) for the full history
    tr_values = []
    for i in range(1, len(rates)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_values.append(tr)

    # Compute rolling regime and step
    adaptive_steps = []
    n = len(rates)
    for i in range(WINDOW, n - 1, RECOMPUTE_EVERY):
        # Range over window
        window_ranges = [highs[j] - lows[j] for j in range(i - WINDOW, i)]
        avg_range = sum(window_ranges) / WINDOW

        # ATR at this point (using trailing 14 TRs)
        if i - 1 >= 14:
            atr = sum(tr_values[i - 15:i - 1]) / 14
        else:
            atr = avg_range / 1.4  # fallback

        # Regime
        ra_ratio = avg_range / atr if atr > 0 else 1.0

        # Adaptive step
        step = avg_range * (1.6 - 0.6 * ra_ratio)
        step = max(step, 0.0001)  # floor

        # Regime label
        if ra_ratio < 1.2:
            regime = "TRENDING"
        elif ra_ratio > 1.5:
            regime = "RANGING"
        else:
            regime = "MIXED"

        adaptive_steps.append({
            "bar_idx": i,
            "avg_range": round(avg_range, 6),
            "atr": round(atr, 6),
            "ra_ratio": round(ra_ratio, 3),
            "regime": regime,
            "adaptive_step": round(step, 6),
        })

    if not adaptive_steps:
        continue

    # Current regime (last window)
    current = adaptive_steps[-1]
    first = adaptive_steps[0]

    print(f"{sym:<10} {tf_name[tf]:>4} | Current: step=${current['adaptive_step']:.4f} "
          f"regime={current['regime']} R/A={current['ra_ratio']:.2f}x "
          f"| First: step=${first['adaptive_step']:.4f} "
          f"regime={first['regime']} R/A={first['ra_ratio']:.2f}x")

    # Count regime transitions
    regimes = [s["regime"] for s in adaptive_steps]
    transitions = sum(1 for i in range(1, len(regimes)) if regimes[i] != regimes[i-1])

    # Step range
    steps_list = [s["adaptive_step"] for s in adaptive_steps]
    min_step = min(steps_list)
    max_step = max(steps_list)

    print(f"           | Regime transitions: {transitions}/{len(adaptive_steps)} checks. "
          f"Step range: ${min_step:.4f} - ${max_step:.4f}")

    all_results.append({
        "symbol": sym,
        "timeframe": tf_name[tf],
        "current_step": current["adaptive_step"],
        "current_regime": current["regime"],
        "current_ra_ratio": current["ra_ratio"],
        "min_step": min_step,
        "max_step": max_step,
        "regime_transitions": transitions,
        "total_checks": len(adaptive_steps),
        "regime_history": [{"regime": r, "count": regimes.count(r)} for r in set(regimes)],
        "step_trajectory": adaptive_steps[-10:],  # last 10 for visualization
    })

print()
print("=" * 120)
print("  KEY FINDINGS:")
print("  - Symbols with FEW regime transitions = stable edge (set and forget)")
print("  - Symbols with MANY transitions = ADAPTIVE step is CRUCIAL")
print("  - Step range shows how much the lattice would auto-adjust")
print("=" * 120)

with open(ROOT / "reports" / "regime_adaptive_steps.json", "w") as f:
    json.dump(all_results, f, indent=2)

print(f"\nSaved to reports/regime_adaptive_steps.json")

mt5.shutdown()
