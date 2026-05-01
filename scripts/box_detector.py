#!/usr/bin/env python3
"""
The Box Detector — Simplest Possible Support/Resistance Finder

What the boss described after 12 years: "a little line bouncing around a square box."

This finds THE BOX — the support and resistance levels that price is
trading between right now. No ADX, no RSI, no complex indicators.
Just: where has price bounced, and what's the box dimensions?

Architecture:
1. Find the highest high and lowest low from recent bars
2. Find where price has bounced at least 2× (support or resistance)
3. Define the box: top = resistance, bottom = support
4. Track where price is within the box (0% = at bottom, 100% = at top)
5. Output: box geometry for HH lattice placement

The HH uses the box to:
- Place BUY steps near the bottom (support)
- Place SELL steps near the top (resistance)
- Tighten steps at edges (extract bounces)
- Widen steps in the middle (don't chase)
- Flip geometry when price breaks out of the box

Reads from: MT5 bar data
Writes to: reports/box_state.json
Consumed by: HH runner (box-aware lattice placement)
"""
import MetaTrader5 as mt5
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Box Detection ─────────────────────────────────────────────────────

def find_box(bars, lookback: int = 60, min_bounces: int = 2) -> dict | None:
    """
    Find the box (support/resistance range) from recent bars.

    A level is valid if:
    1. Price touched or came within 0.1% of the level
    2. Price reversed direction after touching (bounce confirmed)
    3. This happened at least min_bounces times

    Returns:
    {
        "top": float,           # resistance level
        "bottom": float,        # support level
        "height": float,        # top - bottom
        "height_pct": float,    # height as percentage of price
        "current_position": float,  # 0.0 = at bottom, 1.0 = at top
        "top_bounces": int,     # number of bounces at resistance
        "bottom_bounces": int,  # number of bounces at support
        "box_age_bars": int,    # how long the box has been valid
        "breakout_direction": str,  # "up", "down", "none"
    }
    """
    if len(bars) < lookback:
        return None

    recent = bars[-lookback:]
    closes = [b["close"] for b in recent]
    highs = [b["high"] for b in recent]
    lows = [b["low"] for b in recent]
    current_price = closes[-1]

    # Find candidate levels (where price reversed)
    support_candidates = []
    resistance_candidates = []

    for i in range(2, len(recent) - 1):
        bar = recent[i]
        prev = recent[i - 1]
        next_bar = recent[i + 1]

        # Support: low is lower than neighbors, price bounced up
        if bar["low"] < prev["low"] and bar["low"] < next_bar["low"]:
            if next_bar["close"] > bar["low"]:  # Confirmed bounce
                support_candidates.append(bar["low"])

        # Resistance: high is higher than neighbors, price bounced down
        if bar["high"] > prev["high"] and bar["high"] > next_bar["high"]:
            if next_bar["close"] < bar["high"]:  # Confirmed rejection
                resistance_candidates.append(bar["high"])

    if len(support_candidates) < min_bounces or len(resistance_candidates) < min_bounces:
        # Fallback: use recent high/low
        support = min(lows)
        resistance = max(highs)
        bottom_bounces = 1
        top_bounces = 1
    else:
        # Cluster support candidates into a level
        support_candidates.sort()
        support = sum(support_candidates[:min(5, len(support_candidates))]) / min(5, len(support_candidates))
        bottom_bounces = len(support_candidates)

        # Cluster resistance candidates into a level
        resistance_candidates.sort(reverse=True)
        resistance = sum(resistance_candidates[:min(5, len(resistance_candidates))]) / min(5, len(resistance_candidates))
        top_bounces = len(resistance_candidates)

    # Ensure support < resistance
    if support >= resistance:
        support = min(lows)
        resistance = max(highs)

    height = resistance - support
    height_pct = (height / current_price * 100) if current_price > 0 else 0

    # Where is current price within the box?
    if height > 0:
        current_position = (current_price - support) / height
    else:
        current_position = 0.5

    # Check for breakout
    breakout_direction = "none"
    if current_price > resistance * 1.001:  # 0.1% above resistance
        breakout_direction = "up"
    elif current_price < support * 0.999:  # 0.1% below support
        breakout_direction = "down"

    # Box age: how long has price been within this range?
    box_age = 0
    for i in range(len(recent) - 1, -1, -1):
        if support <= recent[i]["close"] <= resistance:
            box_age += 1
        else:
            break

    return {
        "top": round(resistance, 5),
        "bottom": round(support, 5),
        "height": round(height, 5),
        "height_pct": round(height_pct, 4),
        "current_position": round(current_position, 3),
        "top_bounces": top_bounces,
        "bottom_bounces": bottom_bounces,
        "box_age_bars": box_age,
        "breakout_direction": breakout_direction,
        "current_price": round(current_price, 5),
    }


# ── Box-Aware Geometry ───────────────────────────────────────────────

def box_to_geometry(box: dict) -> dict:
    """
    Map box position to recommended HH geometry.

    The closer price is to a box edge, the tighter the steps should be
    on the OPPOSITE side (to catch the bounce).

    At support (position 0.0) → BUY-tight (catch the bounce up)
    At resistance (position 1.0) → SELL-tight (catch the rejection down)
    In middle (position 0.5) → symmetric (harvest both)
    Breaking out → asymmetric (ride the breakout)
    """
    if box is None:
        return {"action": "HOLD", "reason": "No box detected", "buy_coeff": 1.0, "sell_coeff": 1.0, "alpha": 0.5}

    position = box["current_position"]
    breakout = box["breakout_direction"]
    bounces = box["top_bounces"] + box["bottom_bounces"]

    # More bounces = stronger levels = tighter steps at edges
    edge_strength = min(1.0, bounces / 6.0)  # 6+ bounces = max strength

    if breakout == "up":
        return {
            "action": "BUY_TIGHT_BREAKOUT",
            "reason": f"Breaking above box top ({box['top']}) — ride the breakout",
            "buy_coeff": 0.7,
            "sell_coeff": 1.5,
            "alpha": 0.6,
        }
    elif breakout == "down":
        return {
            "action": "SELL_TIGHT_BREAKOUT",
            "reason": f"Breaking below box bottom ({box['bottom']}) — ride the breakdown",
            "buy_coeff": 1.5,
            "sell_coeff": 0.7,
            "alpha": 0.6,
        }
    elif position < 0.25:
        # Near support — BUY-tight
        tightness = 1.0 - (position / 0.25) * 0.5 * edge_strength  # 0.5 to 1.0
        return {
            "action": "BUY_TIGHT",
            "reason": f"Near box support ({box['bottom']}) — catch the bounce up",
            "buy_coeff": 0.5 + tightness * 0.3,  # 0.5 to 0.8
            "sell_coeff": 1.2 + (1 - tightness) * 0.5,  # 1.2 to 1.7
            "alpha": 0.3,
        }
    elif position > 0.75:
        # Near resistance — SELL-tight
        tightness = ((position - 0.75) / 0.25) * 0.5 * edge_strength  # 0.0 to 0.5
        return {
            "action": "SELL_TIGHT",
            "reason": f"Near box resistance ({box['top']}) — catch the rejection down",
            "buy_coeff": 1.2 + (1 - tightness) * 0.5,  # 1.2 to 1.7
            "sell_coeff": 0.5 + tightness * 0.3,  # 0.5 to 0.8
            "alpha": 0.3,
        }
    else:
        # In the middle — symmetric, moderate
        return {
            "action": "SYMMETRIC_MODERATE",
            "reason": f"Inside box ({box['bottom']} — {box['top']}) — harvest both sides",
            "buy_coeff": 0.8,
            "sell_coeff": 0.8,
            "alpha": 0.4,
        }


# ── Main ──────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS = [
    "GBPUSD", "EURUSD", "USDJPY", "NZDUSD",
    "NAS100", "US30",
    "BTCUSD", "ETHUSD",
    "XAUUSD",
]


def probe_symbol(symbol: str, bar_count: int = 100) -> dict:
    """Probe a symbol for box structure."""
    mt5.initialize()

    bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, bar_count)
    if bars is None or len(bars) < 30:
        return {"error": f"Insufficient data for {symbol}"}

    box = find_box(bars, lookback=60, min_bounces=2)
    geometry = box_to_geometry(box) if box else {"error": "No box found"}

    result = {
        "symbol": symbol,
        "box": box,
        "geometry": geometry,
        "detected_at": utc_now_iso(),
    }

    mt5.shutdown()
    return result


def probe_all_symbols(symbols=None, bar_count: int = 100) -> dict:
    """Probe all symbols for box structure."""
    if symbols is None:
        symbols = list(DEFAULT_SYMBOLS)

    mt5.initialize()
    results = {}

    for sym in symbols:
        bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, bar_count)
        if bars is None or len(bars) < 30:
            results[sym] = {"error": f"Insufficient data for {sym}"}
            continue

        box = find_box(bars, lookback=60, min_bounces=2)
        geometry = box_to_geometry(box) if box else {"error": "No box found"}

        results[sym] = {
            "symbol": sym,
            "box": box,
            "geometry": geometry,
            "detected_at": utc_now_iso(),
        }

    mt5.shutdown()
    return results


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    symbols = list(DEFAULT_SYMBOLS)

    results = probe_all_symbols(symbols, bar_count=120)

    print(f"{'Symbol':<10} {'Box Bottom':>12} {'Box Top':>12} {'Height%':>8} {'Position':>10} {'Bounces':>9} {'Breakout':>10} {'Geometry Action'}")
    print("-" * 130)

    for sym, data in sorted(results.items()):
        if "error" in data:
            print(f"{sym:<10} {'ERROR':>12} {'ERROR':>12} {'N/A':>8} {'N/A':>10} {'N/A':>9} {'N/A':>10} {data['error']}")
            continue

        box = data["box"]
        if box is None:
            print(f"{sym:<10} {'No box':>12} {'No box':>12} {'N/A':>8} {'N/A':>10} {'N/A':>9} {'N/A':>10} No box detected")
            continue

        geometry = data["geometry"]
        action = geometry.get("action", "UNKNOWN")
        reason = geometry.get("reason", "")[:50]

        position = box["current_position"]
        pos_bar = "█" * int(position * 10) + "░" * (10 - int(position * 10))

        breakout = box["breakout_direction"]
        breakout_str = "↗ UP" if breakout == "up" else ("↘ DOWN" if breakout == "down" else "— none")

        print(f"{sym:<10} {box['bottom']:>12.5f} {box['top']:>12.5f} {box['height_pct']:>7.3f}% [{pos_bar}] {box['top_bounces'] + box['bottom_bounces']:>9} {breakout_str:>10} {action} — {reason}")

    # Save report
    report_dir = Path(__file__).parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "box_state.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
