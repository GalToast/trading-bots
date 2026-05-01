#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import adaptive_lattice_controller as controller
try:
    import tape_read_bridge as tape_bridge
except ImportError:
    from scripts import tape_read_bridge as tape_bridge


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
DEFAULT_SHAPE_LIBRARY_PATH = ROOT / "configs" / "adaptive_lattice_shape_library.json"
DEFAULT_REGIME_PATH = ROOT / "reports" / "regime_classification_live.json"
DEFAULT_RUNTIME_AUDIT_PATH = ROOT / "reports" / "btc_adaptive_runtime_audit.json"
DEFAULT_UNIFIED_SPEC_PATH = ROOT / "reports" / "unified_lattice_design_spec.json"
DEFAULT_OUTPUT_JSON = ROOT / "reports" / "adaptive_btc_shadow_runner_plan.json"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "adaptive_btc_shadow_runner_plan.md"
SPEC_KEY = "btc_m15_aggressive"


REGIME_MAP = {
    "STRONG_TREND": "trending",
    "WEAK_TREND": "trending",
    "TRANSITION": "mixed",
    "RANGE": "ranging",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_optional_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return load_json(path)


def find_registry_lane(registry: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for lane in list(registry.get("lanes") or []):
        if str(lane.get("name") or "") == lane_name:
            return lane
    raise KeyError(f"Unknown runner registry lane: {lane_name}")


def restart_args_to_flags(args: list[str]) -> dict[str, str]:
    flags: dict[str, str] = {}
    idx = 0
    while idx < len(args):
        item = str(args[idx])
        if not item.startswith("--"):
            idx += 1
            continue
        if idx + 1 >= len(args) or str(args[idx + 1]).startswith("--"):
            flags[item] = "true"
            idx += 1
            continue
        flags[item] = str(args[idx + 1])
        idx += 2
    return flags


SUPPORTED_RUNTIME_OVERLAYS = [
    "guard_open_admission",
    "cluster_aware_escape",
    "suppress_additional_levels_after_burst",
]


def build_runtime_overlay_contract(
    flags: dict[str, str],
    runtime_overlays: list[str] | None,
) -> dict[str, Any]:
    requested = [str(item) for item in list(runtime_overlays or []) if str(item or "").strip()]
    supported = list(SUPPORTED_RUNTIME_OVERLAYS)
    executable: list[str] = []
    unsupported: list[str] = []
    command_flags: list[str] = []

    if "guard_open_admission" in requested:
        executable.append("guard_open_admission")
        command_flags.append("--guard-open-admission")

    if "cluster_aware_escape" in requested:
        executable.append("cluster_aware_escape")
        command_flags.append("--cluster-aware-escape")
        tolerance = flags.get("--cluster-fill-tolerance") or "0.01"
        command_flags.extend(["--cluster-fill-tolerance", str(tolerance)])
    if "suppress_additional_levels_after_burst" in requested:
        executable.append("suppress_additional_levels_after_burst")
        command_flags.append("--suppress-additional-levels-after-burst")
        burst_open_threshold = flags.get("--burst-open-threshold") or "2"
        command_flags.extend(["--burst-open-threshold", str(burst_open_threshold)])

    for overlay in requested:
        if overlay not in executable:
            unsupported.append(overlay)

    if requested and unsupported:
        supported_read = ", ".join(supported)
        contract_read = (
            "Controller requested runtime overlays. This scaffold can execute the current guarded-toxic-flow controls "
            f"({supported_read}), but some requested overlays still remain unsupported/manual-review obligations."
        )
    elif requested:
        contract_read = "All currently requested runtime overlays are executable from this scaffold."
    else:
        supported_read = ", ".join(supported)
        contract_read = (
            "Controller did not request any runtime overlays for this scaffold. "
            f"This scaffold can currently express {supported_read} when a future controller state requests them."
        )

    return {
        "supported_overlays": supported,
        "requested_overlays": requested,
        "executable_overlays": executable,
        "unsupported_overlays": unsupported,
        "command_flags": command_flags,
        "read": contract_read,
    }


def regime_row(regime_payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    for row in list(regime_payload.get("symbols") or []):
        if str(row.get("symbol") or "").upper() == symbol.upper():
            return row
    raise KeyError(f"Missing regime row for symbol: {symbol}")


def normalize_regime(source_regime: str) -> str:
    normalized = REGIME_MAP.get(str(source_regime or "").upper())
    if normalized:
        return normalized
    return "mixed"


def slugify_token(value: str) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "unknown"


def default_shadow_runtime_identity(symbol: str, timeframe: str | None) -> dict[str, str]:
    symbol_slug = slugify_token(symbol)
    timeframe_slug = slugify_token(timeframe or "m1")
    lane_name = f"shadow_{symbol_slug}_{timeframe_slug}_adaptive_regime"
    stem = f"penetration_lattice_shadow_{symbol_slug}_{timeframe_slug}_adaptive_regime"
    return {
        "lane_name": lane_name,
        "state_path": f"reports/{stem}_state.json",
        "event_path": f"reports/{stem}_events.jsonl",
    }


def default_plan_output_paths(symbol: str) -> tuple[Path, Path]:
    symbol_slug = slugify_token(symbol)
    base = ROOT / "reports" / f"adaptive_{symbol_slug}_regime_switch_plan"
    return base.with_suffix(".json"), base.with_suffix(".md")


def find_shape(library: dict[str, Any], symbol: str, shape_id: str) -> dict[str, Any]:
    symbol_payload = dict((library.get("symbols") or {}).get(symbol) or {})
    for shape in list(symbol_payload.get("candidate_shapes") or []):
        if str(shape.get("shape_id") or "") == shape_id:
            return shape
    raise KeyError(f"Missing shape {shape_id} for {symbol}")


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def runtime_objective_context(runtime_audit: dict[str, Any] | None) -> dict[str, Any]:
    runtime_lane = dict((runtime_audit or {}).get("runtime_lane") or {})
    realized_close_count = max(
        safe_int(runtime_lane.get("realized_close_count", runtime_lane.get("realized_closes"))),
        0,
    )
    realized_net_usd = safe_float(runtime_lane.get("realized_net_usd"))
    realized_avg_per_close = safe_float(runtime_lane.get("realized_avg_per_close"))
    if realized_avg_per_close is None and realized_close_count > 0 and realized_net_usd is not None:
        realized_avg_per_close = realized_net_usd / realized_close_count
    return {
        "audit_present": bool(runtime_audit),
        "lane_name": runtime_lane.get("lane_name", ""),
        "open_count": max(safe_int(runtime_lane.get("open_count")), 0),
        "runner_session_trade_closes": max(safe_int(runtime_lane.get("runner_session_trade_closes")), 0),
        "runner_session_trade_realized_usd": safe_float(runtime_lane.get("runner_session_trade_realized_usd")),
        "pre_start_state_carry_realized_usd": safe_float(runtime_lane.get("pre_start_state_carry_realized_usd")),
        "realized_close_count": realized_close_count,
        "realized_net_usd": realized_net_usd,
        "realized_avg_per_close": realized_avg_per_close,
        "realized_win_rate": safe_float(runtime_lane.get("realized_win_rate")),
        "anchor_reset_count": max(safe_int(runtime_lane.get("anchor_reset_count", runtime_lane.get("anchor_resets"))), 0),
        "tape_read_present": False,
        "tape_profit_mode": "",
        "tape_profit_mode_confidence": 0.0,
    }


def merge_non_null_fields(base: dict[str, Any], overlay: dict[str, Any] | None) -> dict[str, Any]:
    for key, value in dict(overlay or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        base[key] = value
    return base


def merge_tape_signals_into_regime_row(
    regime_row_payload: dict[str, Any],
    tape_signals: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(regime_row_payload)
    signals = dict(tape_signals or {})
    for key in ("same_bar_open_burst_count", "same_tick_open_burst_count"):
        if key in signals and signals.get(key) is not None:
            merged[key] = max(safe_int(merged.get(key)), safe_int(signals.get(key)))
            signals.pop(key, None)
    return merge_non_null_fields(merged, signals)


def maybe_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text)


def resolve_baseline_step(
    symbol: str,
    baseline_step: float,
    state_path: Path | None,
) -> float:
    if baseline_step > 0:
        return baseline_step
    state = load_optional_json(state_path)
    if not state:
        return baseline_step
    symbol_state = dict((state.get("symbols") or {}).get(symbol) or {})
    base_step = safe_float(symbol_state.get("base_step_px"))
    if base_step is not None and base_step > 0:
        return base_step
    buy_step = safe_float(symbol_state.get("base_step_buy_px"))
    sell_step = safe_float(symbol_state.get("base_step_sell_px"))
    if buy_step is not None and sell_step is not None and buy_step > 0 and sell_step > 0:
        return round((buy_step + sell_step) / 2.0, 5)
    return baseline_step


def load_lane_tape_read(symbol: str, state_path: Path | None, event_path: Path | None) -> dict[str, Any] | None:
    if state_path is None:
        return None
    state = tape_bridge.load_state(state_path)
    events = tape_bridge.load_events(event_path) if event_path is not None else []
    if not state and not events:
        return None
    tape_read = tape_bridge.build_tape_read(state, events, symbol)
    if not events:
        tape_signals = dict(tape_read.get("tape_signals") or {})
        for key in (
            "directional_bias",
            "same_bar_round_trip_rate",
            "same_bar_open_burst_count",
            "same_tick_open_burst_count",
        ):
            tape_signals.pop(key, None)
        tape_read["tape_signals"] = tape_signals
    return tape_read


def merge_objective_context_with_tape(
    objective_context: dict[str, Any],
    tape_read: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(objective_context)
    if not tape_read:
        return merged

    merged["tape_read_present"] = True
    merged["tape_profit_mode"] = str(tape_read.get("profit_mode") or "")
    merged["tape_profit_mode_confidence"] = float(tape_read.get("profit_mode_confidence") or 0.0)

    realized = dict(tape_read.get("realized_evidence") or {})
    tape_close_count = max(safe_int(realized.get("realized_close_count")), 0)
    tape_net = safe_float(realized.get("realized_net_usd"))
    tape_avg = safe_float(realized.get("realized_avg_per_close"))

    current_close_count = max(safe_int(merged.get("realized_close_count")), 0)
    current_net = safe_float(merged.get("realized_net_usd"))
    tape_is_richer = tape_close_count > current_close_count
    if (
        not tape_is_richer
        and tape_close_count > 0
        and tape_close_count == current_close_count
        and tape_net is not None
        and current_net is not None
        and abs(tape_net) > abs(current_net)
    ):
        tape_is_richer = True

    if tape_is_richer:
        merged["realized_close_count"] = tape_close_count
        merged["realized_net_usd"] = tape_net
        merged["realized_avg_per_close"] = tape_avg

    if max(safe_int(merged.get("runner_session_trade_closes")), 0) <= 0 and tape_close_count > 0:
        merged["runner_session_trade_closes"] = tape_close_count
    tape_has_realized_signal = bool(tape_close_count > 0 or (tape_net is not None and abs(tape_net) > 0))
    if merged.get("runner_session_trade_realized_usd") is None and tape_net is not None and tape_has_realized_signal:
        merged["runner_session_trade_realized_usd"] = tape_net

    return merged


def build_step_review(
    *,
    adaptive_step: float,
    baseline_step: float,
    unified_spec: dict[str, Any] | None,
) -> dict[str, Any]:
    comparators: list[dict[str, Any]] = []
    severe_warnings: list[str] = []
    notes: list[str] = []

    if baseline_step > 0 and adaptive_step > 0:
        ratio = adaptive_step / baseline_step
        comparators.append(
            {
                "comparator_id": "legacy_warp_baseline",
                "baseline_step": baseline_step,
                "adaptive_step": adaptive_step,
                "ratio": round(ratio, 2),
                "status": "legacy_microstep_separation" if ratio >= 5.0 else "near_baseline",
                "read": (
                    "High separation from the baseline lane can be valid when regime switching selects a materially coarser adaptive contract; the baseline is lineage context, not the design target."
                    if ratio >= 5.0
                    else "Adaptive step remains near the baseline lane."
                ),
            }
        )
        if ratio >= 5.0:
            notes.append(
                f"legacy_warp_baseline_separation_expected:{ratio:.2f}x (adaptive={adaptive_step}, baseline={baseline_step})"
            )

    spec_shape = dict((unified_spec or {}).get("shapes", {}).get(SPEC_KEY) or {})
    design_step = safe_float(spec_shape.get("step"))
    if design_step is not None and design_step > 0 and adaptive_step > 0:
        delta = round(adaptive_step - design_step, 5)
        pct_delta = round(abs(delta) / design_step * 100.0, 2)
        tolerance = max(50.0, design_step * 0.15)
        near_design = abs(delta) <= tolerance
        comparators.append(
            {
                "comparator_id": "unified_design_target",
                "target_step": design_step,
                "adaptive_step": adaptive_step,
                "delta": delta,
                "pct_delta": pct_delta,
                "tolerance": round(tolerance, 5),
                "status": "near_design_target" if near_design else "design_target_mismatch",
                "read": (
                    f"Adaptive step stays within tolerance of the unified BTC M15 design target ({adaptive_step} vs {design_step})."
                    if near_design
                    else f"Adaptive step is materially outside the unified BTC M15 design target ({adaptive_step} vs {design_step})."
                ),
            }
        )
        if near_design:
            notes.append(f"adaptive_step_near_unified_design_target:{adaptive_step} vs {design_step} ({pct_delta}% delta)")
        else:
            severe_warnings.append(
                f"adaptive_step_vs_unified_design_target_high:{pct_delta}% (adaptive={adaptive_step}, design={design_step})"
            )

    has_design_target = any(item.get("comparator_id") == "unified_design_target" for item in comparators)
    if has_design_target:
        review_read = (
            "Adaptive step should be judged against the unified design target first and the baseline lane only as historical lineage context."
        )
    elif comparators:
        review_read = (
            "Adaptive step should be judged against the baseline lane as a regime-switch divergence check; larger separation can be valid when the selected contract is intentionally coarser."
        )
    else:
        review_read = "Adaptive step review has no comparator context."
    return {
        "comparators": comparators,
        "review_read": review_read,
        "notes": notes,
        "severe_warnings": severe_warnings,
    }


def resolve_steps(shape: dict[str, Any], baseline_step: float, live_regime: dict[str, Any]) -> dict[str, Any]:
    step_method = dict(shape.get("step_method") or {})
    kind = str(step_method.get("kind") or "")
    current_atr = float(live_regime.get("current_atr", 0.0) or 0.0)

    if kind == "range_atr_formula":
        avg_range = safe_float(live_regime.get("avg_range"))
        range_atr_ratio = safe_float(live_regime.get("range_atr_ratio"))
        published_step = safe_float(live_regime.get("range_atr_formula_step"))
        published_coeff = safe_float(live_regime.get("range_atr_clamped_coeff"))
        formula = str(step_method.get("formula") or "step = avg_range * clamp(1.6 - 0.6 * range_atr_ratio, 0.5, 1.2)")
        min_coeff = safe_float(step_method.get("min_coeff"))
        max_coeff = safe_float(step_method.get("max_coeff"))
        min_coeff = 0.5 if min_coeff is None else min_coeff
        max_coeff = 1.2 if max_coeff is None else max_coeff
        missing_inputs: list[str] = []
        if avg_range is None or avg_range <= 0:
            missing_inputs.append("avg_range")
        if range_atr_ratio is None or range_atr_ratio <= 0:
            missing_inputs.append("range_atr_ratio")
        if not missing_inputs:
            raw_coeff = 1.6 - 0.6 * range_atr_ratio
            coeff = max(min_coeff, min(max_coeff, raw_coeff))
            adaptive_step = round(published_step, 5) if published_step is not None and published_step > 0 else round(avg_range * coeff, 5)
            coeff_read = round(published_coeff, 5) if published_coeff is not None and published_coeff > 0 else round(coeff, 5)
            return {
                "kind": kind,
                "step": adaptive_step,
                "step_buy": adaptive_step,
                "step_sell": adaptive_step,
                "step_source": (
                    "regime_classification_live.range_atr_formula_step"
                    if published_step is not None and published_step > 0
                    else "regime_classification_live.avg_range * clamp(1.6 - 0.6 * range_atr_ratio, 0.5, 1.2)"
                ),
                "formula": formula,
                "formula_inputs_available": True,
                "avg_range": round(avg_range, 5),
                "range_atr_ratio": round(range_atr_ratio, 5),
                "range_atr_clamped_coeff": coeff_read,
            }
        return {
            "kind": kind,
            "step": baseline_step,
            "step_buy": baseline_step,
            "step_sell": baseline_step,
            "step_source": "baseline_step_fallback_missing_range_atr_inputs",
            "formula": formula,
            "formula_inputs_available": False,
            "missing_inputs": missing_inputs,
            "warnings": [f"range_atr_formula_inputs_missing:{','.join(missing_inputs)}"],
        }
    if kind == "atr_multiple_asymmetric":
        buy = round(current_atr * float(step_method.get("buy_coeff", 1.0) or 1.0), 5)
        sell = round(current_atr * float(step_method.get("sell_coeff", 1.0) or 1.0), 5)
        return {
            "kind": kind,
            "step": round((buy + sell) / 2.0, 5),
            "step_buy": buy,
            "step_sell": sell,
            "step_source": "regime_classification_live.current_atr * asymmetric_coeffs",
        }
    if kind == "atr_multiple":
        coeff = float(step_method.get("coeff", 1.0) or 1.0)
        adaptive_step = round(current_atr * coeff, 5) if current_atr > 0 else round(baseline_step * coeff, 5)
        return {
            "kind": kind,
            "step": adaptive_step,
            "step_buy": adaptive_step,
            "step_sell": adaptive_step,
            "step_source": "current_atr * coeff" if current_atr > 0 else "baseline_step * coeff",
        }
    return {
        "kind": kind or "unknown",
        "step": baseline_step,
        "step_buy": baseline_step,
        "step_sell": baseline_step,
        "step_source": "baseline_step",
    }


def build_command(
    base_script: str,
    flags: dict[str, str],
    step_plan: dict[str, Any],
    shape: dict[str, Any],
    shadow_state_path: str,
    shadow_event_path: str,
    runtime_overlay_contract: dict[str, Any] | None = None,
) -> list[str]:
    close = dict(shape.get("close") or {})
    rearm = dict(shape.get("rearm") or {})
    is_crypto_runner = "tick_crypto_shadow" in base_script
    symbol_flag = "--symbols" if flags.get("--symbols") else "--symbol" if flags.get("--symbol") else ""

    cmd = ["python", base_script]
    ordered = [
        (symbol_flag, flags.get(symbol_flag) if symbol_flag else None),
        ("--timeframe", flags.get("--timeframe") if is_crypto_runner else None),
        ("--step", str(step_plan["step"]) if is_crypto_runner else None),
        ("--step-buy", str(step_plan["step_buy"]) if not is_crypto_runner else None),
        ("--step-sell", str(step_plan["step_sell"]) if not is_crypto_runner else None),
        ("--max-open-per-side", flags.get("--max-open-per-side")),
        ("--raw-close-alpha", str(close.get("alpha", flags.get("--raw-close-alpha", "1.0")))),
        ("--raw-rearm-variant", str(rearm.get("variant", flags.get("--raw-rearm-variant", "rearm_lvl2_exc1")))),
        ("--raw-rearm-cooldown-bars", str(rearm.get("cooldown_bars", flags.get("--raw-rearm-cooldown-bars", "0")))),
        ("--raw-sell-gap", str(close.get("sell_gap", flags.get("--raw-sell-gap", "1")))),
        ("--raw-buy-gap", str(close.get("buy_gap", flags.get("--raw-buy-gap", "1")))),
        ("--shared-price-max-age-ms", flags.get("--shared-price-max-age-ms")),
        ("--poll-seconds", flags.get("--poll-seconds")),
        ("--max-floating-loss-usd", flags.get("--max-floating-loss-usd")),
        ("--max-lattice-window-bars", flags.get("--max-lattice-window-bars")),
        ("--state-path", shadow_state_path),
        ("--event-path", shadow_event_path),
    ]
    for flag, value in ordered:
        if value is None or value == "":
            continue
        cmd.extend([flag, value])

    if flags.get("--raw-rearm-momentum-gate", "").lower() == "true":
        cmd.append("--raw-rearm-momentum-gate")
    cmd.extend(list((runtime_overlay_contract or {}).get("command_flags") or []))
    return cmd


def build_plan(
    *,
    lane_name: str = "shadow_btcusd_m15_warp",
    symbol: str = "BTCUSD",
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    shape_library_path: Path = DEFAULT_SHAPE_LIBRARY_PATH,
    regime_path: Path = DEFAULT_REGIME_PATH,
    runtime_audit_path: Path | None = DEFAULT_RUNTIME_AUDIT_PATH,
    unified_spec_path: Path = DEFAULT_UNIFIED_SPEC_PATH,
) -> dict[str, Any]:
    registry = load_json(registry_path)
    lane = find_registry_lane(registry, lane_name)
    restart_args = list(lane.get("restart_args") or [])
    flags = restart_args_to_flags(restart_args)
    base_script = str(restart_args[0]) if restart_args else "scripts/live_penetration_lattice_tick_crypto_shadow.py"
    state_path = maybe_path(lane.get("state_path"))
    event_path = maybe_path(lane.get("event_path"))
    baseline_step = resolve_baseline_step(
        symbol,
        float(flags.get("--step", 0.0) or 0.0),
        state_path,
    )
    timeframe = str(flags.get("--timeframe") or "M1")
    shadow_identity = default_shadow_runtime_identity(symbol, timeframe)

    regime_payload = load_json(regime_path)
    live_regime = regime_row(regime_payload, symbol)
    normalized_regime = normalize_regime(str(live_regime.get("regime") or ""))
    runtime_audit = load_optional_json(runtime_audit_path)
    tape_read = load_lane_tape_read(symbol, state_path, event_path)
    objective_context = merge_objective_context_with_tape(
        runtime_objective_context(runtime_audit),
        tape_read,
    )

    library = controller.load_json(shape_library_path)
    controller_regime_row = dict(live_regime)
    if tape_read:
        controller_regime_row = merge_tape_signals_into_regime_row(
            controller_regime_row,
            dict(tape_read.get("tape_signals") or {}),
        )
    controller_regime_row.update(
        {
            "realized_close_count": objective_context.get("realized_close_count"),
            "realized_net_usd": objective_context.get("realized_net_usd"),
            "realized_avg_per_close": objective_context.get("realized_avg_per_close"),
            "realized_win_rate": objective_context.get("realized_win_rate"),
            "anchor_reset_count": objective_context.get("anchor_reset_count"),
        }
    )
    controller_context = controller.context_from_regime_row(
        controller_regime_row,
        regime=normalized_regime,
        open_count=objective_context["open_count"],
        runner_session_trade_closes=objective_context["runner_session_trade_closes"],
        runner_session_trade_realized_usd=objective_context["runner_session_trade_realized_usd"],
        pre_start_state_carry_realized_usd=objective_context["pre_start_state_carry_realized_usd"],
    )
    recommendation = controller.recommend_shape(
        library,
        symbol,
        controller_context,
    )
    runtime_overlay_contract = build_runtime_overlay_contract(
        flags,
        list(recommendation.get("runtime_overlays") or []),
    )
    if recommendation.get("status") not in ("ok", "blocked_by_survival_constraint"):
        return {
            "generated_from_lane": lane_name,
            "symbol": symbol,
            "status": recommendation.get("status"),
            "baseline": {
                "lane_name": lane_name,
                "script": base_script,
                "timeframe": flags.get("--timeframe"),
                "step": baseline_step,
                "max_open_per_side": int(flags.get("--max-open-per-side", "0") or 0),
                "state_path": lane.get("state_path"),
                "event_path": lane.get("event_path"),
            },
            "live_regime": live_regime,
            "runtime_objective_context": {
                **objective_context,
                "close_conversion_pressure": recommendation.get("close_conversion_pressure", False),
                "negative_carry_pressure": recommendation.get("negative_carry_pressure", False),
                "objective_read": recommendation.get("objective_read", ""),
            },
            "controller_recommendation": recommendation,
            "runtime_overlay_contract": runtime_overlay_contract,
            "adaptive_step_plan": {},
            "step_review": {
                "review_read": "No adaptive step was proposed for the current market state.",
                "comparators": [],
                "notes": [],
            },
            "warnings": [recommendation.get("motion_read") or recommendation.get("why")],
            "proposed_lane_name": "",
            "proposed_command": [],
            "notes": [
                "Scaffold only: this plan does not mutate the registry or launch a process.",
                "Uses the selected lane as the baseline arg surface.",
                "Controller did not approve a launch shape for the current market state.",
                "Tape signals were sourced from the lane state/event artifacts when present.",
            ],
        }
    shape = find_shape(library, symbol, str(recommendation.get("recommended_shape_id") or ""))
    step_plan = resolve_steps(shape, baseline_step, live_regime)
    unified_spec = load_optional_json(unified_spec_path) if symbol.upper() == "BTCUSD" else None

    warnings: list[str] = list(step_plan.get("warnings") or [])
    # Survival constraint warning — plan is for review, not launch
    if recommendation.get("status") == "blocked_by_survival_constraint":
        survival_reason = recommendation.get("survival_block_reason", "unknown")
        warnings.append(f"survival_constraint_blocked:{survival_reason}")
    adaptive_step = float(step_plan["step"] or 0.0)
    step_review = build_step_review(
        adaptive_step=adaptive_step,
        baseline_step=baseline_step,
        unified_spec=unified_spec,
    )
    warnings.extend(list(step_review.get("severe_warnings") or []))
    if adaptive_step <= 0:
        warnings.append("adaptive_step_non_positive")
    if runtime_overlay_contract.get("unsupported_overlays"):
        warnings.append(
            "runtime_overlay_not_yet_launchable:"
            + ",".join(list(runtime_overlay_contract.get("unsupported_overlays") or []))
        )

    status = "ready" if not warnings else "manual_review_required"
    command = build_command(
        base_script,
        flags,
        step_plan,
        shape,
        shadow_identity["state_path"],
        shadow_identity["event_path"],
        runtime_overlay_contract,
    )

    return {
        "generated_from_lane": lane_name,
        "symbol": symbol,
        "status": status,
        "baseline": {
            "lane_name": lane_name,
            "script": base_script,
            "timeframe": flags.get("--timeframe"),
            "step": baseline_step,
            "max_open_per_side": int(flags.get("--max-open-per-side", "0") or 0),
            "state_path": lane.get("state_path"),
            "event_path": lane.get("event_path"),
        },
        "live_regime": live_regime,
        "runtime_objective_context": {
            **objective_context,
            "close_conversion_pressure": recommendation.get("close_conversion_pressure", False),
            "negative_carry_pressure": recommendation.get("negative_carry_pressure", False),
            "objective_read": recommendation.get("objective_read", ""),
        },
        "controller_recommendation": recommendation,
        "runtime_overlay_contract": runtime_overlay_contract,
        "adaptive_step_plan": step_plan,
        "step_review": {
            "review_read": step_review.get("review_read"),
            "comparators": step_review.get("comparators"),
            "notes": step_review.get("notes"),
        },
        "warnings": warnings,
        "proposed_lane_name": shadow_identity["lane_name"],
        "proposed_state_path": shadow_identity["state_path"],
        "proposed_event_path": shadow_identity["event_path"],
        "proposed_command": command,
        "notes": [
            "Scaffold only: this plan does not mutate the registry or launch a process.",
            "Uses the existing lane as the baseline arg surface.",
            "Consumes health-check's regime_classification_live.json rather than recomputing the regime here.",
            "When a runtime audit file is present, the selector also prices in close-conversion pressure and negative carry before picking a shape.",
            "Tape signals from the lane state/event artifacts override stale regime-only fields when runtime evidence is available.",
            "Range/ATR adaptive shapes must use explicit avg_range and range_atr_ratio inputs; missing formula inputs fall back to baseline_step with manual-review warnings.",
            "Legacy warp-step comparisons are lineage context only; when present, the true plan comparator is the unified design target plus current branch doctrine.",
            "Only runtime overlays with real runner flags become launch args here; the rest stay visible as doctrine obligations until the runner surface grows them.",
        ],
    }


def write_markdown(plan: dict[str, Any], output_path: Path) -> None:
    recommendation = dict(plan.get("controller_recommendation") or {})
    runtime_overlay_contract = dict(plan.get("runtime_overlay_contract") or {})
    step_plan = dict(plan.get("adaptive_step_plan") or {})
    symbol = str(plan.get("symbol") or "UNKNOWN")
    lines = [
        f"# Adaptive {symbol} Regime Switch Shadow Plan",
        "",
        "This is a generated scaffold for a regime-switched adaptive shadow launch. It does not launch or mutate runtime state.",
        "",
        "## Current Read",
        "",
        f"- status: `{plan['status']}`",
        f"- baseline lane: `{plan['baseline']['lane_name']}`",
        f"- proposed lane: `{plan.get('proposed_lane_name') or '-'}`",
        f"- controller shape: `{recommendation.get('recommended_shape_id') or '-'}`",
        f"- live regime: `{plan['live_regime']['regime']}` -> normalized `{recommendation.get('regime') or '-'}`",
        f"- monetization objective: {dict(plan.get('runtime_objective_context') or {}).get('objective_read', '-')}",
        f"- baseline step: `{plan['baseline']['step']}`",
        f"- adaptive step: `{step_plan.get('step', '-')}` from `{step_plan.get('step_source', '-')}`",
        f"- runtime overlay contract: {runtime_overlay_contract.get('read', '-')}",
        "",
        "## Step Review",
        "",
        f"- review: {dict(plan.get('step_review') or {}).get('review_read')}",
    ]
    for item in list(dict(plan.get("step_review") or {}).get("notes") or []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Runtime Overlay Contract",
            "",
            f"- supported_overlays: `{runtime_overlay_contract.get('supported_overlays', [])}`",
            f"- requested_overlays: `{runtime_overlay_contract.get('requested_overlays', [])}`",
            f"- executable_overlays: `{runtime_overlay_contract.get('executable_overlays', [])}`",
            f"- unsupported_overlays: `{runtime_overlay_contract.get('unsupported_overlays', [])}`",
            f"- command_flags: `{runtime_overlay_contract.get('command_flags', [])}`",
            "",
            "## Warnings",
            "",
        ]
    )
    if plan["warnings"]:
        for item in plan["warnings"]:
            lines.append(f"- {item}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Proposed Command",
            "",
        ]
    )
    if plan["proposed_command"]:
        lines.extend(
            [
                "```bash",
                " ".join(plan["proposed_command"]),
                "```",
            ]
        )
    else:
        lines.append("_No command proposed for the current market state._")

    lines.extend(
        [
            "",
            "## Notes",
            "",
        ]
    )
    for item in plan["notes"]:
        lines.append(f"- {item}")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a regime-switched adaptive shadow-runner scaffold from current research surfaces.")
    parser.add_argument("--lane-name", default="shadow_btcusd_m15_warp")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--registry-path", default=str(DEFAULT_REGISTRY_PATH))
    parser.add_argument("--shape-library-path", default=str(DEFAULT_SHAPE_LIBRARY_PATH))
    parser.add_argument("--regime-path", default=str(DEFAULT_REGIME_PATH))
    parser.add_argument("--runtime-audit-path", default=str(DEFAULT_RUNTIME_AUDIT_PATH))
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbol = str(args.symbol)
    plan = build_plan(
        lane_name=str(args.lane_name),
        symbol=symbol,
        registry_path=Path(args.registry_path),
        shape_library_path=Path(args.shape_library_path),
        regime_path=Path(args.regime_path),
        runtime_audit_path=Path(args.runtime_audit_path),
    )
    default_json, default_md = default_plan_output_paths(symbol)
    output_json = Path(args.output_json) if str(args.output_json).strip() else default_json
    output_md = Path(args.output_md) if str(args.output_md).strip() else default_md
    output_json.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    write_markdown(plan, output_md)
    print(f"Wrote {output_json}")
    print(f"Wrote {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
