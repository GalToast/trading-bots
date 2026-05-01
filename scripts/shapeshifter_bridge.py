#!/usr/bin/env python3
"""Combined Shapeshifter Bridge — Zone-Aware Geometry + Regime Personality.

Combines @main-agent's zone-aware geometry (price-action-first) with
@main's shapeshifter_v2 regime detection (ADX/ATR-based) for the
complete Hungry Hippo v2 adaptation layer.

The formula:
    step_buy = ATR × zone_buy_coeff × regime_step_ratio
    step_sell = ATR × zone_sell_coeff × regime_step_ratio
    alpha = zone_alpha × regime_close_alpha / 0.5  # normalized

Plus: escape params, max_open, anchor_mode from regime personality.

Usage (in HH runner):
    from shapeshifter_bridge import check_and_adapt

    # Call every N bars
    result = check_and_adapt(
        engine=self,
        symbol="NAS100",
        bars=recent_bars,
        zone_state_path="reports/price_zone_state.json",
        current_personality=self.current_personality,
        hysteresis_state=self.hysteresis_state,
        bar_counter=self.bar_counter,
    )
    if result.get("changed"):
        self.current_personality = result["personality"]
"""
from __future__ import annotations

import math
from typing import Any

# Import zone-aware geometry
from zone_aware_geometry import get_zone_aware_geometry, load_zone_state

# Import regime detection
from shapeshifter_v2 import (
    detect_regime,
    select_personality,
    apply_personality,
    PERSONALITIES,
    compute_atr,
)


def combined_geometry(
    symbol: str,
    zone_state_path: str,
    regime_result: dict,
    bars: list[dict],
    atr: float | None = None,
) -> dict:
    """Combine zone-aware coefficients with regime personality.

    Args:
        symbol: Symbol name
        zone_state_path: Path to price_zone_state.json
        regime_result: Output from regime_check_for_runner or detect_regime
        bars: Recent bars for ATR computation
        atr: Pre-computed ATR (optional)

    Returns:
        Dict with step_buy, step_sell, alpha, escape_bars, escape_threshold_usd,
        max_open_per_side, anchor_mode, close_style
    """
    # Get zone-aware coefficients
    zone_buy_coeff, zone_sell_coeff, zone_alpha = get_zone_aware_geometry(
        symbol=symbol,
        zone_state_path=zone_state_path,
        atr=1.0,  # We'll multiply by ATR ourselves
    )

    # Get regime personality
    personality_name = regime_result.get("personality", "chop")
    personality = PERSONALITIES.get(personality_name, PERSONALITIES["chop"])
    regime_step_ratio = personality["step_ratio"]
    regime_alpha = personality["close_alpha"]

    # Compute ATR if not provided
    if atr is None:
        atr = compute_atr(bars) or 1.0

    # Combined geometry: zone coefficients × regime step ratio
    step_buy = atr * zone_buy_coeff * regime_step_ratio
    step_sell = atr * zone_sell_coeff * regime_step_ratio

    # Combined alpha: blend zone alpha with regime alpha
    # Normalize: zone_alpha is 0.2-0.8, regime_alpha is 0.05-0.8
    # Take the more aggressive (higher) value for faster closes in trending regimes
    alpha = max(zone_alpha, regime_alpha)

    return {
        "step_buy": round(step_buy, 6),
        "step_sell": round(step_sell, 6),
        "alpha": round(alpha, 3),
        "close_style": personality["close_style"],
        "escape_bars": personality["escape_bars"],
        "escape_threshold_usd": personality["escape_threshold_usd"],
        "max_open_per_side": personality["max_open_per_side"],
        "anchor_mode": personality["anchor_mode"],
        "rearm_cooldown_bars": personality["rearm_cooldown_bars"],
        "momentum_gate": personality["momentum_gate"],
        "zone_buy_coeff": round(zone_buy_coeff, 2),
        "zone_sell_coeff": round(zone_sell_coeff, 2),
        "zone_alpha": round(zone_alpha, 2),
        "personality": personality_name,
        "regime": regime_result.get("regime", {}),
    }


def apply_combined_geometry(
    engine,
    geometry: dict,
    dry_run: bool = False,
) -> dict:
    """Apply combined geometry to a running engine.

    Args:
        engine: TickStatefulRearmEngine or TickBoundedRearmEngine instance
        geometry: Output from combined_geometry()
        dry_run: If True, return what would change without applying

    Returns:
        Dict describing what was changed
    """
    changes = {
        "step_buy": geometry["step_buy"],
        "step_sell": geometry["step_sell"],
        "alpha": geometry["alpha"],
        "close_style": geometry["close_style"],
        "escape_bars": geometry["escape_bars"],
        "escape_threshold_usd": geometry["escape_threshold_usd"],
        "max_open_per_side": geometry["max_open_per_side"],
        "anchor_mode": geometry["anchor_mode"],
        "zone_buy_coeff": geometry["zone_buy_coeff"],
        "zone_sell_coeff": geometry["zone_sell_coeff"],
        "personality": geometry["personality"],
    }

    if not dry_run and engine is not None:
        engine.base_step_buy_px = geometry["step_buy"]
        engine.base_step_sell_px = geometry["step_sell"]
        engine.close_alpha = geometry["alpha"]
        engine.close_style = geometry["close_style"]
        engine.escape_bars = geometry["escape_bars"]
        engine.escape_threshold_usd = geometry["escape_threshold_usd"]
        engine.cooldown_bars = geometry["rearm_cooldown_bars"]
        engine.momentum_gate = geometry["momentum_gate"]
        engine.sell_gap = geometry["max_open_per_side"]
        engine.buy_gap = geometry["max_open_per_side"]

        if hasattr(engine, "anchor_mode"):
            engine.anchor_mode = geometry["anchor_mode"]

    return changes


def check_and_adapt(
    engine,
    symbol: str,
    bars: list[dict],
    zone_state_path: str,
    current_personality: str,
    hysteresis_state: dict | None = None,
    bar_counter: int = 0,
    regime_check_interval_bars: int = 5,
    hysteresis_bars: int = 3,
    atr: float | None = None,
) -> dict:
    """Main entry point for runner integration.

    Combines zone detection + regime detection → applies combined geometry.

    Args:
        engine: The running engine
        symbol: Symbol name
        bars: Recent bars (at least 30)
        zone_state_path: Path to price_zone_state.json
        current_personality: Current personality name
        hysteresis_state: Dict with {"pending": str|None, "count": int}
        bar_counter: Current bar count
        regime_check_interval_bars: How often to check regime
        hysteresis_bars: How many consecutive bars before switching
        atr: Pre-computed ATR (optional)

    Returns:
        Dict with changed, regime, personality, geometry, changes, skip
    """
    if hysteresis_state is None:
        hysteresis_state = {"pending": None, "count": 0}

    if bar_counter % regime_check_interval_bars != 0:
        return {"skip": True, "hysteresis_state": hysteresis_state}

    # Detect regime
    regime = detect_regime(bars)
    personality = select_personality(
        regime["regime"],
        regime["trend_direction"],
        regime["price_position"],
    )

    # Hysteresis logic
    if personality == current_personality:
        hysteresis_state["pending"] = None
        hysteresis_state["count"] = 0
        # Still apply zone-aware geometry with current personality
        geometry = combined_geometry(symbol, zone_state_path, {
            "personality": current_personality,
            "regime": regime,
        }, bars, atr)
        changes = apply_combined_geometry(engine, geometry)
        return {
            "skip": False,
            "changed": False,
            "regime": regime,
            "personality": personality,
            "geometry": geometry,
            "changes": changes,
            "hysteresis_state": hysteresis_state,
        }
    elif personality == hysteresis_state["pending"]:
        hysteresis_state["count"] += 1
        if hysteresis_state["count"] >= hysteresis_bars:
            # Confirmed — apply combined geometry
            geometry = combined_geometry(symbol, zone_state_path, {
                "personality": personality,
                "regime": regime,
            }, bars, atr)
            changes = apply_combined_geometry(engine, geometry)
            hysteresis_state["pending"] = None
            hysteresis_state["count"] = 0
            return {
                "skip": False,
                "changed": True,
                "regime": regime,
                "personality": personality,
                "geometry": geometry,
                "changes": changes,
                "hysteresis_state": hysteresis_state,
            }
    else:
        hysteresis_state["pending"] = personality
        hysteresis_state["count"] = 0

    # Not yet confirmed, but still apply zone-aware geometry with current personality
    geometry = combined_geometry(symbol, zone_state_path, {
        "personality": current_personality,
        "regime": regime,
    }, bars, atr)
    changes = apply_combined_geometry(engine, geometry)
    return {
        "skip": False,
        "changed": False,
        "regime": regime,
        "personality": personality,
        "geometry": geometry,
        "changes": changes,
        "hysteresis_state": hysteresis_state,
    }


# ── CLI (for testing) ─────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import random
    from pathlib import Path

    ROOT = Path(__file__).resolve().parent.parent
    REPORTS = ROOT / "reports"

    # Generate synthetic bars for testing
    random.seed(42)
    base_price = 15000.0
    bars = []
    for i in range(200):
        ts = 1700000000 + i * 900
        change_pct = random.gauss(0, 0.005)
        base_price *= (1 + change_pct)
        h = base_price * (1 + abs(random.gauss(0, 0.002)))
        l = base_price * (1 - abs(random.gauss(0, 0.002)))
        o = base_price * (1 + random.gauss(0, 0.001))
        bars.append({
            "start": ts,
            "open": o,
            "high": h,
            "low": l,
            "close": base_price,
        })

    # Test with zone state (if available)
    zone_state_path = REPORTS / "price_zone_state.json"

    print("=== Shapeshifter Bridge — Combined Geometry Test ===")
    print()

    # Run regime check every 5 bars
    current_personality = "chop"
    hysteresis_state = {"pending": None, "count": 0}
    changes_count = 0

    for i in range(30, len(bars), 5):
        window = bars[:i]

        result = check_and_adapt(
            engine=None,  # Dry run — apply_combined_geometry skips when engine is None
            symbol="NAS100",
            bars=window,
            zone_state_path=str(zone_state_path),
            current_personality=current_personality,
            hysteresis_state=hysteresis_state,
            bar_counter=i,
        )

        if result.get("changed"):
            changes_count += 1
            geometry = result["geometry"]
            print(f"  Bar {i:4d}: PERSONALITY FLIP {current_personality} → {result['personality']}")
            print(f"    ADX={result['regime']['adx']:.1f}  "
                  f"step_buy={geometry['step_buy']:.2f}  "
                  f"step_sell={geometry['step_sell']:.2f}  "
                  f"alpha={geometry['alpha']:.2f}  "
                  f"zone_coeff=({geometry['zone_buy_coeff']:.1f}/{geometry['zone_sell_coeff']:.1f})")
            current_personality = result["personality"]

    print()
    print(f"Total personality changes: {changes_count} across {len(bars)} bars")
