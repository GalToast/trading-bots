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
from live_coinbase_spot_piranha_shadow import (
    CoinbaseSpotPiranhaEngine,
    fetch_coinbase_tick,
    load_state,
    save_state,
)
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "configs" / "coinbase_spot_piranha_bundle_shadow.json"


@dataclass
class BundleLane:
    lane_name: str
    product_id: str
    timeframe: str
    buy_step: float
    profit_target: float
    quote_per_buy: float
    starting_cash: float
    max_lots: int
    taker_fee_bps: float
    min_hold_seconds: int
    poll_seconds: float
    state_path: Path
    event_path: Path


@dataclass
class BundleRuntime:
    lane: BundleLane
    engine: CoinbaseSpotPiranhaEngine
    metadata: dict[str, Any]
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
    default_poll = max(1.0, float(payload.get("poll_seconds") or 5.0))
    lanes_payload = payload.get("lanes") or []
    if not isinstance(lanes_payload, list):
        raise RuntimeError("Bundle config lanes must be a list")
    lanes: list[BundleLane] = []
    for raw_lane in lanes_payload:
        if not isinstance(raw_lane, dict):
            raise RuntimeError("Bundle lane entry must be an object")
        lane_name = str(raw_lane.get("lane_name") or "").strip()
        if not lane_name:
            raise RuntimeError("Bundle lane entry missing lane_name")
        if lane_names and lane_name not in lane_names:
            continue
        lanes.append(
            BundleLane(
                lane_name=lane_name,
                product_id=str(raw_lane.get("product_id") or "").strip().upper(),
                timeframe=str(raw_lane.get("timeframe") or "M1").upper(),
                buy_step=float(raw_lane.get("buy_step") or 0.0),
                profit_target=float(raw_lane.get("profit_target") or 0.0),
                quote_per_buy=float(raw_lane.get("quote_per_buy") or 5.0),
                starting_cash=float(raw_lane.get("starting_cash") or 48.0),
                max_lots=int(raw_lane.get("max_lots") or 6),
                taker_fee_bps=float(raw_lane.get("taker_fee_bps") or 60.0),
                min_hold_seconds=int(raw_lane.get("min_hold_seconds") or 0),
                poll_seconds=max(1.0, float(poll_seconds_override if poll_seconds_override is not None else raw_lane.get("poll_seconds") or default_poll)),
                state_path=_to_path(str(raw_lane.get("state_path") or "")),
                event_path=_to_path(str(raw_lane.get("event_path") or "")),
            )
        )
    if lane_names and not lanes:
        raise RuntimeError(f"Requested bundle lanes not found: {', '.join(sorted(lane_names))}")
    if not lanes:
        raise RuntimeError("Bundle config selected zero lanes")
    return lanes


def build_runtime(lane: BundleLane, client: CoinbaseAdvancedClient, *, fee_tier: CoinbaseSpotFeeTier | None = None) -> BundleRuntime:
    product = client.get_product(lane.product_id)
    if str(product.get("product_type") or "").upper() != "SPOT":
        raise RuntimeError(f"{lane.product_id} is not a SPOT product")
    engine = CoinbaseSpotPiranhaEngine(
        product_id=lane.product_id,
        timeframe_name=lane.timeframe,
        buy_step_px=lane.buy_step,
        profit_target_px=lane.profit_target,
        quote_per_buy_usd=lane.quote_per_buy,
        starting_cash_usd=lane.starting_cash,
        max_lots=lane.max_lots,
        taker_fee_bps=lane.taker_fee_bps,
        min_hold_seconds=lane.min_hold_seconds,
    )
    if fee_tier is not None:
        engine.apply_fee_tier(fee_tier)
    metadata = {
        "venue": "coinbase_advanced",
        "product_id": lane.product_id,
        "product_type": str(product.get("product_type") or ""),
        "display_name": str(product.get("display_name") or ""),
        "timeframe": lane.timeframe,
        "buy_step": lane.buy_step,
        "profit_target": lane.profit_target,
        "quote_per_buy": lane.quote_per_buy,
        "starting_cash": lane.starting_cash,
        "max_lots": lane.max_lots,
        "taker_fee_bps": engine.taker_fee_bps,
        "fee_bps_per_side": engine.taker_fee_bps,
        "fee_model": engine.fee_model,
        "fee_source": engine.fee_source,
        "fee_tier": engine.fee_tier,
        "min_hold_seconds": lane.min_hold_seconds,
        "tick_native": True,
        "shadow_only": True,
        "strategy_kind": "spot_piranha",
    }
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
        "fee_bps_per_side": round(engine.taker_fee_bps, 4),
        "fee_source": engine.fee_source,
        "fee_tier": engine.fee_tier,
    }
    return BundleRuntime(lane=lane, engine=engine, metadata=metadata, runner_status=runner_status)


def bootstrap_runtime(client: CoinbaseAdvancedClient, runtime: BundleRuntime, *, fresh_start: bool = False) -> None:
    lane = runtime.lane
    if lane.state_path.exists() and not fresh_start:
        if load_state(lane.state_path, runtime.engine):
            return
    tick = fetch_coinbase_tick(client, lane.product_id)
    runtime.engine.prime((float(tick["bid"]) + float(tick["ask"])) / 2.0, int(tick["time"]))
    save_state(lane.state_path, runtime.engine, runtime.metadata, runner=runtime.runner_status)
    append_jsonl(
        lane.event_path,
        {
            "ts_utc": utc_now_iso(),
            "action": "fresh_start_fee_model_reset" if fresh_start else "bootstrap_complete",
            "symbols": [lane.product_id],
            **runtime.metadata,
        },
    )


def run_lane_once(client: CoinbaseAdvancedClient, runtime: BundleRuntime) -> None:
    lane = runtime.lane
    tick = fetch_coinbase_tick(client, lane.product_id)
    if int(tick["time_msc"]) > int(runtime.engine.last_tick_msc or 0):
        runtime.engine.process_tick(tick, event_path=lane.event_path, emit=True)
    runtime.runner_status["heartbeat_at"] = utc_now_iso()
    runtime.runner_status["last_successful_run_at"] = runtime.runner_status["heartbeat_at"]
    runtime.runner_status["consecutive_exceptions"] = 0
    runtime.runner_status["last_exception_at"] = None
    runtime.runner_status["last_exception_type"] = ""
    runtime.runner_status["last_exception_message"] = ""
    save_state(lane.state_path, runtime.engine, runtime.metadata, runner=runtime.runner_status)


def mark_lane_exception(runtime: BundleRuntime, exc: BaseException, *, phase: str) -> None:
    runtime.runner_status["consecutive_exceptions"] = int(runtime.runner_status.get("consecutive_exceptions", 0) or 0) + 1
    runtime.runner_status["last_exception_at"] = utc_now_iso()
    runtime.runner_status["last_exception_type"] = type(exc).__name__
    runtime.runner_status["last_exception_message"] = str(exc)
    save_state(runtime.lane.state_path, runtime.engine, runtime.metadata, runner=runtime.runner_status)
    log_runner_exception(runtime.lane.event_path, exc, phase=phase)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bundle multiple Coinbase spot piranha shadow lanes into one process.")
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
    lanes = load_bundle_config(config_path, lane_names=lane_filter or None, poll_seconds_override=args.poll_seconds)
    client = CoinbaseAdvancedClient()
    fallback_fee_bps = max(float(lane.taker_fee_bps) for lane in lanes)
    fee_tier = resolve_spot_fee_tier(client, fallback_taker_bps=fallback_fee_bps)
    runtimes = [build_runtime(lane, client, fee_tier=fee_tier) for lane in lanes]
    print(
        f"[{utc_now_iso()}] Bootstrapping Coinbase piranha bundle ({len(runtimes)} lanes, "
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
    print(f"[{utc_now_iso()}] Coinbase piranha bundle shadow runner started. Polling every {poll_seconds}s")
    while True:
        time.sleep(poll_seconds)
        run_bundle_once()


if __name__ == "__main__":
    raise SystemExit(main())
