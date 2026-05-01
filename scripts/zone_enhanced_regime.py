#!/usr/bin/env python3
"""
ZONE-AWARE REGIME ENHANCER — Merge Pressure Zones with Regime Signals

Takes the existing regime_signal.json and overlays pressure zone detection.
Modifies buy_step_coeff and sell_step_coeff based on:
1. Zone proximity (at support → tighten BUY, at resistance → tighten SELL)
2. Wedge compression (narrow both sides)
3. Breakout confirmation (widen against-breakout side)

Output: reports/zone_enhanced_regime.json — drop-in replacement for HH configs
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import numpy as np


ROOT = Path(__file__).resolve().parent.parent
REGIME_SIGNAL_PATH = ROOT / "reports" / "regime_signal.json"
PRESSURE_ZONES_PATH = ROOT / "reports" / "pressure_zones_live.json"
OUTPUT_JSON = ROOT / "reports" / "zone_enhanced_regime.json"

LOOKBACK_BARS = 500


def load_zones_live() -> dict[str, Any]:
    """Load or compute fresh pressure zones."""
    if PRESSURE_ZONES_PATH.exists():
        mtime = datetime.fromtimestamp(PRESSURE_ZONES_PATH.stat().st_mtime, tz=timezone.utc)
        age = datetime.now(timezone.utc) - mtime
        if age < timedelta(minutes=5):
            return json.loads(PRESSURE_ZONES_PATH.read_text(encoding="utf-8"))

    # Zones are stale or missing — compute fresh
    from pressure_zone_detector import compute_pressure_state

    zones: dict[str, Any] = {}
    symbols = ["NAS100", "US30", "EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "ETHUSD"]
    for symbol in symbols:
        state = compute_pressure_state(symbol)
        if state:
            zones[symbol] = {
                "pattern": state.pattern,
                "zone_near": state.zone_near,
                "zone_distance_pct": state.zone_distance_pct,
                "price_position_in_range": state.price_position_in_range,
                "range_low": state.range_low,
                "range_high": state.range_high,
                "current_price": state.current_price,
                "recommended_asymmetry": state.recommended_asymmetry,
                "breakout_confirmed": state.breakout_confirmed,
                "breakout_direction": state.breakout_direction,
            }

    # Save for next time
    PRESSURE_ZONES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRESSURE_ZONES_PATH.write_text(json.dumps(zones, indent=2, sort_keys=True), encoding="utf-8")
    return zones


def enhance_row(row: dict[str, Any], zone: dict[str, Any] | None) -> dict[str, Any]:
    """Enhance a regime signal row with zone-aware step coefficients."""
    symbol = row["symbol"]
    buy_coeff = row.get("buy_step_coeff", 1.0)
    sell_coeff = row.get("sell_step_coeff", 1.0)
    alpha = row.get("alpha", 0.5)
    control_mode = row.get("control_mode", "wait_extreme_confirmation")

    adjustments: list[str] = []

    if zone is None:
        # No zone data — pass through unchanged
        return {
            **row,
            "zone_enhanced": False,
            "zone_adjustments": ["no_zone_data"],
        }

    pattern = zone.get("pattern", "")
    zone_near = zone.get("zone_near")
    zone_distance = zone.get("zone_distance_pct")
    price_position = zone.get("price_position_in_range", 0.5)
    breakout_confirmed = zone.get("breakout_confirmed", False)
    breakout_direction = zone.get("breakout_direction")

    # === Zone Proximity Adjustment ===
    if zone_near == "resistance":
        # At resistance — tighten SELL, widen BUY (expect rejection)
        sell_coeff *= 0.7  # 30% tighter SELL
        buy_coeff *= 1.3   # 30% wider BUY
        adjustments.append(f"resistance: SELL×0.7 BUY×1.3")

    elif zone_near == "support":
        # At support — tighten BUY, widen SELL (expect bounce)
        buy_coeff *= 0.7   # 30% tighter BUY
        sell_coeff *= 1.3  # 30% wider SELL
        adjustments.append(f"support: BUY×0.7 SELL×1.3")

    elif zone_near == "consolidation":
        # In consolidation — preserve asymmetry ratio, just narrow both equally
        # This keeps the regime's BUY vs SELL bias intact
        narrowing = 0.95  # 5% narrower in tight ranges
        buy_coeff *= narrowing
        sell_coeff *= narrowing
        adjustments.append(f"consolidation: narrow×{narrowing}")

    # === Wedge Compression (caution, don't override regime) ===
    if pattern == "wedge":
        # Narrow both sides equally — preserve regime asymmetry
        # The wedge just tells us the RANGE is compressing, not the direction
        narrowing = 0.92  # 8% narrower
        buy_coeff *= narrowing
        sell_coeff *= narrowing
        adjustments.append(f"wedge: narrow×{narrowing}")
        # Add a caution note about edge position but don't change coefficients
        if price_position > 0.95:
            adjustments.append(f"wedge_caution: near_top({price_position:.2f})")
        elif price_position < 0.05:
            adjustments.append(f"wedge_caution: near_bottom({price_position:.2f})")

    # === Breakout Confirmation ===
    if breakout_confirmed:
        if breakout_direction == "up":
            # Breakout UP confirmed — BUY-tight (catch pullbacks), SELL-wide (don't chase)
            buy_coeff = min(buy_coeff, 0.8)
            sell_coeff = max(sell_coeff, 1.5)
            adjustments.append(f"breakout_up: BUY≤0.8 SELL≥1.5")
        elif breakout_direction == "down":
            # Breakout DOWN confirmed — SELL-tight (catch rallies), BUY-wide
            sell_coeff = min(sell_coeff, 0.8)
            buy_coeff = max(buy_coeff, 1.5)
            adjustments.append(f"breakout_down: SELL≤0.8 BUY≥1.5")

    # === Price Position Refinement ===
    # Fine-tune based on exact position in range
    if zone_distance is not None and abs(zone_distance) < 1.0:
        # Within 1% of a zone — amplify the adjustment
        if zone_near == "resistance" and price_position > 0.95:
            sell_coeff *= 0.9  # Extra tight SELL at resistance
            adjustments.append(f"at_resistance_edge: SELL×0.9")
        elif zone_near == "support" and price_position < 0.05:
            buy_coeff *= 0.9  # Extra tight BUY at support
            adjustments.append(f"at_support_edge: BUY×0.9")

    # Clamp to sane bounds
    buy_coeff = max(0.3, min(3.0, buy_coeff))
    sell_coeff = max(0.3, min(3.0, sell_coeff))

    # Recompute steps with enhanced coefficients
    original_buy_step = row.get("computed_buy_step", 0)
    original_sell_step = row.get("computed_sell_step", 0)

    enhanced_buy_step = original_buy_step * (buy_coeff / row.get("buy_step_coeff", 1.0)) if original_buy_step > 0 else 0
    enhanced_sell_step = original_sell_step * (sell_coeff / row.get("sell_step_coeff", 1.0)) if original_sell_step > 0 else 0

    # Determine enhanced control mode
    asymmetry_ratio = buy_coeff / max(0.01, sell_coeff)
    if asymmetry_ratio > 1.3:
        enhanced_control_mode = "BUY-tight"
    elif asymmetry_ratio < 0.77:
        enhanced_control_mode = "SELL-tight"
    else:
        enhanced_control_mode = "symmetric"

    return {
        **row,
        "zone_enhanced": True,
        "zone_data": zone,
        "original_buy_step_coeff": row.get("buy_step_coeff", 1.0),
        "original_sell_step_coeff": row.get("sell_step_coeff", 1.0),
        "enhanced_buy_step_coeff": round(buy_coeff, 3),
        "enhanced_sell_step_coeff": round(sell_coeff, 3),
        "original_buy_step": round(original_buy_step, 6),
        "original_sell_step": round(original_sell_step, 6),
        "enhanced_buy_step": round(enhanced_buy_step, 6),
        "enhanced_sell_step": round(enhanced_sell_step, 6),
        "enhanced_control_mode": enhanced_control_mode,
        "zone_adjustments": adjustments,
    }


def main():
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        return

    try:
        # Load regime signals
        regime_data = json.loads(REGIME_SIGNAL_PATH.read_text(encoding="utf-8"))
        rows = regime_data.get("rows", [])

        # Load or compute zones
        zones = load_zones_live()

        print(f"\n{'='*80}")
        print(f"ZONE-AWARE REGIME ENHANCER")
        print(f"{'='*80}\n")
        print(f"Regime signals: {len(rows)} symbols")
        print(f"Zone data: {len(zones)} symbols")
        print()

        enhanced_rows = []
        for row in rows:
            symbol = row["symbol"]
            zone = zones.get(symbol)
            enhanced = enhance_row(row, zone)
            enhanced_rows.append(enhanced)

            # Print summary
            if enhanced.get("zone_enhanced"):
                adj = enhanced.get("zone_adjustments", [])
                buy_orig = enhanced.get("original_buy_step", 0)
                sell_orig = enhanced.get("original_sell_step", 0)
                buy_enh = enhanced.get("enhanced_buy_step", 0)
                sell_enh = enhanced.get("enhanced_sell_step", 0)
                mode = enhanced.get("enhanced_control_mode", "")

                buy_change = f"{buy_orig:.4f} → {buy_enh:.4f}" if buy_orig > 0 else "—"
                sell_change = f"{sell_orig:.4f} → {sell_enh:.4f}" if sell_orig > 0 else "—"

                print(f"  {symbol}: {mode}")
                print(f"    BUY:  {buy_change}")
                print(f"    SELL: {sell_change}")
                print(f"    Adjustments: {'; '.join(adj)}")
                print()
            else:
                print(f"  {symbol}: (no zone data — passthrough)")
                print()

        # Build output
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "zone_enhanced_regime",
            "description": "Regime signals enhanced with pressure zone detection",
            "rows": enhanced_rows,
        }

        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nEnhanced regime data: {OUTPUT_JSON}")

        # Summary of control modes
        modes: dict[str, list[str]] = {}
        for row in enhanced_rows:
            mode = row.get("enhanced_control_mode", row.get("control_mode", "unknown"))
            modes.setdefault(mode, []).append(row["symbol"])

        print("\nEnhanced Control Modes:")
        for mode, symbols in sorted(modes.items()):
            print(f"  {mode}: {', '.join(symbols)}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
