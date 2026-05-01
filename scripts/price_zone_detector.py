#!/usr/bin/env python3
"""
Price Zone Detector — Understanding the Language Price Speaks

After 12 years of watching price, you know:
- Price respects levels (support/resistance from prior reactions)
- Price consolidates before it moves (energy building)
- Wedges compress → breakout
- Flags pause → continuation
- Price reacts at pressure zones (where buyers/sellers fought before)
- When price CAN'T overcome pressure → rejection, reversal, wick
- When price CAN overcome pressure → momentum, follow-through, retest

This detector finds ZONES (not lines) and classifies what price is DOING at them.

Architecture:
1. Build zone map from recent price history (where did price react before?)
2. Detect current price behavior relative to zones
3. Classify: consolidation, approach, rejection, breakout, retest
4. Output: recommended HH geometry per zone behavior

The HH responds:
- At zone, price rejecting → tight steps on rejection side (extract the bounce)
- At zone, price consolidating → tight symmetric (extract micro-oscillations)
- Breaking through zone → wide steps on breakout side (ride the momentum)
- After breakout, retesting zone → tight steps at zone level (extract the retest bounce)
- In wedge/flag → widen steps, prepare for breakout

Reads from: MT5 bar data (builds zone map) + tick stream (current behavior)
Writes to: reports/price_zone_state.json
Consumed by: HH runner (zone-aware step adjustment), escape hatches, auto-flip
"""
import MetaTrader5 as mt5
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Zone Builder ───────────────────────────────────────────────────────

def build_zone_map(bars, min_touches: int = 2, zone_width_pct: float = 0.1):
    """
    Build a map of price zones from bar history.

    A zone is a price level where price has reacted (reversed, bounced, rejected)
    at least min_touches times. Zones are ranges, not lines.

    Returns: list of zones sorted by recency
    [
        {
            "center": float,       # midpoint of zone
            "top": float,          # upper bound
            "bottom": float,       # lower bound
            "touches": int,        # number of times price reacted here
            "last_touch": int,     # timestamp of last reaction
            "type": str,           # "support", "resistance", "both"
            "strength": float,     # 0-1 (more touches = stronger)
            "recent_reactions": [  # last few reactions at this zone
                {"type": "bounce_up", "timestamp": int, "depth": float},
                ...
            ]
        },
        ...
    ]
    """
    if len(bars) < 20:
        return []

    # Find all potential reaction points
    reactions = []
    for i in range(2, len(bars) - 1):
        bar = bars[i]
        prev_bar = bars[i - 1]
        next_bar = bars[i + 1]

        # Swing high: bar's high is higher than neighbors
        if bar["high"] > prev_bar["high"] and bar["high"] > next_bar["high"]:
            reactions.append({
                "price": bar["high"],
                "time": int(bar["time"]),
                "type": "resistance_test",
            })

        # Swing low: bar's low is lower than neighbors
        if bar["low"] < prev_bar["low"] and bar["low"] < next_bar["low"]:
            reactions.append({
                "price": bar["low"],
                "time": int(bar["time"]),
                "type": "support_test",
            })

        # Wick rejection: long wick, small body = rejection at that level
        body = abs(bar["close"] - bar["open"])
        total_range = bar["high"] - bar["low"]
        if total_range > 0:
            upper_wick = bar["high"] - max(bar["open"], bar["close"])
            lower_wick = min(bar["open"], bar["close"]) - bar["low"]

            if upper_wick > body * 2 and upper_wick > total_range * 0.4:
                # Long upper wick = rejection at high
                reactions.append({
                    "price": bar["high"],
                    "time": int(bar["time"]),
                    "type": "rejection_high",
                })

            if lower_wick > body * 2 and lower_wick > total_range * 0.4:
                # Long lower wick = rejection at low
                reactions.append({
                    "price": bar["low"],
                    "time": int(bar["time"]),
                    "type": "rejection_low",
                })

    if len(reactions) < 2:
        return []

    # Cluster reactions into zones
    reactions.sort(key=lambda r: r["price"])
    zones = []
    used = set()

    for i in range(len(reactions)):
        if i in used:
            continue

        zone_price = reactions[i]["price"]
        zone_width = zone_price * zone_width_pct / 100  # percentage-based width
        zone_reactions = [reactions[i]]
        used.add(i)

        # Find all reactions within zone width
        for j in range(i + 1, len(reactions)):
            if j in used:
                continue
            if abs(reactions[j]["price"] - zone_price) <= zone_width:
                zone_reactions.append(reactions[j])
                used.add(j)

        # Only keep zones with min_touches
        if len(zone_reactions) >= min_touches:
            prices = [r["price"] for r in zone_reactions]
            times = [r["time"] for r in zone_reactions]

            # Determine zone type
            types = [r["type"] for r in zone_reactions]
            has_support = any("support" in t or "rejection_low" in t for t in types)
            has_resistance = any("resistance" in t or "rejection_high" in t for t in types)

            if has_support and has_resistance:
                zone_type = "both"
            elif has_support:
                zone_type = "support"
            else:
                zone_type = "resistance"

            zones.append({
                "center": sum(prices) / len(prices),
                "top": max(prices),
                "bottom": min(prices),
                "touches": len(zone_reactions),
                "last_touch": max(times),
                "type": zone_type,
                "strength": min(1.0, len(zone_reactions) / 5.0),  # 5+ touches = max strength
                "recent_reactions": zone_reactions[-5:],  # last 5 reactions
            })

    # Sort by last_touch (most recent first)
    zones.sort(key=lambda z: z["last_touch"], reverse=True)
    return zones[:10]  # Keep top 10 most recent zones


# ── Behavior Classifier ────────────────────────────────────────────────

def classify_behavior_at_zone(current_price: float, recent_bars: list, zones: list) -> dict:
    """
    Classify what price is DOING relative to the nearest zone.

    Behaviors:
    - "consolidating": price moving sideways near a zone (energy building)
    - "approaching": price moving toward a zone (will it bounce or break?)
    - "rejecting": price hit zone and is bouncing away (rejection)
    - "breaking_through": price is punching through zone level (breakout)
    - "retesting": price broke through, now coming back to test zone
    - "free": price not near any zone (no resistance)

    Returns:
    {
        "behavior": str,
        "nearest_zone": dict or None,
        "distance_to_zone_pct": float,
        "zone_type": str,
        "confidence": float,
        "recommended_geometry": dict,
    }
    """
    if not zones or len(recent_bars) == 0:
        return {
            "behavior": "no_data",
            "nearest_zone": None,
            "distance_to_zone_pct": None,
            "zone_type": None,
            "confidence": 0.0,
            "recommended_geometry": {"action": "HOLD"},
        }

    current = current_price
    closes = [b["close"] for b in recent_bars[-20:]]
    highs = [b["high"] for b in recent_bars[-20:]]
    lows = [b["low"] for b in recent_bars[-20:]]

    # Find nearest zone
    nearest_zone = None
    nearest_distance = float("inf")
    for zone in zones:
        if zone["bottom"] <= current <= zone["top"]:
            # Price is INSIDE the zone
            distance = 0
        else:
            distance = min(abs(current - zone["top"]), abs(current - zone["bottom"]))
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_zone = zone

    if nearest_zone is None:
        return {
            "behavior": "free",
            "nearest_zone": None,
            "distance_to_zone_pct": None,
            "zone_type": None,
            "confidence": 0.0,
            "recommended_geometry": {"action": "RIDE_TREND", "reason": "no zones nearby"},
        }

    distance_pct = (nearest_distance / current * 100) if current > 0 else 0
    zone_type = nearest_zone["type"]
    inside_zone = nearest_zone["bottom"] <= current <= nearest_zone["top"]

    # Determine behavior
    behavior = "approaching"
    confidence = 0.5

    if inside_zone:
        # Price is inside the zone — what is it doing?
        if len(closes) >= 5:
            recent_range = max(highs[-5:]) - min(lows[-5:])
            avg_bar_range = sum(highs[i] - lows[i] for i in range(-5, 0)) / 5

            if avg_bar_range > 0 and recent_range < avg_bar_range * 2:
                # Tight range = consolidation
                behavior = "consolidating"
                confidence = 0.8
            else:
                # Volatile inside zone = breakout attempt
                # Check if price is moving toward top or bottom
                if closes[-1] > closes[-3]:
                    behavior = "breaking_through" if zone_type in ("resistance", "both") else "rejecting"
                else:
                    behavior = "breaking_through" if zone_type in ("support", "both") else "rejecting"
                confidence = 0.6
    else:
        # Price is outside the zone
        # Check if approaching or moving away
        if len(closes) >= 5:
            recent_trend = closes[-1] - closes[-5]

            if zone_type == "resistance":
                if recent_trend > 0 and distance_pct < 0.5:
                    behavior = "approaching"
                    confidence = 0.7
                elif recent_trend < 0:
                    behavior = "rejecting"
                    confidence = 0.8
            elif zone_type == "support":
                if recent_trend < 0 and distance_pct < 0.5:
                    behavior = "approaching"
                    confidence = 0.7
                elif recent_trend > 0:
                    behavior = "rejecting"
                    confidence = 0.8
            else:  # both
                behavior = "approaching"
                confidence = 0.5

        # Check for retest (price broke through zone, now coming back)
        if len(closes) >= 10:
            # Was price on the other side 10 bars ago?
            was_above = closes[-10] > nearest_zone["top"]
            was_below = closes[-10] < nearest_zone["bottom"]
            now_above = current > nearest_zone["top"]
            now_below = current < nearest_zone["bottom"]

            if was_above and now_below and distance_pct < 0.3:
                behavior = "retesting"
                confidence = 0.8
            elif was_below and now_above and distance_pct < 0.3:
                behavior = "retesting"
                confidence = 0.8

    # Map behavior to geometry
    geometry = _behavior_to_geometry(behavior, zone_type, distance_pct, nearest_zone["strength"])

    return {
        "behavior": behavior,
        "nearest_zone": {
            "center": nearest_zone["center"],
            "top": nearest_zone["top"],
            "bottom": nearest_zone["bottom"],
            "type": nearest_zone["type"],
            "strength": nearest_zone["strength"],
        },
        "distance_to_zone_pct": round(distance_pct, 4),
        "zone_type": zone_type,
        "confidence": round(confidence, 3),
        "recommended_geometry": geometry,
    }


def _behavior_to_geometry(behavior, zone_type, distance_pct, zone_strength):
    """Map zone behavior to recommended HH geometry."""
    if behavior == "consolidating":
        return {
            "action": "SYMMETRIC_TIGHT",
            "reason": f"Consolidating near {zone_type} zone — extract micro-oscillations",
            "buy_step_coeff": 0.6,
            "sell_step_coeff": 0.6,
            "alpha": 0.3,
        }

    elif behavior == "approaching":
        if zone_type == "resistance":
            return {
                "action": "TIGHTEN_SELL",
                "reason": f"Approaching resistance — prepare for rejection",
                "buy_step_coeff": 1.2,
                "sell_step_coeff": 0.7,
                "alpha": 0.4,
            }
        elif zone_type == "support":
            return {
                "action": "TIGHTEN_BUY",
                "reason": f"Approaching support — prepare for bounce",
                "buy_step_coeff": 0.7,
                "sell_step_coeff": 1.2,
                "alpha": 0.4,
            }
        else:
            return {
                "action": "SYMMETRIC_MODERATE",
                "reason": f"Approaching {zone_type} zone",
                "buy_step_coeff": 0.9,
                "sell_step_coeff": 0.9,
                "alpha": 0.5,
            }

    elif behavior == "rejecting":
        if zone_type == "resistance":
            return {
                "action": "SELL_TIGHT",
                "reason": f"Rejected at resistance — harvest the drop",
                "buy_step_coeff": 1.5,
                "sell_step_coeff": 0.5,
                "alpha": 0.3,
            }
        elif zone_type == "support":
            return {
                "action": "BUY_TIGHT",
                "reason": f"Bounced off support — harvest the rally",
                "buy_step_coeff": 0.5,
                "sell_step_coeff": 1.5,
                "alpha": 0.3,
            }
        else:
            return {
                "action": "WAIT",
                "reason": f"Rejected at {zone_type} zone, direction unclear",
                "buy_step_coeff": 1.0,
                "sell_step_coeff": 1.0,
                "alpha": 0.5,
            }

    elif behavior == "breaking_through":
        return {
            "action": "RIDE_BREAKOUT",
            "reason": f"Breaking through zone — ride the momentum",
            "buy_step_coeff": 0.8,
            "sell_step_coeff": 1.5,
            "alpha": 0.6,
        }

    elif behavior == "retesting":
        return {
            "action": "TIGHT_AT_ZONE",
            "reason": f"Retesting zone — extract the bounce",
            "buy_step_coeff": 0.5,
            "sell_step_coeff": 0.5,
            "alpha": 0.2,
        }

    elif behavior == "free":
        return {
            "action": "RIDE_TREND",
            "reason": "No zones nearby — follow trend",
            "buy_step_coeff": 0.8,
            "sell_step_coeff": 1.2,
            "alpha": 0.5,
        }

    else:
        return {
            "action": "HOLD",
            "reason": "Unknown behavior",
            "buy_step_coeff": 1.0,
            "sell_step_coeff": 1.0,
            "alpha": 0.5,
        }


# ── Consolidation/Wedge/Flag Detector ─────────────────────────────────

def detect_chart_patterns(bars, lookback: int = 30) -> dict:
    """
    Detect consolidation, wedge, and flag patterns.

    Returns:
    {
        "pattern": str,  # "consolidation", "wedge", "flag", "none"
        "details": dict,
        "recommended_geometry": dict,
    }
    """
    if len(bars) < lookback:
        return {"pattern": "insufficient_data", "details": {}, "recommended_geometry": {"action": "HOLD"}}

    recent = bars[-lookback:]
    closes = [b["close"] for b in recent]
    highs = [b["high"] for b in recent]
    lows = [b["low"] for b in recent]

    # Consolidation: range is shrinking over time
    first_half_range = max(highs[:lookback // 2]) - min(lows[:lookback // 2])
    second_half_range = max(highs[lookback // 2:]) - min(lows[lookback // 2:])

    if first_half_range > 0:
        range_change = second_half_range / first_half_range
    else:
        range_change = 1.0

    # Wedge: price making higher highs (or lower lows) but range shrinking
    first_half_highs = highs[:lookback // 2]
    second_half_highs = highs[lookback // 2:]
    if second_half_highs:
        hh_increasing = second_half_highs[-1] > first_half_highs[0] if first_half_highs else False
        ll_increasing = min(second_half_highs) > min(first_half_highs) if first_half_highs else False
    else:
        hh_increasing = False
        ll_increasing = False

    # Flag: strong move followed by consolidation in opposite direction
    first_move = closes[lookback // 2] - closes[0]
    second_move = closes[-1] - closes[lookback // 2]

    # Classification
    if range_change < 0.6:
        # Range shrinking significantly
        if hh_increasing and ll_increasing:
            pattern = "wedge_up"
        elif not hh_increasing and not ll_increasing:
            pattern = "wedge_down"
        else:
            pattern = "consolidation"
    elif abs(first_move) > abs(second_move) * 3 and abs(second_move) > 0:
        # Strong move followed by weak counter-move = flag
        if first_move > 0 and second_move < 0:
            pattern = "bull_flag"
        elif first_move < 0 and second_move > 0:
            pattern = "bear_flag"
        else:
            pattern = "none"
    else:
        pattern = "none"

    # Map to geometry
    if pattern == "consolidation":
        geometry = {
            "action": "SYMMETRIC_TIGHT",
            "reason": "Consolidation — extract micro-oscillations in range",
            "buy_step_coeff": 0.5,
            "sell_step_coeff": 0.5,
            "alpha": 0.2,
        }
    elif pattern.startswith("wedge"):
        direction = "up" if "up" in pattern else "down"
        geometry = {
            "action": "WIDEN_PREPARE_BREAKOUT",
            "reason": f"Wedge {direction} — compression building for breakout",
            "buy_step_coeff": 1.3,
            "sell_step_coeff": 1.3,
            "alpha": 0.7,
        }
    elif pattern.endswith("_flag"):
        direction = "bull" if "bull" in pattern else "bear"
        if direction == "bull":
            geometry = {
                "action": "BUY_TIGHT",
                "reason": "Bull flag — prepare for continuation up",
                "buy_step_coeff": 0.7,
                "sell_step_coeff": 1.3,
                "alpha": 0.4,
            }
        else:
            geometry = {
                "action": "SELL_TIGHT",
                "reason": "Bear flag — prepare for continuation down",
                "buy_step_coeff": 1.3,
                "sell_step_coeff": 0.7,
                "alpha": 0.4,
            }
    else:
        geometry = {"action": "HOLD", "reason": "No pattern detected"}

    return {
        "pattern": pattern,
        "details": {
            "range_change": round(range_change, 3),
            "first_move": round(first_move, 5),
            "second_move": round(second_move, 5),
        },
        "recommended_geometry": geometry,
    }


# ── Main ───────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS = [
    "GBPUSD", "EURUSD", "USDJPY", "NZDUSD",
    "NAS100", "US30",
    "BTCUSD", "ETHUSD",
    "XAUUSD",
]


def probe_symbol(symbol: str, bar_count: int = 100) -> dict:
    """Probe a symbol for zone structure and current behavior."""
    mt5.initialize()

    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, bar_count)
    if bars is None or len(bars) < 20:
        return {"error": f"Insufficient data for {symbol}"}

    # Build zone map
    zones = build_zone_map(bars, min_touches=2, zone_width_pct=0.08)

    # Detect chart patterns
    patterns = detect_chart_patterns(bars, lookback=30)

    # Classify current behavior at zones
    current_price = bars[-1]["close"]
    behavior = classify_behavior_at_zone(current_price, bars, zones)

    result = {
        "symbol": symbol,
        "current_price": round(current_price, 5),
        "zones": zones,
        "zone_count": len(zones),
        "behavior": behavior,
        "chart_pattern": patterns,
        "detected_at": utc_now_iso(),
    }

    mt5.shutdown()
    return result


def probe_all_symbols(symbols=None, bar_count: int = 100) -> dict:
    """Probe all symbols for zone structure."""
    if symbols is None:
        symbols = list(DEFAULT_SYMBOLS)

    mt5.initialize()
    results = {}

    for sym in symbols:
        bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, bar_count)
        if bars is None or len(bars) < 20:
            results[sym] = {"error": f"Insufficient data for {sym}"}
            continue

        zones = build_zone_map(bars, min_touches=2, zone_width_pct=0.08)
        patterns = detect_chart_patterns(bars, lookback=30)
        current_price = bars[-1]["close"]
        behavior = classify_behavior_at_zone(current_price, bars, zones)

        results[sym] = {
            "symbol": sym,
            "current_price": round(current_price, 5),
            "zones": zones,
            "zone_count": len(zones),
            "behavior": behavior,
            "chart_pattern": patterns,
            "detected_at": utc_now_iso(),
        }

    mt5.shutdown()
    return results


# ── CLI ─────────────────────────────────────────────────────────────────

def _action_emoji(action: str) -> str:
    emojis = {
        "SYMMETRIC_TIGHT": "⚪ Sym tight",
        "TIGHTEN_SELL": "🔴 Tight SELL",
        "TIGHTEN_BUY": "🟢 Tight BUY",
        "SELL_TIGHT": "🔴 SELL-tight",
        "BUY_TIGHT": "🟢 BUY-tight",
        "RIDE_BREAKOUT": "🚀 Breakout",
        "TIGHT_AT_ZONE": "🎯 Tight at zone",
        "RIDE_TREND": "📈 Ride trend",
        "WIDEN_PREPARE_BREAKOUT": "📐 Widen for BO",
        "WAIT": "⏸️ Wait",
        "HOLD": "⏸️ Hold",
    }
    return emojis.get(action, action)


def main():
    symbols = list(DEFAULT_SYMBOLS)

    results = probe_all_symbols(symbols, bar_count=120)

    print(f"{'Symbol':<10} {'Behavior':<18} {'Pattern':<16} {'Zones':>6} {'Dist%':>7} {'Action'}")
    print("-" * 110)

    for sym, data in sorted(results.items()):
        if "error" in data:
            print(f"{sym:<10} {'ERROR':<18} {'N/A':<16} {'N/A':>6} {data['error']}")
            continue

        behavior = data["behavior"]["behavior"]
        pattern = data["chart_pattern"]["pattern"]
        zone_count = data["zone_count"]
        dist = data["behavior"]["distance_to_zone_pct"]
        action = data["behavior"]["recommended_geometry"]["action"]
        reason = data["behavior"]["recommended_geometry"]["reason"]

        action_lbl = f"{_action_emoji(action)} — {reason[:40]}"
        dist_str = f"{dist:.3f}%" if dist is not None else "—"

        print(f"{sym:<10} {behavior:<18} {pattern:<16} {zone_count:>6} {dist_str:>7} {action_lbl}")

    # Save report
    report_dir = Path(__file__).parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "price_zone_state.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {report_path}")

    # Summary
    behaviors = {}
    for sym, data in results.items():
        if "error" not in data:
            b = data["behavior"]["behavior"]
            behaviors[b] = behaviors.get(b, 0) + 1

    print(f"\nBehavior summary:")
    for b, count in sorted(behaviors.items(), key=lambda x: -x[1]):
        print(f"  {b}: {count} symbols")


if __name__ == "__main__":
    main()
