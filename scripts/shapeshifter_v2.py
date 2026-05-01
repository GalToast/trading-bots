#!/usr/bin/env python3
"""Shapeshifter v2 Adaptation Layer — Continuous regime-based parameter switching.

This module provides:
1. `detect_regime(bars)` — classifies current market regime every bar
2. `select_personality(regime, symbol_config)` — picks personality from shapeshifter config
3. `apply_personality(engine, personality, atr)` — mutates engine state with new params

Usage from runner:
    from shapeshifter_v2 import detect_regime, select_personality, apply_personality

    # In the runner's main loop, every N bars:
    regime = detect_regime(recent_bars)
    personality = select_personality(regime, shapeshifter_config)
    if personality != engine.current_personality:
        apply_personity(engine, personality, current_atr)
        engine.current_personality = personality
"""
from __future__ import annotations

import math
import statistics
from typing import Any

# === Personality definitions (matches hungry_hippo_shapeshifter.json) ===
PERSONALITIES = {
    "chop": {
        "step_ratio": 0.8,
        "asymmetry": 1.0,
        "max_open_per_side": 12,
        "close_alpha": 0.2,
        "close_style": "all_profitable",
        "anchor_mode": "fixed",
        "escape_bars": 8,
        "escape_threshold_usd": 5,
        "rearm_cooldown_bars": 6,
        "momentum_gate": False,
    },
    "chop_aggressive": {
        "step_ratio": 0.7,
        "asymmetry": 1.0,
        "max_open_per_side": 12,
        "close_alpha": 0.1,
        "close_style": "all_profitable",
        "anchor_mode": "fixed",
        "escape_bars": 5,
        "escape_threshold_usd": 3,
        "rearm_cooldown_bars": 3,
        "momentum_gate": False,
    },
    "breakout": {
        "step_ratio": 1.0,
        "asymmetry": 3.0,
        "max_open_per_side": 8,
        "close_alpha": 0.5,
        "close_style": "all_profitable",
        "anchor_mode": "trailing",
        "escape_bars": 10,
        "escape_threshold_usd": 8,
        "rearm_cooldown_bars": 12,
        "momentum_gate": True,
    },
    "trend": {
        "step_ratio": 1.2,
        "asymmetry": 8.0,
        "max_open_per_side": 4,
        "close_alpha": 0.8,
        "close_style": "outer",
        "anchor_mode": "trailing",
        "escape_bars": 15,
        "escape_threshold_usd": 10,
        "rearm_cooldown_bars": 20,
        "momentum_gate": True,
    },
    "defensive": {
        "step_ratio": 2.0,
        "asymmetry": 1.0,
        "max_open_per_side": 3,
        "close_alpha": 0.05,
        "close_style": "outer",
        "anchor_mode": "fixed",
        "escape_bars": 3,
        "escape_threshold_usd": 2,
        "rearm_cooldown_bars": 30,
        "momentum_gate": True,
    },
}


def compute_atr(bars: list[dict], period: int = 14) -> float:
    """Compute ATR from a list of bar dicts with high/low/close keys."""
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        tr = max(
            float(bars[i]["high"]) - float(bars[i]["low"]),
            abs(float(bars[i]["high"]) - float(bars[i-1]["close"])),
            abs(float(bars[i]["low"]) - float(bars[i-1]["close"])),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


def compute_adx(bars: list[dict], period: int = 14) -> float:
    """Compute ADX from bar data using Wilder's smoothing."""
    if len(bars) < period * 2:
        return 0.0

    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    closes = [float(b["close"]) for b in bars]

    plus_dm = []
    minus_dm = []
    trs = []

    for i in range(1, len(highs)):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]

        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)

        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
        trs.append(tr)

    def wilder_smooth(values: list[float], period: int) -> float:
        if len(values) < period:
            return 0
        result = sum(values[:period]) / period
        for i in range(period, len(values)):
            result = (result * (period - 1) + values[i]) / period
        return result

    atr = wilder_smooth(trs, period)
    if atr == 0:
        return 0.0

    plus_di = 100 * wilder_smooth(plus_dm, period) / atr
    minus_di = 100 * wilder_smooth(minus_dm, period) / atr

    if plus_di + minus_di == 0:
        return 0.0

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    return dx


def detect_regime(bars: list[dict], adx_period: int = 14, atr_period: int = 14) -> dict:
    """Detect current market regime from recent bars.
    
    Returns dict with:
        regime: "chop" | "breakout" | "trend" | "defensive"
        adx: ADX value
        atr: ATR value
        atr_pct: ATR as % of price
        trend_direction: "up" | "down" | "neutral"
        price_position: position in recent range (0-1)
    """
    if len(bars) < 30:
        return {
            "regime": "defensive",
            "adx": 0,
            "atr": 0,
            "atr_pct": 0,
            "trend_direction": "neutral",
            "price_position": 0.5,
        }

    adx = compute_adx(bars, adx_period)
    atr = compute_atr(bars, atr_period)

    closes = [float(b["close"]) for b in bars[-atr_period:]]
    avg_price = sum(closes) / len(closes)
    atr_pct = (atr / avg_price * 100) if avg_price > 0 else 0

    # Price position in recent range
    recent_highs = [float(b["high"]) for b in bars[-30:]]
    recent_lows = [float(b["low"]) for b in bars[-30:]]
    range_high = max(recent_highs)
    range_low = min(recent_lows)
    current_price = closes[-1]
    price_position = (current_price - range_low) / (range_high - range_low) if (range_high - range_low) > 0 else 0.5

    # Trend direction from recent closes
    recent_closes = closes[-10:]
    if len(recent_closes) >= 5:
        first_half = sum(recent_closes[:5]) / 5
        second_half = sum(recent_closes[-5:]) / 5
        if second_half > first_half * 1.001:
            trend_direction = "up"
        elif second_half < first_half * 0.999:
            trend_direction = "down"
        else:
            trend_direction = "neutral"
    else:
        trend_direction = "neutral"

    # Regime classification
    if adx < 20:
        regime = "chop" if atr_pct >= 0.5 else "chop_aggressive"
    elif adx < 30:
        regime = "breakout"
    elif adx >= 30:
        regime = "trend"
    else:
        regime = "defensive"

    return {
        "regime": regime,
        "adx": round(adx, 1),
        "atr": round(atr, 4),
        "atr_pct": round(atr_pct, 2),
        "trend_direction": trend_direction,
        "price_position": round(price_position, 3),
    }


def select_personality(
    regime: str,
    trend_direction: str = "neutral",
    price_position: float = 0.5,
    shapeshifter_config: dict | None = None,
) -> str:
    """Select personality based on regime classification.
    
    The personality names match PERSONALITIES dict keys.
    """
    # Default mapping: regime → personality
    regime_to_personality = {
        "chop": "chop",
        "chop_aggressive": "chop_aggressive",
        "breakout": "breakout",
        "trend": "trend",
        "defensive": "defensive",
    }

    personality = regime_to_personality.get(regime, "chop")

    # For breakout/trend, consider trend direction for asymmetry notes
    # (The personality itself handles asymmetry via the asymmetry parameter)

    return personality


def apply_personality(
    engine,
    personality_name: str,
    atr: float,
    dry_run: bool = False,
) -> dict:
    """Apply personality params to a running TickEngineState.
    
    Mutates the engine's step sizes, alpha, escape params, etc.
    
    Args:
        engine: TickStatefulRearmEngine or TickBoundedRearmEngine instance
        personality_name: One of PERSONALITIES keys
        atr: Current ATR value for step computation
        dry_run: If True, return what would change without applying
    
    Returns:
        Dict describing what was changed
    """
    p = PERSONALITIES.get(personality_name)
    if p is None:
        return {"error": f"Unknown personality: {personality_name}"}

    step_base = atr * p["step_ratio"]

    # Compute asymmetric steps
    if p["asymmetry"] > 1.0:
        asym_sqrt = math.sqrt(p["asymmetry"])
        step_buy = step_base / asym_sqrt
        step_sell = step_base * asym_sqrt
    else:
        step_buy = step_base
        step_sell = step_base

    changes = {
        "personality": personality_name,
        "step_buy": round(step_buy, 6),
        "step_sell": round(step_sell, 6),
        "close_alpha": p["close_alpha"],
        "close_style": p["close_style"],
        "escape_bars": p["escape_bars"],
        "escape_threshold_usd": p["escape_threshold_usd"],
        "max_open_per_side": p["max_open_per_side"],
        "anchor_mode": p["anchor_mode"],
        "rearm_cooldown_bars": p["rearm_cooldown_bars"],
        "momentum_gate": p["momentum_gate"],
    }

    if not dry_run:
        # Apply to engine
        engine.base_step_buy_px = step_buy
        engine.base_step_sell_px = step_sell
        engine.close_alpha = p["close_alpha"]
        engine.close_style = p["close_style"]
        engine.escape_bars = p["escape_bars"]
        engine.escape_threshold_usd = p["escape_threshold_usd"]
        engine.cooldown_bars = p["rearm_cooldown_bars"]
        engine.momentum_gate = p["momentum_gate"]

        # Update gap based on max_open
        engine.sell_gap = p["max_open_per_side"]
        engine.buy_gap = p["max_open_per_side"]

        # Handle anchor mode change
        if p["anchor_mode"] == "trailing":
            # Set anchor to trail — this is handled by the runner's anchor update logic
            # The engine needs to know it should trail
            if hasattr(engine, "anchor_mode"):
                engine.anchor_mode = "trailing"
        else:
            if hasattr(engine, "anchor_mode"):
                engine.anchor_mode = "fixed"

    return changes


def regime_check_for_runner(
    bars: list[dict],
    engine,
    current_personality: str,
    shapeshifter_config: dict | None = None,
    regime_check_interval_bars: int = 5,
    bar_counter: int = 0,
    hysteresis_bars: int = 3,
    hysteresis_state: dict | None = None,
) -> dict:
    """Main entry point for runner integration.

    Call this every `regime_check_interval_bars` from the runner's main loop.

    Args:
        bars: Recent bars (at least 30 for reliable regime detection)
        engine: The running engine
        current_personality: Current personality name
        shapeshifter_config: Optional shapeshifter config for symbol overrides
        regime_check_interval_bars: How often to check regime
        bar_counter: Current bar count
        hysteresis_bars: How many consecutive bars of new regime before switching
        hysteresis_state: Dict with {"pending": str|None, "count": int} — caller tracks this

    Returns:
        Dict with:
            changed: bool — whether personality changed
            regime: regime detection result
            personality: selected personality
            changes: what was applied (if changed)
            skip: bool — whether to skip this check (not yet at interval)
            hysteresis_state: updated state for caller to persist
    """
    if hysteresis_state is None:
        hysteresis_state = {"pending": None, "count": 0}

    if bar_counter % regime_check_interval_bars != 0:
        return {"skip": True}

    regime = detect_regime(bars)
    personality = select_personality(
        regime["regime"],
        regime["trend_direction"],
        regime["price_position"],
        shapeshifter_config,
    )

    # Hysteresis logic
    if personality == current_personality:
        # Same regime, reset pending
        hysteresis_state["pending"] = None
        hysteresis_state["count"] = 0
        return {
            "skip": False,
            "changed": False,
            "regime": regime,
            "personality": personality,
            "hysteresis_state": hysteresis_state,
        }
    elif personality == hysteresis_state["pending"]:
        # Same pending personality, increment counter
        hysteresis_state["count"] += 1
        if hysteresis_state["count"] >= hysteresis_bars:
            # Confirmed — execute the flip
            changes = apply_personality(engine, personality, regime["atr"])
            hysteresis_state["pending"] = None
            hysteresis_state["count"] = 0
            return {
                "skip": False,
                "changed": True,
                "regime": regime,
                "personality": personality,
                "changes": changes,
                "hysteresis_state": hysteresis_state,
            }
    else:
        # New personality detected, start hysteresis counter
        hysteresis_state["pending"] = personality
        hysteresis_state["count"] = 0

    return {
        "skip": False,
        "changed": False,
        "regime": regime,
        "personality": personality,
        "hysteresis_state": hysteresis_state,
    }


# === CLI for testing ===
if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    # Test with synthetic data
    import random
    random.seed(42)

    # Generate synthetic bars
    base_price = 15000.0
    bars = []
    for i in range(200):
        ts = 1700000000 + i * 900  # M15
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

    print("=== Shapeshifter v2 — Regime Detection Test ===")
    print()

    # Run regime detection every 10 bars
    current_personality = "chop"
    changes_count = 0

    for i in range(30, len(bars), 5):
        window = bars[:i]
        regime = detect_regime(window)
        personality = select_personality(regime["regime"])

        if personality != current_personality:
            changes_count += 1
            print(f"  Bar {i:4d}: REGIME FLIP {current_personality} → {personality}")
            print(f"    ADX={regime['adx']:.1f}  ATR%={regime['atr_pct']:.2f}%  "
                  f"Trend={regime['trend_direction']}  Pos={regime['price_position']:.2f}")
            current_personality = personality

    print()
    print(f"Total personality changes: {changes_count} across {len(bars)} bars")
    print(f"Regime checks: {len(bars) - 30} windows")
    print(f"Flip rate: {changes_count / max(1, len(bars) - 30) * 100:.1f}%")
