#!/usr/bin/env python3
"""Structure-Aware Shapeshifter Bridge — wires price structure detection into the HH runner.

This is the ~20-line integration that prevents the NAS100 disaster.

Called from process_tick() every N bars:
1. Checks if we're on a bar boundary (new bar started)
2. If so, runs structure detection on recent bars
3. If structure changed, mutates engine geometry (step_buy, step_sell, alpha, escape)
4. Returns the adaptation decision for logging

Usage:
    from structure_shapeshifter_bridge import check_and_adapt

    # In process_tick(), after bar processing:
    result = check_and_adapt(
        engine=self,
        bars=self.history[-60:],
        current_bar=self._current_bar,
        check_interval_bars=5,
    )
    if result.get("changed"):
        self._record_event(event_path, "structure_flip", tick, **result)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from price_structure_detector import detect_structure, structure_to_geometry

# Track adaptation state per symbol
_ADAPTATION_STATE = {}  # symbol -> {last_structure, bar_count, pending_flip}


def check_and_adapt(
    engine,
    bars: list[dict],
    current_bar: dict | None = None,
    check_interval_bars: int = 5,
    hysteresis_bars: int = 3,
) -> dict[str, Any]:
    """Check structure and adapt engine geometry if structure changed.

    Args:
        engine: TickStatefulRearmEngine instance (mutates engine.state)
        bars: Recent completed bars (from engine.history)
        current_bar: Current in-progress bar (from engine._current_bar)
        check_interval_bars: Run structure check every N bars
        hysteresis_bars: Require N consecutive confirmations before flip

    Returns:
        Adaptation decision (empty if no change, or flip details if changed)
    """
    symbol = engine.symbol
    state = _ADAPTATION_STATE.setdefault(symbol, {
        "last_structure": None,
        "pending_structure": None,
        "pending_count": 0,
    })

    # Need enough bars for structure detection
    all_bars = list(bars)
    if current_bar:
        all_bars.append(current_bar)

    if len(all_bars) < 50:
        return {}

    # Run structure detection
    structure = detect_structure(symbol, all_bars)
    if "error" in structure:
        return {}

    current_structure = structure.get("primary_structure")
    if current_structure is None:
        return {}

    # Hysteresis: require N confirmations before flip
    if current_structure != state["last_structure"]:
        if state["pending_structure"] == current_structure:
            state["pending_count"] += 1
        else:
            state["pending_structure"] = current_structure
            state["pending_count"] = 1
    else:
        state["pending_structure"] = None
        state["pending_count"] = 0

    # Has the new structure been confirmed?
    if state["pending_count"] < hysteresis_bars:
        return {}

    # Structure flip confirmed — adapt geometry
    previous_structure = state.get("last_structure")
    geometry = structure_to_geometry(structure)
    old_step_buy = getattr(engine, 'base_step_buy_px', 0)
    old_step_sell = getattr(engine, 'base_step_sell_px', 0)
    old_alpha = getattr(engine, 'close_alpha', 0.5)

    # Convert step_mult into actual price units using ATR from the analysis
    # The lattice geometry uses step_mult as a multiplier against ATR
    atr = structure.get("atr", 1.0)
    step_mult = geometry.get("step_mult", 1.0)
    asymmetry_ratio = geometry.get("asymmetry_ratio", 1.0)
    adapted_step = atr * step_mult
    adapted_step_sell = adapted_step * asymmetry_ratio

    # Mutate engine geometry with ATR-scaled steps
    if hasattr(engine, 'base_step_buy_px'):
        engine.base_step_buy_px = adapted_step
    if hasattr(engine, 'base_step_sell_px'):
        engine.base_step_sell_px = adapted_step_sell
    if hasattr(engine, 'close_alpha'):
        engine.close_alpha = geometry.get("alpha", old_alpha)

    # Update adaptation state
    state["last_structure"] = current_structure
    state["pending_structure"] = None
    state["pending_count"] = 0

    return {
        "changed": True,
        "symbol": symbol,
        "from_structure": previous_structure if previous_structure is not None else "unknown",
        "to_structure": current_structure,
        "from_step_buy": old_step_buy,
        "to_step_buy": adapted_step,
        "from_step_sell": old_step_sell,
        "to_step_sell": adapted_step_sell,
        "from_alpha": old_alpha,
        "to_alpha": geometry.get("alpha", old_alpha),
        "asymmetry_ratio": asymmetry_ratio,
        "step_mult": step_mult,
        "atr_used": atr,
        "mode": geometry.get("mode", "atr_scaled"),
        "reason": geometry.get("reason", "structure_flip"),
        "hysteresis_bars": hysteresis_bars,
    }
