#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Ensure scripts/ directory is on PYTHONPATH so bare imports work
# regardless of cwd (repo root vs scripts/ directory)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import MetaTrader5 as mt5

import live_penetration_lattice_mirror as live_mirror
import mt5_terminal_guard
from live_penetration_lattice_shadow import (
    append_jsonl,
    default_apex_mix,
    log_runner_exception,
    run_direct_live_exec,
    save_state,
    utc_now_iso,
)
from live_penetration_lattice_tick_crypto_shadow import (
    build_direct_live_action_sink,
    sync_engine_to_broker,
)
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import pip_size_for
from penetration_lattice_lab_v3_bounded import Config as BoundedConfig
from tick_penetration_lattice_core import (
    TickBoundedRearmEngine,
    TickStatefulRearmEngine,
    bounded_engine_from_args,
    engine_from_args,
    load_latest_tick,
    load_recent_bars,
    load_ticks_since_with_source,
    normalize_raw_close_style,
    purge_stale_rearm_tickets,
    timeframe_seconds,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "penetration_lattice_tick_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "penetration_lattice_tick_shadow_events.jsonl"
DEFAULT_DIRECT_EXEC_STATE_PATH = ROOT / "reports" / "penetration_lattice_live_mirror_state.json"
DEFAULT_DIRECT_EXEC_LOG_PATH = ROOT / "reports" / "penetration_lattice_live_mirror_events.jsonl"


def _record_runner_symbol_source(
    runner_status: dict[str, Any] | None,
    *,
    status_field: str,
    symbol: str,
    source: str,
) -> None:
    if runner_status is None or not str(source or "").strip():
        return
    bucket = runner_status.setdefault(f"{status_field}_by_symbol", {})
    symbol_bucket = bucket.setdefault(str(symbol), {"last": "", "counts": {}})
    symbol_bucket["last"] = str(source)
    counts = symbol_bucket.setdefault("counts", {})
    counts[str(source)] = int(counts.get(str(source), 0) or 0) + 1


def _refresh_positive_only_runner_status(
    runner_status: dict[str, Any] | None,
    engines: dict[str, TickStatefulRearmEngine | TickBoundedRearmEngine],
) -> None:
    if runner_status is None:
        return
    held_symbols: list[str] = []
    reasons: dict[str, str] = {}
    since_by_symbol: dict[str, int] = {}
    for symbol, engine in engines.items():
        state = getattr(engine, "state", None)
        if not bool(getattr(state, "positive_only_hold_active", False)):
            continue
        held_symbols.append(str(symbol))
        reason = str(getattr(state, "positive_only_hold_reason", "") or "")
        if reason:
            reasons[str(symbol)] = reason
        hold_since = int(getattr(state, "positive_only_hold_since", 0) or 0)
        if hold_since > 0:
            since_by_symbol[str(symbol)] = hold_since
    held_symbols.sort()
    runner_status["positive_only_hold_active"] = bool(held_symbols)
    runner_status["positive_only_hold_symbols"] = held_symbols
    runner_status["positive_only_hold_reason_by_symbol"] = reasons
    runner_status["positive_only_hold_since_by_symbol"] = since_by_symbol
    if held_symbols:
        runner_status["status"] = "positive_only_hold_active"
        if len(held_symbols) == 1:
            runner_status["positive_only_hold_reason"] = reasons.get(held_symbols[0], "")
        else:
            runner_status["positive_only_hold_reason"] = "; ".join(
                f"{symbol}:{reasons[symbol]}" for symbol in held_symbols if reasons.get(symbol)
            )
    else:
        runner_status["positive_only_hold_reason"] = ""
        if str(runner_status.get("status") or "") == "positive_only_hold_active":
            runner_status["status"] = ""


def is_good_session() -> bool:
    """Check if current UTC hour is within good trading session (07:00-21:00 UTC)."""
    utc_hour = datetime.now(timezone.utc).hour
    return 7 <= utc_hour < 21


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    raise RuntimeError(f"Unsupported boolean value: {value}")


def _normalize_raw_symbol_override(symbol: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Raw symbol override for {symbol} must be an object")
    override: dict[str, Any] = {}
    alpha_value = payload.get("raw_close_alpha", payload.get("close_alpha"))
    if alpha_value is not None:
        override["raw_close_alpha"] = max(0.0, min(1.0, float(alpha_value)))
    close_style_value = payload.get("raw_close_style", payload.get("close_style"))
    if close_style_value is not None:
        override["raw_close_style"] = normalize_raw_close_style(str(close_style_value))
    variant_value = payload.get("raw_rearm_variant", payload.get("rearm_variant"))
    if variant_value is not None:
        override["raw_rearm_variant"] = str(variant_value)
    cooldown_value = payload.get("raw_rearm_cooldown_bars", payload.get("rearm_cooldown_bars"))
    if cooldown_value is not None:
        override["raw_rearm_cooldown_bars"] = max(0, int(cooldown_value))
    momentum_value = payload.get("raw_rearm_momentum_gate", payload.get("rearm_momentum_gate"))
    if momentum_value is not None:
        override["raw_rearm_momentum_gate"] = _coerce_bool(momentum_value)
    sell_gap_value = payload.get("raw_sell_gap", payload.get("sell_gap"))
    if sell_gap_value is not None:
        override["raw_sell_gap"] = max(0, int(sell_gap_value))
    buy_gap_value = payload.get("raw_buy_gap", payload.get("buy_gap"))
    if buy_gap_value is not None:
        override["raw_buy_gap"] = max(0, int(buy_gap_value))
    step_buy_value = payload.get("step_buy", payload.get("raw_step_buy"))
    if step_buy_value is not None:
        override["step_buy"] = max(0.0, float(step_buy_value))
    step_sell_value = payload.get("step_sell", payload.get("raw_step_sell"))
    if step_sell_value is not None:
        override["step_sell"] = max(0.0, float(step_sell_value))
    max_entry_spread_ratio_value = payload.get("max_entry_spread_ratio", payload.get("raw_max_entry_spread_ratio"))
    if max_entry_spread_ratio_value is not None:
        override["max_entry_spread_ratio"] = max(0.0, float(max_entry_spread_ratio_value))
    min_positive_close_profit_usd_value = payload.get(
        "min_positive_close_profit_usd",
        payload.get("raw_min_positive_close_profit_usd"),
    )
    if min_positive_close_profit_usd_value is not None:
        override["min_positive_close_profit_usd"] = max(0.0, float(min_positive_close_profit_usd_value))
    offensive_closure_value = payload.get(
        "offensive_closure_enabled",
        payload.get("offensive_closure"),
    )
    if offensive_closure_value is not None:
        override["offensive_closure_enabled"] = _coerce_bool(offensive_closure_value)
    return override


def load_raw_symbol_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Raw symbol overrides file must contain an object keyed by symbol")
    overrides: dict[str, dict[str, Any]] = {}
    for symbol, raw_override in payload.items():
        normalized = _normalize_raw_symbol_override(str(symbol or "").upper(), raw_override)
        if normalized:
            overrides[str(symbol or "").upper()] = normalized
    return overrides


def default_raw_gap_for_cfg(cfg: RawConfig, *, side: str) -> int:
    side_key = str(side or "").strip().lower()
    explicit_gap = getattr(cfg, "sell_gap" if side_key == "sell" else "buy_gap", None)
    if explicit_gap is not None:
        try:
            return max(0, int(explicit_gap))
        except Exception:
            pass
    close_mode = str(getattr(cfg, "close_mode", "") or "").strip().lower()
    return 1 if close_mode == "one_level" else 2


def load_compatible_state(path: Path, engines: dict[str, TickStatefulRearmEngine | TickBoundedRearmEngine]) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    for symbol, snap in (payload.get("symbols") or {}).items():
        engine = engines.get(str(symbol or "").upper())
        if engine is not None:
            engine.load_snapshot(snap or {})


def hydrate_tick_histories(engines: dict[str, TickStatefulRearmEngine | TickBoundedRearmEngine]) -> None:
    for symbol, engine in engines.items():
        if not hasattr(engine, "hydrate_history"):
            continue
        lookback = 600
        cfg = getattr(engine, "cfg", None)
        lookback = max(
            lookback,
            int(getattr(cfg, "regime_lookback_bars", 0) or 0) + 10,
            int(getattr(cfg, "vwap_lookback", 0) or 0) + 10,
        )
        bars = load_recent_bars(symbol, engine.timeframe_name, lookback)
        if bars:
            engine.hydrate_history(bars)


def prime_engines_fresh(engines: dict[str, TickStatefulRearmEngine | TickBoundedRearmEngine]) -> None:
    for symbol, engine in engines.items():
        bars = load_recent_bars(symbol, engine.timeframe_name, 600)
        if not bars:
            continue
        if hasattr(engine, "hydrate_history"):
            engine.hydrate_history(bars)
        engine.state.last_bar_time = int(bars[-1]["time"])
        engine.state.open_tickets = []
        engine.state.rearm_tokens = []
        engine.state.rearm_opens = 0
        engine.state.realized_closes = 0
        engine.state.realized_net_usd = 0.0
        engine.state.anchor_resets = 0
        engine.state.max_open_total = 0
        engine.state.last_tick_time = 0
        engine.state.last_tick_msc = 0
        if hasattr(engine, "prime"):
            engine.prime(float(bars[-1]["close"]), int(bars[-1]["time"]))


def bootstrap(
    engines: dict[str, TickStatefulRearmEngine | TickBoundedRearmEngine],
    state_path: Path,
    event_path: Path,
    fresh_start: bool,
    metadata: dict[str, Any],
) -> None:
    if state_path.exists():
        load_compatible_state(state_path, engines)
        hydrate_tick_histories(engines)
        removed_by_symbol: dict[str, list[dict[str, Any]]] = {}
        if bool(metadata.get("direct_live")):
            for symbol, engine in engines.items():
                removed = purge_stale_rearm_tickets(engine)
                if removed:
                    removed_by_symbol[str(symbol or "").upper()] = removed
        if removed_by_symbol:
            save_state(state_path, engines, metadata=metadata)
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "purged_stale_rearm_tickets",
                    "symbols": sorted(removed_by_symbol.keys()),
                    "removed": removed_by_symbol,
                    **metadata,
                },
            )
        return
    prime_engines_fresh(engines)
    save_state(state_path, engines, metadata=metadata)
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "fresh_start_prime" if fresh_start else "bootstrap_complete",
            "symbols": sorted(engines.keys()),
            **metadata,
        },
    )


def build_engines(
    *,
    symbols: set[str] | None,
    raw_close_alpha: float,
    raw_close_style: str,
    raw_rearm_variant: str | None,
    raw_rearm_cooldown_bars: int,
    raw_rearm_momentum_gate: bool,
    raw_sell_gap: int | None,
    raw_buy_gap: int | None,
    raw_step_buy: float | None,
    raw_step_sell: float | None,
    raw_max_floating_loss_usd: float,
    raw_max_lattice_window_bars: int,
    raw_breakout_buffer_pips: float,
    raw_cluster_aware_escape: bool,
    raw_cluster_fill_tolerance: float | None,
    raw_guard_open_admission: bool,
    raw_suppress_additional_levels_after_burst: bool,
    raw_burst_open_threshold: int,
    raw_max_entry_spread_ratio: float,
    raw_liquidity_gap_spread_multiplier: float,
    raw_liquidity_gap_spread_lookback: int,
    raw_liquidity_gap_spread_floor_ratio: float,
    raw_adaptive_overlay_autopilot: bool,
    raw_proven_step_ceiling: float,
    raw_proven_step_buy_ceiling: float,
    raw_proven_step_sell_ceiling: float,
    min_positive_close_profit_usd: float,
    positive_only_closes: bool,
    raw_symbol_overrides: dict[str, dict[str, Any]] | None,
    bounded_rearm_variant: str | None,
    bounded_close_gap: int,
    bounded_same_bar_min_pnl: float,
    bounded_same_bar_shallow_level_cap: int,
    bounded_timeframe: str,
    bounded_step_pips: float | None,
    bounded_max_open_per_side: int | None,
    bounded_max_floating_loss_usd: float | None,
    bounded_vwap_lookback: int | None,
    bounded_regime_lookback_bars: int | None,
    bounded_max_range_pips: float | None,
    bounded_breakout_buffer_pips: float | None,
    bounded_max_lattice_window_bars: int | None,
    bounded_cooldown_bars: int | None,
) -> dict[str, TickStatefulRearmEngine | TickBoundedRearmEngine]:
    mix = default_apex_mix()
    selected = symbols or set(mix.keys())
    raw_symbol_overrides = raw_symbol_overrides or {}
    for symbol in raw_symbol_overrides:
        if symbol not in selected:
            raise RuntimeError(f"Raw symbol override supplied for unselected symbol: {symbol}")
        if symbol not in mix:
            raise RuntimeError(f"Raw symbol override supplied for unsupported symbol: {symbol}")
        mode, _cfg = mix[symbol]
        if mode != "raw_close2":
            raise RuntimeError(f"Raw symbol override supplied for non-raw symbol: {symbol}")
    engines: dict[str, TickStatefulRearmEngine | TickBoundedRearmEngine] = {}
    for symbol in sorted(selected):
        if symbol not in mix:
            raise RuntimeError(f"Unsupported symbol for tick runner: {symbol}")
        mode, cfg = mix[symbol]
        if mode == "raw_close2":
            if not isinstance(cfg, RawConfig):
                raise RuntimeError(f"Expected RawConfig for {symbol}")
            info = mt5.symbol_info(symbol)
            if info is None:
                raise RuntimeError(f"Missing symbol info for {symbol}")
            pip_size = float(pip_size_for(info) or 0.0)
            if pip_size <= 0.0:
                raise RuntimeError(f"Invalid pip size for {symbol}")
            if bool(getattr(cfg, "step_is_price_units", False)):
                step_px = float(cfg.step_pips)
            else:
                step_px = float(cfg.step_pips) * pip_size
            symbol_override = raw_symbol_overrides.get(symbol, {})
            cluster_fill_tolerance = symbol_override.get("cluster_fill_tolerance", raw_cluster_fill_tolerance)
            if cluster_fill_tolerance is None:
                cluster_fill_tolerance = pip_size
            default_sell_gap = default_raw_gap_for_cfg(cfg, side="sell")
            default_buy_gap = default_raw_gap_for_cfg(cfg, side="buy")
            offensive_closure_enabled = bool(symbol_override.get("offensive_closure_enabled", False))
            engines[symbol] = engine_from_args(
                symbol=symbol,
                timeframe_name="M1",
                step=step_px,
                max_open_per_side=int(cfg.max_open_per_side),
                variant_name=str(symbol_override.get("raw_rearm_variant", raw_rearm_variant or "rearm_lvl2_exc2")),
                close_alpha=max(0.0, min(1.0, float(symbol_override.get("raw_close_alpha", raw_close_alpha)))),
                close_style=str(symbol_override.get("raw_close_style", raw_close_style)),
                momentum_gate=bool(symbol_override.get("raw_rearm_momentum_gate", raw_rearm_momentum_gate)),
                cooldown_bars=max(0, int(symbol_override.get("raw_rearm_cooldown_bars", raw_rearm_cooldown_bars))),
                sell_gap=max(
                    0,
                    int(symbol_override.get("raw_sell_gap", default_sell_gap if raw_sell_gap is None else raw_sell_gap)),
                ),
                buy_gap=max(
                    0,
                    int(symbol_override.get("raw_buy_gap", default_buy_gap if raw_buy_gap is None else raw_buy_gap)),
                ),
                step_buy=None
                if symbol_override.get("step_buy", raw_step_buy) is None
                else max(0.0, float(symbol_override.get("step_buy", raw_step_buy))),
                step_sell=None
                if symbol_override.get("step_sell", raw_step_sell) is None
                else max(0.0, float(symbol_override.get("step_sell", raw_step_sell))),
                max_floating_loss_usd=float(symbol_override.get("max_floating_loss_usd", raw_max_floating_loss_usd)),
                max_lattice_window_bars=int(symbol_override.get("max_lattice_window_bars", raw_max_lattice_window_bars)),
                breakout_buffer_pips=float(symbol_override.get("breakout_buffer_pips", raw_breakout_buffer_pips)),
                cluster_aware_escape=bool(symbol_override.get("cluster_aware_escape", raw_cluster_aware_escape)),
                cluster_fill_tolerance=float(cluster_fill_tolerance),
                guard_open_admission=bool(symbol_override.get("guard_open_admission", raw_guard_open_admission)),
                suppress_additional_levels_after_burst=bool(
                    symbol_override.get(
                        "suppress_additional_levels_after_burst",
                        raw_suppress_additional_levels_after_burst,
                    )
                ),
                burst_open_threshold=max(
                    1,
                    int(symbol_override.get("burst_open_threshold", raw_burst_open_threshold)),
                ),
                max_entry_spread_ratio=max(
                    0.0,
                    float(symbol_override.get("max_entry_spread_ratio", raw_max_entry_spread_ratio)),
                ),
                liquidity_gap_spread_multiplier=max(
                    0.0,
                    float(
                        symbol_override.get(
                            "liquidity_gap_spread_multiplier",
                            raw_liquidity_gap_spread_multiplier,
                        )
                    ),
                ),
                liquidity_gap_spread_lookback=max(
                    0,
                    int(
                        symbol_override.get(
                            "liquidity_gap_spread_lookback",
                            raw_liquidity_gap_spread_lookback,
                        )
                    ),
                ),
                liquidity_gap_spread_floor_ratio=max(
                    0.0,
                    float(
                        symbol_override.get(
                            "liquidity_gap_spread_floor_ratio",
                            raw_liquidity_gap_spread_floor_ratio,
                        )
                    ),
                ),
                adaptive_overlay_autopilot=bool(
                    symbol_override.get("adaptive_overlay_autopilot", raw_adaptive_overlay_autopilot)
                ),
                offensive_closure_enabled=offensive_closure_enabled,
                offensive_safety_margin_usd=float(symbol_override.get("offensive_safety_margin_usd", 0.0))
                if offensive_closure_enabled
                else 0.0,
                offensive_safety_margin_pct=float(symbol_override.get("offensive_safety_margin_pct", 0.0))
                if offensive_closure_enabled
                else 0.0,
                offensive_cut_cooldown_bars=max(
                    0,
                    int(symbol_override.get("offensive_cut_cooldown_bars", 0)),
                )
                if offensive_closure_enabled
                else 0,
                offensive_breakeven_band_usd=float(symbol_override.get("offensive_breakeven_band_usd", 0.0))
                if offensive_closure_enabled
                else 0.0,
                offensive_budget_share=float(symbol_override.get("offensive_budget_share", 0.0))
                if offensive_closure_enabled
                else 0.0,
                proven_step_ceiling=max(
                    0.0,
                    float(symbol_override.get("proven_step_ceiling", raw_proven_step_ceiling)),
                ),
                proven_step_buy_ceiling=max(
                    0.0,
                    float(symbol_override.get("proven_step_buy_ceiling", raw_proven_step_buy_ceiling)),
                ),
                proven_step_sell_ceiling=max(
                    0.0,
                    float(symbol_override.get("proven_step_sell_ceiling", raw_proven_step_sell_ceiling)),
                ),
                min_positive_close_profit_usd=max(
                    0.0,
                    float(symbol_override.get("min_positive_close_profit_usd", min_positive_close_profit_usd)),
                ),
                positive_only_closes=bool(symbol_override.get("positive_only_closes", positive_only_closes)),
            )
            continue
        if not isinstance(cfg, BoundedConfig):
            raise RuntimeError(f"Expected BoundedConfig for {symbol}")
        bounded_cfg = cfg
        bounded_overrides: dict[str, Any] = {}
        if bounded_step_pips is not None:
            bounded_overrides["step_pips"] = float(bounded_step_pips)
        if bounded_max_open_per_side is not None:
            bounded_overrides["max_open_per_side"] = max(1, int(bounded_max_open_per_side))
        if bounded_max_floating_loss_usd is not None:
            bounded_overrides["max_floating_loss_usd"] = float(bounded_max_floating_loss_usd)
        if bounded_vwap_lookback is not None:
            bounded_overrides["vwap_lookback"] = max(1, int(bounded_vwap_lookback))
        if bounded_regime_lookback_bars is not None:
            bounded_overrides["regime_lookback_bars"] = max(1, int(bounded_regime_lookback_bars))
        if bounded_max_range_pips is not None:
            bounded_overrides["max_range_pips"] = max(0.0, float(bounded_max_range_pips))
        if bounded_breakout_buffer_pips is not None:
            bounded_overrides["breakout_buffer_pips"] = max(0.0, float(bounded_breakout_buffer_pips))
        if bounded_max_lattice_window_bars is not None:
            bounded_overrides["max_lattice_window_bars"] = max(1, int(bounded_max_lattice_window_bars))
        if bounded_cooldown_bars is not None:
            bounded_overrides["cooldown_bars"] = max(0, int(bounded_cooldown_bars))
        if bounded_overrides:
            bounded_cfg = replace(cfg, **bounded_overrides)
        bounded_info = mt5.symbol_info(symbol)
        bounded_cluster_fill_tolerance = raw_cluster_fill_tolerance
        if bounded_cluster_fill_tolerance is None and bounded_info is not None:
            bounded_cluster_fill_tolerance = float(pip_size_for(bounded_info) or 0.0) or None
        engines[symbol] = bounded_engine_from_args(
            symbol=symbol,
            timeframe_name=str(bounded_timeframe).upper(),
            cfg=bounded_cfg,
            variant_name=str(bounded_rearm_variant or "rearm_lvl2_exc2"),
            close_gap=max(1, int(bounded_close_gap)),
            same_bar_min_pnl=max(0.0, float(bounded_same_bar_min_pnl)),
            same_bar_shallow_level_cap=max(0, int(bounded_same_bar_shallow_level_cap)),
            cluster_aware_escape=bool(raw_cluster_aware_escape),
            cluster_fill_tolerance=float(bounded_cluster_fill_tolerance or 0.01),
            guard_open_admission=bool(raw_guard_open_admission),
            suppress_additional_levels_after_burst=bool(raw_suppress_additional_levels_after_burst),
            burst_open_threshold=max(1, int(raw_burst_open_threshold)),
            max_entry_spread_ratio=max(0.0, float(raw_max_entry_spread_ratio)),
            adaptive_overlay_autopilot=bool(raw_adaptive_overlay_autopilot),
            min_positive_close_profit_usd=max(0.0, float(min_positive_close_profit_usd)),
            positive_only_closes=bool(positive_only_closes),
        )
    return engines


def run_once(
    engines: dict[str, TickStatefulRearmEngine | TickBoundedRearmEngine],
    *,
    state_path: Path,
    event_path: Path,
    metadata: dict[str, Any],
    direct_exec: dict[str, Any] | None,
    runner_status: dict[str, Any] | None,
    session_gate: bool = False,
    shared_price_max_age_ms: int = 0,
    shared_max_floating_loss_usd: float | None = None,
    escape_hatch_config: dict[str, Any] | None = None,
) -> None:
    # Session gate: skip tick processing during off-session hours
    if session_gate and not is_good_session():
        utc_hour = datetime.now(timezone.utc).hour
        if direct_exec:
            for engine in engines.values():
                sync_engine_to_broker(
                    engine,
                    exec_state=direct_exec["state"],
                    exec_log_path=direct_exec["log_path"],
                    event_path=event_path,
                    live_magic=direct_exec["live_magic"],
                    attached_live_magics=direct_exec.get("attached_live_magics"),
                )
            live_mirror.save_state(direct_exec["state_path"], direct_exec["state"])
        if runner_status is not None:
            runner_status["heartbeat_at"] = utc_now_iso()
            runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
            runner_status["consecutive_exceptions"] = 0
            runner_status["session_gated"] = True
            runner_status["gated_hour"] = utc_hour
        _refresh_positive_only_runner_status(runner_status, engines)
        save_state(state_path, engines, metadata=metadata, runner=runner_status)
        return
    action_sink = None
    if direct_exec:
        for engine in engines.values():
            sync_engine_to_broker(
                engine,
                exec_state=direct_exec["state"],
                exec_log_path=direct_exec["log_path"],
                event_path=event_path,
                live_magic=direct_exec["live_magic"],
                attached_live_magics=direct_exec.get("attached_live_magics"),
            )
        action_sink = build_direct_live_action_sink(
            exec_state=direct_exec["state"],
            exec_log_path=direct_exec["log_path"],
            live_magic=direct_exec["live_magic"],
            live_comment_prefix=direct_exec["live_comment_prefix"],
            live_volume=direct_exec["live_volume"],
        )

    # Shared floating loss budget: check BEFORE processing ticks
    if shared_max_floating_loss_usd is not None and runner_status is not None:
        net_floating_usd = 0.0
        all_positions = mt5.positions_get()
        if all_positions is not None:
            for pos in all_positions:
                net_floating_usd += float(pos.profit or 0.0) + float(pos.swap or 0.0)
        runner_status["net_floating_loss_usd"] = round(net_floating_usd, 2)
        runner_status["shared_max_floating_loss_usd"] = shared_max_floating_loss_usd
        runner_status["shared_budget_ok"] = net_floating_usd >= shared_max_floating_loss_usd
        if net_floating_usd < shared_max_floating_loss_usd:
            # Budget breached — skip tick processing, don't open new positions
            runner_status["shared_budget_breached"] = True
            runner_status["heartbeat_at"] = utc_now_iso()
            runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
            runner_status["consecutive_exceptions"] = 0
            _refresh_positive_only_runner_status(runner_status, engines)
            save_state(state_path, engines, metadata=metadata, runner=runner_status)
            if direct_exec:
                live_mirror.save_state(direct_exec["state_path"], direct_exec["state"])
            return
        else:
            runner_status["shared_budget_breached"] = False

    # Escape hatch: surgical exit from stale/negative positions
    if escape_hatch_config is not None:
        from escape_hatch import execute_escape_hatch as _run_escape
        dry_run = not escape_hatch_config.get("enabled", False)
        escape_result = _run_escape(
            engines=engines,
            action_sink=action_sink if 'action_sink' in dir() else None,
            event_path=event_path,
            escape_config=escape_hatch_config,
            dry_run=dry_run,
        )
        if runner_status is not None:
            runner_status["escape_hatch"] = escape_result

    for symbol, engine in engines.items():
        lookback_seconds = max(120, timeframe_seconds(engine.timeframe_name) * 3)
        ticks, ticks_source = load_ticks_since_with_source(
            symbol,
            int(engine.state.last_tick_msc or 0),
            lookback_seconds=lookback_seconds,
            shared_price_max_age_ms=shared_price_max_age_ms,
        )
        live_tick, live_tick_source = load_latest_tick(symbol, shared_price_max_age_ms=shared_price_max_age_ms)
        if live_tick is not None:
            live_tick_msc = int(live_tick["time_msc"])
            latest_loaded_msc = int(ticks[-1]["time_msc"]) if ticks else int(engine.state.last_tick_msc or 0)
            if live_tick_msc > latest_loaded_msc:
                ticks.append(live_tick)
                append_jsonl(
                    event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "tick_history_fallback",
                    "symbol": symbol,
                    "reason": f"{live_tick_source}_newer_than_loaded_history",
                    "live_tick_source": live_tick_source,
                    "last_tick_msc_before": int(engine.state.last_tick_msc or 0),
                    "latest_loaded_msc": latest_loaded_msc,
                    "live_tick_msc": live_tick_msc,
                    "bid": float(live_tick.get("bid", 0.0) or 0.0),
                    "ask": float(live_tick.get("ask", 0.0) or 0.0),
                    "last": float(live_tick.get("last", 0.0) or 0.0),
                },
            )
                _record_runner_symbol_source(
                    runner_status,
                    status_field="latest_tick_append_source",
                    symbol=symbol,
                    source=live_tick_source,
                )
        _record_runner_symbol_source(
            runner_status,
            status_field="tick_history_source",
            symbol=symbol,
            source=ticks_source,
        )
        _record_runner_symbol_source(
            runner_status,
            status_field="latest_tick_source",
            symbol=symbol,
            source=live_tick_source,
        )
        if ticks:
            engine.process_ticks(ticks, action_sink=action_sink, event_path=event_path, emit=True)
    if runner_status is not None:
        # Report per-symbol floating loss breakdown (net already computed above for enforcement)
        if shared_max_floating_loss_usd is not None:
            per_symbol_floating: dict[str, float] = {}
            all_positions = mt5.positions_get()
            if all_positions is not None:
                for pos in all_positions:
                    sym = pos.symbol
                    per_symbol_floating[sym] = per_symbol_floating.get(sym, 0.0) + float(pos.profit or 0.0) + float(pos.swap or 0.0)
            runner_status["per_symbol_floating_loss_usd"] = {k: round(v, 2) for k, v in per_symbol_floating.items()}
        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
    _refresh_positive_only_runner_status(runner_status, engines)
    save_state(state_path, engines, metadata=metadata, runner=runner_status)
    if direct_exec:
        run_direct_live_exec(
            direct_exec["state"],
            source_state_path=state_path,
            source_event_path=event_path,
            exec_state_path=direct_exec["state_path"],
            exec_log_path=direct_exec["log_path"],
            allowed_symbols=direct_exec["allowed_symbols"],
            live_magic=direct_exec["live_magic"],
            attached_live_magics=direct_exec.get("attached_live_magics"),
            live_comment_prefix=direct_exec["live_comment_prefix"],
            live_volume=direct_exec["live_volume"],
        )
        for engine in engines.values():
                sync_engine_to_broker(
                    engine,
                    exec_state=direct_exec["state"],
                    exec_log_path=direct_exec["log_path"],
                    event_path=event_path,
                    live_magic=direct_exec["live_magic"],
                    attached_live_magics=direct_exec.get("attached_live_magics"),
                )
        save_state(state_path, engines, metadata=metadata, runner=runner_status)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tick-native live/shadow runner for the current FX penetration-lattice mix.")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--raw-close-alpha", type=float, default=0.0)
    parser.add_argument("--raw-close-style", default="all_profitable")
    parser.add_argument("--raw-rearm-variant", default=None)
    parser.add_argument("--raw-rearm-cooldown-bars", type=int, default=0)
    parser.add_argument("--raw-rearm-momentum-gate", action="store_true")
    parser.add_argument("--raw-sell-gap", type=int, default=None)
    parser.add_argument("--raw-buy-gap", type=int, default=None)
    parser.add_argument("--step-buy", type=float, default=None)
    parser.add_argument("--step-sell", type=float, default=None)
    parser.add_argument("--raw-symbol-overrides-path", default=None)
    parser.add_argument("--bounded-rearm-variant", default=None)
    parser.add_argument("--bounded-close-gap", type=int, default=1)
    parser.add_argument("--bounded-same-bar-min-pnl", type=float, default=0.0)
    parser.add_argument("--bounded-same-bar-shallow-level-cap", type=int, default=0)
    parser.add_argument("--bounded-timeframe", default="M1")
    parser.add_argument("--bounded-step-pips", type=float, default=None)
    parser.add_argument("--bounded-max-open-per-side", type=int, default=None)
    parser.add_argument("--bounded-max-floating-loss-usd", type=float, default=None)
    parser.add_argument("--bounded-vwap-lookback", type=int, default=None)
    parser.add_argument("--bounded-regime-lookback-bars", type=int, default=None)
    parser.add_argument("--bounded-max-range-pips", type=float, default=None)
    parser.add_argument("--bounded-breakout-buffer-pips", type=float, default=None)
    parser.add_argument("--bounded-max-lattice-window-bars", type=int, default=None)
    parser.add_argument("--bounded-cooldown-bars", type=int, default=None)
    parser.add_argument("--direct-live", action="store_true")
    parser.add_argument("--direct-exec-state-path", default=str(DEFAULT_DIRECT_EXEC_STATE_PATH))
    parser.add_argument("--direct-exec-log-path", default=str(DEFAULT_DIRECT_EXEC_LOG_PATH))
    parser.add_argument("--live-magic", type=int, default=live_mirror.DEFAULT_LIVE_MAGIC)
    parser.add_argument("--attach-broker-magic", action="append", type=int, default=[],
                        help="Additional broker magics this live lane should adopt into its managed inventory.")
    parser.add_argument("--live-comment-prefix", default=live_mirror.DEFAULT_LIVE_COMMENT_PREFIX)
    parser.add_argument("--live-volume", type=float, default=live_mirror.DEFAULT_LIVE_VOLUME)
    parser.add_argument("--shared-price-max-age-ms", type=int, default=0)
    parser.add_argument("--max-floating-loss-usd", type=float, default=-15.0)
    parser.add_argument("--max-lattice-window-bars", type=int, default=240)
    parser.add_argument("--breakout-buffer-pips", type=float, default=0.0)
    parser.add_argument("--cluster-aware-escape", action="store_true",
                        help="Enable cluster-aware escape on raw engines: group same-fill positions and apply threshold to cluster total.")
    parser.add_argument("--cluster-fill-tolerance", type=float, default=None,
                        help="Max raw price difference to consider positions in the same cluster. Defaults to one pip for the symbol when omitted.")
    parser.add_argument("--guard-open-admission", action="store_true",
                        help="Guard new same-side raw opens until existing side inventory has shown recovery.")
    parser.add_argument("--suppress-additional-levels-after-burst", action="store_true",
                        help="Stop stacking new raw opens once same-bar/tick burst concentration reaches the configured threshold.")
    parser.add_argument("--burst-open-threshold", type=int, default=2,
                        help="Burst count that triggers suppression of additional raw opens within the same tick/bar (default 2).")
    parser.add_argument("--max-entry-spread-ratio", type=float, default=0.0,
                        help="Skip new opens when spread exceeds this fraction of the base step size. Disabled at 0.")
    parser.add_argument("--liquidity-gap-spread-multiplier", type=float, default=0.0,
                        help="Optional rolling spread blowout gate for raw engines. Blocks new opens only when spread ratio exceeds the recent median by this multiplier. Disabled at 0.")
    parser.add_argument("--liquidity-gap-spread-lookback", type=int, default=0,
                        help="Rolling tick count used for the liquidity-gap spread baseline on raw engines. Disabled below 4.")
    parser.add_argument("--liquidity-gap-spread-floor-ratio", type=float, default=0.0,
                        help="Minimum spread ratio required before the liquidity-gap gate may block a raw open. Useful for ignoring ordinary venue drift.")
    parser.add_argument("--adaptive-overlay-autopilot", action="store_true",
                        help="Auto-arm guarded admission, cluster-aware escape, and burst suppression on raw engines after burst concentration or a toxic first-path close.")
    parser.add_argument("--proven-step-ceiling", type=float, default=0.0,
                        help="Maximum allowed adaptive step for raw engines. Zero means no ceiling.")
    parser.add_argument("--proven-step-buy-ceiling", type=float, default=0.0,
                        help="Maximum allowed BUY adaptive step for raw engines. Zero falls back to the global ceiling.")
    parser.add_argument("--proven-step-sell-ceiling", type=float, default=0.0,
                        help="Maximum allowed SELL adaptive step for raw engines. Zero falls back to the global ceiling.")
    parser.add_argument("--min-positive-close-profit-usd", type=float, default=0.0,
                        help="Minimum projected executable profit required before ordinary close paths may fire. Zero keeps the old cross-zero behavior.")
    parser.add_argument(
        "--positive-only-closes",
        action="store_true",
        help="Block negative emergency exits and stand the lane down instead of realizing the loss.",
    )
    parser.add_argument("--shared-max-floating-loss-usd", type=float, default=None,
                        help="Shared floating loss budget across ALL symbols in this process. "
                             "When set, floating PnL is aggregated across symbols and checked "
                             "against this budget instead of per-symbol limits. "
                             "Enables cross-symbol lattice hedging.")
    parser.add_argument("--session-gate", action="store_true",
                        help="Skip tick processing during off-session hours (21:00-07:00 UTC). "
                             "Good session is 07:00-21:00 UTC (London, overlap, NY).")
    # Escape hatch: surgical exit from stale/negative positions
    parser.add_argument("--escape-hatch", action="store_true",
                        help="Enable escape hatch: close stale unprofitable positions at ~$0 cost, "
                             "and surgically cut worst positions at extremes. Prevents death spiral.")
    parser.add_argument("--escape-max-bars", type=int, default=20,
                        help="Max bars a position can be open without profit before breakeven escape. Default: 20.")
    parser.add_argument("--escape-max-loss", type=float, default=1.0,
                        help="Max acceptable loss for breakeven escape ($). Default: 1.0.")
    parser.add_argument("--escape-cut-count", type=int, default=1,
                        help="Number of worst positions to cut in extreme escape. Default: 1.")
    parser.add_argument("--escape-max-cut-loss", type=float, default=5.0,
                        help="Max loss per position for extreme escape ($). Default: 5.0.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(
        mt5_module=mt5,
        require_trade_allowed=bool(args.direct_live),
    )
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    try:
        state_path = Path(args.state_path)
        event_path = Path(args.event_path)
        raw_symbol_overrides_path = Path(args.raw_symbol_overrides_path) if args.raw_symbol_overrides_path else None
        raw_symbol_overrides = load_raw_symbol_overrides(raw_symbol_overrides_path)
        selected_symbols = {str(s or "").upper() for s in args.symbols} if args.symbols else None
        attached_broker_magics = sorted(
            {
                int(magic)
                for magic in list(args.attach_broker_magic or [])
                if int(magic or 0) > 0 and int(magic or 0) != int(args.live_magic)
            }
        )
        metadata = {
            "symbols": sorted(selected_symbols) if selected_symbols else sorted(default_apex_mix().keys()),
            "tick_native": True,
            "timeframe": "M1",
            "raw_close_alpha": max(0.0, min(1.0, float(args.raw_close_alpha))),
            "raw_close_style": normalize_raw_close_style(args.raw_close_style),
            "raw_rearm_variant": str(args.raw_rearm_variant or ""),
            "raw_rearm_cooldown_bars": max(0, int(args.raw_rearm_cooldown_bars)),
            "raw_rearm_momentum_gate": bool(args.raw_rearm_momentum_gate),
            "step_buy": None if args.step_buy is None else float(args.step_buy),
            "step_sell": None if args.step_sell is None else float(args.step_sell),
            "min_positive_close_profit_usd": max(0.0, float(args.min_positive_close_profit_usd)),
            "positive_only_closes": bool(args.positive_only_closes),
            "raw_symbol_overrides_path": "" if raw_symbol_overrides_path is None else str(raw_symbol_overrides_path),
            "raw_symbol_overrides": raw_symbol_overrides,
            "bounded_rearm_variant": str(args.bounded_rearm_variant or ""),
            "bounded_timeframe": str(args.bounded_timeframe or "M1").upper(),
            "bounded_close_gap": max(1, int(args.bounded_close_gap)),
            "bounded_same_bar_min_pnl": max(0.0, float(args.bounded_same_bar_min_pnl)),
            "bounded_same_bar_shallow_level_cap": max(0, int(args.bounded_same_bar_shallow_level_cap)),
            "bounded_step_pips": None if args.bounded_step_pips is None else float(args.bounded_step_pips),
            "bounded_max_open_per_side": None if args.bounded_max_open_per_side is None else max(1, int(args.bounded_max_open_per_side)),
            "bounded_max_floating_loss_usd": None if args.bounded_max_floating_loss_usd is None else float(args.bounded_max_floating_loss_usd),
            "bounded_vwap_lookback": None if args.bounded_vwap_lookback is None else max(1, int(args.bounded_vwap_lookback)),
            "bounded_regime_lookback_bars": None if args.bounded_regime_lookback_bars is None else max(1, int(args.bounded_regime_lookback_bars)),
            "bounded_max_range_pips": None if args.bounded_max_range_pips is None else max(0.0, float(args.bounded_max_range_pips)),
            "bounded_breakout_buffer_pips": None if args.bounded_breakout_buffer_pips is None else max(0.0, float(args.bounded_breakout_buffer_pips)),
            "bounded_max_lattice_window_bars": None if args.bounded_max_lattice_window_bars is None else max(1, int(args.bounded_max_lattice_window_bars)),
            "bounded_cooldown_bars": None if args.bounded_cooldown_bars is None else max(0, int(args.bounded_cooldown_bars)),
            "live_close_realism_mode": "tick_native",
            "live_open_realism_mode": "tick_native",
            "direct_live": bool(args.direct_live),
            "live_magic": int(args.live_magic),
            "attached_broker_magics": attached_broker_magics,
            "live_comment_prefix": str(args.live_comment_prefix),
            "live_volume": float(args.live_volume),
            "session_gate": bool(args.session_gate),
            "shared_price_max_age_ms": max(0, int(args.shared_price_max_age_ms)),
            "max_floating_loss_usd": float(args.max_floating_loss_usd),
            "shared_max_floating_loss_usd": float(args.shared_max_floating_loss_usd) if args.shared_max_floating_loss_usd is not None else None,
            "max_lattice_window_bars": int(args.max_lattice_window_bars),
            "breakout_buffer_pips": float(args.breakout_buffer_pips),
            "cluster_aware_escape": bool(args.cluster_aware_escape),
            "cluster_fill_tolerance": None if args.cluster_fill_tolerance is None else float(args.cluster_fill_tolerance),
            "guard_open_admission": bool(args.guard_open_admission),
            "suppress_additional_levels_after_burst": bool(args.suppress_additional_levels_after_burst),
            "burst_open_threshold": max(1, int(args.burst_open_threshold)),
            "max_entry_spread_ratio": max(0.0, float(args.max_entry_spread_ratio)),
            "liquidity_gap_spread_multiplier": max(0.0, float(args.liquidity_gap_spread_multiplier)),
            "liquidity_gap_spread_lookback": max(0, int(args.liquidity_gap_spread_lookback)),
            "liquidity_gap_spread_floor_ratio": max(0.0, float(args.liquidity_gap_spread_floor_ratio)),
            "adaptive_overlay_autopilot": bool(args.adaptive_overlay_autopilot),
            "proven_step_ceiling": float(args.proven_step_ceiling) if args.proven_step_ceiling > 0 else None,
            "proven_step_buy_ceiling": float(args.proven_step_buy_ceiling) if args.proven_step_buy_ceiling > 0 else None,
            "proven_step_sell_ceiling": float(args.proven_step_sell_ceiling) if args.proven_step_sell_ceiling > 0 else None,
            "mt5_connection": mt5_connection,
        }
        runner_status = {
            "pid": os.getpid(),
            "script": Path(__file__).name,
            "started_at": utc_now_iso(),
            "poll_seconds": max(1.0, float(args.poll_seconds)),
            "heartbeat_at": None,
            "last_successful_run_at": None,
            "consecutive_exceptions": 0,
            "last_exception_at": None,
            "last_exception_type": "",
            "last_exception_message": "",
            "mt5_identity_ok": bool(mt5_connection.get("identity_ok")),
            "mt5_terminal_path": str(mt5_connection.get("terminal_path") or ""),
            "mt5_login": int(mt5_connection.get("login") or 0),
            "mt5_server": str(mt5_connection.get("server") or ""),
            "positive_only_closes": bool(args.positive_only_closes),
        }
        engines = build_engines(
            symbols=selected_symbols,
            raw_close_alpha=args.raw_close_alpha,
            raw_close_style=args.raw_close_style,
            raw_rearm_variant=args.raw_rearm_variant,
            raw_rearm_cooldown_bars=args.raw_rearm_cooldown_bars,
            raw_rearm_momentum_gate=args.raw_rearm_momentum_gate,
            raw_sell_gap=args.raw_sell_gap,
            raw_buy_gap=args.raw_buy_gap,
            raw_step_buy=None if args.step_buy is None else float(args.step_buy),
            raw_step_sell=None if args.step_sell is None else float(args.step_sell),
            raw_max_floating_loss_usd=float(args.max_floating_loss_usd),
            raw_max_lattice_window_bars=int(args.max_lattice_window_bars),
            raw_breakout_buffer_pips=float(args.breakout_buffer_pips),
            raw_cluster_aware_escape=bool(args.cluster_aware_escape),
            raw_cluster_fill_tolerance=None if args.cluster_fill_tolerance is None else float(args.cluster_fill_tolerance),
            raw_guard_open_admission=bool(args.guard_open_admission),
            raw_suppress_additional_levels_after_burst=bool(args.suppress_additional_levels_after_burst),
            raw_burst_open_threshold=max(1, int(args.burst_open_threshold)),
            raw_max_entry_spread_ratio=max(0.0, float(args.max_entry_spread_ratio)),
            raw_liquidity_gap_spread_multiplier=max(0.0, float(args.liquidity_gap_spread_multiplier)),
            raw_liquidity_gap_spread_lookback=max(0, int(args.liquidity_gap_spread_lookback)),
            raw_liquidity_gap_spread_floor_ratio=max(0.0, float(args.liquidity_gap_spread_floor_ratio)),
            raw_adaptive_overlay_autopilot=bool(args.adaptive_overlay_autopilot),
            raw_proven_step_ceiling=max(0.0, float(args.proven_step_ceiling)),
            raw_proven_step_buy_ceiling=max(0.0, float(args.proven_step_buy_ceiling)),
            raw_proven_step_sell_ceiling=max(0.0, float(args.proven_step_sell_ceiling)),
            min_positive_close_profit_usd=max(0.0, float(args.min_positive_close_profit_usd)),
            positive_only_closes=bool(args.positive_only_closes),
            raw_symbol_overrides=raw_symbol_overrides,
            bounded_rearm_variant=args.bounded_rearm_variant,
            bounded_close_gap=args.bounded_close_gap,
            bounded_same_bar_min_pnl=args.bounded_same_bar_min_pnl,
            bounded_same_bar_shallow_level_cap=args.bounded_same_bar_shallow_level_cap,
            bounded_timeframe=args.bounded_timeframe,
            bounded_step_pips=args.bounded_step_pips,
            bounded_max_open_per_side=args.bounded_max_open_per_side,
            bounded_max_floating_loss_usd=float(args.max_floating_loss_usd),
            bounded_vwap_lookback=args.bounded_vwap_lookback,
            bounded_regime_lookback_bars=args.bounded_regime_lookback_bars,
            bounded_max_range_pips=args.bounded_max_range_pips,
            bounded_breakout_buffer_pips=float(args.breakout_buffer_pips),
            bounded_max_lattice_window_bars=int(args.max_lattice_window_bars),
            bounded_cooldown_bars=args.bounded_cooldown_bars,
        )
        bootstrap(
            engines,
            state_path=state_path,
            event_path=event_path,
            fresh_start=bool(args.fresh_start),
            metadata=metadata,
        )
        direct_exec = None
        if args.direct_live:
            exec_state_path = Path(args.direct_exec_state_path)
            exec_log_path = Path(args.direct_exec_log_path)
            direct_exec = {
                "state": live_mirror.load_state(exec_state_path),
                "state_path": exec_state_path,
                "log_path": exec_log_path,
                "allowed_symbols": set(metadata["symbols"]),
                "live_magic": metadata["live_magic"],
                "attached_live_magics": metadata["attached_broker_magics"],
                "live_comment_prefix": metadata["live_comment_prefix"],
                "live_volume": metadata["live_volume"],
            }
        try:
            run_once(
                engines,
                state_path=state_path,
                event_path=event_path,
                metadata=metadata,
                direct_exec=direct_exec,
                runner_status=runner_status,
                session_gate=bool(args.session_gate),
                shared_price_max_age_ms=max(0, int(args.shared_price_max_age_ms)),
                shared_max_floating_loss_usd=float(args.shared_max_floating_loss_usd) if args.shared_max_floating_loss_usd is not None else None,
            )
        except Exception as exc:
            runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
            runner_status["last_exception_at"] = utc_now_iso()
            runner_status["last_exception_type"] = type(exc).__name__
            runner_status["last_exception_message"] = str(exc)
            log_runner_exception(event_path, exc, phase="initial_run_once")
        if args.once:
            return 0
        while True:
            time.sleep(max(1.0, float(args.poll_seconds)))
            try:
                run_once(
                    engines,
                    state_path=state_path,
                    event_path=event_path,
                    metadata=metadata,
                    direct_exec=direct_exec,
                    runner_status=runner_status,
                    session_gate=bool(args.session_gate),
                    shared_price_max_age_ms=max(0, int(args.shared_price_max_age_ms)),
                    shared_max_floating_loss_usd=float(args.shared_max_floating_loss_usd) if args.shared_max_floating_loss_usd is not None else None,
                )
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
