"""Hungry Hippo Integration Pipeline.

Combines all Hungry Hippo components into a single runnable pipeline:
1. regime_signal.json → classify the target symbol
2. hungry_hippo_atr_step_params.json → get ATR-scaled steps
3. session_regime_step_table_v2.json → apply session weights
4. deploy_validation_gate → verify the target symbol passes
5. hungry_hippo_rearm_params.json → configure rearm
6. Output: configs/hungry_hippo_<symbol>_live.json
7. Optionally run forward shadow test (default 7-day replay for the target symbol)
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from tick_penetration_lattice_core import (
    engine_from_args,
    load_ticks_range,
)

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc

from hungry_hippo_symbol_profiles import runtime_defaults_for_symbol


def load_component(name: str) -> dict | list:
    """Load a Hungry Hippo component output."""
    path = REPO / "reports" / name
    with open(path) as f:
        return json.load(f)


def resolve_rearm_policy(rearm_params: dict[str, Any], symbol: str) -> dict[str, Any]:
    symbol = symbol.upper()
    current_state = dict((rearm_params.get("current_state_rearm_params") or {}).get(symbol) or {})
    guardrail_summary = dict(rearm_params.get("guardrail_metadata") or {})
    return {
        "symbol": symbol,
        "current_state": current_state,
        "guardrail_summary": guardrail_summary,
        "guardrail_status": str(current_state.get("canonical_guardrail_status") or "uncovered"),
        "auto_rearm_allowed": bool(current_state.get("auto_rearm_allowed")),
        "session_window": str(current_state.get("session_window") or "None"),
        "guardrail_reasons": list(current_state.get("canonical_guardrail_reasons") or []),
        "kill_response_table": dict(rearm_params.get("kill_response_table") or {}),
    }


def convert_kill_response_table(kill_response_table: dict[str, Any]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for reason, payload in kill_response_table.items():
        converted[str(reason)] = {
            "cooldown_mult": float(payload.get("cooldown_multiplier") or 0.0),
            "variant": str(payload.get("variant") or ""),
            "max_retries": int(payload.get("max_injections") or 0),
        }
    return converted


def find_symbol_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    symbol = symbol.upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() == symbol:
            return row
    return None


def fallback_rearm_policy(symbol: str) -> dict[str, Any]:
    defaults = runtime_defaults_for_symbol(symbol)
    return {
        "symbol": symbol.upper(),
        "current_state": {},
        "guardrail_summary": {"note": "no_rearm_policy_present_for_symbol"},
        "guardrail_status": "uncovered",
        "auto_rearm_allowed": False,
        "session_window": "None",
        "guardrail_reasons": [
            f"No Hungry Hippo rearm policy exists yet for {symbol.upper()}, so deployable auto-rearm stays disabled."
        ],
        "kill_response_table": {},
        "runtime_defaults": defaults,
    }


def validate_symbol_deploy(symbol: str, atr_params: dict, regime_signal: dict, rearm_params: dict[str, Any]) -> dict:
    """Run validation gate on a symbol."""
    symbol = symbol.upper()
    defaults = runtime_defaults_for_symbol(symbol)
    atr_row = find_symbol_row(list(atr_params.get("symbols") or []), symbol)
    regime_row = find_symbol_row(list(regime_signal.get("rows") or []), symbol)
    rearm_policy = resolve_rearm_policy(rearm_params, symbol)
    if rearm_policy["guardrail_status"] == "uncovered":
        rearm_policy = fallback_rearm_policy(symbol)

    if not atr_row:
        return {
            "symbol": symbol,
            "asset_class": defaults["asset_class"],
            "deployable": False,
            "reason": f"{symbol} not found in ATR params",
            "rearm_policy": rearm_policy,
            "timeframe": defaults["timeframe"],
        }

    if not regime_row:
        return {
            "symbol": symbol,
            "asset_class": defaults["asset_class"],
            "deployable": False,
            "reason": f"{symbol} not found in regime signal",
            "step": atr_row.get("step"),
            "step_buy": atr_row.get("step_buy"),
            "step_sell": atr_row.get("step_sell"),
            "asymmetry_ratio": atr_row.get("asymmetry_ratio"),
            "session_weight": atr_row.get("session_weight"),
            "raw_close_alpha": atr_row.get("raw_close_alpha", 0.5),
            "max_open_per_side": atr_row.get("max_open_per_side", defaults["max_open_per_side"]),
            "atr_current": atr_row.get("atr_current", defaults["base_step"]),
            "rearm_policy": rearm_policy,
            "timeframe": defaults["timeframe"],
        }

    control_mode = regime_row.get("control_mode", "unknown")
    consensus = regime_row.get("consensus", "unknown")
    action_bias = regime_row.get("action_bias", "NEUTRAL")

    if rearm_policy["guardrail_status"] != "aligned":
        reason = rearm_policy["guardrail_reasons"] or [f"GBPUSD rearm guardrail status={rearm_policy['guardrail_status']}"]
        return {
            "symbol": symbol,
            "asset_class": defaults["asset_class"],
            "deployable": False,
            "reason": f"{symbol} rearm guardrail blocks deployment: {reason[0]}",
            "step": atr_row.get("step"),
            "step_buy": atr_row.get("step_buy"),
            "step_sell": atr_row.get("step_sell"),
            "asymmetry_ratio": atr_row.get("asymmetry_ratio"),
            "session_weight": atr_row.get("session_weight"),
            "raw_close_alpha": atr_row.get("raw_close_alpha", 0.5),
            "max_open_per_side": atr_row.get("max_open_per_side", defaults["max_open_per_side"]),
            "atr_current": atr_row.get("atr_current", defaults["base_step"]),
            "rearm_policy": rearm_policy,
            "timeframe": defaults["timeframe"],
        }

    # GBPUSD is deployable if:
    # - Not at extreme (not wait_extreme_confirmation)
    # - Regime allows new entries
    # Mean-reversion lattices THRIVE at extremes — that's where the chop/reversal alpha is
    # wait_extreme_confirmation = AGGRESSIVE deployment, not HOLD
    if control_mode == "wait_extreme_confirmation":
        return {
            "symbol": symbol,
            "asset_class": defaults["asset_class"],
            "deployable": True,
            "regime": atr_row["regime"],
            "control_mode": control_mode,
            "consensus": consensus,
            "action_bias": action_bias,
            "step": atr_row["step"],
            "step_buy": atr_row["step_buy"],
            "step_sell": atr_row["step_sell"],
            "asymmetry_ratio": atr_row["asymmetry_ratio"],
            "session_weight": atr_row["session_weight"],
            "raw_close_alpha": atr_row.get("raw_close_alpha", 0.5),
            "max_open_per_side": atr_row.get("max_open_per_side", defaults["max_open_per_side"]),
            "atr_current": atr_row.get("atr_current", defaults["base_step"]),
            "regime_row": regime_row,
            "rearm_policy": rearm_policy,
            "timeframe": defaults["timeframe"],
            # Extreme-specific adjustments
            "extreme_mode": True,
            "step_multiplier": 1.5,  # Wider steps for bigger bounces
            "max_open_multiplier": 1.5,  # More positions to capture chop
            "close_alpha_override": 0.3,  # Take profits faster on bounces
        }

    if control_mode in ("trend_follow", "bounce_reversal", "breakout_follow"):
        return {
            "symbol": symbol,
            "asset_class": defaults["asset_class"],
            "deployable": True,
            "regime": atr_row["regime"],
            "control_mode": control_mode,
            "consensus": consensus,
            "action_bias": action_bias,
            "step": atr_row["step"],
            "step_buy": atr_row["step_buy"],
            "step_sell": atr_row["step_sell"],
            "asymmetry_ratio": atr_row["asymmetry_ratio"],
            "session_weight": atr_row["session_weight"],
            "raw_close_alpha": atr_row.get("raw_close_alpha", 0.5),
            "max_open_per_side": atr_row.get("max_open_per_side", defaults["max_open_per_side"]),
            "atr_current": atr_row.get("atr_current", defaults["base_step"]),
            "regime_row": regime_row,
            "rearm_policy": rearm_policy,
            "timeframe": defaults["timeframe"],
        }

    return {
        "symbol": symbol,
        "asset_class": defaults["asset_class"],
        "deployable": False,
        "reason": f"{symbol} regime not deployable: {control_mode}",
        "step": atr_row.get("step"),
        "step_buy": atr_row.get("step_buy"),
        "step_sell": atr_row.get("step_sell"),
        "asymmetry_ratio": atr_row.get("asymmetry_ratio"),
        "session_weight": atr_row.get("session_weight"),
        "raw_close_alpha": atr_row.get("raw_close_alpha", 0.5),
        "max_open_per_side": atr_row.get("max_open_per_side", defaults["max_open_per_side"]),
        "atr_current": atr_row.get("atr_current", defaults["base_step"]),
        "rearm_policy": rearm_policy,
        "timeframe": defaults["timeframe"],
    }


def build_hungry_hippo_config(symbol: str, validation: dict, rearm_params: dict) -> dict:
    """Build the unified Hungry Hippo config for symbol deployment."""
    symbol = symbol.upper()
    defaults = runtime_defaults_for_symbol(symbol)
    # Use validation params if deployable, otherwise fall back to safe defaults
    step = validation.get("step", defaults["base_step"])
    step_buy = validation.get("step_buy", step)
    step_sell = validation.get("step_sell", step)
    asymmetry_ratio = validation.get("asymmetry_ratio", 2.0)
    session_weight = validation.get("session_weight", 0.5)
    regime = validation.get("regime", "WEAK_TREND")
    raw_close_alpha = validation.get("raw_close_alpha", 0.5)
    max_open = validation.get("max_open_per_side", defaults["max_open_per_side"])
    control_mode = validation.get("control_mode", "unknown")
    rearm_policy = validation.get("rearm_policy") or resolve_rearm_policy(rearm_params, symbol)
    if rearm_policy["guardrail_status"] == "uncovered":
        rearm_policy = fallback_rearm_policy(symbol)
    current_rearm = dict(rearm_policy.get("current_state") or {})
    kill_response_table = convert_kill_response_table(rearm_policy.get("kill_response_table") or {})

    # Apply extreme mode adjustments if applicable
    is_extreme = validation.get("extreme_mode", False)
    if is_extreme:
        step = step * validation.get("step_multiplier", 1.5)
        step_buy = step_buy * validation.get("step_multiplier", 1.5)
        step_sell = step_sell * validation.get("step_multiplier", 1.5)
        max_open = int(max_open * validation.get("max_open_multiplier", 1.5))
        raw_close_alpha = validation.get("close_alpha_override", raw_close_alpha)
        regime_coeff = 2.0  # Aggressive at extremes
    else:
        regime_coeff_map = {"STRONG_TREND": 1.5, "WEAK_TREND": 1.0, "TRANSITION": 0.8, "RANGING": 0.5}
        regime_coeff = regime_coeff_map.get(regime, 1.0)
    atr_current = validation.get("atr_current")
    if atr_current is None:
        atr_current = step / (session_weight * regime_coeff) if session_weight * regime_coeff > 0 else defaults["base_step"]

    return {
        "version": "hungry_hippo_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "timeframe": validation.get("timeframe", defaults["timeframe"]),
        "magic": 941800,
        "comment_prefix": f"HH-{symbol}",
        "volume": 0.01,
        "live": False,
        "deployable": validation.get("deployable", False),
        "deploy_reason": validation.get("reason", "") if not validation.get("deployable") else f"{control_mode} regime, {validation.get('consensus', '')} consensus",
        "guardrails": {
            "rearm_guardrail_status": rearm_policy.get("guardrail_status"),
            "auto_rearm_allowed": rearm_policy.get("auto_rearm_allowed"),
            "rearm_guardrail_reasons": rearm_policy.get("guardrail_reasons"),
            "session_window": rearm_policy.get("session_window"),
            "current_state_status_counts": rearm_policy.get("guardrail_summary", {}).get("current_state_status_counts", {}),
        },
        "geometry": {
            "step": round(step, 8),
            "step_buy": round(step_buy, 8),
            "step_sell": round(step_sell, 8),
            "asymmetric": asymmetry_ratio != 1.0,
            "asymmetry_ratio": asymmetry_ratio,
            "adaptive": {
                "atr_scaled": True,
                "atr_current": round(atr_current, 8),
                "regime_coeff": regime_coeff,
                "session_weight": session_weight,
                "threshold_1": 10,
                "threshold_2": 20,
                "multiplier_1": 1.5,
                "multiplier_2": 2.0,
            },
        },
        "close": {
            "alpha": raw_close_alpha,
            "style": "all_profitable",
            "gap": 1,
        },
        "rearm": {
            "variant": current_rearm.get("rearm_variant", "rearm_lvl2_exc2"),
            "cooldown_bars": 12,
            "momentum_gate": True,
            "enabled": bool(rearm_policy.get("auto_rearm_allowed")),
            "guardrail_status": rearm_policy.get("guardrail_status"),
            "guardrail_reasons": rearm_policy.get("guardrail_reasons"),
            "session_window": rearm_policy.get("session_window"),
            "kill_response": kill_response_table,
            "failure_backoff": {
                "enabled": True,
                "base_cooldown_seconds": 30,
                "multiplier_per_failure": 2.0,
                "max_cooldown_seconds": 1800,
            },
        },
        "risk": {
            "max_floating_loss_usd": defaults["max_floating_loss_usd"],
            "max_open_per_side": max_open,
            "max_lattice_window_bars": 240,
            "breakout_buffer_pips": defaults["breakout_buffer_pips"],
        },
        "regime": {
            "control_mode": control_mode,
            "regime": regime,
            "gate_enabled": True,
        },
        "session": {
            "gate_enabled": True,
            "current_weight": session_weight,
        },
        "hungry_hippo_metadata": {
            "asset_class": defaults["asset_class"],
            "validation_status": (
                "deployable_from_component_stack"
                if validation.get("deployable")
                else "research_only_component_gap_or_guardrail_block"
            ),
        },
    }


def run_forward_shadow(symbol: str, days: int, config: dict) -> dict:
    """Run a forward shadow test comparing Hungry Hippo vs baseline."""
    end_utc = datetime.now(UTC)
    start_utc = end_utc - timedelta(days=days)

    print(f"Loading {days} days of {symbol} M15 ticks...")
    ticks = load_ticks_range(symbol, start_utc, end_utc)
    if not ticks:
        print(f"No ticks loaded for {symbol}")
        return {"error": "No ticks loaded"}

    print(f"Loaded {len(ticks)} ticks")

    # Run Hungry Hippo config
    print(f"\n--- Hungry Hippo Config ---")
    print(f"  Step: {config['geometry']['step']:.6f}")
    print(f"  Step BUY: {config['geometry']['step_buy']:.6f}")
    print(f"  Step SELL: {config['geometry']['step_sell']:.6f}")
    print(f"  Asymmetric: {config['geometry']['asymmetric']}")
    print(f"  Asymmetry: {config['geometry']['asymmetry_ratio']:.1f}:1")
    print(f"  Close alpha: {config['close']['alpha']}")
    print(f"  Max open/side: {config['risk']['max_open_per_side']}")
    print()

    # Build engine with Hungry Hippo params
    hh_engine = engine_from_args(
        symbol=symbol,
        step=config["geometry"]["step"],
        max_open_per_side=config["risk"]["max_open_per_side"],
        variant_name="rearm_lvl2_exc2",
        timeframe_name="M15",
        close_alpha=config["close"]["alpha"],
        momentum_gate=config["rearm"]["momentum_gate"],
        cooldown_bars=config["rearm"]["cooldown_bars"],
        sell_gap=config["close"]["gap"],
        buy_gap=config["close"]["gap"],
    )

    # Process all ticks
    for tick in ticks:
        hh_engine.process_tick(tick)

    hh_closes = int(hh_engine.state.realized_closes)
    hh_net = float(hh_engine.state.realized_net_usd)
    hh_per_close = hh_net / max(1, hh_closes)
    hh_opens = len(hh_engine.state.open_tickets)

    print(f"--- Hungry Hippo Results ---")
    print(f"  Closes: {hh_closes}")
    print(f"  Net PnL: ${hh_net:.2f}")
    print(f"  $/close: ${hh_per_close:.4f}")
    print(f"  Open positions: {hh_opens}")
    print(f"  Anchor resets: {hh_engine.state.anchor_resets}")

    return {
        "symbol": symbol,
        "days": days,
        "ticks_processed": len(ticks),
        "hungry_hippo": {
            "closes": hh_closes,
            "net_usd": round(hh_net, 2),
            "per_close": round(hh_per_close, 4),
            "open_positions": hh_opens,
            "anchor_resets": hh_engine.state.anchor_resets,
        },
    }


def main():
    import MetaTrader5 as mt5
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="GBPUSD", help="Symbol to build/run (default: GBPUSD)")
    parser.add_argument("--days", type=int, default=7, help="Replay days for forward shadow test")
    parser.add_argument("--skip-shadow-test", action="store_true", help="Only build the config, skip the replay")
    args = parser.parse_args()
    symbol = args.symbol.upper()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        print("=" * 80)
        print(f"HUNGRY HIPPO INTEGRATION PIPELINE — {symbol}")
        print("=" * 80)
        print()

        # Step 1-5: Load all components
        print("Loading components...")
        atr_params = load_component("hungry_hippo_atr_step_params.json")
        rearm_params = load_component("hungry_hippo_rearm_params.json")
        session_table = load_component("session_regime_step_table_v2.json")
        regime_signal = load_component("regime_signal.json")
        print(f"  [ok] ATR step params ({len(atr_params['symbols'])} symbols)")
        print("  [ok] Rearm params")
        print("  [ok] Session table V2")
        print("  [ok] Regime signal")
        print()

        # Step 6: Validate symbol
        print(f"Running validation gate on {symbol}...")
        validation = validate_symbol_deploy(symbol, atr_params, regime_signal, rearm_params)
        if not validation["deployable"]:
            print(f"  [blocked] {symbol} not deployable: {validation['reason']}")
            print("  Building shadow config anyway for research purposes.")
        else:
            print(f"  [ok] {symbol} deployable")
            print(f"     Regime: {validation['regime']}")
            print(f"     Control mode: {validation['control_mode']}")
            print(f"     Rearm guardrail: {validation['rearm_policy']['guardrail_status']}")
        print()

        # Step 7: Build unified config
        print("Building unified Hungry Hippo config...")
        config = build_hungry_hippo_config(symbol, validation, rearm_params)
        config_path = REPO / "configs" / f"hungry_hippo_{symbol.lower()}_live.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"  [ok] Config saved to: {config_path}")
        print()

        # Step 8: Run forward shadow test
        shadow_results = None
        if not args.skip_shadow_test:
            print(f"Running forward shadow test ({args.days}-day {symbol} {config['timeframe']} replay)...")
            shadow_results = run_forward_shadow(symbol, args.days, config)
        else:
            print("Skipping forward shadow test by request.")

        # Save shadow results
        results_path = REPO / "reports" / f"hungry_hippo_{symbol.lower()}_shadow_{args.days}d.json"
        results_path.parent.mkdir(parents=True, exist_ok=True)
        with open(results_path, "w") as f:
            json.dump({
                "config": config,
                "validation": validation,
                "shadow_test": shadow_results,
            }, f, indent=2)
        print(f"\n  [ok] Results saved to: {results_path}")

        print()
        print("=" * 80)
        print("INTEGRATION PIPELINE COMPLETE")
        print("=" * 80)

    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())
