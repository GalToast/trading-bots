#!/usr/bin/env python3
"""
PRESSURE ZONE DETECTOR — Read the Language Price Speaks

Identifies:
1. Support/resistance zones (price clusters where pressure builds)
2. Consolidation ranges (balanced pressure, range-bound)
3. Wedge patterns (compressing range = pressure building)
4. Flag patterns (trend pause = continuation likely)
5. Breakout states (testing vs breaking vs bouncing)

Output: reports/pressure_zones_live.json + reports/pressure_zones_live.md

This feeds directly into the Hungry Hippo regime system.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
import numpy as np


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_JSON = ROOT / "reports" / "pressure_zones_live.json"
OUTPUT_MD = ROOT / "reports" / "pressure_zones_live.md"

SYMBOLS = ["NAS100", "US30", "EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "ETHUSD"]
DEFAULT_SYMBOL = "NAS100"
LOOKBACK_BARS = 500  # ~5 days of M15


@dataclass
class Zone:
    """A price zone (support or resistance cluster)."""
    level_low: float
    level_high: float
    touch_count: int
    last_touch_time: str
    last_touch_price: float
    zone_type: str  # "support", "resistance", "consolidation"
    strength: float  # 0-1, based on touches and recency


@dataclass
class PressureState:
    """Current pressure state for a symbol."""
    symbol: str
    current_price: float
    price_position_in_range: float  # 0=support, 1=resistance
    range_low: float
    range_high: float
    range_width: float
    zone_near: str | None  # "support", "resistance", or None
    zone_distance_pct: float | None  # % distance to nearest zone
    pattern: str  # "consolidation", "wedge", "flag", "trend", "breakout_up", "breakout_down"
    breakout_confirmed: bool
    breakout_direction: str | None
    recommended_asymmetry: str  # "BUY-tight", "SELL-tight", "symmetric"


def find_zones(prices: np.ndarray, timestamps: np.ndarray, tolerance_pct: float = 0.005) -> list[Zone]:
    """Find price clusters where price repeatedly tests the same level."""
    zones: list[Zone] = []
    n = len(prices)
    if n < 20:
        return zones

    # Find local minima and maxima
    minima = []
    maxima = []
    for i in range(1, n - 1):
        if prices[i] <= prices[i - 1] and prices[i] <= prices[i + 1]:
            minima.append((i, prices[i]))
        if prices[i] >= prices[i - 1] and prices[i] >= prices[i + 1]:
            maxima.append((i, prices[i]))

    # Cluster minima into support zones
    support_zones = _cluster_levels(minima, tolerance_pct, "support", timestamps)
    resistance_zones = _cluster_levels(maxima, tolerance_pct, "resistance", timestamps)

    zones.extend(support_zones)
    zones.extend(resistance_zones)

    # Detect consolidation (overlapping support/resistance)
    zones = _detect_consolidation(zones)

    return zones


def _cluster_levels(
    extremes: list[tuple[int, float]],
    tolerance_pct: float,
    zone_type: str,
    timestamps: np.ndarray,
) -> list[Zone]:
    """Cluster price extremes into zones."""
    if not extremes:
        return []

    # Sort by price
    extremes.sort(key=lambda x: x[1])

    zones: list[Zone] = []
    cluster: list[tuple[int, float]] = [extremes[0]]

    for i in range(1, len(extremes)):
        idx, price = extremes[i]
        cluster_center = sum(p for _, p in cluster) / len(cluster)
        if abs(price - cluster_center) / cluster_center <= tolerance_pct:
            cluster.append((idx, price))
        else:
            if len(cluster) >= 2:
                zones.append(_make_zone(cluster, zone_type, timestamps))
            cluster = [(idx, price)]

    # Don't forget the last cluster
    if len(cluster) >= 2:
        zones.append(_make_zone(cluster, zone_type, timestamps))

    return zones


def _make_zone(cluster: list[tuple[int, float]], zone_type: str, timestamps: np.ndarray) -> Zone:
    """Create a Zone from a cluster of price extremes."""
    prices = [p for _, p in cluster]
    indices = [i for i, _ in cluster]

    level_low = min(prices)
    level_high = max(prices)
    touch_count = len(cluster)

    last_touch_idx = max(indices)
    last_touch_time = datetime.fromtimestamp(int(timestamps[last_touch_idx]), tz=timezone.utc).isoformat()
    last_touch_price = prices[indices.index(last_touch_idx)]

    # Strength: more touches + more recent = stronger
    recency_factor = last_touch_idx / max(1, len(timestamps) - 1)
    strength = min(1.0, (touch_count / 10.0) * 0.6 + recency_factor * 0.4)

    return Zone(
        level_low=round(level_low, 6),
        level_high=round(level_high, 6),
        touch_count=touch_count,
        last_touch_time=last_touch_time,
        last_touch_price=round(last_touch_price, 6),
        zone_type=zone_type,
        strength=round(strength, 3),
    )


def _detect_consolidation(zones: list[Zone]) -> list[Zone]:
    """Detect consolidation zones where support and resistance overlap."""
    support_zones = [z for z in zones if z.zone_type == "support"]
    resistance_zones = [z for z in zones if z.zone_type == "resistance"]

    for s_zone in support_zones:
        for r_zone in resistance_zones:
            # Check if support and resistance zones overlap or are very close
            gap = r_zone.level_low - s_zone.level_high
            avg_price = (s_zone.level_low + r_zone.level_high) / 2
            if avg_price > 0 and gap / avg_price < 0.003:
                # Overlapping zones = consolidation
                s_zone.zone_type = "consolidation"
                s_zone.level_high = max(s_zone.level_high, r_zone.level_high)
                s_zone.zone_low = min(s_zone.level_low, r_zone.level_low)
                s_zone.strength = max(s_zone.strength, r_zone.strength)

    return zones


def detect_pattern(
    prices: np.ndarray,
    timestamps: np.ndarray,
    range_low: float,
    range_high: float,
    current_price: float,
    lookback: int = 50,
) -> str:
    """Detect the current price pattern: consolidation, wedge, flag, trend, breakout."""
    if len(prices) < lookback + 10:
        lookback = max(10, len(prices) - 10)

    recent = prices[-lookback:]
    range_width = range_high - range_low
    avg_price = (range_high + range_low) / 2

    if range_width <= 0 or avg_price <= 0:
        return "trend"

    # Check for trend (consistent direction)
    slope = (recent[-1] - recent[0]) / max(1, len(recent))
    trend_pct = abs(slope) / avg_price

    if trend_pct > 0.005:
        # Strong directional move
        if slope > 0:
            # Check if breaking resistance
            if current_price > range_high * 1.001:
                return "breakout_up"
            return "trend"
        else:
            # Check if breaking support
            if current_price < range_low * 0.999:
                return "breakout_down"
            return "trend"

    # Check for wedge (compressing range)
    first_half = recent[: len(recent) // 2]
    second_half = recent[len(recent) // 2:]
    if len(first_half) > 5 and len(second_half) > 5:
        first_range = float(np.max(first_half)) - float(np.min(first_half))
        second_range = float(np.max(second_half)) - float(np.min(second_half))
        if second_range < first_range * 0.7 and first_range > 0:
            return "wedge"

    # Check for flag (pause after trend)
    # Look at bars before the lookback window
    if len(prices) > lookback + 20:
        pre_window = prices[-(lookback + 20) : -lookback]
        pre_slope = (pre_window[-1] - pre_window[0]) / max(1, len(pre_window))
        pre_trend_pct = abs(pre_slope) / avg_price
        if pre_trend_pct > 0.003 and trend_pct < 0.001:
            return "flag"

    # Default: consolidation
    return "consolidation"


def compute_pressure_state(
    symbol: str,
    bars: int = LOOKBACK_BARS,
) -> PressureState | None:
    """Compute the current pressure state for a symbol."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return None

    # Get M15 bars
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, bars)
    if rates is None or len(rates) == 0:
        return None

    closes = np.array([r["close"] for r in rates])
    highs = np.array([r["high"] for r in rates])
    lows = np.array([r["low"] for r in rates])
    timestamps = np.array([r["time"] for r in rates])

    current_price = float(closes[-1])

    # Find zones
    zones = find_zones(closes, timestamps)

    # Compute recent range (last 100 bars)
    recent_bars = 100
    recent_closes = closes[-recent_bars:]
    range_low = float(np.min(recent_closes))
    range_high = float(np.max(recent_closes))
    range_width = range_high - range_low

    # Price position in range (0=support, 1=resistance)
    if range_width > 0:
        price_position = (current_price - range_low) / range_width
    else:
        price_position = 0.5

    # Detect pattern
    pattern = detect_pattern(closes, timestamps, range_low, range_high, current_price)

    # Find nearest zone
    nearest_zone: str | None = None
    zone_distance: float | None = None
    for z in zones:
        if z.zone_type == "consolidation":
            if z.level_low <= current_price <= z.level_high:
                nearest_zone = "consolidation"
                zone_distance = 0.0
                break
        elif z.zone_type == "support":
            dist = (current_price - z.level_high) / current_price if current_price > 0 else 999
            if dist < 0.01 and (zone_distance is None or dist < zone_distance):
                nearest_zone = "support"
                zone_distance = dist
        elif z.zone_type == "resistance":
            dist = (z.level_low - current_price) / current_price if current_price > 0 else 999
            if dist < 0.01 and (zone_distance is None or dist < zone_distance):
                nearest_zone = "resistance"
                zone_distance = dist

    # Breakout detection
    breakout_confirmed = pattern in ("breakout_up", "breakout_down")
    breakout_direction = "up" if pattern == "breakout_up" else ("down" if pattern == "breakout_down" else None)

    # Recommended asymmetry based on pressure state
    if breakout_confirmed:
        # Breakout confirmed → catch pullbacks (tight on breakout side)
        recommended = "BUY-tight" if breakout_direction == "up" else "SELL-tight"
    elif nearest_zone == "resistance":
        # At resistance → SELL-tight (expect rejection)
        recommended = "SELL-tight"
    elif nearest_zone == "support":
        # At support → BUY-tight (expect bounce)
        recommended = "BUY-tight"
    elif pattern == "wedge":
        # Wedge compressing → prepare for breakout, stay symmetric
        recommended = "symmetric"
    elif pattern == "flag":
        # Flag after trend → continue with trend asymmetry
        # Determine trend direction from recent slope
        slope = (current_price - float(closes[-recent_bars])) / max(1, current_price)
        recommended = "BUY-tight" if slope > 0 else "SELL-tight"
    elif pattern == "consolidation":
        # Range-bound → symmetric
        recommended = "symmetric"
    else:
        # Trend → BUY-tight for uptrend
        slope = (current_price - float(closes[-recent_bars])) / max(1, current_price)
        recommended = "BUY-tight" if slope > 0 else "SELL-tight"

    return PressureState(
        symbol=symbol,
        current_price=round(current_price, 6),
        price_position_in_range=round(price_position, 3),
        range_low=round(range_low, 6),
        range_high=round(range_high, 6),
        range_width=round(range_width, 6),
        zone_near=nearest_zone,
        zone_distance_pct=round(zone_distance * 100, 3) if zone_distance is not None else None,
        pattern=pattern,
        breakout_confirmed=breakout_confirmed,
        breakout_direction=breakout_direction,
        recommended_asymmetry=recommended,
    )


def main():
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        return

    try:
        print(f"\n{'='*80}")
        print(f"PRESSURE ZONE DETECTOR — Reading the Language Price Speaks")
        print(f"{'='*80}\n")

        results: dict[str, Any] = {}
        md_lines = [
            "# Pressure Zone Detection Report",
            f"\n**Generated:** {datetime.now(timezone.utc).isoformat()}",
            f"\n**Lookback:** {LOOKBACK_BARS} M15 bars (~{LOOKBACK_BARS * 15 / 60 / 24:.1f} days)",
            "\n## Symbol Pressure States\n",
            "| Symbol | Price | Pattern | Zone Near | Zone Dist % | Asymmetry | Range Low | Range High |",
            "|--------|-------|---------|-----------|-------------|-----------|-----------|------------|",
        ]

        for symbol in SYMBOLS:
            print(f"Scanning {symbol}...")
            state = compute_pressure_state(symbol)
            if state is None:
                print(f"  ⚠️ No data for {symbol}")
                continue

            results[symbol] = {
                "current_price": state.current_price,
                "price_position_in_range": state.price_position_in_range,
                "range_low": state.range_low,
                "range_high": state.range_high,
                "range_width": state.range_width,
                "pattern": state.pattern,
                "zone_near": state.zone_near,
                "zone_distance_pct": state.zone_distance_pct,
                "breakout_confirmed": state.breakout_confirmed,
                "breakout_direction": state.breakout_direction,
                "recommended_asymmetry": state.recommended_asymmetry,
            }

            zone_label = state.zone_near or "none"
            dist_label = f"{state.zone_distance_pct:.2f}%" if state.zone_distance_pct is not None else "—"

            md_lines.append(
                f"| {symbol} | {state.current_price} | {state.pattern} | {zone_label} | {dist_label} | "
                f"**{state.recommended_asymmetry}** | {state.range_low} | {state.range_high} |"
            )

            print(f"  Pattern: {state.pattern}, Zone: {zone_label} ({dist_label}), Asymmetry: {state.recommended_asymmetry}")

        # Summary insights
        md_lines.extend([
            "\n## Key Insights\n",
        ])

        buy_tight = [s for s, r in results.items() if r["recommended_asymmetry"] == "BUY-tight"]
        sell_tight = [s for s, r in results.items() if r["recommended_asymmetry"] == "SELL-tight"]
        symmetric = [s for s, r in results.items() if r["recommended_asymmetry"] == "symmetric"]
        breakouts = [s for s, r in results.items() if r["breakout_confirmed"]]

        md_lines.append(f"- **BUY-tight recommended:** {', '.join(buy_tight) or 'none'}")
        md_lines.append(f"- **SELL-tight recommended:** {', '.join(sell_tight) or 'none'}")
        md_lines.append(f"- **Symmetric recommended:** {', '.join(symmetric) or 'none'}")
        if breakouts:
            md_lines.append(f"- **🚨 Active breakouts:** {', '.join(breakouts)}")

        md_lines.extend([
            "\n## What Price is Saying\n",
            "- **Consolidation** → Pressure balanced, harvest both sides",
            "- **Wedge** → Pressure building, prepare for breakout",
            "- **Flag** → Trend resting, continue with trend",
            "- **Breakout** → Pressure overcome, follow the break",
            "- **At Support** → Pressure won downward, expect bounce",
            "- **At Resistance** → Pressure won upward, expect rejection",
        ])

        # Write outputs
        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_JSON.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
        OUTPUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

        print(f"\nJSON: {OUTPUT_JSON}")
        print(f"Report: {OUTPUT_MD}")

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
