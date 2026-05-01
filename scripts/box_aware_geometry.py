#!/usr/bin/env python3
"""
BOX-AWARE GEOMETRY MAPPER — The Boss's 12-Year Truth Codified

Combines:
1. Box detector position (WHERE price is in the box: support/middle/resistance)
2. Tuning asymmetry ratio (the proven BUY/SELL step ratio from historical optimization)
3. Zone context (wedge/flag/consolidation/breakout)

Output: Final step_buy and step_sell values for the HH runner.

The Formula:
  base_step = ATR × regime_coeff  (from tuning/regime signal)
  asymmetry = tuning_buy_coeff / tuning_sell_coeff  (from 300-combo sweep)
  box_adjustment = f(box_position, box_height, pattern)  (from box detector)

  step_buy  = base_step × asymmetry_buy × box_adjustment_buy
  step_sell = base_step × asymmetry_sell × box_adjustment_sell

The box adjustment:
  - At support (box_position < 0.15): tighten BUY (catch bounce), widen SELL (don't chase)
  - At resistance (box_position > 0.85): tighten SELL (catch rejection), widen BUY (don't chase)
  - In middle (0.3 < pos < 0.7): symmetric moderate
  - In wedge: narrow both (compression = less room)
  - In breakout: widen against-breakout side, tighten continuation side
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
BOX_STATE_PATH = ROOT / "reports" / "box_state.json"
ZONE_STATE_PATH = ROOT / "reports" / "price_zone_state.json"
REGIME_SIGNAL_PATH = ROOT / "reports" / "regime_signal.json"
OUTPUT_PATH = ROOT / "reports" / "box_aware_geometry.json"


@dataclass
class GeometryOutput:
    symbol: str
    step_buy: float
    step_sell: float
    step_base: float
    asymmetry_ratio: float  # step_sell / step_buy (>1 = BUY-tight, <1 = SELL-tight)
    box_position: float
    box_bottom: float
    box_top: float
    box_height_pct: float
    pattern: str
    adjustment_reason: str


def load_box_state() -> dict[str, Any]:
    """Load the box detector output."""
    if BOX_STATE_PATH.exists():
        return json.loads(BOX_STATE_PATH.read_text(encoding="utf-8"))
    return {}


def load_zone_state() -> dict[str, Any]:
    """Load the zone detector output."""
    if ZONE_STATE_PATH.exists():
        return json.loads(ZONE_STATE_PATH.read_text(encoding="utf-8"))
    return {}


def load_regime_signal() -> dict[str, Any]:
    """Load the regime signal (tuning coefficients)."""
    if REGIME_SIGNAL_PATH.exists():
        return json.loads(REGIME_SIGNAL_PATH.read_text(encoding="utf-8"))
    return {}


def compute_box_adjustment(
    box_position: float,
    box_height_pct: float,
    pattern: str,
    asymmetry_from_tuning: float,
) -> tuple[float, float, str]:
    """
    Compute box-based step adjustments.

    Returns:
        (buy_adjustment, sell_adjustment, reason)
        - buy_adjustment: multiplier for step_buy (0.5 = tighten, 2.0 = widen)
        - sell_adjustment: multiplier for step_sell
        - reason: human-readable explanation
    """
    buy_adj = 1.0
    sell_adj = 1.0
    reason = "neutral"

    # === Box Position Adjustment ===
    if box_position < 0 or box_position > 1.0:
        # Price is OUTSIDE the box — breakout
        if box_position > 1.0:
            # Above box — breakout up
            buy_adj *= 0.8   # Tight BUY (catch pullback in new range)
            sell_adj *= 1.4  # Wide SELL (don't chase breakout)
            reason = f"breakout_above({box_position:.2f}): BUY-tight continuation"
        else:
            # Below box — breakdown
            sell_adj *= 0.8  # Tight SELL (catch rally in new range)
            buy_adj *= 1.4   # Wide BUY (don't chase breakdown)
            reason = f"breakdown_below({box_position:.2f}): SELL-tight continuation"

    elif box_position < 0.15:
        # Near support — tighten BUY to catch bounce, widen SELL
        buy_adj *= 0.7   # 30% tighter BUY
        sell_adj *= 1.3  # 30% wider SELL
        reason = f"support({box_position:.2f}): BUY×0.7 SELL×1.3"

    elif box_position > 0.85:
        # Near resistance — tighten SELL to catch rejection, widen BUY
        sell_adj *= 0.7  # 30% tighter SELL
        buy_adj *= 1.3   # 30% wider BUY
        reason = f"resistance({box_position:.2f}): SELL×0.7 BUY×1.3"

    elif 0.3 < box_position < 0.7:
        # In middle — stay symmetric, slight narrowing
        buy_adj *= 0.95
        sell_adj *= 0.95
        reason = f"middle({box_position:.2f}): both×0.95"

    # === Box Height Adjustment ===
    # Tight box (< 0.5%): narrow both (less room to maneuver)
    if box_height_pct < 0.005:
        buy_adj *= 0.85
        sell_adj *= 0.85
        reason += f" + tight_box({box_height_pct:.3f}%)"

    # Wide box (> 3%): widen both (more room to oscillate)
    elif box_height_pct > 0.03:
        buy_adj *= 1.15
        sell_adj *= 1.15
        reason += f" + wide_box({box_height_pct:.3f}%)"

    # === Pattern Adjustment ===
    if pattern == "wedge":
        # Compressing range — narrow both sides
        buy_adj *= 0.9
        sell_adj *= 0.9
        reason += " + wedge_narrow"

    elif pattern == "flag":
        # Trend pause — keep asymmetry from tuning, don't change
        reason += " + flag_hold"

    elif pattern in ("breakout_up", "breakthrough_resistance"):
        # Breakout up — BUY-tight (catch pullbacks), SELL-wide (don't chase)
        buy_adj *= 0.8
        sell_adj *= 1.4
        reason += " + breakout_up"

    elif pattern in ("breakout_down", "breakthrough_support"):
        # Breakout down — SELL-tight (catch rallies), BUY-wide (don't chase)
        sell_adj *= 0.8
        buy_adj *= 1.4
        reason += " + breakout_down"

    # === Asymmetry Reconciliation ===
    # If tuning says BUY-tight (asymmetry > 1.3) but box says SELL-tight (near resistance),
    # we keep the tuning asymmetry but apply a MODERATE box adjustment
    if asymmetry_from_tuning > 1.3 and sell_adj < buy_adj:
        # Tuning: BUY-tight, Box: SELL-tight → moderate both
        buy_adj *= 1.1
        sell_adj *= 1.1
        reason += " + reconcile_buy_tight"

    elif asymmetry_from_tuning < 0.77 and buy_adj < sell_adj:
        # Tuning: SELL-tight, Box: BUY-tight → moderate both
        buy_adj *= 1.1
        sell_adj *= 1.1
        reason += " + reconcile_sell_tight"

    # Clamp to sane bounds
    buy_adj = max(0.3, min(3.0, buy_adj))
    sell_adj = max(0.3, min(3.0, sell_adj))

    return buy_adj, sell_adj, reason


def compute_geometry_for_symbol(symbol: str, *, configured_step: float = 0.0) -> GeometryOutput | None:
    """Compute box-aware geometry for a single symbol.

    Args:
        symbol: The symbol to compute geometry for.
        configured_step: The runner's configured --step value. If provided, this is used
            as the base step and box adjustments are applied as multipliers around it.
            If not provided, falls back to the legacy box-height-based computation.
    """
    box_state = load_box_state()
    zone_state = load_zone_state()
    regime = load_regime_signal()

    # Get box data (nested structure)
    box_data = box_state.get(symbol, {})
    if not box_data:
        return None

    box = box_data.get("box", {})
    box_bottom = box.get("bottom", 0.0)
    box_top = box.get("top", 0.0)
    box_position = box.get("current_position", 0.5)
    box_height_pct = box.get("height_pct", 0.0)
    bounces = box.get("top_bounces", 0) + box.get("bottom_bounces", 0)

    if box_bottom <= 0 or box_top <= 0:
        return None

    # Get zone pattern
    zone_data = zone_state.get(symbol, {})
    pattern = zone_data.get("pattern", "consolidation")

    # === NEW: Use configured_step as base if provided ===
    if configured_step > 0:
        base_step = configured_step
    else:
        # Fallback: compute from box height (legacy behavior)
        regime_rows = regime.get("rows", [])
        regime_row = None
        for row in regime_rows:
            if row.get("symbol") == symbol:
                regime_row = row
                break
        if regime_row is None:
            base_step = (box_top - box_bottom) * 0.25
        else:
            base_step = regime_row.get("computed_step", 0.0)
            if base_step <= 0:
                base_step = (box_top - box_bottom) * 0.25

    # Get tuning coefficients from regime signal
    regime_rows = regime.get("rows", [])
    regime_row = None
    for row in regime_rows:
        if row.get("symbol") == symbol:
            regime_row = row
            break

    if regime_row is None:
        # Fallback: symmetric
        buy_coeff = 1.0
        sell_coeff = 1.0
        alpha = 0.5
    else:
        buy_coeff = regime_row.get("buy_step_coeff", 1.0)
        sell_coeff = regime_row.get("sell_step_coeff", 1.0)
        alpha = regime_row.get("alpha", 0.5)

    # Compute asymmetry from tuning
    asymmetry_from_tuning = buy_coeff / max(0.01, sell_coeff)

    # Compute box adjustments
    buy_adj, sell_adj, reason = compute_box_adjustment(
        box_position, box_height_pct, pattern, asymmetry_from_tuning
    )

    # Final geometry
    step_buy = base_step * buy_coeff * buy_adj
    step_sell = base_step * sell_coeff * sell_adj

    # Final asymmetry ratio (buy/sell — >1 means BUY step is wider, <1 means BUY step is tighter)
    # For display: BUY-tight means buy_step < sell_step (catches pullbacks more frequently)
    # SELL-tight means sell_step < buy_step (catches rallies more frequently)
    final_asymmetry = step_buy / max(0.01, step_sell)

    return GeometryOutput(
        symbol=symbol,
        step_buy=round(step_buy, 6),
        step_sell=round(step_sell, 6),
        step_base=round(base_step, 6),
        asymmetry_ratio=round(final_asymmetry, 3),
        box_position=round(box_position, 3),
        box_bottom=round(box_bottom, 6),
        box_top=round(box_top, 6),
        box_height_pct=round(box_height_pct, 4),
        pattern=pattern,
        adjustment_reason=reason,
    )


def main():
    print(f"\n{'='*80}")
    print(f"BOX-AWARE GEOMETRY MAPPER — Combined Edge")
    print(f"{'='*80}\n")

    box_state = load_box_state()
    if not box_state:
        print("⚠️ No box state found. Run box_detector.py first.")
        print(f"   Expected: {BOX_STATE_PATH}")
        return

    symbols = list(box_state.keys())
    print(f"Symbols in box state: {', '.join(symbols)}\n")

    results: dict[str, Any] = {}
    md_lines = [
        "# Box-Aware Geometry — Combined Edge",
        "",
        "| Symbol | Box Position | Pattern | Step BUY | Step SELL | Asymmetry | Adjustment |",
        "|--------|-------------|---------|----------|-----------|-----------|------------|",
    ]

    for symbol in symbols:
        geom = compute_geometry_for_symbol(symbol)
        if geom is None:
            print(f"  {symbol}: ⚠️ No geometry computed")
            continue

        results[symbol] = {
            "step_buy": geom.step_buy,
            "step_sell": geom.step_sell,
            "step_base": geom.step_base,
            "asymmetry_ratio": geom.asymmetry_ratio,
            "box_position": geom.box_position,
            "box_bottom": geom.box_bottom,
            "box_top": geom.box_top,
            "box_height_pct": geom.box_height_pct,
            "pattern": geom.pattern,
            "adjustment_reason": geom.adjustment_reason,
        }

        # Determine asymmetry label
        # final_asymmetry = step_buy / step_sell
        # > 1.3: BUY step wider → SELL-tight (SELL catches more frequently)
        # < 0.77: BUY step tighter → BUY-tight (BUY catches more frequently)
        if geom.asymmetry_ratio > 1.3:
            asym_label = "SELL-tight"
        elif geom.asymmetry_ratio < 0.77:
            asym_label = "BUY-tight"
        else:
            asym_label = "symmetric"

        print(f"  {symbol}:")
        print(f"    Box: {geom.box_bottom} - {geom.box_top} (pos={geom.box_position:.2f}, height={geom.box_height_pct:.2f}%)")
        print(f"    Pattern: {geom.pattern}")
        print(f"    Step BUY:  {geom.step_buy}")
        print(f"    Step SELL: {geom.step_sell}")
        print(f"    Asymmetry: {asym_label} ({geom.asymmetry_ratio:.2f})")
        print(f"    Adjustment: {geom.adjustment_reason}")
        print()

        md_lines.append(
            f"| {symbol} | {geom.box_position:.2f} | {geom.pattern} | "
            f"{geom.step_buy} | {geom.step_sell} | **{asym_label}** ({geom.asymmetry_ratio:.2f}) | "
            f"{geom.adjustment_reason} |"
        )

    # Write outputs
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")

    md_path = ROOT / "reports" / "box_aware_geometry.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"JSON: {OUTPUT_PATH}")
    print(f"Report: {md_path}")


if __name__ == "__main__":
    main()
