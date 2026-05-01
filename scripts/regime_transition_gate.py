#!/usr/bin/env python3
"""
Regime Transition Gate — Multi-Timeframe Confirmation for Hungry Hippo Shapeshifter

Reads short-term regime (M5/M15), confirms against high timeframe structure (H1/H4),
and emits smooth transition signals with hysteresis to prevent whipsaw.

This is the missing link between regime_detection.py and the live lattice runner.

Usage:
    python scripts/regime_transition_gate.py --symbol BTCUSD --short-tf M15 --high-tf H1
    python scripts/regime_transition_gate.py --symbol ETHUSD --short-tf M5 --high-tf H4

Output:
    reports/regime_transition_gate_{symbol}.json — current transition state
    reports/regime_transition_gate_{symbol}_history.jsonl — transition log

API:
    from regime_transition_gate import RegimeTransitionGate
    gate = RegimeTransitionGate(symbol, short_tf="M15", high_tf="H1")
    signal = gate.evaluate(short_bars, high_bars, bar_index)
    # Returns: {"regime": "chop", "confidence": 0.85, "transition": False, ...}
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from shapeshifter_v2 import compute_adx, compute_atr, detect_regime as detect_regime_short

OUTPUT_DIR = ROOT / "reports"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Default symbols
DEFAULT_SYMBOLS = ["BTCUSD", "ETHUSD", "EURUSD", "GBPUSD", "NAS100", "US30", "XAUUSD"]

# Transition gate thresholds
HYSTERESIS_BARS = 5          # Bars of sustained confirmation before transition
CONFIDENCE_THRESHOLD = 0.65  # Minimum confidence to trigger transition
MIN_ADX_FOR_TREND = 22       # ADX must exceed this to call it a trend
MIN_ATR_PCT_FOR_VOLATILE = 2.0  # ATR% must exceed this to call it volatile


class RegimeTransitionGate:
    """Multi-timeframe regime transition gate with hysteresis.

    Reads short-term regime (M5/M15), confirms against high timeframe (H1/H4),
    and emits smooth transition signals.
    """

    def __init__(self, symbol: str, short_tf: str = "M15", high_tf: str = "H1"):
        self.symbol = symbol
        self.short_tf = short_tf
        self.high_tf = high_tf

        # State
        self.current_regime = "chop"  # Default
        self.current_personality = "chop"
        self.pending_regime = None
        self.pending_since_bar = 0
        self.transition_count = 0

        # History
        self.regime_history = []  # list of regime scores over time
        self.transition_log = []  # list of confirmed transitions

    def evaluate(self, short_bars: list[dict], high_bars: list[dict],
                 bar_index: int) -> dict:
        """Evaluate regime and return transition signal.

        Args:
            short_bars: Short timeframe bars (M5/M15)
            high_bars: High timeframe bars (H1/H4)
            bar_index: Current bar index for hysteresis tracking

        Returns:
            dict with regime, confidence, transition status, and recommended geometry
        """
        # 1. Detect short-term regime
        short_regime = detect_regime_short(short_bars)

        # 2. Confirm against high timeframe
        high_confirmation = self._confirm_high_timeframe(high_bars, short_regime)

        # 3. Compute confidence (weighted blend of short + high TF agreement)
        confidence = self._compute_confidence(short_regime, high_confirmation)

        # 4. Apply hysteresis — don't switch on single-bar noise
        confirmed_regime = self._apply_hysteresis(
            short_regime["regime"], confidence, bar_index
        )

        # 5. Map to personality and geometry
        if confirmed_regime:
            personality = self._regime_to_personality(confirmed_regime)
            geometry = self._recommended_geometry(personality, short_regime)
        else:
            personality = self.current_personality
            geometry = self._recommended_geometry(personality, short_regime)

        result = {
            "timestamp": utc_now_iso(),
            "symbol": self.symbol,
            "short_tf_regime": short_regime["regime"],
            "short_tf_adx": short_regime.get("adx", 0),
            "short_tf_atr_pct": short_regime.get("atr_pct", 0),
            "high_tf_confirmation": high_confirmation,
            "confidence": round(confidence, 3),
            "confirmed_regime": confirmed_regime or self.current_regime,
            "transition": confirmed_regime is not None,
            "previous_regime": self.current_regime,
            "personality": personality,
            "geometry": geometry,
            "transition_count": self.transition_count,
        }

        # Update state if transition confirmed
        if confirmed_regime:
            self.transition_log.append(result)
            self.current_regime = confirmed_regime
            self.current_personality = personality
            self.transition_count += 1

        self.regime_history.append(result)

        return result

    def _confirm_high_timeframe(self, high_bars: list[dict],
                                 short_regime: dict) -> dict:
        """Confirm short-term regime against high timeframe structure.

        Returns dict with:
            trend_aligned: bool — does HTF agree with STF trend direction?
            htf_adx: float — HTF trend strength
            htf_atr_pct: float — HTF volatility
            structure_level: str — "support", "resistance", "mid_range"
            confirmation_score: float 0-1
        """
        if len(high_bars) < 30:
            return {"confirmed": False, "reason": "insufficient_high_tf_data",
                    "confirmation_score": 0.0}

        # HTF ADX — trend strength on high timeframe
        highs = [float(b["high"]) for b in high_bars]
        lows = [float(b["low"]) for b in high_bars]
        closes = [float(b["close"]) for b in high_bars]

        htf_adx = compute_adx(highs, lows, closes, 14)
        htf_atr = compute_atr(highs, lows, closes, 14)
        htf_atr_pct = (htf_atr / statistics.mean(closes[-14:])) * 100 if len(closes) >= 14 else 0

        # HTF trend direction
        recent_highs = highs[-20:]
        recent_lows = lows[-20:]
        range_high = max(recent_highs)
        range_low = min(recent_lows)
        current_price = closes[-1]
        price_position = (current_price - range_low) / (range_high - range_low) if (range_high - range_low) > 0 else 0.5

        # Determine structure level
        if price_position > 0.8:
            structure_level = "resistance"
        elif price_position < 0.2:
            structure_level = "support"
        else:
            structure_level = "mid_range"

        # HTF trend direction
        if len(closes) >= 10:
            first_half = sum(closes[:5]) / 5
            second_half = sum(closes[-5:]) / 5
            if second_half > first_half * 1.001:
                htf_trend = "up"
            elif second_half < first_half * 0.999:
                htf_trend = "down"
            else:
                htf_trend = "neutral"
        else:
            htf_trend = "neutral"

        # Confirmation logic
        stf_regime = short_regime["regime"]
        stf_trend = short_regime.get("trend_direction", "neutral")

        # For trend regimes: HTF must agree on direction
        if stf_regime in ("trend",):
            if htf_trend == stf_trend and htf_adx > MIN_ADX_FOR_TREND:
                confirmed = True
                confirmation_score = min(1.0, htf_adx / 50.0)
            elif htf_trend != stf_trend:
                confirmed = False
                confirmation_score = 0.2  # Strong disagreement
            else:
                confirmed = False
                confirmation_score = 0.5  # Weak HTF trend
        # For chop regimes: HTF should show low ADX (range agreement)
        elif stf_regime in ("chop", "chop_aggressive"):
            if htf_adx < 20:
                confirmed = True
                confirmation_score = 0.8
            elif htf_adx < 25:
                confirmed = True
                confirmation_score = 0.6
            else:
                confirmed = False  # HTF says trending, STF says chop
                confirmation_score = 0.3
        # For breakout: HTF should show expanding ATR
        elif stf_regime == "breakout":
            if htf_atr_pct > MIN_ATR_PCT_FOR_VOLATILE:
                confirmed = True
                confirmation_score = 0.7
            else:
                confirmed = False
                confirmation_score = 0.4
        else:
            confirmed = False
            confirmation_score = 0.5

        return {
            "confirmed": confirmed,
            "htf_adx": round(htf_adx, 1),
            "htf_atr_pct": round(htf_atr_pct, 2),
            "htf_trend": htf_trend,
            "structure_level": structure_level,
            "price_position": round(price_position, 3),
            "confirmation_score": round(confirmation_score, 3),
        }

    def _compute_confidence(self, short_regime: dict,
                            high_confirmation: dict) -> float:
        """Compute blended confidence score (0-1)."""
        stf_score = short_regime.get("adx", 0) / 50.0  # Normalize ADX to 0-1
        htf_score = high_confirmation.get("confirmation_score", 0.5)

        # Weighted blend: 60% STF, 40% HTF
        confidence = 0.6 * stf_score + 0.4 * htf_score

        # Bonus if both agree on direction
        if high_confirmation.get("confirmed", False):
            confidence = min(1.0, confidence + 0.15)

        return confidence

    def _apply_hysteresis(self, candidate_regime: str, confidence: float,
                          bar_index: int) -> str | None:
        """Apply hysteresis to prevent whipsaw transitions.

        Returns confirmed regime string or None (no transition).
        """
        if candidate_regime == self.current_regime:
            self.pending_regime = None
            self.pending_since_bar = 0
            return None

        if confidence < CONFIDENCE_THRESHOLD:
            return None  # Below confidence threshold

        if candidate_regime != self.pending_regime:
            # New candidate — start confirmation counter
            self.pending_regime = candidate_regime
            self.pending_since_bar = bar_index
            return None

        # Same pending regime — check confirmation window
        bars_pending = bar_index - self.pending_since_bar
        if bars_pending >= HYSTERESIS_BARS:
            # Transition confirmed
            confirmed = self.pending_regime
            self.pending_regime = None
            self.pending_since_bar = 0
            return confirmed

        return None  # Still waiting for confirmation

    def _regime_to_personality(self, regime: str) -> str:
        """Map regime name to Hungry Hippo personality."""
        mapping = {
            "chop": "chop",
            "chop_aggressive": "chop_aggressive",
            "breakout": "breakout",
            "trend": "trend",
            "defensive": "defensive",
        }
        return mapping.get(regime, "chop")

    def _recommended_geometry(self, personality: str,
                              short_regime: dict) -> dict:
        """Compute recommended lattice geometry for current regime."""
        atr = short_regime.get("atr", 0.01)

        geometry = {
            "personality": personality,
            "atr": round(atr, 4),
            "atr_pct": short_regime.get("atr_pct", 0),
            "adx": short_regime.get("adx", 0),
        }

        # Personality-specific geometry
        if personality == "chop":
            geometry.update({
                "step_ratio": 0.8,
                "asymmetry": 1.0,
                "max_open_per_side": 12,
                "close_alpha": 0.2,
                "close_style": "all_profitable",
                "anchor_mode": "fixed",
                "rearm_cooldown_bars": 6,
                "momentum_gate": False,
                "stance": "full_engagement_tight_steps",
            })
        elif personality == "chop_aggressive":
            geometry.update({
                "step_ratio": 0.7,
                "asymmetry": 1.0,
                "max_open_per_side": 12,
                "close_alpha": 0.1,
                "close_style": "all_profitable",
                "anchor_mode": "fixed",
                "rearm_cooldown_bars": 3,
                "momentum_gate": False,
                "stance": "extreme_mode_early_escape",
            })
        elif personality == "breakout":
            geometry.update({
                "step_ratio": 1.0,
                "asymmetry": 3.0,
                "max_open_per_side": 8,
                "close_alpha": 0.5,
                "close_style": "all_profitable",
                "anchor_mode": "trailing",
                "rearm_cooldown_bars": 12,
                "momentum_gate": True,
                "stance": "asymmetric_counter_escape",
            })
        elif personality == "trend":
            trend_dir = short_regime.get("trend_direction", "neutral")
            if trend_dir == "up":
                asymmetry = 0.125  # buy_tight: buy step = 0.125 × sell step
                stance = "favor_longs_asymmetric"
            elif trend_dir == "down":
                asymmetry = 8.0  # sell_tight: sell step = 0.125 × buy step
                stance = "favor_shorts_asymmetric"
            else:
                asymmetry = 1.0
                stance = "symmetric_wide_steps"

            geometry.update({
                "step_ratio": 1.2,
                "asymmetry": asymmetry,
                "max_open_per_side": 4,
                "close_alpha": 0.8,
                "close_style": "outer",
                "anchor_mode": "trailing",
                "rearm_cooldown_bars": 20,
                "momentum_gate": True,
                "stance": stance,
            })
        elif personality == "defensive":
            geometry.update({
                "step_ratio": 2.0,
                "asymmetry": 1.0,
                "max_open_per_side": 3,
                "close_alpha": 0.05,
                "close_style": "outer",
                "anchor_mode": "fixed",
                "rearm_cooldown_bars": 30,
                "momentum_gate": True,
                "stance": "minimal_exposure_fast_escape",
            })

        # Compute actual steps
        step_base = atr * geometry["step_ratio"]
        if geometry["asymmetry"] > 1.0:
            asym_sqrt = math.sqrt(geometry["asymmetry"])
            geometry["step_buy"] = round(step_base / asym_sqrt, 6)
            geometry["step_sell"] = round(step_base * asym_sqrt, 6)
        else:
            geometry["step_buy"] = round(step_base, 6)
            geometry["step_sell"] = round(step_base, 6)

        return geometry


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CLI Entry Point ──

def parse_args():
    parser = argparse.ArgumentParser(
        description="Regime Transition Gate — Multi-timeframe confirmation"
    )
    parser.add_argument("--symbol", type=str, default="BTCUSD")
    parser.add_argument("--short-tf", type=str, default="M15",
                        help="Short timeframe for regime detection")
    parser.add_argument("--high-tf", type=str, default="H1",
                        help="High timeframe for confirmation")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--max-cycles", type=int, default=0,
                        help="0 = run forever")
    return parser.parse_args()


def main():
    args = parse_args()
    print(f"=== Regime Transition Gate: {args.symbol} ({args.short_tf} → {args.high_tf}) ===")
    print(f"Polling every {args.poll_seconds}s, max cycles: {args.max_cycles or 'unlimited'}")
    print()

    gate = RegimeTransitionGate(args.symbol, args.short_tf, args.high_tf)

    # State file paths
    state_json = OUTPUT_DIR / f"regime_transition_gate_{args.symbol.lower()}_{args.short_tf.lower()}_{args.high_tf.lower()}.json"
    history_jsonl = OUTPUT_DIR / f"regime_transition_gate_{args.symbol.lower()}_{args.short_tf.lower()}_{args.high_tf.lower()}_history.jsonl"

    cycle = 0
    while True:
        cycle += 1
        if args.max_cycles > 0 and cycle > args.max_cycles:
            break

        # TODO: Fetch real bars from MT5
        # For now, emit placeholder structure
        print(f"Cycle {cycle}: Waiting for bar feed integration...")
        print(f"  State: {gate.current_regime} → pending: {gate.pending_regime}")
        print(f"  Transitions: {gate.transition_count}")

        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
