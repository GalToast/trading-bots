#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import MetaTrader5 as mt5

from live_penetration_lattice_shadow import (
    REARM_VARIANTS,
    StatefulRearmRawEngine,
    _normalize_open_realism_mode,
    append_jsonl,
    load_state,
    log_runner_exception,
    price_bar_to_dict,
    run_direct_live_exec,
    save_state,
    utc_now_iso,
)
from live_penetration_lattice_mirror import (
    DEFAULT_LIVE_COMMENT_PREFIX,
    DEFAULT_LIVE_MAGIC,
    DEFAULT_LIVE_VOLUME,
    load_state as load_exec_state,
)
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import pip_size_for


TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}
DEFAULT_DIRECT_EXEC_STATE_PATH = Path("reports/penetration_lattice_live_crypto_exec_state.json")
DEFAULT_DIRECT_EXEC_LOG_PATH = Path("reports/penetration_lattice_live_crypto_exec_events.jsonl")


def load_recent_closed_bars(symbol: str, timeframe_name: str, count: int) -> list[dict]:
    timeframe = TIMEFRAME_MAP[timeframe_name]
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, count)
    if rates is None:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def hydrate_engine_histories(engines, timeframe_name: str, lookback_bars: int = 120) -> None:
    for symbol, engine in engines.items():
        bars = load_recent_closed_bars(symbol, timeframe_name, lookback_bars)
        if not bars:
            continue
        engine.history = [price_bar_to_dict(bar) for bar in bars]


def prime_engines_fresh(engines, timeframe_name: str, lookback_bars: int = 120) -> None:
    for symbol, engine in engines.items():
        bars = load_recent_closed_bars(symbol, timeframe_name, lookback_bars)
        if not bars:
            continue
        engine.history = [price_bar_to_dict(bar) for bar in bars]
        last_bar = engine.history[-1]
        engine.state.last_bar_time = int(last_bar["time"])
        engine.state.open_tickets = []
        engine.state.lattice_started_time = 0
        engine.state.cooldown_until_time = 0
        engine.state.rearm_tokens = []
        engine.state.rearm_opens = 0
        engine.state.realized_closes = 0
        engine.state.realized_net_usd = 0.0
        engine.state.breakout_flushes = 0
        engine.state.breakout_net_usd = 0.0
        engine.state.forced_unwinds = 0
        engine.state.forced_net_usd = 0.0
        engine.state.anchor_resets = 0
        engine.state.max_open_total = 0
        anchor = float(last_bar["close"])
        engine.state.anchor = anchor
        engine.state.next_sell_level = anchor + engine.base_step_px
        engine.state.next_buy_level = anchor - engine.base_step_px


def bootstrap(engines, timeframe_name: str, state_path: Path, event_path: Path, fresh_start: bool, metadata: dict) -> None:
    if state_path.exists():
        load_state(state_path, engines)
        hydrate_engine_histories(engines, timeframe_name)
        return
    if fresh_start:
        prime_engines_fresh(engines, timeframe_name)
        save_state(state_path, engines, metadata=metadata)
        append_jsonl(event_path, {"ts_utc": utc_now_iso(), "action": "fresh_start_prime", "symbols": sorted(engines.keys()), **metadata})
        return
    prime_engines_fresh(engines, timeframe_name)
    save_state(state_path, engines, metadata=metadata)
    append_jsonl(event_path, {"ts_utc": utc_now_iso(), "action": "bootstrap_complete", "symbols": sorted(engines.keys()), **metadata})


def run_once(
    engines,
    timeframe_name: str,
    state_path: Path,
    event_path: Path,
    metadata: dict,
    direct_exec: dict | None = None,
    runner_status: dict | None = None,
) -> None:
    for symbol, engine in engines.items():
        bars = load_recent_closed_bars(symbol, timeframe_name, 5)
        new_bars = [b for b in bars if int(b["time"]) > int(engine.state.last_bar_time or 0)]
        for bar in new_bars:
            engine.process_bar(bar, event_path=event_path, emit=True)
    if runner_status is not None:
        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
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
            live_comment_prefix=direct_exec["live_comment_prefix"],
            live_volume=direct_exec["live_volume"],
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dedicated crypto H1 shadow runner using raw stateful rearm.")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--timeframe", default="H1", choices=sorted(TIMEFRAME_MAP.keys()))
    parser.add_argument("--step", type=float, required=True)
    parser.add_argument("--max-open-per-side", type=int, default=30)
    parser.add_argument("--raw-close-alpha", type=float, default=1.0)
    parser.add_argument("--raw-rearm-variant", default="rearm_lvl2_exc1")
    parser.add_argument("--raw-rearm-cooldown-bars", type=int, default=0)
    parser.add_argument("--raw-rearm-momentum-gate", action="store_true")
    parser.add_argument("--raw-sell-gap", type=int, default=1)
    parser.add_argument("--raw-buy-gap", type=int, default=1)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--event-path", required=True)
    parser.add_argument("--direct-live", action="store_true")
    parser.add_argument("--live-close-realism-mode", choices=["auto", "intrabar", "bar_close"], default="auto")
    parser.add_argument("--live-open-realism-mode", choices=["auto", "intrabar", "broker_touch"], default="auto")
    parser.add_argument("--direct-exec-state-path", default=str(DEFAULT_DIRECT_EXEC_STATE_PATH))
    parser.add_argument("--direct-exec-log-path", default=str(DEFAULT_DIRECT_EXEC_LOG_PATH))
    parser.add_argument("--live-magic", type=int, default=DEFAULT_LIVE_MAGIC)
    parser.add_argument("--live-comment-prefix", default=DEFAULT_LIVE_COMMENT_PREFIX)
    parser.add_argument("--live-volume", type=float, default=DEFAULT_LIVE_VOLUME)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        symbol = args.symbol.upper()
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"Missing symbol info for {symbol}")
            return 1
        variant = REARM_VARIANTS.get(args.raw_rearm_variant or "")
        if variant is None:
            print(f"Unknown raw rearm variant: {args.raw_rearm_variant}")
            return 1
        pip_size = float(pip_size_for(info) or 0.0)
        if pip_size <= 0:
            print(f"Invalid pip size for {symbol}")
            return 1
        close_realism_mode = "bar_close" if args.direct_live and args.live_close_realism_mode == "auto" else (
            "intrabar" if args.live_close_realism_mode == "auto" else str(args.live_close_realism_mode)
        )
        open_realism_mode = "broker_touch" if args.direct_live and args.live_open_realism_mode == "auto" else (
            _normalize_open_realism_mode(None if args.live_open_realism_mode == "auto" else args.live_open_realism_mode)
        )
        # Crypto configs specify raw price steps (e.g. BTCUSD step=$50), but the shared
        # raw engine expects step_pips and multiplies by pip_size internally.
        engine_step_pips = float(args.step) / pip_size
        cfg = RawConfig(step_pips=engine_step_pips, max_open_per_side=int(args.max_open_per_side), close_mode="two_level")
        engines = {
            symbol: StatefulRearmRawEngine(
                symbol,
                cfg,
                info,
                variant=variant,
                close_alpha=float(args.raw_close_alpha),
                cooldown_bars=int(args.raw_rearm_cooldown_bars),
                momentum_gate=bool(args.raw_rearm_momentum_gate),
                sell_gap=int(args.raw_sell_gap),
                buy_gap=int(args.raw_buy_gap),
                close_realism_mode=close_realism_mode,
                open_realism_mode=open_realism_mode,
            )
        }
        state_path = Path(args.state_path)
        event_path = Path(args.event_path)
        metadata = {
            "symbols": [symbol],
            "timeframe": args.timeframe,
            "step": float(args.step),
            "max_open_per_side": int(args.max_open_per_side),
            "raw_close_alpha": float(args.raw_close_alpha),
            "raw_rearm_variant": str(args.raw_rearm_variant),
            "raw_rearm_cooldown_bars": int(args.raw_rearm_cooldown_bars),
            "raw_rearm_momentum_gate": bool(args.raw_rearm_momentum_gate),
            "raw_sell_gap": int(args.raw_sell_gap),
            "raw_buy_gap": int(args.raw_buy_gap),
            "live_close_realism_mode": close_realism_mode,
            "live_open_realism_mode": open_realism_mode,
            "direct_live": bool(args.direct_live),
            "live_magic": int(args.live_magic),
            "live_comment_prefix": str(args.live_comment_prefix),
            "live_volume": float(args.live_volume),
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
        }
        bootstrap(engines, args.timeframe, state_path, event_path, args.fresh_start, metadata)
        direct_exec = None
        if args.direct_live:
            exec_state_path = Path(args.direct_exec_state_path)
            exec_log_path = Path(args.direct_exec_log_path)
            direct_exec = {
                "state": load_exec_state(exec_state_path),
                "state_path": exec_state_path,
                "log_path": exec_log_path,
                "allowed_symbols": {symbol},
                "live_magic": metadata["live_magic"],
                "live_comment_prefix": metadata["live_comment_prefix"],
                "live_volume": metadata["live_volume"],
            }
        try:
            run_once(
                engines,
                args.timeframe,
                state_path,
                event_path,
                metadata,
                direct_exec=direct_exec,
                runner_status=runner_status,
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
            try:
                run_once(
                    engines,
                    args.timeframe,
                    state_path,
                    event_path,
                    metadata,
                    direct_exec=direct_exec,
                    runner_status=runner_status,
                )
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                log_runner_exception(event_path, exc, phase="loop_run_once")
            time.sleep(max(1.0, float(args.poll_seconds)))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
