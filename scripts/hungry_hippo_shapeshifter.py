"""Hungry Hippo Shapeshifter — Regime-Adaptive Lattice Geometry.

Maps regime signals to lattice personalities:
- CHOP: Symmetric mean-reversion (what we already do)
- BREAKOUT: Asymmetric, follow the breakout direction
- TREND: One-sided lattice + trailing anchor (the holy grail)
- EXTREME: Aggressive chop at range boundaries (reversal bounces)
- DEFENSIVE: Minimal exposure during uncertainty

Output: Per-symbol lattice geometry configs that adapt to regime.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc


# ─── Personality Definitions ───────────────────────────────────────────────

PERSONALITIES = {
    "chop": {
        "name": "CHOP",
        "emoji": "🔄",
        "description": "Symmetric mean-reversion. Vacuum cleaner mode. Tight steps + fast closes = minimal floating loss.",
        "step_ratio": 0.8,           # Tight steps — positions close to entry, low floating loss per position
        "asymmetry": 1.0,            # Symmetric (both sides equal)
        "max_open_per_side": 12,     # Cap at 12 — control floating exposure
        "close_alpha": 0.2,          # Fast closes — don't let positions sit underwater
        "close_style": "all_profitable",
        "anchor_mode": "fixed",      # Anchor stays put
        "rearm_variant": "rearm_lvl2_exc2",
        "rearm_cooldown_bars": 6,    # Fast rearm in chop
        "momentum_gate": False,      # Don't need momentum confirmation
        "escape_bars": 8,            # Escape after 8 bars if not profitable
        "escape_threshold_usd": 5,   # Cut dead weight early
        "floating_loss_minimization": "TIGHT_STEPS + FAST_CLOSES + CAP_OPEN",
    },
    "chop_aggressive": {
        "name": "CHOP_AGGRESSIVE",
        "emoji": "🔥",
        "description": "Extreme mode. Tight steps + hyper-fast closes minimize floating loss. The whipsaw is our friend — we close BEFORE the drift hurts us.",
        "step_ratio": 0.7,           # TIGHTER steps — positions closer to entry = less floating loss per position
        "asymmetry": 1.0,            # Symmetric (don't bias direction at extremes)
        "max_open_per_side": 12,     # CAP at 12 — don't over-accumulate (limits max floating exposure)
        "close_alpha": 0.1,          # HYPER-AGGRESSIVE — close on ANY micro-profit, even $0.01
        "close_style": "all_profitable",
        "anchor_mode": "fixed",
        "rearm_variant": "rearm_lvl2_exc2",
        "rearm_cooldown_bars": 3,    # Ultra-fast rearm — the whipsaw won't wait
        "momentum_gate": False,
        "escape_bars": 5,            # Escape after 5 bars if not profitable (cut dead weight)
        "escape_threshold_usd": 3,   # Max loss per escape cut — surgical, not bloody
        "floating_loss_minimization": "TIGHT_STEPS + FAST_CLOSES + CAP_OPEN + EARLY_ESCAPE",
    },
    "breakout": {
        "name": "BREAKOUT",
        "emoji": "🚀",
        "description": "Asymmetric. Tight on breakout side, VERY WIDE on counter side. Minimize floating loss by NOT building against the trend.",
        "step_ratio": 1.0,           # Moderate steps on breakout side
        "asymmetry": 3.0,            # STRONG asymmetry — counter-side is nearly disabled (3× wider)
        "max_open_per_side": 8,      # Lower cap — only build on the breakout side
        "close_alpha": 0.5,          # Moderate closes — let breakout winners run, close counter-side quickly
        "close_style": "all_profitable",
        "anchor_mode": "trailing",   # Anchor follows breakout direction
        "rearm_variant": "rearm_lvl2_exc1",
        "rearm_cooldown_bars": 12,   # Standard cooldown
        "momentum_gate": True,       # Need momentum confirmation
        "escape_bars": 10,           # Counter-side positions escape after 10 bars
        "escape_threshold_usd": 8,   # Cut counter-side losers early
        "floating_loss_minimization": "ASYMMETRIC + COUNTER_ESCAPE + TRAILING_ANCHOR",
    },
    "trend": {
        "name": "TREND",
        "emoji": "📈",
        "description": "One-sided lattice. ONLY trade with the trend. Counter-side is nearly disabled. Floating loss minimized by never building against the move.",
        "step_ratio": 1.2,           # Moderate steps — let the trend carry us
        "asymmetry": 8.0,            # EXTREME — counter-side is virtually disabled
        "max_open_per_side": 4,      # Sniper — few positions, all with the trend
        "close_alpha": 0.8,          # Very conservative — let trend runners go
        "close_style": "outer",      # Close only the outermost (let inner runners compound)
        "anchor_mode": "trailing",   # Anchor trails price in trend direction
        "rearm_variant": "rearm_lvl3_exc1",  # Deep levels only
        "rearm_cooldown_bars": 20,   # Slow rearm (wait for pullback)
        "momentum_gate": True,       # Strong momentum required
        "escape_bars": 15,           # Long leash for trend positions
        "escape_threshold_usd": 10,  # Wider stop — trends need room
        "floating_loss_minimization": "ONE_SIDED + EXTREME_ASYMMETRY + TRAILING_ANCHOR",
    },
    "defensive": {
        "name": "DEFENSIVE",
        "emoji": "🛡️",
        "description": "Minimal exposure. Wide steps, close everything fast. Survive the uncertainty.",
        "step_ratio": 2.0,           # Very wide steps — few positions
        "asymmetry": 1.0,            # Symmetric (no directional bet)
        "max_open_per_side": 3,      # Minimal positions — maximum safety
        "close_alpha": 0.05,         # Close ANYTHING profitable immediately (even $0.01)
        "close_style": "outer",
        "anchor_mode": "fixed",
        "rearm_variant": "rearm_lvl3_exc2",  # Conservative rearm
        "rearm_cooldown_bars": 30,   # Very slow rearm
        "momentum_gate": True,
        "escape_bars": 3,            # ESCAPE FAST — if not profitable in 3 bars, cut it
        "escape_threshold_usd": 2,   # Tiny loss acceptable — survival mode
        "floating_loss_minimization": "MINIMAL_OPEN + HYPER_CLOSE + FAST_ESCAPE",
    },
}


# ─── Regime → Personality Mapping ──────────────────────────────────────────

def regime_to_personality(control_mode: str, action_bias: str, confluence: int) -> str:
    """Map regime signal to lattice personality.

    Args:
        control_mode: From regime_signal (wait_extreme_confirmation, trend_follow, etc.)
        action_bias: BUY, SELL, or NEUTRAL
        confluence: 0-100, how many signals agree

    Returns:
        Personality key (chop, chop_aggressive, breakout, trend, defensive)
    """
    mapping = {
        "wait_extreme_confirmation": "chop_aggressive",  # Extremes = reversal bounces
        "trend_follow": "breakout",                      # Following a trend = breakout mode
        "breakout_follow": "breakout",                   # Breakout = asymmetric
        "bounce_reversal": "chop",                       # Bounce = mean-reversion
        "mixed_hold": "defensive",                       # Uncertainty = defensive
    }
    return mapping.get(control_mode, "defensive")


# ─── Direction-Aware Step Computation ──────────────────────────────────────

def compute_directional_steps(
    base_atr: float,
    personality: dict,
    action_bias: str,
    regime_strength: float = 1.0,
) -> dict[str, float]:
    """Compute BUY and SELL steps based on personality and direction.

    In CHOP mode: step_buy == step_sell (symmetric)
    In BREAKOUT mode: step_tight on breakout side, step_wide on counter
    In TREND mode: step_tight on trend side, step_VERY_WIDE on counter (one-sided)

    Args:
        base_atr: Current ATR for the symbol
        personality: Personality config from PERSONALITIES
        action_bias: BUY, SELL, or NEUTRAL
        regime_strength: 0.0-1.0, how strong the regime signal is

    Returns:
        {"step_buy": float, "step_sell": float, "mode": str}
    """
    base_step = base_atr * personality["step_ratio"]
    asym = personality["asymmetry"]

    if asym <= 1.1:
        # Symmetric (CHOP, CHOP_AGGRESSIVE, DEFENSIVE)
        step_buy = base_step
        step_sell = base_step
        mode = "symmetric"
    else:
        # Asymmetric (BREAKOUT, TREND)
        # The side aligned with action_bias gets the tight step
        # The counter side gets the wide step
        tight_step = base_step
        wide_step = base_step * asym

        if action_bias == "BUY":
            step_buy = tight_step   # BUY pullbacks in uptrend
            step_sell = wide_step   # SELL only on sharp counter-rallies
            mode = "buy_tight"
        elif action_bias == "SELL":
            step_buy = wide_step    # BUY only on sharp dips
            step_sell = tight_step  # SELL rallies in downtrend
            mode = "sell_tight"
        else:
            # NEUTRAL — default to BUY-tight (slight upward bias for indices)
            step_buy = tight_step
            step_sell = wide_step
            mode = "neutral_buy_tight"

    # Scale by regime strength — stronger signal = more confidence
    step_buy *= (0.5 + 0.5 * regime_strength)
    step_sell *= (0.5 + 0.5 * regime_strength)

    return {
        "step_buy": round(step_buy, 8),
        "step_sell": round(step_sell, 8),
        "mode": mode,
        "asymmetry_actual": round(step_sell / step_buy if step_buy > 0 else 1.0, 2),
    }


# ─── Trailing Anchor Logic ────────────────────────────────────────────────

def compute_trailing_anchor(
    current_price: float,
    current_anchor: float,
    anchor_mode: str,
    trend_direction: str,
    trail_distance_pips: float,
) -> float:
    """Compute the new anchor position for trailing mode.

    In FIXED mode: anchor doesn't move.
    In TRAILING mode: anchor follows price in the trend direction.

    Args:
        current_price: Current market price
        current_anchor: Previous anchor value
        anchor_mode: "fixed" or "trailing"
        trend_direction: "BUY" or "SELL" (direction of trend)
        trail_distance_pips: How far behind price the anchor trails

    Returns:
        New anchor value
    """
    if anchor_mode == "fixed":
        return current_anchor

    # Trailing: anchor follows price but never moves against the trend
    if trend_direction == "BUY":
        # In uptrend, anchor trails below price
        new_anchor = current_price - trail_distance_pips
        return max(current_anchor, new_anchor)  # Only move up
    elif trend_direction == "SELL":
        # In downtrend, anchor trails above price
        new_anchor = current_price + trail_distance_pips
        return min(current_anchor, new_anchor)  # Only move down

    return current_anchor


# ─── Main Pipeline ─────────────────────────────────────────────────────────

def build_shapeshifter_configs(
    regime_signal_path: str | Path,
    atr_params_path: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build regime-adaptive lattice configs for all symbols.

    Reads regime signal + ATR params, maps to personalities,
    computes directional steps, and outputs unified configs.
    """
    regime_signal_path = Path(regime_signal_path)
    atr_params_path = Path(atr_params_path)

    with open(regime_signal_path) as f:
        regime_signal = json.load(f)
    with open(atr_params_path) as f:
        atr_params = json.load(f)

    if output_path is None:
        output_path = REPO / "configs" / "hungry_hippo_shapeshifter.json"
    else:
        output_path = Path(output_path)

    results = {
        "version": "hungry_hippo_shapeshifter_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "personalities": {k: {kk: vv for kk, vv in v.items() if kk not in ("emoji", "description")} for k, v in PERSONALITIES.items()},
        "symbols": [],
    }

    # Build ATR lookup
    atr_lookup = {}
    for s in atr_params.get("symbols", []):
        atr_lookup[s["symbol"]] = s

    for row in regime_signal.get("rows", []):
        symbol = row["symbol"]
        control_mode = row.get("control_mode", "unknown")
        action_bias = row.get("action_bias", "NEUTRAL")
        confluence = row.get("confluence", 50)
        coarse_regime = row.get("coarse_regime", "UNKNOWN")

        # Get ATR for this symbol
        atr_data = atr_lookup.get(symbol)
        if not atr_data:
            continue

        base_atr = atr_data.get("atr_current", 0.0001)

        # Map to personality
        personality_key = regime_to_personality(control_mode, action_bias, confluence)
        personality = PERSONALITIES[personality_key]

        # Compute directional steps
        regime_strength = confluence / 100.0
        steps = compute_directional_steps(base_atr, personality, action_bias, regime_strength)

        # Compute trailing anchor if applicable
        trail_distance = base_atr * 2.0  # 2 ATR trail distance
        current_price = atr_data.get("current_price", 0)  # Would need live price
        current_anchor = current_price  # Default: anchor at current price

        new_anchor = compute_trailing_anchor(
            current_price, current_anchor,
            personality["anchor_mode"], action_bias, trail_distance
        )

        config = {
            "symbol": symbol,
            "control_mode": control_mode,
            "action_bias": action_bias,
            "confluence": confluence,
            "personality": personality_key,
            "personality_emoji": personality["emoji"],
            "personality_name": personality["name"],
            "step_buy": steps["step_buy"],
            "step_sell": steps["step_sell"],
            "step_mode": steps["mode"],
            "asymmetry": steps["asymmetry_actual"],
            "max_open_per_side": personality["max_open_per_side"],
            "close_alpha": personality["close_alpha"],
            "close_style": personality["close_style"],
            "anchor_mode": personality["anchor_mode"],
            "anchor": new_anchor,
            "rearm_variant": personality["rearm_variant"],
            "rearm_cooldown_bars": personality["rearm_cooldown_bars"],
            "momentum_gate": personality["momentum_gate"],
            "base_atr": base_atr,
            "regime_strength": regime_strength,
            "deployable": True,  # All symbols deployable with correct personality
            "note": f"{personality['emoji']} {personality['name']}: {personality['description']}",
        }
        results["symbols"].append(config)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    return results


def main():
    regime_path = REPO / "reports" / "regime_signal.json"
    atr_path = REPO / "reports" / "hungry_hippo_atr_step_params.json"

    if not regime_path.exists():
        print(f"❌ Regime signal not found: {regime_path}")
        return 1
    if not atr_path.exists():
        print(f"❌ ATR params not found: {atr_path}")
        return 1

    results = build_shapeshifter_configs(regime_path, atr_path)

    print("=" * 80)
    print("HUNGRY HIPPO SHAPESHIFTER — Regime-Adaptive Lattice Geometry")
    print("=" * 80)
    print()

    for sym in results["symbols"]:
        print(f"  {sym['personality_emoji']} {sym['symbol']:10s} | "
              f"{sym['personality_name']:16s} | "
              f"BUY step: {sym['step_buy']:.6f} | "
              f"SELL step: {sym['step_sell']:.6f} | "
              f"Asym: {sym['asymmetry']:.1f}x | "
              f"Max open: {sym['max_open_per_side']} | "
              f"Alpha: {sym['close_alpha']}")

    print()
    print(f"Config saved to: configs/hungry_hippo_shapeshifter.json")

    # Summary
    personality_counts = {}
    for sym in results["symbols"]:
        pk = sym["personality_name"]
        personality_counts[pk] = personality_counts.get(pk, 0) + 1

    print()
    print("Personality Distribution:")
    for pk, count in sorted(personality_counts.items(), key=lambda x: -x[1]):
        print(f"  {pk}: {count} symbols")

    return 0


if __name__ == "__main__":
    sys.exit(main())
