#!/usr/bin/env python3
"""Tick-native unified shadow runner for the 10-symbol mixed FX/crypto basket."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5

import mt5_terminal_guard
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, save_state, utc_now_iso
from penetration_lattice_lab_v2 import pip_size_for
from tick_penetration_lattice_core import engine_from_args, load_recent_bars, load_ticks_since, timeframe_seconds


ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def state_path_for_symbol(state_dir: Path, symbol: str) -> Path:
    return state_dir / f"unified_shadow_{str(symbol or '').lower()}_state.json"


def event_path_for_symbol(state_dir: Path, symbol: str) -> Path:
    return state_dir / f"unified_shadow_{str(symbol or '').lower()}_events.jsonl"


def load_current_tick(symbol: str) -> dict[str, Any] | None:
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return None
    return {
        "time": int(getattr(tick, "time", 0) or 0),
        "time_msc": int(getattr(tick, "time_msc", 0) or 0),
        "bid": float(getattr(tick, "bid", 0.0) or 0.0),
        "ask": float(getattr(tick, "ask", 0.0) or 0.0),
        "last": float(getattr(tick, "last", 0.0) or 0.0),
        "flags": int(getattr(tick, "flags", 0) or 0),
        "volume": int(getattr(tick, "volume", 0) or 0),
        "volume_real": float(getattr(tick, "volume_real", 0.0) or 0.0),
    }


def resolve_step_is_price_units(symbol: str, cfg: dict[str, Any]) -> bool:
    if "step_is_price_units" in cfg:
        return bool(cfg["step_is_price_units"])
    timeframe = str(cfg.get("timeframe", "") or "").upper()
    # This unified basket is FX M1 + crypto H1. Default unresolved M1 steps to pips.
    return timeframe != "M1"


def step_to_price(symbol: str, cfg: dict[str, Any]) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Missing symbol info for {symbol}")
    step = float(cfg.get("step", 0.0) or 0.0)
    if resolve_step_is_price_units(symbol, cfg):
        return step
    pip_size = float(pip_size_for(info) or 0.0)
    if pip_size <= 0.0:
        raise RuntimeError(f"Invalid pip size for {symbol}")
    return step * pip_size


def build_engine(symbol: str, cfg: dict[str, Any]):
    close_gap = max(1, int(cfg.get("close_gap", 1) or 1))
    return engine_from_args(
        symbol=symbol,
        timeframe_name=str(cfg.get("timeframe", "H1") or "H1").upper(),
        step=step_to_price(symbol, cfg),
        max_open_per_side=max(1, int(cfg.get("max_open_per_side", 30) or 30)),
        variant_name=str(cfg.get("rearm_variant", "rearm_lvl2_exc1") or "rearm_lvl2_exc1"),
        close_alpha=max(0.0, min(1.0, float(cfg.get("close_alpha", 1.0) or 0.0))),
        momentum_gate=bool(cfg.get("momentum_gate", True)),
        cooldown_bars=max(0, int(cfg.get("rearm_cooldown_bars", 0) or 0)),
        sell_gap=close_gap,
        buy_gap=close_gap,
    )


def symbol_metadata(symbol: str, cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "symbols": [symbol],
        "timeframe": str(cfg.get("timeframe", "H1") or "H1").upper(),
        "step": float(cfg.get("step", 0.0) or 0.0),
        "step_is_price_units": resolve_step_is_price_units(symbol, cfg),
        "step_price": step_to_price(symbol, cfg),
        "max_open_per_side": max(1, int(cfg.get("max_open_per_side", 30) or 30)),
        "raw_close_alpha": max(0.0, min(1.0, float(cfg.get("close_alpha", 1.0) or 0.0))),
        "raw_rearm_variant": str(cfg.get("rearm_variant", "rearm_lvl2_exc1") or "rearm_lvl2_exc1"),
        "raw_rearm_cooldown_bars": max(0, int(cfg.get("rearm_cooldown_bars", 0) or 0)),
        "raw_rearm_momentum_gate": bool(cfg.get("momentum_gate", True)),
        "raw_sell_gap": max(1, int(cfg.get("close_gap", 1) or 1)),
        "raw_buy_gap": max(1, int(cfg.get("close_gap", 1) or 1)),
        "tick_native": True,
        "live_open_realism_mode": "tick_native",
        "live_close_realism_mode": "tick_native",
        "unified_shadow": True,
        "direct_live": False,
    }


def prime_engine_fresh(engine) -> None:
    bars = load_recent_bars(engine.symbol, engine.timeframe_name, 240)
    if not bars:
        return
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
    engine.state.lattice_started_time = 0
    engine.prime(float(bars[-1]["close"]), int(bars[-1]["time"]))


def load_compatible_state(path: Path, symbol: str, engine) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    metadata = payload.get("metadata") or {}
    if not bool(metadata.get("tick_native")):
        return False
    snap = (payload.get("symbols") or {}).get(symbol)
    if not isinstance(snap, dict):
        return False
    engine.load_snapshot(snap)
    return True


def bootstrap_symbol(
    engine,
    *,
    symbol: str,
    state_dir: Path,
    fresh_start: bool,
    metadata: dict[str, Any],
    runner_status: dict[str, Any],
) -> None:
    state_path = state_path_for_symbol(state_dir, symbol)
    event_path = event_path_for_symbol(state_dir, symbol)
    if not fresh_start and load_compatible_state(state_path, symbol, engine):
        save_state(state_path, {symbol: engine}, metadata=metadata, runner=runner_status)
        return
    prime_engine_fresh(engine)
    save_state(state_path, {symbol: engine}, metadata=metadata, runner=runner_status)
    append_jsonl(
        event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "fresh_start_prime" if fresh_start else "bootstrap_complete",
            "symbols": [symbol],
            **metadata,
        },
    )


def run_once(
    engines: dict[str, Any],
    symbol_cfgs: dict[str, dict[str, Any]],
    *,
    state_dir: Path,
    runner_status: dict[str, Any],
) -> None:
    now = utc_now_iso()
    runner_status["heartbeat_at"] = now
    runner_status["last_successful_run_at"] = now
    runner_status["consecutive_exceptions"] = 0
    for symbol, engine in engines.items():
        event_path = event_path_for_symbol(state_dir, symbol)
        state_path = state_path_for_symbol(state_dir, symbol)
        ticks = load_ticks_since(
            symbol,
            int(engine.state.last_tick_msc or 0),
            lookback_seconds=max(120, timeframe_seconds(engine.timeframe_name) * 3),
        )
        live_tick = load_current_tick(symbol)
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
                        "reason": "symbol_info_tick_newer_than_loaded_history",
                        "last_tick_msc_before": int(engine.state.last_tick_msc or 0),
                        "latest_loaded_msc": latest_loaded_msc,
                        "live_tick_msc": live_tick_msc,
                        "bid": float(live_tick.get("bid", 0.0) or 0.0),
                        "ask": float(live_tick.get("ask", 0.0) or 0.0),
                        "last": float(live_tick.get("last", 0.0) or 0.0),
                    },
                )
        if ticks:
            engine.process_ticks(ticks, action_sink=None, event_path=event_path, emit=True)
        save_state(state_path, {symbol: engine}, metadata=symbol_metadata(symbol, symbol_cfgs[symbol]), runner=runner_status)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tick-native unified shadow runner for the 10-symbol mixed basket.")
    parser.add_argument("--config", default=str(ROOT / "configs" / "universal_10symbol_rearm.json"))
    parser.add_argument("--state-dir", default=str(ROOT / "reports"))
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mt5_ready, mt5_connection = mt5_terminal_guard.initialize_mt5(mt5_module=mt5)
    if not mt5_ready:
        print(mt5_terminal_guard.failure_summary(mt5_connection))
        return 1

    try:
        config = load_config(Path(args.config))
        symbol_cfgs = {str(symbol or "").upper(): dict(cfg or {}) for symbol, cfg in (config.get("symbols") or {}).items()}
        state_dir = Path(args.state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        runner_status = {
            "pid": os.getpid(),
            "script": Path(__file__).name,
            "config_path": str(Path(args.config)),
            "started_at": utc_now_iso(),
            "poll_seconds": max(1.0, float(args.poll_seconds)),
            "heartbeat_at": None,
            "last_successful_run_at": None,
            "consecutive_exceptions": 0,
            "last_exception_at": None,
            "last_exception_type": "",
            "last_exception_message": "",
        }
        engines = {symbol: build_engine(symbol, cfg) for symbol, cfg in symbol_cfgs.items()}
        for symbol, engine in engines.items():
            bootstrap_symbol(
                engine,
                symbol=symbol,
                state_dir=state_dir,
                fresh_start=bool(args.fresh_start),
                metadata=symbol_metadata(symbol, symbol_cfgs[symbol]),
                runner_status=runner_status,
            )
        try:
            run_once(engines, symbol_cfgs, state_dir=state_dir, runner_status=runner_status)
        except Exception as exc:
            runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
            runner_status["last_exception_at"] = utc_now_iso()
            runner_status["last_exception_type"] = type(exc).__name__
            runner_status["last_exception_message"] = str(exc)
            for symbol in sorted(symbol_cfgs.keys()):
                log_runner_exception(event_path_for_symbol(state_dir, symbol), exc, phase="initial_run_once")
        if args.once:
            return 0
        while True:
            time.sleep(max(1.0, float(args.poll_seconds)))
            try:
                run_once(engines, symbol_cfgs, state_dir=state_dir, runner_status=runner_status)
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                for symbol in sorted(symbol_cfgs.keys()):
                    log_runner_exception(event_path_for_symbol(state_dir, symbol), exc, phase="loop_run_once")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
