#!/usr/bin/env python3
"""
Cross-family profit-mode classifier for the adaptive lattice.

PROBLEM: The current controller derives profit_mode from the winning shape's
monetization_profile (shape-driven). This is backwards: the tape structure
should determine the mode FIRST, then shapes are scored by how well they match.

SOLUTION: A tape-driven classifier that reads market microstructure and outputs
the highest-EV profit mode independently of any shape library.

Profit modes (canonical, from adaptive doctrine):
  - micro_harvest: quiet tape, low ATR, high mean-reversion rate, spread manageable
  - trend_harvest: directional bias present, extension profitable, low same-bar churn
  - cash_repair_harvest: negative carry pressure, need fast close conversion
  - friction_survivor: spread dominates step range, survival > aggression
  - guarded_toxic_flow: toxic one-way flow, guarded admission required
  - balanced_harvest: no single dominant edge, use best balanced shape

Classification logic (decision tree):
  1. If same_bar_round_trip_rate >= 0.2 AND spread_to_step_ratio < 0.35:
     -> micro_harvest (quiet but extractable micro fluctuations)
  2. If same_bar_open_burst_count >= 3 OR first_path_verdict == 'never_green_toxic_continuation':
     -> guarded_toxic_flow (toxic burst or one-way flow)
  3. If spread_to_range_ratio >= 0.6 OR spread_to_step_ratio >= 0.35:
     -> friction_survivor (cost-dominated tape)
  4. If directional_bias >= 0.15 AND regime == 'trending' AND same_bar_round_trip_rate < 0.2:
     -> trend_harvest (directional participation strong)
  5. If close_conversion_pressure OR negative_carry_pressure:
     -> cash_repair_harvest (need fast cash conversion)
  6. Default: balanced_harvest (tradable without single dominant edge)

Each mode outputs a confidence score (0.0-1.0) and a reason string.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProfitModeResult:
    profit_mode: str
    confidence: float
    reason: str
    mode_scores: dict[str, float]  # all mode scores for transparency
    tape_signals: dict[str, Any]   # which tape signals drove the decision


# Mode thresholds — these are the "knobs" that tune the classifier
MICRO_HARVEST_ROUND_TRIP_THRESHOLD = 0.2
MICRO_HARVEST_MAX_SPREAD_STEP_RATIO = 0.35
TOXIC_BURST_THRESHOLD = 3
FRICTION_SPREAD_RANGE_THRESHOLD = 0.6
FRICTION_SPREAD_STEP_THRESHOLD = 0.35
TREND_DIRECTIONAL_BIAS_THRESHOLD = 0.15
TREND_MAX_ROUND_TRIP_RATE = 0.2

MODE_PRIORITY = [
    "micro_harvest",
    "guarded_toxic_flow",
    "friction_survivor",
    "trend_harvest",
    "cash_repair_harvest",
    "balanced_harvest",
]


def classify_profit_mode(
    *,
    same_bar_round_trip_rate: float | None = None,
    spread_to_step_ratio: float | None = None,
    spread_to_range_ratio: float | None = None,
    same_bar_open_burst_count: int = 0,
    same_tick_open_burst_count: int = 0,
    first_path_verdict: str = "",
    directional_bias: float | None = None,
    regime: str = "mixed",
    close_conversion_pressure: bool = False,
    negative_carry_pressure: bool = False,
    current_atr: float | None = None,
    atr_percentile: float | None = None,
) -> ProfitModeResult:
    """Classify the highest-EV profit mode from tape structure alone.

    This is deliberately shape-agnostic: it reads market microstructure
    and outputs the mode, NOT a shape. The controller then scores shapes
    by how well they match this mode.
    """

    # Normalize inputs
    abs_directional_bias = abs(directional_bias) if directional_bias is not None else 0.0
    burst_count = max(same_bar_open_burst_count, same_tick_open_burst_count)

    # Track which signals are present (for transparency)
    tape_signals: dict[str, Any] = {}

    # --- Score all modes ---
    mode_scores: dict[str, float] = {
        "micro_harvest": 0.0,
        "trend_harvest": 0.0,
        "cash_repair_harvest": 0.0,
        "friction_survivor": 0.0,
        "guarded_toxic_flow": 0.0,
        "balanced_harvest": 0.3,  # baseline default
    }

    # Signal 1: Micro-harvest eligibility
    # Quiet tape with high mean-reversion rate, spread manageable
    if same_bar_round_trip_rate is not None and spread_to_step_ratio is not None:
        tape_signals["same_bar_round_trip_rate"] = same_bar_round_trip_rate
        tape_signals["spread_to_step_ratio"] = spread_to_step_ratio
        if (same_bar_round_trip_rate >= MICRO_HARVEST_ROUND_TRIP_THRESHOLD
                and spread_to_step_ratio < MICRO_HARVEST_MAX_SPREAD_STEP_RATIO):
            # Confidence scales with round-trip rate (higher = more micro opportunities)
            micro_score = min((same_bar_round_trip_rate - MICRO_HARVEST_ROUND_TRIP_THRESHOLD) * 2.0 + 0.5, 1.0)
            mode_scores["micro_harvest"] = max(mode_scores["micro_harvest"], micro_score)
            tape_signals["micro_harvest_trigger"] = True
        elif same_bar_round_trip_rate >= MICRO_HARVEST_ROUND_TRIP_THRESHOLD:
            # Round-trip rate says micro exists, but spread is too high -> friction instead
            tape_signals["micro_harvest_blocked_by_spread"] = True
            mode_scores["friction_survivor"] = max(
                mode_scores["friction_survivor"],
                min(spread_to_step_ratio / (MICRO_HARVEST_MAX_SPREAD_STEP_RATIO * 2), 1.0)
            )

    # Signal 2: Toxic flow / burst detection
    if burst_count >= TOXIC_BURST_THRESHOLD:
        tape_signals["burst_count"] = burst_count
        tape_signals["toxic_burst_trigger"] = True
        # Confidence scales with burst severity
        toxic_score = min((burst_count - TOXIC_BURST_THRESHOLD) * 0.2 + 0.7, 1.0)
        mode_scores["guarded_toxic_flow"] = max(mode_scores["guarded_toxic_flow"], toxic_score)

    if first_path_verdict == "never_green_toxic_continuation":
        tape_signals["first_path_verdict"] = first_path_verdict
        tape_signals["toxic_continuation_trigger"] = True
        mode_scores["guarded_toxic_flow"] = max(mode_scores["guarded_toxic_flow"], 0.9)

    # Signal 3: Friction domination
    if spread_to_range_ratio is not None and spread_to_step_ratio is not None:
        tape_signals["spread_to_range_ratio"] = spread_to_range_ratio
        if spread_to_range_ratio >= FRICTION_SPREAD_RANGE_THRESHOLD:
            tape_signals["friction_trigger_range"] = True
            friction_score = min(
                (spread_to_range_ratio - FRICTION_SPREAD_RANGE_THRESHOLD) * 2.0 + 0.6,
                1.0
            )
            mode_scores["friction_survivor"] = max(mode_scores["friction_survivor"], friction_score)
        if spread_to_step_ratio >= FRICTION_SPREAD_STEP_THRESHOLD:
            tape_signals["friction_trigger_step"] = True
            friction_score = min(
                (spread_to_step_ratio - FRICTION_SPREAD_STEP_THRESHOLD) * 2.0 + 0.5,
                1.0
            )
            mode_scores["friction_survivor"] = max(mode_scores["friction_survivor"], friction_score)

    # Signal 4: Trend harvesting
    if (abs_directional_bias >= TREND_DIRECTIONAL_BIAS_THRESHOLD
            and regime == "trending"
            and (same_bar_round_trip_rate is None or same_bar_round_trip_rate < TREND_MAX_ROUND_TRIP_RATE)):
        tape_signals["directional_bias"] = abs_directional_bias
        tape_signals["regime"] = regime
        tape_signals["trend_trigger"] = True
        # Confidence scales with directional bias strength
        trend_score = min(
            (abs_directional_bias - TREND_DIRECTIONAL_BIAS_THRESHOLD) * 3.0 + 0.5,
            1.0
        )
        mode_scores["trend_harvest"] = max(mode_scores["trend_harvest"], trend_score)

    # Signal 5: Cash repair pressure
    if close_conversion_pressure or negative_carry_pressure:
        tape_signals["close_conversion_pressure"] = close_conversion_pressure
        tape_signals["negative_carry_pressure"] = negative_carry_pressure
        tape_signals["cash_repair_trigger"] = True
        cash_score = 0.6
        if close_conversion_pressure and negative_carry_pressure:
            cash_score = 0.85
        elif close_conversion_pressure:
            cash_score = 0.7
        mode_scores["cash_repair_harvest"] = max(mode_scores["cash_repair_harvest"], cash_score)

    # Signal 6: Low-motion override (if ATR is essentially zero, nothing is extractable)
    if current_atr is not None and current_atr <= 0:
        tape_signals["current_atr"] = current_atr
        tape_signals["no_motion"] = True
        # All modes get suppressed; balanced stays at low baseline
        for mode in mode_scores:
            mode_scores[mode] = 0.0
        mode_scores["balanced_harvest"] = 0.1

    # --- Select winner ---
    priority_index = {mode: idx for idx, mode in enumerate(MODE_PRIORITY)}
    best_mode = max(  # type: ignore
        mode_scores,
        key=lambda mode: (
            mode_scores[mode],
            -priority_index.get(mode, len(MODE_PRIORITY)),
        ),
    )
    best_score = mode_scores[best_mode]

    # Build reason string
    reason_parts: list[str] = []
    if tape_signals.get("toxic_burst_trigger") or tape_signals.get("toxic_continuation_trigger"):
        reason_parts.append(f"toxic flow detected (burst={burst_count}, verdict={first_path_verdict})")
    if tape_signals.get("friction_trigger_range") or tape_signals.get("friction_trigger_step"):
        reason_parts.append(f"friction dominated (spread/range={spread_to_range_ratio}, spread/step={spread_to_step_ratio})")
    if tape_signals.get("trend_trigger"):
        reason_parts.append(f"trending regime (bias={abs_directional_bias:.2f}, regime={regime})")
    if tape_signals.get("cash_repair_trigger"):
        reason_parts.append("cash conversion pressure" if close_conversion_pressure else "negative carry pressure")
    if tape_signals.get("micro_harvest_trigger"):
        reason_parts.append(f"micro extractable (round-trip={same_bar_round_trip_rate:.2f}, spread/step={spread_to_step_ratio:.2f})")
    if tape_signals.get("no_motion"):
        reason_parts.append("no extractable motion (ATR <= 0)")

    if not reason_parts:
        reason_parts.append("no single dominant edge; balanced harvest baseline")

    reason = "; ".join(reason_parts)

    return ProfitModeResult(
        profit_mode=best_mode,
        confidence=round(best_score, 3),
        reason=reason,
        mode_scores={k: round(v, 3) for k, v in mode_scores.items()},
        tape_signals=tape_signals,
    )


def score_shape_for_mode(
    shape: dict[str, Any],
    target_mode: str,
    mode_confidence: float,
) -> float:
    """Score a shape by how well it matches the tape-derived profit mode.

    This REPLACES the old regime-only scoring in score_shape().
    Shapes get bonus points for matching the tape-derived mode,
    and lose points for mismatching.
    """
    mode_score = 0.0

    monetization_profile = str(shape.get("monetization_profile") or "")
    risk_profile = str(shape.get("risk_profile") or "")
    portfolio_profile = str(shape.get("portfolio_profile") or "")
    close_alpha = None
    close_data = shape.get("close")
    if close_data:
        try:
            close_alpha = float(close_data.get("alpha", 0.5))
        except (TypeError, ValueError):
            close_alpha = None

    # Mode matching (the primary signal)
    mode_matches = {
        "micro_harvest": {"micro_harvest", "cash_harvest", "friction_survivor"},
        "trend_harvest": {"trend_harvest", "trend_extension", "breakout_extension"},
        "cash_repair_harvest": {"cash_harvest", "friction_survivor"},
        "friction_survivor": {"friction_survivor", "cash_harvest"},
        "guarded_toxic_flow": set(),  # all shapes are eligible but runtime overlays apply
        "balanced_harvest": {"balanced", "friction_survivor", "cash_harvest", "trend_harvest", "micro_harvest"},
    }

    matching_profiles = mode_matches.get(target_mode, set())

    if target_mode == "guarded_toxic_flow":
        # Toxic flow is not a neutral "all shapes equal" state. The runtime overlays
        # keep the lane alive, but the selector should still prefer defensive
        # monetization, lighter inventory posture, and faster realization while the
        # tape is explicitly hostile.
        mode_score = mode_confidence * 1.0
        if monetization_profile in {"cash_harvest", "friction_survivor", "micro_harvest"}:
            mode_score += mode_confidence * 3.5
        elif monetization_profile in {"trend_harvest", "trend_extension", "breakout_extension"}:
            mode_score -= mode_confidence * 3.0

        if risk_profile == "conservative":
            mode_score += mode_confidence * 1.0
        elif risk_profile == "aggressive":
            mode_score -= mode_confidence * 1.0

        if portfolio_profile in {"light", "medium"}:
            mode_score += mode_confidence * 0.75
        elif portfolio_profile == "heavy":
            mode_score -= mode_confidence * 0.75

        if close_alpha is not None:
            if close_alpha <= 0.5:
                mode_score += mode_confidence * 0.75
            elif close_alpha > 0.8:
                mode_score -= mode_confidence * 0.75
    elif monetization_profile in matching_profiles:
        mode_score = mode_confidence * 5.0  # strong bonus for mode match
    elif monetization_profile and monetization_profile not in matching_profiles:
        mode_score = -mode_confidence * 2.0  # penalty for mismatch

    # Close alpha alignment for cash_repair and micro_harvest
    if target_mode in ("cash_repair_harvest", "micro_harvest") and close_alpha is not None:
        if close_alpha <= 0.5:
            mode_score += mode_confidence * 1.5  # fast-close bonus
        elif close_alpha > 0.8:
            mode_score -= mode_confidence * 0.5  # slow-close penalty

    # Risk profile alignment for friction_survivor
    if target_mode == "friction_survivor" and risk_profile == "conservative":
        mode_score += mode_confidence * 1.0

    return mode_score


if __name__ == "__main__":
    import json
    import argparse

    parser = argparse.ArgumentParser(description="Classify adaptive lattice profit mode from tape structure.")
    parser.add_argument("--same-bar-round-trip-rate", type=float)
    parser.add_argument("--spread-to-step-ratio", type=float)
    parser.add_argument("--spread-to-range-ratio", type=float)
    parser.add_argument("--same-bar-open-burst-count", type=int, default=0)
    parser.add_argument("--same-tick-open-burst-count", type=int, default=0)
    parser.add_argument("--first-path-verdict", default="")
    parser.add_argument("--directional-bias", type=float)
    parser.add_argument("--regime", default="mixed")
    parser.add_argument("--close-conversion-pressure", action="store_true")
    parser.add_argument("--negative-carry-pressure", action="store_true")
    parser.add_argument("--current-atr", type=float)
    parser.add_argument("--atr-percentile", type=float)
    args = parser.parse_args()

    result = classify_profit_mode(
        same_bar_round_trip_rate=args.same_bar_round_trip_rate,
        spread_to_step_ratio=args.spread_to_step_ratio,
        spread_to_range_ratio=args.spread_to_range_ratio,
        same_bar_open_burst_count=args.same_bar_open_burst_count,
        same_tick_open_burst_count=args.same_tick_open_burst_count,
        first_path_verdict=args.first_path_verdict,
        directional_bias=args.directional_bias,
        regime=args.regime,
        close_conversion_pressure=args.close_conversion_pressure,
        negative_carry_pressure=args.negative_carry_pressure,
        current_atr=args.current_atr,
        atr_percentile=args.atr_percentile,
    )

    output = {
        "profit_mode": result.profit_mode,
        "confidence": result.confidence,
        "reason": result.reason,
        "mode_scores": result.mode_scores,
        "tape_signals": result.tape_signals,
    }
    print(json.dumps(output, indent=2))
