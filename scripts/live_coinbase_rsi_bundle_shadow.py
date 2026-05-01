#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient
from coinbase_fee_model import CoinbaseSpotFeeTier, resolve_spot_fee_tier
from live_coinbase_rsi_shadow import (
    CoinbaseRSIShadowEngine,
    apply_latest_candle,
    fetch_latest_candle,
    fetch_recent_candles,
    load_json,
    restore_engine_from_payload,
    save_state,
)
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "configs" / "coinbase_rsi_bundle_shadow.json"


@dataclass
class BundleLane:
    lane_name: str
    product_id: str
    state_path: Path
    event_path: Path
    rsi_period: int
    oversold: float
    overbought: float
    profit_target_pct: float
    stop_loss_pct: float
    max_hold_bars: int
    maker_fee_bps: float
    deploy_pct: float
    starting_cash: float
    granularity: str
    poll_seconds: float


@dataclass
class BundleRuntime:
    lane: BundleLane
    engine: CoinbaseRSIShadowEngine
    runner_status: dict[str, Any]


def _to_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def load_bundle_config(path: Path, *, lane_names: set[str] | None = None, poll_seconds_override: float | None = None) -> list[BundleLane]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Bundle config must be an object")
    default_poll = max(1.0, float(payload.get("poll_seconds") or 30.0))
    lanes_payload = payload.get("lanes") or []
    if not isinstance(lanes_payload, list):
        raise RuntimeError("Bundle config lanes must be a list")
    selected: list[BundleLane] = []
    for raw_lane in lanes_payload:
        if not isinstance(raw_lane, dict):
            raise RuntimeError("Bundle lane entry must be an object")
        lane_name = str(raw_lane.get("lane_name") or "").strip()
        if not lane_name:
            raise RuntimeError("Bundle lane entry missing lane_name")
        if lane_names and lane_name not in lane_names:
            continue
        selected.append(
            BundleLane(
                lane_name=lane_name,
                product_id=str(raw_lane.get("product_id") or "").strip().upper(),
                state_path=_to_path(str(raw_lane.get("state_path") or "")),
                event_path=_to_path(str(raw_lane.get("event_path") or "")),
                rsi_period=int(raw_lane.get("rsi_period") or 7),
                oversold=float(raw_lane.get("oversold") or 30.0),
                overbought=float(raw_lane.get("overbought") or 70.0),
                profit_target_pct=float(raw_lane.get("profit_target_pct") or 0.02),
                stop_loss_pct=float(raw_lane.get("stop_loss_pct") or 0.003),
                max_hold_bars=int(raw_lane.get("max_hold_bars") or 48),
                maker_fee_bps=float(raw_lane.get("maker_fee_bps") or 5.0),
                deploy_pct=float(raw_lane.get("deploy_pct") or 0.9),
                starting_cash=float(raw_lane.get("starting_cash") or 48.0),
                granularity=str(raw_lane.get("granularity") or "FIVE_MINUTE"),
                poll_seconds=max(1.0, float(poll_seconds_override if poll_seconds_override is not None else raw_lane.get("poll_seconds") or default_poll)),
            )
        )
    if lane_names and not selected:
        missing = ", ".join(sorted(lane_names))
        raise RuntimeError(f"Requested bundle lanes not found: {missing}")
    if not selected:
        raise RuntimeError("Bundle config selected zero lanes")
    return selected


def build_runtime(lane: BundleLane, *, fee_tier: CoinbaseSpotFeeTier | None = None) -> BundleRuntime:
    engine = CoinbaseRSIShadowEngine(
        product_id=lane.product_id,
        starting_cash_usd=lane.starting_cash,
        rsi_period=lane.rsi_period,
        oversold_threshold=lane.oversold,
        overbought_threshold=lane.overbought,
        profit_target_pct=lane.profit_target_pct,
        stop_loss_pct=lane.stop_loss_pct,
        max_hold_bars=lane.max_hold_bars,
        maker_fee_bps=lane.maker_fee_bps,
        deploy_pct=lane.deploy_pct,
        candle_granularity=lane.granularity,
    )
    if fee_tier is not None:
        engine.apply_fee_tier(fee_tier)
    runner_status = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "lane_name": lane.lane_name,
        "product_id": lane.product_id,
        "started_at": utc_now_iso(),
        "poll_seconds": lane.poll_seconds,
        "heartbeat_at": None,
        "last_successful_run_at": None,
        "consecutive_exceptions": 0,
        "last_exception_at": None,
        "last_exception_type": "",
        "last_exception_message": "",
        "fee_bps_per_side": round(engine.maker_fee_bps, 4),
        "fee_source": engine.fee_source,
        "fee_tier": engine.fee_tier,
    }
    return BundleRuntime(lane=lane, engine=engine, runner_status=runner_status)


def bootstrap_runtime(client: CoinbaseAdvancedClient, runtime: BundleRuntime, *, fresh_start: bool = False) -> None:
    lane = runtime.lane
    prior_payload = None if fresh_start else load_json(lane.state_path)
    bootstrap_candles = fetch_recent_candles(
        client,
        lane.product_id,
        lane.granularity,
        count=50,
        event_logger=lambda record: append_jsonl(lane.event_path, record),
    )
    restored = restore_engine_from_payload(runtime.engine, prior_payload, bootstrap_candles=bootstrap_candles)
    if not restored:
        if fresh_start:
            append_jsonl(
                lane.event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "fresh_start_fee_model_reset",
                    "product_id": lane.product_id,
                    "fee_bps_per_side": round(runtime.engine.maker_fee_bps, 4),
                    "fee_source": runtime.engine.fee_source,
                    "fee_tier": runtime.engine.fee_tier,
                },
            )
        for candle in bootstrap_candles[:-1]:
            runtime.engine.process_candle(candle, event_path=lane.event_path)
        save_state(lane.state_path, runtime.engine, runner=runtime.runner_status)


def run_lane_once(client: CoinbaseAdvancedClient, runtime: BundleRuntime) -> None:
    lane = runtime.lane
    latest = fetch_latest_candle(
        client,
        lane.product_id,
        lane.granularity,
        event_logger=lambda record: append_jsonl(lane.event_path, record),
    )
    apply_latest_candle(
        runtime.engine,
        latest,
        runner_status=runtime.runner_status,
        state_path=lane.state_path,
        event_path=lane.event_path,
    )


def mark_lane_exception(runtime: BundleRuntime, exc: BaseException, *, phase: str) -> None:
    runtime.runner_status["consecutive_exceptions"] = int(runtime.runner_status.get("consecutive_exceptions", 0) or 0) + 1
    runtime.runner_status["last_exception_at"] = utc_now_iso()
    runtime.runner_status["last_exception_type"] = type(exc).__name__
    runtime.runner_status["last_exception_message"] = str(exc)
    save_state(runtime.lane.state_path, runtime.engine, runner=runtime.runner_status)
    log_runner_exception(runtime.lane.event_path, exc, phase=phase)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bundle multiple Coinbase RSI shadow lanes into one process.")
    parser.add_argument("--config-path", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--poll-seconds", type=float, default=None)
    parser.add_argument("--lanes", nargs="*", default=None)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = _to_path(str(args.config_path or DEFAULT_CONFIG_PATH))
    lane_filter = {str(name or "").strip() for name in (args.lanes or []) if str(name or "").strip()}
    lanes = load_bundle_config(
        config_path,
        lane_names=lane_filter or None,
        poll_seconds_override=args.poll_seconds,
    )
    client = CoinbaseAdvancedClient()
    fallback_fee_bps = max(float(lane.maker_fee_bps) for lane in lanes)
    fee_tier = resolve_spot_fee_tier(client, fallback_taker_bps=fallback_fee_bps)
    runtimes = [build_runtime(lane, fee_tier=fee_tier) for lane in lanes]

    print(
        f"[{utc_now_iso()}] Bootstrapping Coinbase RSI bundle ({len(runtimes)} lanes, "
        f"fee={fee_tier.taker_bps:.4f}bps source={fee_tier.source})"
    )
    for runtime in runtimes:
        print(f"  - {runtime.lane.lane_name} ({runtime.lane.product_id})")
        bootstrap_runtime(client, runtime, fresh_start=bool(args.fresh_start))

    def run_bundle_once() -> int:
        failures = 0
        for runtime in runtimes:
            try:
                run_lane_once(client, runtime)
            except Exception as exc:
                failures += 1
                mark_lane_exception(runtime, exc, phase="bundle_run_once")
        return failures

    failures = run_bundle_once()
    if args.once:
        return 1 if failures == len(runtimes) else 0

    poll_seconds = max(runtime.lane.poll_seconds for runtime in runtimes)
    print(f"[{utc_now_iso()}] Coinbase RSI bundle shadow runner started. Polling every {poll_seconds}s")
    while True:
        time.sleep(poll_seconds)
        run_bundle_once()


if __name__ == "__main__":
    raise SystemExit(main())
