#!/usr/bin/env python3
"""
Zone-Aware Geometry Module — The Shapeshifter Bridge

Reads zone state from price_zone_detector.py output and returns
dynamic geometry coefficients for the HH runner.

Usage (in HH runner):
    from zone_aware_geometry import get_zone_aware_geometry

    # Call every N bars
    buy_coeff, sell_coeff, alpha = get_zone_aware_geometry(
        symbol="NAS100",
        zone_state_path="reports/price_zone_state.json",
        atr=23.74,  # current ATR for step computation
    )

    # Apply to engine
    engine.base_step_buy_px = atr * buy_coeff
    engine.base_step_sell_px = atr * sell_coeff
    engine.close_alpha = alpha

The geometry overrides are based on:
1. Current zone behavior (consolidating, approaching, rejecting, breaking, retesting)
2. Chart patterns (wedge, flag, consolidation)
3. Zone proximity (inside zone, near zone, far from zone)

This is the SHAPESHIFTER — the HH geometry adapts to market structure in real-time.
"""
import json
import time
from pathlib import Path
from typing import Tuple, Optional


# ── Geometry Presets ──────────────────────────────────────────────────

BEHAVIOR_GEOMETRY = {
    "consolidating":        {"buy_coeff": 0.6, "sell_coeff": 0.6, "alpha": 0.2},
    "approaching":          {"buy_coeff": 1.0, "sell_coeff": 1.0, "alpha": 0.5},  # default, refined by zone type
    "rejecting":            {"buy_coeff": 1.0, "sell_coeff": 1.0, "alpha": 0.3},  # refined by zone type
    "breaking_through":     {"buy_coeff": 0.8, "sell_coeff": 1.5, "alpha": 0.6},
    "retesting":            {"buy_coeff": 0.5, "sell_coeff": 0.5, "alpha": 0.2},
    "free":                 {"buy_coeff": 0.8, "sell_coeff": 1.2, "alpha": 0.5},
    "no_data":              {"buy_coeff": 1.0, "sell_coeff": 1.0, "alpha": 0.5},
}

# Refine "approaching" and "rejecting" by zone type
ZONE_TYPE_REFINEMENTS = {
    "approaching": {
        "resistance": {"buy_coeff": 1.2, "sell_coeff": 0.7, "alpha": 0.4},
        "support":    {"buy_coeff": 0.7, "sell_coeff": 1.2, "alpha": 0.4},
        "both":       {"buy_coeff": 0.9, "sell_coeff": 0.9, "alpha": 0.5},
    },
    "rejecting": {
        "resistance": {"buy_coeff": 1.5, "sell_coeff": 0.5, "alpha": 0.3},
        "support":    {"buy_coeff": 0.5, "sell_coeff": 1.5, "alpha": 0.3},
        "both":       {"buy_coeff": 1.0, "sell_coeff": 1.0, "alpha": 0.5},
    },
}

# Chart pattern overrides (applied ON TOP of behavior geometry)
PATTERN_OVERRIDES = {
    "consolidation":      {"buy_coeff": 0.5, "sell_coeff": 0.5, "alpha": 0.2},
    "wedge_up":           {"buy_coeff": 1.3, "sell_coeff": 1.3, "alpha": 0.7},
    "wedge_down":         {"buy_coeff": 1.3, "sell_coeff": 1.3, "alpha": 0.7},
    "bull_flag":          {"buy_coeff": 0.7, "sell_coeff": 1.3, "alpha": 0.4},
    "bear_flag":          {"buy_coeff": 1.3, "sell_coeff": 0.7, "alpha": 0.4},
}


# ── State Cache ───────────────────────────────────────────────────────

# Cache zone state to avoid re-reading JSON every bar
_zone_state_cache = {
    "data": None,
    "loaded_at": 0.0,
    "path": None,
}


def load_zone_state(zone_state_path: str, max_age_seconds: float = 30.0) -> Optional[dict]:
    """
    Load zone state from JSON file, with caching.

    Only re-reads if file is older than max_age_seconds.
    """
    global _zone_state_cache

    path = Path(zone_state_path)
    now = time.time()

    # Check if cache is still valid
    if (
        _zone_state_cache["path"] == zone_state_path
        and _zone_state_cache["data"] is not None
        and (now - _zone_state_cache["loaded_at"]) < max_age_seconds
    ):
        return _zone_state_cache["data"]

    # Load fresh data
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _zone_state_cache["data"] = data
        _zone_state_cache["loaded_at"] = now
        _zone_state_cache["path"] = zone_state_path
        return data
    except Exception:
        return None


def get_zone_aware_geometry(
    symbol: str,
    zone_state_path: str,
    atr: float,
    max_age_seconds: float = 30.0,
) -> Tuple[float, float, float]:
    """
    Get zone-aware geometry coefficients for a symbol.

    Returns:
        (step_buy_coeff, step_sell_coeff, alpha) tuple

    Usage:
        buy_coeff, sell_coeff, alpha = get_zone_aware_geometry(
            symbol="NAS100",
            zone_state_path="reports/price_zone_state.json",
            atr=23.74,
        )
        engine.base_step_buy_px = atr * buy_coeff
        engine.base_step_sell_px = atr * sell_coeff
        engine.close_alpha = alpha
    """
    # Load zone state
    state = load_zone_state(zone_state_path, max_age_seconds)
    if state is None:
        return (1.0, 1.0, 0.5)  # default geometry

    # Find symbol's zone data
    symbol_data = state.get(symbol)
    if symbol_data is None or "error" in symbol_data:
        return (1.0, 1.0, 0.5)

    # Get behavior and pattern
    behavior = symbol_data.get("behavior", {})
    behavior_type = behavior.get("behavior", "no_data")
    zone_type = behavior.get("zone_type")
    confidence = behavior.get("confidence", 0.5)

    chart_pattern = symbol_data.get("chart_pattern", {})
    pattern = chart_pattern.get("pattern", "none")

    # Start with behavior geometry
    geo = BEHAVIOR_GEOMETRY.get(behavior_type, BEHAVIOR_GEOMETRY["no_data"])

    # Refine by zone type for "approaching" and "rejecting"
    if behavior_type in ZONE_TYPE_REFINEMENTS and zone_type:
        refinements = ZONE_TYPE_REFINEMENTS[behavior_type].get(zone_type)
        if refinements:
            geo = refinements

    # Apply pattern override (if pattern is detected and confidence is high)
    if pattern != "none" and pattern in PATTERN_OVERRIDES and confidence >= 0.6:
        pattern_geo = PATTERN_OVERRIDES[pattern]
        # Blend pattern override with behavior geometry (50/50)
        geo = {
            "buy_coeff": (geo["buy_coeff"] + pattern_geo["buy_coeff"]) / 2,
            "sell_coeff": (geo["sell_coeff"] + pattern_geo["sell_coeff"]) / 2,
            "alpha": (geo["alpha"] + pattern_geo["alpha"]) / 2,
        }

    return (geo["buy_coeff"], geo["sell_coeff"], geo["alpha"])


# ── CLI (for testing) ─────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Zone-aware geometry lookup")
    parser.add_argument("--zone-state-path", default="reports/price_zone_state.json")
    parser.add_argument("--atr", type=float, default=1.0, help="ATR for step computation")
    args = parser.parse_args()

    state = load_zone_state(args.zone_state_path, max_age_seconds=0)
    if state is None:
        print(f"No zone state found at {args.zone_state_path}")
        return

    print(f"{'Symbol':<10} {'Behavior':<18} {'Pattern':<16} {'BuyCoeff':>9} {'SellCoeff':>9} {'Alpha':>6} {'StepBuy':>9} {'StepSell':>9}")
    print("-" * 100)

    for sym, data in sorted(state.items()):
        if "error" in data:
            print(f"{sym:<10} {'ERROR':<18} {'N/A':<16} {data['error']}")
            continue

        behavior = data.get("behavior", {})
        behavior_type = behavior.get("behavior", "no_data")
        pattern = data.get("chart_pattern", {}).get("pattern", "none")

        buy_coeff, sell_coeff, alpha = get_zone_aware_geometry(
            symbol=sym,
            zone_state_path=args.zone_state_path,
            atr=args.atr,
            max_age_seconds=0,
        )

        step_buy = args.atr * buy_coeff
        step_sell = args.atr * sell_coeff

        print(f"{sym:<10} {behavior_type:<18} {pattern:<16} {buy_coeff:>9.2f} {sell_coeff:>9.2f} {alpha:>6.2f} {step_buy:>9.2f} {step_sell:>9.2f}")


if __name__ == "__main__":
    main()
