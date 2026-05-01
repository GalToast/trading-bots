#!/usr/bin/env python3
"""
HUNGRY HIPPO — Personality Selector Module (Sprint 2)

Maps regime signals to concrete lattice geometry configurations.
The brain of the Hungry Hippo — connects @codex_healthcheck's regime_signal
to the lattice geometry engine.

Design based on:
- @qwen-2's 3-personality architecture (CHOP/BREAKOUT/TREND modes)
- @health-check's tuning data (SELL-tight beats BUY-tight on GBPUSD, alpha=0.3 best)
- @codex_healthcheck's regime_signal surface (control_mode + action_bias)
- @codex_rearm_audit's guardrail metadata

Output: reports/hungry_hippo_personality_selector.json
"""
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "reports" / "hungry_hippo_personality_selector.json"
REGIME_SIGNAL_PATH = ROOT / "reports" / "regime_signal.json"
ATR_PARAMS_PATH = ROOT / "reports" / "hungry_hippo_atr_step_params.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

# Canonical control modes from regime_signal.json
CONTROL_MODES = {
    "wait_extreme_confirmation": "CHOP_AGGRESSIVE",
    "trend_follow": "BREAKOUT",
    "breakout_follow": "BREAKOUT",
    "bounce_reversal": "CHOP_MODERATE",
    "range_harvest": "CHOP_MODERATE",
    "transition_wait": "DEFENSIVE",
    "mixed_hold": "DEFENSIVE",
}

# Personality definitions with concrete geometry params
PERSONALITIES = {
    "CHOP_AGGRESSIVE": {
        "description": "Mean-reversion vacuum at range extremes. Captures reversal bounces with wide steps and aggressive closes.",
        "regime_targets": ["wait_extreme_confirmation"],
        "step_coefficient": 2.0,       # 2.0× ATR — wide steps for bigger bounces
        "asymmetry_ratio": 1.0,        # 1:1 symmetric — don't bias at extremes
        "alpha": 0.3,                   # Aggressive early closes
        "max_open_per_side": 15,       # High — capture the chop
        "close_style": "all_profitable",
        "rearm_variant": "exc1",
        "rearm_cooldown_bars": 3,
        "session_gate": None,           # Trade 24/7 at extremes
        "one_sided": False,
        "trailing_anchor": False,
        "guardrails": {
            "kill_on_reset_storm": True,
            "max_resets_per_hour": 10,
            "floating_loss_limit": -15.0,
        }
    },
    "BREAKOUT": {
        "description": "Asymmetric geometry following breakout direction. Tight steps on breakout side, wide on counter.",
        "regime_targets": ["trend_follow", "breakout_follow"],
        "step_coefficient": 1.5,       # 1.5× ATR — trend needs room
        "asymmetry_ratio": 1.5,        # 1.5:1 — tight on breakout side
        "alpha": 0.7,                   # Conservative — let winners run
        "max_open_per_side": 10,       # Moderate — don't overcommit
        "close_style": "all_profitable",
        "rearm_variant": "exc1",
        "rearm_cooldown_bars": 6,
        "session_gate": "active_hours_only",
        "one_sided": False,             # Both sides but asymmetric
        "trailing_anchor": True,        # Anchor follows price in trends
        "guardrails": {
            "kill_on_reset_storm": True,
            "max_resets_per_hour": 5,
            "floating_loss_limit": -15.0,
        }
    },
    "CHOP_MODERATE": {
        "description": "Balanced mean-reversion in bounce/reversal regimes. Symmetric steps, moderate aggression.",
        "regime_targets": ["bounce_reversal"],
        "step_coefficient": 1.0,       # 1.0× ATR — moderate
        "asymmetry_ratio": 1.0,        # 1:1 symmetric
        "alpha": 0.5,                   # Balanced close timing
        "max_open_per_side": 12,       # Moderate
        "close_style": "all_profitable",
        "rearm_variant": "exc1",
        "rearm_cooldown_bars": 4,
        "session_gate": "active_hours_only",
        "one_sided": False,
        "trailing_anchor": False,
        "guardrails": {
            "kill_on_reset_storm": True,
            "max_resets_per_hour": 8,
            "floating_loss_limit": -15.0,
        }
    },
    "DEFENSIVE": {
        "description": "Minimal exposure during mixed/unclear regimes. Wide steps, low open count, fast closes.",
        "regime_targets": ["mixed_hold"],
        "step_coefficient": 2.0,       # 2.0× ATR — very wide, few fills
        "asymmetry_ratio": 1.0,        # 1:1 symmetric
        "alpha": 0.3,                   # Fast closes — get out quickly
        "max_open_per_side": 6,        # Low — minimal exposure
        "close_style": "all_profitable",
        "rearm_variant": "exc2",
        "rearm_cooldown_bars": 12,
        "session_gate": "active_hours_only",
        "one_sided": False,
        "trailing_anchor": False,
        "guardrails": {
            "kill_on_reset_storm": True,
            "max_resets_per_hour": 3,
            "floating_loss_limit": -10.0,
        }
    },
    "TREND_SNIPER": {
        "description": "One-sided lattice for established trends. Only trades WITH the trend, against-trend side is wide hedge.",
        "regime_targets": [],           # Not yet in regime_signal — future extension
        "step_coefficient": 1.5,       # 1.5× ATR
        "asymmetry_ratio": 4.0,        # 4:1 — effectively one-sided
        "alpha": 0.7,                   # Let trend winners run
        "max_open_per_side": 6,        # Low — sniper, not machine gun
        "close_style": "all_profitable",
        "rearm_variant": "exc1",
        "rearm_cooldown_bars": 8,
        "session_gate": "active_hours_only",
        "one_sided": True,              # Only trend side
        "trailing_anchor": True,
        "guardrails": {
            "kill_on_reset_storm": True,
            "max_resets_per_hour": 3,
            "floating_loss_limit": -20.0,
        }
    },
}

# Symbol-specific overrides based on @health-check's tuning data
SYMBOL_OVERRIDES = {
    "GBPUSD": {
        # Tuning found SELL-tight beats BUY-tight (1:2 asymmetry)
        # Even in ALL_BULLISH regime, sell the rallies tight
        "asymmetry_ratio": 0.5,         # 1:2 SELL-tight (inverse of BUY-tight)
        "alpha": 0.3,                    # Early closes compound better
        "step_coefficient": 1.0,         # 1.0× ATR (validated by tuning)
        "notes": "SELL-tight validated by 300-combination tuning sweep. +$35.29 vs -$6.07 baseline.",
    },
    "NAS100": {
        # STRONG_TREND breakout_follow — trend mode
        "personality": "BREAKOUT",
        "step_coefficient": 1.5,
        "asymmetry_ratio": 1.0,          # Symmetric for now (trend direction known)
        "session_gate_hours": [14, 15, 16, 17, 18, 19],  # NY session only
        "notes": "99.4% session-dependent. Trade ONLY 14:00-19:00 UTC.",
    },
    "US30": {
        "personality": "BREAKOUT",
        "step_coefficient": 1.0,
        "asymmetry_ratio": 1.0,
        "session_gate_hours": [14, 15, 16, 17, 18, 19],
        "notes": "85.5% session-dependent. Trade ONLY 14:00-19:00 UTC.",
    },
    "BTCUSD": {
        # BTC is DOWNTREND per regime_signal — need SELL-tight or HOLD
        "personality": "CHOP_MODERATE",  # bounce_reversal for now
        "asymmetry_ratio": 2.0,          # BUY-tight for bounce reversal
        "step_coefficient": 1.5,
        "hold_gate": True,               # Don't trade until bullish realignment
        "notes": "Regime signal says SELL bias. HOLD until realignment. See btc_downtrend_handoff.json.",
    },
    "ETHUSD": {
        # ETH is recovering, Asian hours are productive
        "personality": "CHOP_MODERATE",
        "asymmetry_ratio": 1.4,
        "step_coefficient": 1.5,
        "session_gate_hours": [4, 5, 6, 14, 15, 16, 17, 18, 19, 20, 21],
        "notes": "48.2% session PnL. Asian hours (04:00-06:00 UTC) are productive.",
    },
    "NZDUSD": {
        # TRANSITION regime, 20.9% session-dependent
        "personality": "CHOP_MODERATE",
        "asymmetry_ratio": 2.0,          # BUY-tight
        "step_coefficient": 0.8,
        "session_gate": None,            # No gate — evenly distributed
        "notes": "20.9% session-dependent. Trade 24/7.",
    },
    "XAUUSD": {
        # STRONG_TREND but at extreme — needs real session window
        "personality": "CHOP_AGGRESSIVE",
        "step_coefficient": 1.5,
        "asymmetry_ratio": 1.0,
        "session_gate_hours": [14, 15, 16, 17, 18, 19],
        "notes": "51.2% session PnL. Trade NY session. ATR=$5.58.",
    },
    "USDJPY": {
        # STRONG_TREND
        "personality": "BREAKOUT",
        "step_coefficient": 1.5,
        "asymmetry_ratio": 1.0,
        "notes": "100% session-independent. Trade 24/7. ATR=0.039.",
    },
    "EURUSD": {
        # WEAK_TREND, at extreme — HOLD
        "personality": "DEFENSIVE",
        "hold_gate": True,
        "notes": "AT_EXTREME_HIGH. HOLD until pullback. ATR=0.00029.",
    },
    "SOLUSD": {
        # STRONG_TREND
        "personality": "BREAKOUT",
        "step_coefficient": 1.5,
        "asymmetry_ratio": 1.5,
        "session_gate_hours": [14, 15, 16, 17, 18, 19, 20, 21],
        "notes": "71.9% session-dependent. Trade 14:00-21:00 UTC.",
    },
    "XRPUSD": {
        # STRONG_TREND
        "personality": "BREAKOUT",
        "step_coefficient": 1.5,
        "asymmetry_ratio": 1.5,
        "session_gate_hours": [14, 15, 16, 17, 18, 19, 20, 21],
        "notes": "50.0% session-dependent.",
    },
}


def seed_note_for_row(regime_data):
    consensus = str((regime_data or {}).get("consensus") or "")
    if consensus.startswith("seeded_"):
        return "Seeded canonical policy pending richer live regime coverage."
    return ""


def compute_personality_config(symbol, control_mode, atr_current, regime_data=None):
    """
    Compute the full personality config for a symbol.
    
    Args:
        symbol: trading symbol
        control_mode: from regime_signal (wait_extreme_confirmation, trend_follow, etc.)
        atr_current: current ATR value
        regime_data: optional regime signal dict for additional context
    
    Returns:
        dict with complete geometry config
    """
    # Get base personality
    default_personality_name = CONTROL_MODES.get(control_mode, "DEFENSIVE")
    
    # Apply symbol-specific overrides
    override = SYMBOL_OVERRIDES.get(symbol, {})
    personality_name = str(override.get("personality", default_personality_name) or default_personality_name)
    personality = PERSONALITIES.get(personality_name, PERSONALITIES[default_personality_name])
    note = override.get("notes") or seed_note_for_row(regime_data)
    
    # Merge configs
    config = {
        "symbol": symbol,
        "control_mode": control_mode,
        "personality": personality_name,
        "step": atr_current * override.get("step_coefficient", personality["step_coefficient"]),
        "asymmetry_ratio": override.get("asymmetry_ratio", personality["asymmetry_ratio"]),
        "alpha": override.get("alpha", personality["alpha"]),
        "max_open_per_side": override.get("max_open_per_side", personality["max_open_per_side"]),
        "close_style": personality["close_style"],
        "rearm_variant": personality["rearm_variant"],
        "rearm_cooldown_bars": personality["rearm_cooldown_bars"],
        "session_gate": override.get("session_gate", personality["session_gate"]),
        "session_gate_hours": override.get("session_gate_hours", None),
        "one_sided": personality["one_sided"],
        "trailing_anchor": personality["trailing_anchor"],
        "hold_gate": override.get("hold_gate", False),
        "guardrails": personality["guardrails"],
        "notes": note,
    }
    
    # Compute asymmetric steps
    asym_ratio = config["asymmetry_ratio"]
    base_step = config["step"]
    if asym_ratio > 0:
        config["step_sell"] = (2 * base_step) / (1 + asym_ratio)
        config["step_buy"] = asym_ratio * config["step_sell"]
    else:
        config["step_buy"] = base_step
        config["step_sell"] = base_step
    
    return config


def atr_values_by_symbol(atr_payload):
    values = {}
    for row in list(atr_payload.get("symbols") or []):
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            values[symbol] = float(row.get("atr_current") or 0.0)
    return values


def infer_atr_from_regime_row(row):
    candidates = []
    for step_key, coeff_key in (
        ("computed_buy_step", "buy_step_coeff"),
        ("computed_sell_step", "sell_step_coeff"),
    ):
        step_value = float((row or {}).get(step_key) or 0.0)
        coeff_value = float((row or {}).get(coeff_key) or 0.0)
        if step_value > 0.0 and coeff_value > 0.0:
            candidates.append(step_value / coeff_value)
    if not candidates:
        return 0.0
    return sum(candidates) / len(candidates)


def build_symbol_configs(regime_payload, atr_payload):
    atr_values = atr_values_by_symbol(atr_payload)
    results = {}
    for row in sorted(list(regime_payload.get("rows") or []), key=lambda item: str(item.get("symbol") or "")):
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        atr = atr_values.get(symbol) or infer_atr_from_regime_row(row)
        if not atr:
            continue
        control_mode = str(row.get("control_mode") or "mixed_hold")
        results[symbol] = compute_personality_config(symbol, control_mode, atr, regime_data=row)
    return results


def build_payload(regime_payload, atr_payload):
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "personality_definitions": PERSONALITIES,
        "symbol_configs": build_symbol_configs(regime_payload, atr_payload),
        "control_mode_mapping": CONTROL_MODES,
        "guardrails": {
            "btc_hold_gate": "Do not deploy bullish BTC shapes while regime_signal says action_bias=SELL. See btc_downtrend_handoff.json.",
            "extreme_opportunity": "wait_extreme_confirmation symbols should use CHOP_AGGRESSIVE, not HOLD. Extremes are mean-reversion alpha.",
            "session_gates": "Use symbol-specific session windows from session_regime_step_table_v2.json. Not universal.",
            "rearm_audit": "See hungry_hippo_shapeshifter_guardrail_audit.md for the current selector-vs-regime guardrail truth.",
        },
    }


if __name__ == "__main__":
    regime_payload = load_json(REGIME_SIGNAL_PATH)
    atr_payload = load_json(ATR_PARAMS_PATH)
    output = build_payload(regime_payload, atr_payload)

    with open(OUTPUT, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"Personality selector written to {OUTPUT}")
    print(f"\nSymbol configs:")
    for sym, cfg in output["symbol_configs"].items():
        status = "HOLD" if cfg.get("hold_gate") else "DEPLOY"
        print(f"  {sym:12} personality={cfg['personality']:20} step={cfg['step']:.6f} asym={cfg['asymmetry_ratio']:.1f} alpha={cfg['alpha']} status={status}")
