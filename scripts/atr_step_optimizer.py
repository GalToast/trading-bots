#!/usr/bin/env python3
"""
ATR-normalized step optimizer for crypto penetration lattice.

Pulls ATR for all crypto symbols at M5 and M15, computes optimal steps
at 0.25x, 0.5x, 1.0x ATR, and compares against current step choices.

Usage: python scripts/atr_step_optimizer.py
"""
from __future__ import annotations

import json
import MetaTrader5 as mt5
from datetime import datetime, timezone

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LTCUSD", "ADAUSD"]
TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
}
ATR_PERIOD = 14
ATR_MULTIPLIERS = [0.25, 0.5, 1.0]

# Current step choices (updated 2026-04-15 with ATR optimization)
CURRENT_STEPS = {
    "BTCUSD": {"M5": 100, "M5_live": 150, "M15": 15, "M15_on20": 20},
    "ETHUSD": {"M5_atr_opt": 5.0, "M15_atr_opt": 13.0, "M15_asym": 7.79},
    "SOLUSD": {"M5_v2": 0.30, "M15": None},
    "XRPUSD": {"M5_v2": 0.02, "M15": None},
    "LTCUSD": {"M15": 0.15},
    "ADAUSD": {"M15": 0.0015},
}


def main():
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        return

    print("=" * 80)
    print(f"ATR-Normalized Step Optimizer — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 80)

    results = []
    for sym in SYMBOLS:
        for tf_name, tf in TIMEFRAMES.items():
            rates = mt5.copy_rates_from_pos(sym, tf, 0, 100)
            if rates is None or len(rates) < ATR_PERIOD + 1:
                continue

            # Calculate ATR manually from rates
            trs = []
            for i in range(1, len(rates)):
                high = rates[i]["high"]
                low = rates[i]["low"]
                prev_close = rates[i - 1]["close"]
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)
            atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

            price = rates[-1]["close"]
            step_pct = (atr / price) * 100 if price > 0 else 0

            row = {"symbol": sym, "tf": tf_name, "ATR": round(atr, 6),
                   "price": round(price, 6), "step_pct": round(step_pct, 4)}

            for mult in ATR_MULTIPLIERS:
                optimal_step = atr * mult
                row[f"step_{mult}x"] = round(optimal_step, 6)

            # Current step comparison
            cur_key = tf_name
            if sym == "BTCUSD" and tf_name == "M5":
                cur_key = "M5_live"
            elif sym == "SOLUSD" and tf_name == "M5":
                cur_key = "M5_v2"
            elif sym == "XRPUSD" and tf_name == "M5":
                cur_key = "M5_v2"

            current = CURRENT_STEPS.get(sym, {}).get(cur_key)
            if current and atr > 0:
                current_ratio = current / atr
                row["current_step"] = current
                row["current_x_ATR"] = round(current_ratio, 3)

            results.append(row)

    mt5.shutdown()

    # Print results
    print(f"\n{'Symbol':<10} {'TF':<5} {'ATR':<12} {'Price':<12} {'0.25x ATR':<12} {'0.5x ATR':<12} {'1.0x ATR':<12} {'Current':<10} {'Current x ATR':<14}")
    print("-" * 120)
    for r in results:
        cur = r.get("current_step", "—")
        cur_ratio = r.get("current_x_ATR", "—")
        print(f"{r['symbol']:<10} {r['tf']:<5} {r['ATR']:<12.4f} {r['price']:<12.2f} "
              f"{r['step_0.25x']:<12.4f} {r['step_0.5x']:<12.4f} {r['step_1.0x']:<12.4f} "
              f"{str(cur):<10} {str(cur_ratio):<14}")

    # Universal recommendation
    print(f"\n{'=' * 80}")
    print(f"RECOMMENDATION: Use 0.5x ATR as the universal step formula")
    print(f"{'=' * 80}")
    for r in results:
        sym, tf = r["symbol"], r["tf"]
        cur = r.get("current_step")
        optimal = r["step_0.5x"]
        if cur and abs(cur - optimal) / optimal > 0.3:  # >30% off
            action = "WIDEN" if cur < optimal else "TIGHTEN"
            print(f"  {sym} {tf}: {cur} → {optimal:.4f} ({action}, currently {r['current_x_ATR']}x ATR)")
        elif cur:
            print(f"  {sym} {tf}: {cur} ≈ {optimal:.4f} ✅ near-optimal ({r['current_x_ATR']}x ATR)")
        else:
            print(f"  {sym} {tf}: NEW — recommended step = {optimal:.4f}")

    # Save results
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "atr_data": results,
        "universal_formula": "step = ATR × 0.5",
        "champion_reference": "BTC M5 LIVE $100 ≈ 0.5× ATR → $25.79/close, 100% WR",
    }
    import os
    out_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports", "atr_step_optimization.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
