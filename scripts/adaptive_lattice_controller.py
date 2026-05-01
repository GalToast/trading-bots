#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from profit_mode_classifier import classify_profit_mode, score_shape_for_mode
except ImportError:
    # Fallback when running from outside scripts/ directory
    from scripts.profit_mode_classifier import classify_profit_mode, score_shape_for_mode

try:
    from unified_objective import UnifiedObjective, ObjectiveInput
except ImportError:
    from scripts.unified_objective import UnifiedObjective, ObjectiveInput


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
LOW_MOTION_ATR_PERCENTILE = 15.0
LOW_MOTION_DIRECTIONAL_BIAS = 0.15
MICRO_HARVEST_ROUND_TRIP_RATE = 0.2
REGIME_MAP = {
    "STRONG_TREND": "trending",
    "WEAK_TREND": "trending",
    "TRANSITION": "mixed",
    "RANGE": "ranging",
}


@dataclass(frozen=True)
class ControlContext:
    regime: str
    high_friction: bool = False
    high_churn: bool = False
    portfolio_pressure: bool = False
    allow_blocked_families: bool = False
    allow_low_motion: bool = False
    atr_percentile: float | None = None
    directional_bias: float | None = None
    avg_range: float | None = None
    current_atr: float | None = None
    range_atr_ratio: float | None = None
    spread_to_range_ratio: float | None = None
    spread_to_step_ratio: float | None = None
    same_bar_round_trip_rate: float | None = None
    same_bar_open_burst_count: int = 0
    same_tick_open_burst_count: int = 0
    first_path_verdict: str = ""
    market_state_hypothesis_verdict: str = ""
    open_count: int = 0
    runner_session_trade_closes: int = 0
    runner_session_trade_realized_usd: float | None = None
    pre_start_state_carry_realized_usd: float | None = None
    close_conversion_pressure: bool = False
    negative_carry_pressure: bool = False
    # EV evidence fields for max-profit scoring
    realized_close_count: int = 0
    realized_net_usd: float = 0.0
    realized_avg_per_close: float | None = None
    realized_win_rate: float | None = None
    anchor_reset_count: int = 0


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_regime(source_regime: str, fallback: str = "mixed") -> str:
    normalized = REGIME_MAP.get(str(source_regime or "").upper())
    if normalized:
        return normalized
    cleaned = str(source_regime or fallback).strip().lower()
    return cleaned or fallback


def context_from_regime_row(
    regime_row: dict[str, Any],
    *,
    regime: str | None = None,
    high_friction: bool = False,
    high_churn: bool | None = None,
    portfolio_pressure: bool = False,
    allow_blocked_families: bool = False,
    allow_low_motion: bool = False,
    open_count: int = 0,
    runner_session_trade_closes: int = 0,
    runner_session_trade_realized_usd: float | None = None,
    pre_start_state_carry_realized_usd: float | None = None,
) -> ControlContext:
    directional_bias = safe_float(regime_row.get("directional_bias"))
    spread_to_range_ratio = safe_float(regime_row.get("spread_to_range_ratio"))
    spread_to_step_ratio = safe_float(regime_row.get("spread_to_step_ratio"))
    normalized_regime = normalize_regime(regime or str(regime_row.get("regime") or "mixed"))
    fresh_realized = safe_float(runner_session_trade_realized_usd)
    carry_realized = safe_float(pre_start_state_carry_realized_usd)
    derived_high_friction = bool(high_friction)
    if not derived_high_friction:
        derived_high_friction = bool(
            (spread_to_range_ratio is not None and spread_to_range_ratio >= 0.6)
            or (spread_to_step_ratio is not None and spread_to_step_ratio >= 0.35)
        )
    derived_high_churn = (
        bool(high_churn)
        if high_churn is not None
        else bool(normalized_regime == "mixed" and directional_bias is not None and abs(directional_bias) < LOW_MOTION_DIRECTIONAL_BIAS)
    )
    close_conversion_pressure = bool(
        runner_session_trade_closes <= 0
        and fresh_realized is not None
        and fresh_realized <= 0
    )
    negative_carry_pressure = bool(carry_realized is not None and carry_realized < 0)
    realized_close_count = max(
        safe_int(regime_row.get("realized_close_count", regime_row.get("realized_closes")), 0),
        0,
    )
    realized_net_usd = safe_float(regime_row.get("realized_net_usd"))
    if realized_net_usd is None:
        realized_net_usd = 0.0
    realized_avg_per_close = safe_float(regime_row.get("realized_avg_per_close"))
    if realized_avg_per_close is None and realized_close_count > 0:
        realized_avg_per_close = realized_net_usd / realized_close_count
    realized_win_rate = safe_float(regime_row.get("realized_win_rate"))
    if realized_win_rate is None:
        realized_wins = max(safe_int(regime_row.get("realized_wins"), 0), 0)
        realized_losses = max(safe_int(regime_row.get("realized_losses"), 0), 0)
        realized_outcomes = realized_wins + realized_losses
        if realized_outcomes > 0:
            realized_win_rate = realized_wins / realized_outcomes
        elif realized_close_count > 0 and realized_net_usd > 0:
            realized_win_rate = 1.0
    anchor_reset_count = max(
        safe_int(regime_row.get("anchor_reset_count", regime_row.get("anchor_resets")), 0),
        0,
    )
    return ControlContext(
        regime=normalized_regime,
        high_friction=derived_high_friction,
        high_churn=derived_high_churn,
        portfolio_pressure=portfolio_pressure,
        allow_blocked_families=allow_blocked_families,
        allow_low_motion=allow_low_motion,
        atr_percentile=safe_float(regime_row.get("atr_percentile")),
        directional_bias=directional_bias,
        avg_range=safe_float(regime_row.get("avg_range")),
        current_atr=safe_float(regime_row.get("current_atr")),
        range_atr_ratio=safe_float(regime_row.get("range_atr_ratio")),
        spread_to_range_ratio=spread_to_range_ratio,
        spread_to_step_ratio=spread_to_step_ratio,
        same_bar_round_trip_rate=safe_float(regime_row.get("same_bar_round_trip_rate")),
        same_bar_open_burst_count=max(
            safe_int(
                regime_row.get("same_bar_open_burst_count_at_open", regime_row.get("same_bar_open_burst_count")),
                0,
            ),
            0,
        ),
        same_tick_open_burst_count=max(
            safe_int(
                regime_row.get("same_tick_open_burst_count_at_open", regime_row.get("same_tick_open_burst_count")),
                0,
            ),
            0,
        ),
        first_path_verdict=str(regime_row.get("first_path_verdict") or ""),
        market_state_hypothesis_verdict=str(regime_row.get("market_state_hypothesis_verdict") or ""),
        open_count=max(int(open_count or 0), 0),
        runner_session_trade_closes=max(int(runner_session_trade_closes or 0), 0),
        runner_session_trade_realized_usd=fresh_realized,
        pre_start_state_carry_realized_usd=carry_realized,
        close_conversion_pressure=close_conversion_pressure,
        negative_carry_pressure=negative_carry_pressure,
        realized_close_count=realized_close_count,
        realized_net_usd=realized_net_usd,
        realized_avg_per_close=realized_avg_per_close,
        realized_win_rate=realized_win_rate,
        anchor_reset_count=anchor_reset_count,
    )


def active_blockers_for_family(library: dict[str, Any], family: str) -> list[dict[str, Any]]:
    blockers = []
    for blocker in list(library.get("blockers") or []):
        if str(blocker.get("status") or "").lower() != "active":
            continue
        applies = [str(item) for item in list(blocker.get("applies_to_families") or [])]
        if family in applies:
            blockers.append(blocker)
    return blockers


def step_read(shape: dict[str, Any]) -> str:
    step = dict(shape.get("step_method") or {})
    kind = str(step.get("kind") or "")
    if kind == "atr_multiple_asymmetric":
        return f"ATR sell={step.get('sell_coeff')} buy={step.get('buy_coeff')}"
    if kind == "atr_multiple":
        return f"ATR x{step.get('coeff')}"
    if kind == "range_atr_formula":
        return "range/ATR adaptive formula"
    return kind or "-"


def close_read(shape: dict[str, Any]) -> str:
    close = dict(shape.get("close") or {})
    if "bounded_close_gap" in close:
        return f"style={close.get('style','-')} gap={close.get('bounded_close_gap')}"
    return (
        f"style={close.get('style','-')} alpha={close.get('alpha','-')} "
        f"sell_gap={close.get('sell_gap','-')} buy_gap={close.get('buy_gap','-')}"
    )


def assess_extractability(context: ControlContext) -> tuple[str, str, bool]:
    if context.allow_low_motion:
        return "override", "extractability override enabled", False

    if context.current_atr is not None and context.current_atr <= 0:
        return "unextractable_no_motion", "current ATR is non-positive; no honest adaptive deployment", True
    if context.avg_range is not None and context.avg_range <= 0:
        return "unextractable_no_motion", "average range is non-positive; no honest adaptive deployment", True

    if context.first_path_verdict == "never_green_toxic_continuation" or context.market_state_hypothesis_verdict == "repricing_or_toxic_flow_risk":
        return (
            "guarded_toxic_flow",
            "fresh path evidence points to toxic one-way flow; keep the shape available but treat opens as guarded until flow normalizes",
            False,
        )

    atr_percentile = context.atr_percentile
    bias = abs(context.directional_bias) if context.directional_bias is not None else None
    low_motion = bool(
        atr_percentile is not None
        and bias is not None
        and atr_percentile <= LOW_MOTION_ATR_PERCENTILE
        and bias <= LOW_MOTION_DIRECTIONAL_BIAS
    )
    if low_motion and context.high_friction:
        reasons: list[str] = []
        if atr_percentile is not None:
            reasons.append(f"atr_percentile={atr_percentile:.1f}")
        if bias is not None:
            reasons.append(f"directional_bias={bias:.2f}")
        if context.spread_to_range_ratio is not None:
            reasons.append(f"spread_to_range_ratio={context.spread_to_range_ratio:.2f}")
        if context.spread_to_step_ratio is not None:
            reasons.append(f"spread_to_step_ratio={context.spread_to_step_ratio:.2f}")
        joined = ", ".join(reasons) if reasons else "low-motion + high-friction"
        return (
            "unextractable_cost_dominated",
            f"{joined} indicate flatness plus cost domination; stand down until extractable edge returns",
            True,
        )
    if atr_percentile is not None and bias is not None:
        if low_motion:
            return (
                "active_microstructure_candidate",
                f"atr_percentile={atr_percentile:.1f} and directional_bias={bias:.2f} are quiet, but absent friction domination the lattice should still hunt micro fluctuations",
                False,
            )

    return "active", "market has enough movement or microstructure edge for adaptive shape selection", False


def score_shape(shape: dict[str, Any], context: ControlContext, tape_profit_mode: str | None = None, tape_mode_confidence: float = 0.0) -> float:
    score = 0.0
    targets = [str(item) for item in list(shape.get("regime_targets") or [])]
    risk_profile = str(shape.get("risk_profile") or "")
    portfolio_profile = str(shape.get("portfolio_profile") or "")
    monetization_profile = str(shape.get("monetization_profile") or "")
    close_alpha = safe_float(dict(shape.get("close") or {}).get("alpha"))

    if context.regime in targets:
        score += 5.0
    elif "mixed" in targets and context.regime in {"trending", "ranging"}:
        score += 2.0

    if context.high_friction and risk_profile == "conservative":
        score += 2.0
    if context.high_friction and risk_profile == "balanced":
        score += 1.0
    if context.high_churn and risk_profile == "conservative":
        score += 2.0
    if context.portfolio_pressure and portfolio_profile == "light":
        score += 2.0
    if context.portfolio_pressure and portfolio_profile == "medium":
        score += 1.0
    if context.regime == "trending" and risk_profile in {"balanced", "aggressive"}:
        score += 1.0
    if context.regime == "ranging" and risk_profile == "conservative":
        score += 1.0
    if context.close_conversion_pressure and monetization_profile == "cash_harvest":
        score += 4.0
    if context.close_conversion_pressure and close_alpha is not None and close_alpha <= 0.5:
        score += 1.5
    if context.close_conversion_pressure and monetization_profile in {"trend_extension", "breakout_extension"}:
        score -= 1.0
    if context.negative_carry_pressure and portfolio_profile in {"light", "medium"}:
        score += 1.0

    # --- Tape-driven profit mode matching (cross-family classifier) ---
    # This is the KEY integration: shapes are scored by how well they match
    # the tape-derived profit mode, not just regime labels.
    if tape_profit_mode:
        mode_bonus = score_shape_for_mode(shape, tape_profit_mode, tape_mode_confidence)
        score += mode_bonus

    evidence = dict(shape.get("evidence") or {})
    status = str(evidence.get("status") or "")
    if status == "survivor":
        score += 2.0
    elif status == "shadow_ready":
        score += 1.5
    elif status == "probation":
        score += 0.5
    elif status in {"research_only", "blocked_runtime"}:
        score -= 1.0

    # --- Realized $/close performance scoring (max-profit integration) ---
    # Shapes that have proven live/shadow lane performance get scored by actual EV,
    # not just regime matching. This is the bridge from "looks right" to "makes money."
    # Two evidence sources: (1) nested evidence dict from shape library, (2) direct Context fields from state files.

    # Source 1: Evidence dict (from shape library / passive surfaces)
    perf = dict(evidence.get("performance") or {})
    ev_realized_usd = safe_float(perf.get("realized_close_usd"))
    ev_close_count = safe_int(perf.get("close_count"), 0)

    # Source 2: Direct Context fields (from runtime state files)
    ctx_realized_usd = context.realized_net_usd
    ctx_close_count = context.realized_close_count
    ctx_avg_per_close = context.realized_avg_per_close
    ctx_win_rate = context.realized_win_rate
    ctx_resets = context.anchor_reset_count

    # Prefer the richer source; fall back to whichever has data
    best_close_count = max(ev_close_count, ctx_close_count)
    # Derive context average when not pre-computed
    derived_ctx_avg = ctx_avg_per_close
    if derived_ctx_avg is None and ctx_close_count > 0 and ctx_realized_usd is not None:
        derived_ctx_avg = ctx_realized_usd / ctx_close_count
    best_avg: float | None = derived_ctx_avg
    if best_avg is None and ev_realized_usd is not None:
        best_avg = ev_realized_usd
    elif ev_realized_usd is not None and ctx_realized_usd is not None and derived_ctx_avg is not None:
        # Both present: use the larger sample's average
        if ctx_close_count >= ev_close_count:
            best_avg = derived_ctx_avg
        else:
            best_avg = ev_realized_usd

    if best_avg is not None and best_close_count > 0 and ctx_close_count == 0:
        # Ad-hoc EV scoring: only fires when unified objective is NOT available.
        # (When ctx_close_count > 0, the unified objective below covers close_efficiency,
        #  win_rate_bonus, anchor_reset_penalty, and compounding_capacity.)
        # This path handles evidence-dict-only performance data from shape library surfaces.
        if best_close_count >= 3:
            # Log-scaled scoring: +1 per $/close for first $10, diminishing returns above
            ev_score = min(best_avg, 10.0) + (best_avg - 10.0) * 0.25 if best_avg > 10.0 else best_avg
            # Scale by sample confidence: sqrt(close_count / 25) caps at 1.0 at 25 closes
            confidence = min((best_close_count / 25.0) ** 0.5, 1.0)
            score += ev_score * confidence
        elif best_close_count >= 1:
            # Small-sample EV: linear scaling, heavily discounted.
            # 1 close: 10% weight, 2 closes: 20% weight.
            # This prevents the controller from being blind to early evidence
            # while still requiring 3+ closes for full consideration.
            small_sample_weight = 0.10 * best_close_count  # 0.10 for 1c, 0.20 for 2c
            # Adversarial guard: cap the absolute impact at +/-1.0 for small samples
            capped_ev = max(-1.0, min(1.0, best_avg))
            score += capped_ev * small_sample_weight

    # Win rate bonus, sample-size confidence, anchor reset penalty:
    # Only apply when unified objective is NOT available (ctx_close_count == 0).
    # The unified objective already covers win_rate_bonus, compounding_capacity, and anchor_reset_penalty.
    if ctx_close_count == 0:
        # Win rate bonus (only meaningful with sufficient sample)
        if ctx_win_rate is not None and ctx_close_count >= 5:
            if ctx_win_rate >= 0.80:
                score += 2.0
            elif ctx_win_rate >= 0.70:
                score += 1.0
            elif ctx_win_rate >= 0.60:
                score += 0.5
            elif ctx_win_rate < 0.40:
                score -= 1.0

        # Sample-size confidence bonus
        if ctx_close_count >= 50:
            score += 2.0
        elif ctx_close_count >= 20:
            score += 1.0
        elif ctx_close_count >= 10:
            score += 0.5
        elif ctx_close_count >= 5:
            score += 0.25

        # Anchor reset penalty (stability signal)
        if ctx_close_count > 0 and ctx_resets > 0:
            reset_rate = ctx_resets / max(ctx_close_count, 1)
            if reset_rate > 1.0:
                score -= 2.0
            elif reset_rate > 0.5:
                score -= 1.0
            elif reset_rate > 0.1:
                score -= 0.5

    # --- Unified objective function (Gap 2: Objective Function unification) ---
    # When there is realized evidence, the unified objective dominates the score.
    # This replaces the ad hoc EV scoring above with a single explicit objective
    # that reconciles realized cashflow, close efficiency, floating burden,
    # inventory, resets, adverse excursion, toxic path, win rate, and compounding.
    if ctx_close_count > 0 or ctx_realized_usd is not None:
        unified_result = UnifiedObjective.evaluate(ObjectiveInput(
            realized_net_usd=ctx_realized_usd if ctx_realized_usd is not None else 0.0,
            close_count=ctx_close_count,
            floating_usd=context.open_count * (-best_avg if best_avg is not None and best_avg < 0 else 0.0),
            open_count=context.open_count,
            anchor_reset_count=ctx_resets,
            max_adverse_excursion_usd=0.0,  # Not yet tracked in ControlContext
            first_path_verdict=context.first_path_verdict,
            realized_win_rate=ctx_win_rate if ctx_win_rate is not None else 0.0,
        ))
        # The unified score replaces the ad hoc EV component but keeps
        # regime-matching, tape-mode, and evidence-status bonuses.
        # Scale: unified scores range ~-15 to +25, so we use it directly.
        score = score + unified_result.total

    return score


def performance_summary_for_context(shape: dict[str, Any], context: ControlContext) -> str:
    evidence = dict(shape.get("evidence") or {})
    perf = dict(evidence.get("performance") or {})
    ev_avg_per_close = safe_float(perf.get("realized_close_usd"))
    ev_close_count = safe_int(perf.get("close_count"), 0)

    use_context = context.realized_close_count > ev_close_count and context.realized_avg_per_close is not None
    if use_context:
        summary = f"${context.realized_avg_per_close:.2f}/close over {context.realized_close_count} closes"
        if context.anchor_reset_count > 0:
            summary = f"{summary}; resets={context.anchor_reset_count}"
        return summary
    if ev_avg_per_close is not None and ev_close_count > 0:
        return f"${ev_avg_per_close:.2f}/close over {ev_close_count} closes"
    return "No realized $/close evidence yet"


def objective_read(context: ControlContext) -> str:
    reads: list[str] = []
    if context.close_conversion_pressure:
        reads.append(
            "fresh session has not booked realized gains, so selection should favor faster cash harvesting"
        )
    if context.negative_carry_pressure:
        reads.append("pre-start carry remains negative, so new shapes should repair realized cashflow first")
    if not reads:
        return "No monetization-pressure override is active."
    return "Monetization pressure active: " + " and ".join(reads) + "."


def derive_profit_mode(
    shape: dict[str, Any] | None,
    context: ControlContext,
    extractability_state: str,
) -> tuple[str, str]:
    if shape is None:
        return (
            "parked_no_edge",
            "Current market state is not honestly extractable after friction and path quality are treated directly.",
        )

    monetization_profile = str(shape.get("monetization_profile") or "")
    regime_targets = [str(item) for item in list(shape.get("regime_targets") or [])]
    risk_profile = str(shape.get("risk_profile") or "")
    portfolio_profile = str(shape.get("portfolio_profile") or "")
    close_alpha = safe_float(dict(shape.get("close") or {}).get("alpha"))

    if extractability_state == "guarded_toxic_flow":
        return (
            "guarded_toxic_flow",
            "Fresh path evidence says opens should stay guarded until one-way flow normalizes, even if the shape remains available.",
        )

    if context.high_friction or monetization_profile == "friction_survivor":
        return (
            "friction_survivor",
            "Spread and executability are large enough that the controller should prioritize survivable harvest over aggressive inventory build.",
        )

    if (context.close_conversion_pressure or context.negative_carry_pressure) and (
        monetization_profile == "cash_harvest"
        or (close_alpha is not None and close_alpha <= 0.5)
    ):
        return (
            "cash_repair_harvest",
            "Realized cashflow is lagging, so the controller should bias toward faster close conversion before extension harvesting.",
        )

    if extractability_state == "active_microstructure_candidate" or (
        context.same_bar_round_trip_rate is not None
        and context.same_bar_round_trip_rate >= MICRO_HARVEST_ROUND_TRIP_RATE
    ):
        return (
            "micro_harvest",
            "The tape is quiet but still extractable, so the controller should hunt micro fluctuations instead of waiting for a larger directional regime.",
        )

    if (
        context.regime == "trending"
        or monetization_profile in {"trend_harvest", "trend_extension", "breakout_extension"}
        or "trending" in regime_targets
    ):
        return (
            "trend_harvest",
            "Directional participation is strong enough that the controller should let extension and asymmetry do the monetization work.",
        )

    return (
        "balanced_harvest",
        "The symbol is tradable without a single dominant edge, so use the currently best balanced harvest profile.",
    )


def derive_runtime_overlays(
    context: ControlContext,
    extractability_state: str,
    tape_profit_mode: str | None = None,
) -> tuple[list[str], str]:
    overlays: list[str] = []
    reads: list[str] = []
    burst_count = max(context.same_bar_open_burst_count, context.same_tick_open_burst_count)

    if extractability_state == "guarded_toxic_flow" or tape_profit_mode == "guarded_toxic_flow":
        overlays.append("guard_open_admission")
        reads.append("fresh path evidence says new opens should stay guarded until one-way flow normalizes")

    if burst_count > 1:
        overlays.append("cluster_aware_escape")
        reads.append(
            "same-tick burst concentration means fills should be treated as one risk unit under escape logic"
        )
        overlays.append("suppress_additional_levels_after_burst")
        reads.append(
            f"same-bar/tick burst count is `{burst_count}`, so additional levels should stay suppressed until the burst dissipates"
        )

    if not overlays:
        return [], "No special runtime overlay is implied beyond the current shape and profit mode."

    deduped_overlays = list(dict.fromkeys(overlays))
    deduped_reads = list(dict.fromkeys(reads))
    return deduped_overlays, "; ".join(deduped_reads) + "."


# ── Survival constraint gate (Gap 2: explicit survival constraints) ─────────────

def check_survival_constraints(context: ControlContext) -> tuple[bool, str]:
    """Return (blocked, reason) if survival constraints are violated.

    These are HARD BLOCKS: even if a shape scores highest, it should not be
    recommended when survival is compromised. The unified objective PENALIZES
    risky behavior; this gate BLOCKS it entirely.
    """
    # 1. Toxic path: never recommend a shape whose first-path is toxic
    if context.first_path_verdict == "never_green_toxic_continuation":
        return True, "toxic_first_path: first-path evidence shows never-green toxic continuation; no new shape should be recommended until flow normalizes"

    # 2. Catastrophic reset rate: more resets than closes means the lattice is breaking faster than it completes
    if context.realized_close_count > 0 and context.anchor_reset_count > context.realized_close_count:
        reset_rate = context.anchor_reset_count / max(context.realized_close_count, 1)
        return True, f"catastrophic_reset_rate: {reset_rate:.1f} resets/close ({context.anchor_reset_count} resets vs {context.realized_close_count} closes); lattice is breaking faster than it completes"

    # 3. Floating burden near-collapse: floating loss approaching or exceeding realized gains
    if context.open_count > 0 and context.realized_close_count > 0:
        realized_net = context.realized_net_usd
        # Estimate floating: open_count * |avg_per_close| if negative, else 0
        avg = context.realized_avg_per_close
        if avg is None and context.realized_close_count > 0 and realized_net is not None:
            avg = realized_net / context.realized_close_count
        if avg is not None and avg < 0:
            estimated_floating = context.open_count * abs(avg)
            denominator = abs(realized_net) + estimated_floating if realized_net is not None else estimated_floating
            if denominator > 0:
                floating_ratio = estimated_floating / denominator
                if floating_ratio > 0.80:
                    return True, f"near_collapse_floating: floating burden ratio {floating_ratio:.2f} ({estimated_floating:.1f} floating vs {abs(realized_net):.1f} realized); near-collapse state"

    return False, ""


def recommend_shape(
    library: dict[str, Any],
    symbol: str,
    context: ControlContext,
) -> dict[str, Any]:
    symbols = dict(library.get("symbols") or {})
    symbol_payload = dict(symbols.get(symbol) or {})
    if not symbol_payload:
        raise KeyError(f"Unknown symbol in adaptive lattice library: {symbol}")

    # STEP 1: Classify profit mode from tape structure (shape-agnostic)
    # This is the tape-driven classifier, NOT the old shape-driven derive_profit_mode.
    tape_mode_result = classify_profit_mode(
        same_bar_round_trip_rate=context.same_bar_round_trip_rate,
        spread_to_step_ratio=context.spread_to_step_ratio,
        spread_to_range_ratio=context.spread_to_range_ratio,
        same_bar_open_burst_count=context.same_bar_open_burst_count,
        same_tick_open_burst_count=context.same_tick_open_burst_count,
        first_path_verdict=context.first_path_verdict,
        directional_bias=context.directional_bias,
        regime=context.regime,
        close_conversion_pressure=context.close_conversion_pressure,
        negative_carry_pressure=context.negative_carry_pressure,
        current_atr=context.current_atr,
        atr_percentile=context.atr_percentile,
    )

    extractability_state, extractability_read, hard_block = assess_extractability(context)
    if hard_block:
        # When the market is unextractable, the profit mode is parked regardless of tape signals.
        # The tape classifier result is still recorded for telemetry, but the controller
        # overrides to parked_no_edge because no honest adaptive deployment is possible.
        runtime_overlays, runtime_overlay_read = derive_runtime_overlays(
            context,
            extractability_state,
            tape_mode_result.profit_mode,
        )
        blocked_candidates = list(symbol_payload.get("candidate_shapes") or [])
        return {
            "symbol": symbol,
            "stage": symbol_payload.get("stage"),
            "regime": context.regime,
            "status": extractability_state,
            "recommended_shape_id": "",
            "family": "",
            "step_read": "-",
            "close_read": "-",
            "blockers": [],
            "score": 0.0,
            "extractability_state": extractability_state,
            "extractability_read": extractability_read,
            "motion_state": extractability_state,
            "motion_read": extractability_read,
            "close_conversion_pressure": context.close_conversion_pressure,
            "negative_carry_pressure": context.negative_carry_pressure,
            "objective_read": objective_read(context),
            "profit_mode": "parked_no_edge",
            "profit_mode_read": "Current market state is not extractable after friction/toxicity is treated honestly. Keep the symbol parked until edge returns.",
            "profit_mode_confidence": tape_mode_result.confidence,
            "profit_mode_scores": tape_mode_result.mode_scores,
            "controller_mode": "parked_no_edge",
            "controller_mode_read": "Current market state is not extractable after friction/toxicity is treated honestly. Keep the symbol parked until edge returns.",
            "runtime_overlays": runtime_overlays,
            "runtime_overlay_read": runtime_overlay_read,
            "why": "Current market state is not extractable after friction/toxicity is treated honestly. Keep the symbol parked until edge returns.",
            "alternatives": [shape.get("shape_id") for shape in blocked_candidates],
        }

    candidates = []
    for shape in list(symbol_payload.get("candidate_shapes") or []):
        shape_family = str(shape.get("family") or "")
        blockers = active_blockers_for_family(library, shape_family)
        if blockers and not context.allow_blocked_families:
            continue
        # Score shape against the tape-derived profit mode (not just regime)
        s = score_shape(shape, context, tape_profit_mode=tape_mode_result.profit_mode, tape_mode_confidence=tape_mode_result.confidence)
        candidates.append((s, shape, blockers))

    if not candidates:
        blocked_read = "All currently suitable profit modes are blocked by active family/runtime constraints."
        runtime_overlays, runtime_overlay_read = derive_runtime_overlays(
            context,
            extractability_state,
            tape_mode_result.profit_mode,
        )
        blocked_candidates = list(symbol_payload.get("candidate_shapes") or [])
        return {
            "symbol": symbol,
            "stage": symbol_payload.get("stage"),
            "regime": context.regime,
            "status": "blocked",
            "recommended_shape_id": "",
            "family": "",
            "step_read": "-",
            "close_read": "-",
            "blockers": [blocker.get("blocker_id") for blocker in library.get("blockers") or [] if blocker.get("status") == "active"],
            "extractability_state": extractability_state,
            "extractability_read": extractability_read,
            "motion_state": extractability_state,
            "motion_read": extractability_read,
            "close_conversion_pressure": context.close_conversion_pressure,
            "negative_carry_pressure": context.negative_carry_pressure,
            "objective_read": objective_read(context),
            "profit_mode": "blocked_by_runtime",
            "profit_mode_read": blocked_read,
            "profit_mode_confidence": tape_mode_result.confidence,
            "profit_mode_scores": tape_mode_result.mode_scores,
            "controller_mode": "blocked_by_runtime",
            "controller_mode_read": blocked_read,
            "runtime_overlays": runtime_overlays,
            "runtime_overlay_read": runtime_overlay_read,
            "why": "No eligible shapes remain under current active blockers.",
            "alternatives": [shape.get("shape_id") for shape in blocked_candidates],
        }

    candidates.sort(key=lambda item: (item[0], str(item[1].get("shape_id") or "")), reverse=True)
    score, best_shape, blockers = candidates[0]

    # --- Survival constraint gate (Gap 2: explicit survival constraints) ---
    # Even the highest-scoring shape must pass survival constraints.
    survival_blocked, survival_reason = check_survival_constraints(context)
    if survival_blocked:
        runtime_overlays, runtime_overlay_read = derive_runtime_overlays(
            context,
            extractability_state,
            tape_mode_result.profit_mode,
        )
        return {
            "symbol": symbol,
            "stage": symbol_payload.get("stage"),
            "regime": context.regime,
            "status": "blocked_by_survival_constraint",
            "recommended_shape_id": best_shape.get("shape_id"),
            "family": best_shape.get("family"),
            "step_read": step_read(best_shape),
            "close_read": close_read(best_shape),
            "blockers": [blocker.get("blocker_id") for blocker in blockers] + ["survival_constraint"],
            "score": round(score, 2),
            "survival_block_reason": survival_reason,
            "extractability_state": extractability_state,
            "extractability_read": extractability_read,
            "motion_state": extractability_state,
            "motion_read": extractability_read,
            "close_conversion_pressure": context.close_conversion_pressure,
            "negative_carry_pressure": context.negative_carry_pressure,
            "objective_read": objective_read(context),
            "profit_mode": tape_mode_result.profit_mode,
            "profit_mode_read": f"Shape exists but survival constraint blocks recommendation: {survival_reason}",
            "profit_mode_confidence": tape_mode_result.confidence,
            "profit_mode_scores": tape_mode_result.mode_scores,
            "controller_mode": tape_mode_result.profit_mode,
            "controller_mode_read": f"Survival constraint blocks: {survival_reason}",
            "runtime_overlays": runtime_overlays,
            "runtime_overlay_read": runtime_overlay_read,
            "why": survival_reason,
        }

    # Profit mode is already tape-derived; derive the human-readable overlay contract
    profit_mode, profit_mode_read = tape_mode_result.profit_mode, tape_mode_result.reason
    runtime_overlays, runtime_overlay_read = derive_runtime_overlays(
        context,
        extractability_state,
        tape_mode_result.profit_mode,
    )

    return {
        "symbol": symbol,
        "stage": symbol_payload.get("stage"),
        "regime": context.regime,
        "status": "ok",
        "recommended_shape_id": best_shape.get("shape_id"),
        "family": best_shape.get("family"),
        "step_read": step_read(best_shape),
        "close_read": close_read(best_shape),
        "blockers": [blocker.get("blocker_id") for blocker in blockers],
        "score": round(score, 2),
        "performance_summary": performance_summary_for_context(best_shape, context),
        "extractability_state": extractability_state,
        "extractability_read": extractability_read,
        "motion_state": extractability_state,
        "motion_read": extractability_read,
        "close_conversion_pressure": context.close_conversion_pressure,
        "negative_carry_pressure": context.negative_carry_pressure,
        "objective_read": objective_read(context),
        "profit_mode": profit_mode,
        "profit_mode_read": profit_mode_read,
        "profit_mode_confidence": tape_mode_result.confidence,
        "profit_mode_scores": tape_mode_result.mode_scores,
        "controller_mode": profit_mode,
        "controller_mode_read": profit_mode_read,
        "runtime_overlays": runtime_overlays,
        "runtime_overlay_read": runtime_overlay_read,
        "why": dict(best_shape.get("evidence") or {}).get("note", ""),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recommend an adaptive lattice shape from the research shape library.")
    parser.add_argument("--library-path", default=str(DEFAULT_LIBRARY_PATH))
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--regime", default="mixed")
    parser.add_argument("--high-friction", action="store_true")
    parser.add_argument("--high-churn", action="store_true")
    parser.add_argument("--portfolio-pressure", action="store_true")
    parser.add_argument("--allow-blocked-families", action="store_true")
    parser.add_argument("--allow-low-motion", action="store_true")
    parser.add_argument("--atr-percentile", type=float)
    parser.add_argument("--directional-bias", type=float)
    parser.add_argument("--avg-range", type=float)
    parser.add_argument("--current-atr", type=float)
    parser.add_argument("--range-atr-ratio", type=float)
    parser.add_argument("--spread-to-range-ratio", type=float)
    parser.add_argument("--spread-to-step-ratio", type=float)
    parser.add_argument("--same-bar-round-trip-rate", type=float)
    parser.add_argument("--same-bar-open-burst-count", type=int, default=0)
    parser.add_argument("--same-tick-open-burst-count", type=int, default=0)
    parser.add_argument("--first-path-verdict", default="")
    parser.add_argument("--market-state-hypothesis-verdict", default="")
    parser.add_argument("--open-count", type=int, default=0)
    parser.add_argument("--runner-session-trade-closes", type=int, default=0)
    parser.add_argument("--runner-session-trade-realized-usd", type=float)
    parser.add_argument("--pre-start-state-carry-realized-usd", type=float)
    parser.add_argument("--realized-close-count", type=int, default=0)
    parser.add_argument("--realized-net-usd", type=float, default=0.0)
    parser.add_argument("--realized-avg-per-close", type=float)
    parser.add_argument("--realized-win-rate", type=float)
    parser.add_argument("--anchor-reset-count", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library = load_json(Path(args.library_path))
    regime_row = {
        "atr_percentile": args.atr_percentile,
        "directional_bias": args.directional_bias,
        "avg_range": args.avg_range,
        "current_atr": args.current_atr,
        "range_atr_ratio": args.range_atr_ratio,
        "spread_to_range_ratio": args.spread_to_range_ratio,
        "spread_to_step_ratio": args.spread_to_step_ratio,
        "same_bar_round_trip_rate": args.same_bar_round_trip_rate,
        "same_bar_open_burst_count": args.same_bar_open_burst_count,
        "same_tick_open_burst_count": args.same_tick_open_burst_count,
        "first_path_verdict": str(args.first_path_verdict or ""),
        "market_state_hypothesis_verdict": str(args.market_state_hypothesis_verdict or ""),
        "realized_close_count": args.realized_close_count,
        "realized_net_usd": args.realized_net_usd,
        "realized_avg_per_close": args.realized_avg_per_close,
        "realized_win_rate": args.realized_win_rate,
        "anchor_reset_count": args.anchor_reset_count,
    }
    result = recommend_shape(
        library,
        args.symbol,
        context_from_regime_row(
            regime_row,
            regime=str(args.regime),
            high_friction=bool(args.high_friction),
            high_churn=bool(args.high_churn),
            portfolio_pressure=bool(args.portfolio_pressure),
            allow_blocked_families=bool(args.allow_blocked_families),
            allow_low_motion=bool(args.allow_low_motion),
            open_count=args.open_count,
            runner_session_trade_closes=args.runner_session_trade_closes,
            runner_session_trade_realized_usd=args.runner_session_trade_realized_usd,
            pre_start_state_carry_realized_usd=args.pre_start_state_carry_realized_usd,
        ),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
