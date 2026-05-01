#!/usr/bin/env python3
"""
Unified Objective Function for the Adaptive Lattice Controller.

This module implements the single explicit objective function that reconciles:
- realized cashflow
- close frequency / efficiency
- adverse excursion (floating burden)
- unresolved inventory burden
- survivability under hostile path dependence
- compounding capacity
- toxic path penalty

Per Gap 2 of the Adaptive Foundational Gap Program:
"The controller must know what it is trying to maximize, and what it is willing to sacrifice to do so."

USAGE:
    from unified_objective import UnifiedObjective, ObjectiveInput

    obj = UnifiedObjective.evaluate(ObjectiveInput(
        realized_net_usd=+125.0,
        close_count=30,
        floating_usd=-15.0,
        open_count=5,
        anchor_reset_count=3,
        max_adverse_excursion_usd=-40.0,
        first_path_verdict="",
        realized_win_rate=0.75,
    ))
    print(obj.total, obj.components, obj.verdict)
"""
from __future__ import annotations

import math
from dataclasses import dataclass


# ── Input contract ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ObjectiveInput:
    """All fields the objective needs. Missing values default to 0.0 / 0 / ''."""
    realized_net_usd: float = 0.0
    close_count: int = 0
    floating_usd: float = 0.0
    open_count: int = 0
    anchor_reset_count: int = 0
    max_adverse_excursion_usd: float = 0.0       # worst MAE seen (negative = loss)
    first_path_verdict: str = ""                   # e.g. "never_green_toxic_continuation"
    realized_win_rate: float = 0.0                 # 0.0–1.0, 0 = unknown


# ── Component weights (tunable, but pinned for reproducibility) ───────────────

WEIGHTS = {
    "realized_cashflow":       1.0,    # primary: realized P/L scaled
    "close_efficiency":        0.5,    # $/close with log-diminishing returns
    "floating_burden":         0.3,    # ratio of floating to realized
    "open_inventory":          0.2,    # per-open penalty
    "anchor_reset_penalty":    0.3,    # resets per close ratio
    "adverse_excursion":       0.2,    # worst MAE relative to step
    "toxic_path_penalty":      5.0,    # flat penalty for toxic first-path
    "win_rate_bonus":          0.5,    # bonus for high WR with sufficient sample
    "compounding_capacity":    0.3,    # realized / (realized + |floating|) ratio
}


# ── Core evaluation ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ObjectiveComponents:
    realized_cashflow: float
    close_efficiency: float
    floating_burden: float
    open_inventory: float
    anchor_reset_penalty: float
    adverse_excursion: float
    toxic_path_penalty: float
    win_rate_bonus: float
    compounding_capacity: float


@dataclass(frozen=True)
class ObjectiveResult:
    total: float
    components: ObjectiveComponents
    verdict: str                # human-readable classification
    component_breakdown: dict   # dict of name -> weighted contribution


class UnifiedObjective:
    """
    The unified objective function for the adaptive lattice controller.

    Maximizes risk-adjusted realized cashflow while penalizing:
    - toxic path dependence
    - unresolved inventory burden
    - adverse excursion severity
    - anchor reset instability
    - low close efficiency
    """

    @staticmethod
    def _realized_cashflow(realized_net_usd: float) -> float:
        """
        Primary signal: realized P/L.
        Scaled linearly but capped to prevent a single lucky close from dominating.
        Cap: +/- 20 points (at +/- $500 realized).
        """
        w = WEIGHTS["realized_cashflow"]
        # Linear scaling: $50 = 1 point, capped at $500 = 10 points * weight
        raw = realized_net_usd / 50.0
        clamped = max(-20.0, min(20.0, raw))
        return w * clamped

    @staticmethod
    def _close_efficiency(realized_net_usd: float, close_count: int) -> float:
        """
        Rewards per-close efficiency with log-diminishing returns.
        A shape at +$10/close scores higher than one at +$2/close even with fewer closes.
        But needs minimum 3 closes to count.
        """
        w = WEIGHTS["close_efficiency"]
        if close_count < 1:
            return 0.0
        avg = realized_net_usd / max(close_count, 1)
        if close_count < 3:
            # Small sample: heavily discounted, max +/- 0.5
            raw = avg / 20.0
            clamped = max(-0.5, min(0.5, raw))
            return w * clamped * (0.1 * close_count)  # 10% at 1c, 20% at 2c
        # Mature sample: log-scaled, +1 per $/close for first $10, diminishing above
        if avg >= 0:
            raw = min(avg, 10.0) + (avg - 10.0) * 0.25 if avg > 10.0 else avg
        else:
            raw = max(avg, -10.0) - (avg + 10.0) * 0.25 if avg < -10.0 else avg
        # Sample confidence: sqrt(close_count / 25), caps at 1.0 at 25 closes
        confidence = min((close_count / 25.0) ** 0.5, 1.0)
        return w * raw * confidence

    @staticmethod
    def _floating_burden(realized_net_usd: float, floating_usd: float) -> float:
        """
        Penalty when floating losses are large relative to realized gains.
        A ratio of 0.5 (floating = 50% of realized) = moderate penalty.
        A ratio > 1.0 (floating exceeds realized) = heavy penalty.
        """
        w = WEIGHTS["floating_burden"]
        denominator = abs(realized_net_usd) + abs(floating_usd)
        if denominator == 0:
            return 0.0
        ratio = abs(floating_usd) / denominator
        if ratio > 0.5:
            # Excessive floating: penalty scales with excess
            excess = (ratio - 0.5) * 2.0  # 0 at 0.5, 1.0 at 1.0
            return -w * excess * 3.0
        return 0.0

    @staticmethod
    def _open_inventory(open_count: int) -> float:
        """
        Per-open penalty. Each unresolved position is future risk.
        Capped at -6 points (30 opens).
        """
        w = WEIGHTS["open_inventory"]
        penalty = min(open_count * 0.5, 6.0)
        return -w * penalty

    @staticmethod
    def _anchor_reset_penalty(anchor_reset_count: int, close_count: int) -> float:
        """
        Penalizes instability: high reset rates mean the lattice keeps breaking.
        reset_rate = resets / closes
        > 1.0 = catastrophic (more resets than closes)
        > 0.5 = concerning
        > 0.1 = mild
        """
        w = WEIGHTS["anchor_reset_penalty"]
        if close_count == 0 or anchor_reset_count == 0:
            return 0.0
        reset_rate = anchor_reset_count / max(close_count, 1)
        if reset_rate > 1.0:
            return -w * 3.0
        elif reset_rate > 0.5:
            return -w * 2.0
        elif reset_rate > 0.1:
            return -w * 1.0
        return 0.0

    @staticmethod
    def _adverse_excursion(max_adverse_excursion_usd: float, close_count: int) -> float:
        """
        Penalizes shapes that experienced large adverse moves relative to their
        realized gains. If the worst MAE is larger than total realized, the shape
        survived by luck, not design.
        """
        w = WEIGHTS["adverse_excursion"]
        if max_adverse_excursion_usd >= 0 or close_count == 0:
            return 0.0
        # max_adverse_excursion_usd is negative (a loss)
        adverse_magnitude = abs(max_adverse_excursion_usd)
        if adverse_magnitude > 100.0:
            # Extreme adverse excursion: penalty scales
            excess = min((adverse_magnitude - 100.0) / 100.0, 2.0)
            return -w * excess * 2.0
        return 0.0

    @staticmethod
    def _toxic_path_penalty(first_path_verdict: str) -> float:
        """
        Flat penalty for toxic first-path evidence.
        A shape whose first-path is "never_green_toxic_continuation" should never
        score positively regardless of other metrics.
        """
        w = WEIGHTS["toxic_path_penalty"]
        if first_path_verdict == "never_green_toxic_continuation":
            return -w * 2.0  # -10 points — effectively disqualifying
        return 0.0

    @staticmethod
    def _win_rate_bonus(realized_win_rate: float, close_count: int) -> float:
        """
        Bonus for high win rate with sufficient sample size.
        Only applies at 5+ closes.
        """
        w = WEIGHTS["win_rate_bonus"]
        if close_count < 5 or realized_win_rate <= 0:
            return 0.0
        if realized_win_rate >= 0.80:
            return w * 2.0
        elif realized_win_rate >= 0.70:
            return w * 1.0
        elif realized_win_rate >= 0.60:
            return w * 0.5
        elif realized_win_rate < 0.40:
            return -w * 1.0
        return 0.0

    @staticmethod
    def _compounding_capacity(realized_net_usd: float, floating_usd: float) -> float:
        """
        Rewards shapes that have converted most of their gross exposure into
        realized cash — the compounding capacity signal.
        A shape with $100 realized and $5 floating scores high.
        A shape with $100 realized and $80 floating scores low.
        """
        w = WEIGHTS["compounding_capacity"]
        denominator = abs(realized_net_usd) + abs(floating_usd)
        if denominator == 0 or realized_net_usd <= 0:
            return 0.0
        conversion_ratio = realized_net_usd / denominator
        if conversion_ratio >= 0.95:
            return w * 2.0
        elif conversion_ratio >= 0.80:
            return w * 1.0
        elif conversion_ratio >= 0.60:
            return w * 0.5
        elif conversion_ratio < 0.30:
            return -w * 1.0
        return 0.0

    @classmethod
    def evaluate(cls, inp: ObjectiveInput) -> ObjectiveResult:
        """Evaluate the unified objective function."""
        c = ObjectiveComponents(
            realized_cashflow=cls._realized_cashflow(inp.realized_net_usd),
            close_efficiency=cls._close_efficiency(inp.realized_net_usd, inp.close_count),
            floating_burden=cls._floating_burden(inp.realized_net_usd, inp.floating_usd),
            open_inventory=cls._open_inventory(inp.open_count),
            anchor_reset_penalty=cls._anchor_reset_penalty(inp.anchor_reset_count, inp.close_count),
            adverse_excursion=cls._adverse_excursion(inp.max_adverse_excursion_usd, inp.close_count),
            toxic_path_penalty=cls._toxic_path_penalty(inp.first_path_verdict),
            win_rate_bonus=cls._win_rate_bonus(inp.realized_win_rate, inp.close_count),
            compounding_capacity=cls._compounding_capacity(inp.realized_net_usd, inp.floating_usd),
        )
        total = (
            c.realized_cashflow
            + c.close_efficiency
            + c.floating_burden
            + c.open_inventory
            + c.anchor_reset_penalty
            + c.adverse_excursion
            + c.toxic_path_penalty
            + c.win_rate_bonus
            + c.compounding_capacity
        )

        # Verdict classification
        if c.toxic_path_penalty < 0:
            verdict = "toxic_path_untradeable"
        elif total >= 10.0 and inp.close_count >= 10:
            verdict = "strong_positive_edge"
        elif total >= 5.0 and inp.close_count >= 5:
            verdict = "positive_edge"
        elif total >= 2.0:
            verdict = "weak_positive"
        elif total >= 0.0:
            verdict = "flat_or_insufficient_sample"
        elif total >= -5.0:
            verdict = "weak_negative"
        else:
            verdict = "strong_negative_edge"

        breakdown = {
            "realized_cashflow": round(c.realized_cashflow, 3),
            "close_efficiency": round(c.close_efficiency, 3),
            "floating_burden": round(c.floating_burden, 3),
            "open_inventory": round(c.open_inventory, 3),
            "anchor_reset_penalty": round(c.anchor_reset_penalty, 3),
            "adverse_excursion": round(c.adverse_excursion, 3),
            "toxic_path_penalty": round(c.toxic_path_penalty, 3),
            "win_rate_bonus": round(c.win_rate_bonus, 3),
            "compounding_capacity": round(c.compounding_capacity, 3),
        }

        return ObjectiveResult(
            total=round(total, 3),
            components=c,
            verdict=verdict,
            component_breakdown=breakdown,
        )

    @classmethod
    def describe(cls, result: ObjectiveResult) -> str:
        """Human-readable description of the objective evaluation."""
        parts = [f"Unified objective: {result.total:+.2f} ({result.verdict})"]
        parts.append("Components:")
        for name, value in result.component_breakdown.items():
            if abs(value) > 0.001:
                parts.append(f"  {name}: {value:+.3f}")
        return "\n".join(parts)


# ── CLI / quick test ─────────────────────────────────────────────────────────

def main() -> int:
    # Test 1: Strong positive edge (BTC M5 Warp-like)
    r1 = UnifiedObjective.evaluate(ObjectiveInput(
        realized_net_usd=+878.0,
        close_count=41,
        floating_usd=+10.0,
        open_count=0,
        anchor_reset_count=0,
        max_adverse_excursion_usd=-50.0,
        first_path_verdict="",
        realized_win_rate=1.0,
    ))
    print(f"Test 1 (BTC M5 Warp): {r1.total:+.2f} ({r1.verdict})")
    assert r1.verdict == "strong_positive_edge", f"Expected strong_positive_edge, got {r1.verdict}"

    # Test 2: Toxic path (should be disqualified)
    r2 = UnifiedObjective.evaluate(ObjectiveInput(
        realized_net_usd=+50.0,
        close_count=5,
        floating_usd=-200.0,
        open_count=10,
        anchor_reset_count=8,
        max_adverse_excursion_usd=-300.0,
        first_path_verdict="never_green_toxic_continuation",
        realized_win_rate=0.20,
    ))
    print(f"Test 2 (toxic): {r2.total:+.2f} ({r2.verdict})")
    assert r2.verdict == "toxic_path_untradeable", f"Expected toxic_path_untradeable, got {r2.verdict}"

    # Test 3: GBPUSD adaptive early proof
    r3 = UnifiedObjective.evaluate(ObjectiveInput(
        realized_net_usd=+2.07,
        close_count=9,
        floating_usd=-5.0,
        open_count=13,
        anchor_reset_count=0,
        max_adverse_excursion_usd=-10.0,
        first_path_verdict="",
        realized_win_rate=0.67,
    ))
    print(f"Test 3 (GBP adaptive): {r3.total:+.2f} ({r3.verdict})")

    # Test 4: Zero evidence
    r4 = UnifiedObjective.evaluate(ObjectiveInput(
        realized_net_usd=0.0,
        close_count=0,
        floating_usd=0.0,
        open_count=0,
        anchor_reset_count=0,
        max_adverse_excursion_usd=0.0,
        first_path_verdict="",
        realized_win_rate=0.0,
    ))
    print(f"Test 4 (no evidence): {r4.total:+.2f} ({r4.verdict})")
    assert r4.total == 0.0
    assert r4.verdict == "flat_or_insufficient_sample"

    print("\nAll tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
