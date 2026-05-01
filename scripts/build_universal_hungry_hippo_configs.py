"""Universal Hungry Hippo Config Generator

Combines shapeshifter optimal configs + escape hatches + regime signal
into deployable configs for ALL symbols.

Usage:
  python scripts/build_universal_hungry_hippo_configs.py
  python scripts/build_universal_hungry_hippo_configs.py --symbol GBPUSD
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc

sys.path.insert(0, str(Path(__file__).parent))
from hungry_hippo_symbol_profiles import (
    discover_symbols,
    escape_defaults_for_symbol,
    runtime_defaults_for_symbol,
)


def load_component(name):
    path = REPO / "reports" / name
    with open(path) as f:
        return json.load(f)


REGIME_CONFIGS = {
    "EXTREME": {"step_mult": 1.5, "asym": "1.0:1", "alpha": 0.2, "desc": "Wide steps, fast takes — the trampoline"},
    "TREND": {"step_mult": 1.0, "asym": "2.0:1", "alpha": 0.5, "desc": "Directional bias + moderate steps"},
    "CHOP": {"step_mult": 0.75, "asym": "1.0:1", "alpha": 0.3, "desc": "Tight vacuum cleaner"},
    "MIXED": {"step_mult": 2.5, "asym": "1.0:1", "alpha": 0.2, "desc": "Defensive wide safety net"},
}

CONTROL_MODE_TO_REGIME = {
    "wait_extreme_confirmation": "EXTREME",
    "bounce_reversal": "EXTREME",
    "trend_follow": "TREND",
    "breakout_follow": "TREND",
    "mixed_hold": "MIXED",
}


def lookup_symbol_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    symbol = str(symbol or "").upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() == symbol:
            return row
    return None


def map_control_mode_to_regime_key(control_mode: str, normalized_regime: str = "") -> str:
    mapped = CONTROL_MODE_TO_REGIME.get(str(control_mode or "").strip().lower())
    if mapped:
        return mapped
    normalized = str(normalized_regime or "").strip().lower()
    if normalized == "ranging":
        return "CHOP"
    if normalized == "mixed":
        return "MIXED"
    if normalized == "trending":
        return "TREND"
    return "MIXED"


def build_symbol_config(symbol, extreme_results, atr_params, regime_signal):
    """Build complete Hungry Hippo config for a symbol."""
    symbol = str(symbol or "").upper()
    sym_data = (extreme_results.get("symbols", {}) or {}).get(symbol)
    atr_data = lookup_symbol_row(list(atr_params.get("symbols", []) or []), symbol)
    regime_row = lookup_symbol_row(list(regime_signal.get("rows", []) or []), symbol)
    if not sym_data and not atr_data and not regime_row:
        return None

    control_mode = regime_row.get("control_mode", "mixed_hold") if regime_row else "mixed_hold"
    regime_key = map_control_mode_to_regime_key(control_mode, (regime_row or {}).get("normalized_regime", ""))

    rc = dict(REGIME_CONFIGS[regime_key])
    regime_opt = dict(((sym_data or {}).get("optimal_by_regime") or {}).get(regime_key) or {})
    defaults = runtime_defaults_for_symbol(symbol)
    step_mult = float(regime_opt.get("step_mult", rc["step_mult"]))
    alpha = float(regime_opt.get("alpha", rc["alpha"]))
    asym_text = str(regime_opt.get("asym", rc["asym"]))

    atr_current = atr_data.get("atr_current", defaults["base_step"]) if atr_data else defaults["base_step"]
    base_step = atr_current * step_mult

    asym_parts = asym_text.split(":")
    asym_ratio = float(asym_parts[0]) / float(asym_parts[1]) if len(asym_parts) == 2 else 1.0

    step_sell = (2 * base_step) / (1 + asym_ratio)
    step_buy = asym_ratio * step_sell

    static_perf = (sym_data or {}).get("strategy_comparison", {}).get("static", {})
    regime_perf = (sym_data or {}).get("strategy_comparison", {}).get("regime_matched", {})
    static_net = float(static_perf.get("net_usd", 0) or 0)
    regime_net = float(regime_perf.get("net_usd", 0) or 0)
    improvement = regime_net / max(static_net, 0.01) if static_net > 0 else (0.0 if regime_net <= 0 else None)
    deployable = atr_data is not None and regime_row is not None
    ec = escape_defaults_for_symbol(symbol, atr_current=atr_current, reference_step=base_step)

    config = {
        "version": "hungry_hippo_shapeshifter_v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "timeframe": defaults["timeframe"],
        "deployable": deployable,
        "deploy_reason": (
            f"Escape hatch enabled, regime-matched ({regime_key}), {improvement:.1f}x improvement over static"
            if improvement is not None
            else f"Escape hatch enabled, regime-matched ({regime_key}), no static baseline available yet"
        ),
        "geometry": {
            "step": round(base_step, 8),
            "step_buy": round(step_buy, 8),
            "step_sell": round(step_sell, 8),
            "asymmetric": asym_ratio != 1.0,
            "asymmetry_ratio": round(asym_ratio, 2),
            "adaptive": {
                "atr_scaled": True,
                "atr_current": atr_current,
                "regime_coeff": step_mult,
                "regime": regime_key,
            },
        },
        "close": {
            "alpha": alpha,
            "style": "all_profitable",
            "gap": 1,
        },
        "rearm": {
            "variant": "rearm_lvl2_exc2",
            "cooldown_bars": 12,
            "momentum_gate": True,
        },
        "risk": {
            "max_floating_loss_usd": defaults["max_floating_loss_usd"],
            "max_open_per_side": defaults["max_open_per_side"],
            "max_lattice_window_bars": 240,
            "breakout_buffer_pips": defaults["breakout_buffer_pips"],
        },
        "escape_hatch": {
            "tier1_breakeven": {
                "max_bars": ec["max_bars"],
                "max_loss": ec["max_escape_loss"],
                "description": f"Close at breakeven after {ec['max_bars']} bars if unprofitable",
            },
            "tier2_extreme": {
                "cut_count": ec["cut_count"],
                "max_loss_per_position": ec["max_cut_loss"],
                "description": f"Cut worst {ec['cut_count']} positions at extremes, max ${ec['max_cut_loss']} each",
            },
            "tier3_full_kill": {
                "max_floating_loss_usd": -15.0,
                "description": "Last resort: kill all positions if net floating exceeds threshold",
            },
        },
        "regime": {
            "control_mode": control_mode,
            "regime": regime_key,
            "gate_enabled": False,
        },
        "performance": {
            "static_net": static_net,
            "regime_matched_net": regime_net,
            "improvement_factor": None if improvement is None else round(improvement, 2),
            "static_closes": static_perf.get("closes", 0),
            "regime_matched_closes": regime_perf.get("closes", 0),
        },
        "hungry_hippo_metadata": {
            "asset_class": defaults["asset_class"],
            "evidence_sources": {
                "extreme_tuning_present": sym_data is not None,
                "atr_params_present": atr_data is not None,
                "regime_signal_present": regime_row is not None,
            },
            "validation_status": (
                "component_coverage_complete"
                if deployable
                else "component_gap_research_only_missing_atr_or_regime_signal"
            ),
        },
    }

    return config


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Build config for specific symbol (default: all)")
    args = parser.parse_args()

    print("=== UNIVERSAL HUNGRY HIPPO CONFIG GENERATOR ===")
    print("Loading components...")

    extreme_results = load_component("hungry_hippo_extreme_tuning_results.json")
    atr_params = load_component("hungry_hippo_atr_step_params.json")
    regime_signal = load_component("regime_signal.json")

    if args.symbol:
        symbols_to_build = [str(args.symbol).upper()]
    else:
        symbols_to_build = discover_symbols(extreme_results, atr_params, regime_signal)

    print(f"Building configs for {len(symbols_to_build)} symbols...")

    all_configs = {}
    for symbol in symbols_to_build:
        config = build_symbol_config(symbol, extreme_results, atr_params, regime_signal)
        if config:
            all_configs[symbol] = config
            perf = config["performance"]
            improvement_text = (
                f"{perf['improvement_factor']}x"
                if perf["improvement_factor"] is not None
                else "n/a"
            )
            print(
                f"  {symbol}: regime={config['regime']['regime']}, "
                f"improvement={improvement_text}, "
                f"static=${perf['static_net']:.2f} -> shapeshifter=${perf['regime_matched_net']:.2f}"
            )

            config_path = REPO / "configs" / f"hungry_hippo_{symbol.lower()}_live.json"
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

    master = {
        "generated_at": datetime.now(UTC).isoformat(),
        "symbol_count": len(all_configs),
        "configs": {sym: cfg["deploy_reason"] for sym, cfg in all_configs.items()},
    }
    master_path = REPO / "configs" / "hungry_hippo_master_index.json"
    with open(master_path, "w") as f:
        json.dump(master, f, indent=2)

    print(f"\nSaved {len(all_configs)} configs to configs/hungry_hippo_*_live.json")
    print(f"Master index: configs/hungry_hippo_master_index.json")


if __name__ == "__main__":
    main()
