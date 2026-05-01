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
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso
from tick_penetration_lattice_core import bucket_start, timeframe_seconds


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "coinbase_spot_piranha_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "coinbase_spot_piranha_shadow_events.jsonl"


@dataclass
class SpotLot:
    entry_price: float
    quantity: float
    cost_usd: float
    opened_time: int
    opened_msc: int


class CoinbaseSpotPiranhaEngine:
    def __init__(
        self,
        *,
        product_id: str,
        timeframe_name: str,
        buy_step_px: float,
        profit_target_px: float,
        quote_per_buy_usd: float,
        starting_cash_usd: float,
        max_lots: int,
        taker_fee_bps: float,
        min_hold_seconds: int = 0,
    ) -> None:
        self.product_id = str(product_id or "").upper()
        self.timeframe_name = str(timeframe_name or "M1").upper()
        self.buy_step_px = float(buy_step_px)
        self.profit_target_px = float(profit_target_px)
        self.quote_per_buy_usd = float(quote_per_buy_usd)
        self.starting_cash_usd = float(starting_cash_usd)
        self.max_lots = int(max_lots)
        self.taker_fee_bps = float(taker_fee_bps)
        self.min_hold_seconds = max(0, int(min_hold_seconds))
        self.cash_usd = float(starting_cash_usd)
        self.anchor = 0.0
        self.next_buy_level = 0.0
        self.next_sell_level = 0.0
        self.last_tick_time = 0
        self.last_tick_msc = 0
        self.last_bar_time = 0
        self.realized_net_usd = 0.0
        self.realized_closes = 0
        self.max_open_total = 0
        self.anchor_resets = 0
        self.open_lots: list[SpotLot] = []
        self.fee_model = "coinbase_spot_taker_fee_bps_per_side"
        self.fee_source = "configured"
        self.fee_tier = ""
        self.fill_model = "best_ask_entry_best_bid_exit_tick_proxy"

    def _fee_rate(self) -> float:
        return self.taker_fee_bps / 10000.0

    def apply_fee_tier(self, fee_tier: CoinbaseSpotFeeTier) -> None:
        self.taker_fee_bps = float(fee_tier.taker_bps)
        self.fee_model = "coinbase_spot_account_taker_fee_tier"
        self.fee_source = fee_tier.source
        self.fee_tier = fee_tier.pricing_tier

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbol": self.product_id,
            "timeframe": self.timeframe_name,
            "mode": "coinbase_spot_piranha_shadow",
            "anchor": self.anchor,
            "next_buy_level": self.next_buy_level,
            "next_sell_level": self.next_sell_level,
            "last_tick_time": self.last_tick_time,
            "last_tick_msc": self.last_tick_msc,
            "last_bar_time": self.last_bar_time,
            "realized_net_usd": self.realized_net_usd,
            "realized_closes": self.realized_closes,
            "anchor_resets": self.anchor_resets,
            "max_open_total": self.max_open_total,
            "cash_usd": self.cash_usd,
            "inventory_units": round(sum(lot.quantity for lot in self.open_lots), 12),
            "buy_step_px": self.buy_step_px,
            "profit_target_px": self.profit_target_px,
            "quote_per_buy_usd": self.quote_per_buy_usd,
            "starting_cash_usd": self.starting_cash_usd,
            "max_lots": self.max_lots,
            "taker_fee_bps": self.taker_fee_bps,
            "fee_bps_per_side": self.taker_fee_bps,
            "fee_model": self.fee_model,
            "fee_source": self.fee_source,
            "fee_tier": self.fee_tier,
            "open_lots": [
                {
                    "entry_price": lot.entry_price,
                    "quantity": lot.quantity,
                    "cost_usd": lot.cost_usd,
                    "opened_time": lot.opened_time,
                    "opened_msc": lot.opened_msc,
                }
                for lot in self.open_lots
            ],
            "open_realism_mode": "tick_native",
            "close_realism_mode": "tick_native",
            "fill_model": self.fill_model,
            "venue": "coinbase_advanced_spot_shadow",
        }

    def load_snapshot(self, payload: dict[str, Any]) -> None:
        self.anchor = float(payload.get("anchor", 0.0) or 0.0)
        self.next_buy_level = float(payload.get("next_buy_level", 0.0) or 0.0)
        self.next_sell_level = float(payload.get("next_sell_level", 0.0) or 0.0)
        self.last_tick_time = int(payload.get("last_tick_time", 0) or 0)
        self.last_tick_msc = int(payload.get("last_tick_msc", 0) or 0)
        self.last_bar_time = int(payload.get("last_bar_time", 0) or 0)
        self.realized_net_usd = float(payload.get("realized_net_usd", 0.0) or 0.0)
        self.realized_closes = int(payload.get("realized_closes", 0) or 0)
        self.anchor_resets = int(payload.get("anchor_resets", 0) or 0)
        self.max_open_total = int(payload.get("max_open_total", 0) or 0)
        self.cash_usd = float(payload.get("cash_usd", self.starting_cash_usd) or self.starting_cash_usd)
        self.open_lots = [
            SpotLot(
                entry_price=float(lot.get("entry_price", 0.0) or 0.0),
                quantity=float(lot.get("quantity", 0.0) or 0.0),
                cost_usd=float(lot.get("cost_usd", 0.0) or 0.0),
                opened_time=int(lot.get("opened_time", 0) or 0),
                opened_msc=int(lot.get("opened_msc", 0) or 0),
            )
            for lot in (payload.get("open_lots") or [])
        ]

    def prime(self, mid_price: float, tick_time: int) -> None:
        self.anchor = float(mid_price)
        self.next_buy_level = self.anchor - self.buy_step_px
        self.next_sell_level = self.anchor + self.profit_target_px
        self.last_bar_time = bucket_start(int(tick_time), self.timeframe_name)

    def _record_event(self, event_path: Path | None, action: str, tick: dict[str, Any], **extra: Any) -> None:
        if event_path is None:
            return
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": action,
                "symbol": self.product_id,
                "mode": "coinbase_spot_piranha_shadow",
                "time": int(tick["time"]),
                "time_msc": int(tick["time_msc"]),
                "bid": float(tick["bid"]),
                "ask": float(tick["ask"]),
                **extra,
            },
        )

    def process_tick(self, tick: dict[str, Any], *, event_path: Path | None = None, emit: bool = True) -> None:
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        tick_time = int(tick["time"])
        tick_msc = int(tick["time_msc"])
        mid = (bid + ask) / 2.0
        fee_rate = self._fee_rate()

        if self.anchor == 0.0:
            self.prime(mid, tick_time)

        while ask <= float(self.next_buy_level) and len(self.open_lots) < int(self.max_lots):
            required_cash = self.quote_per_buy_usd * (1.0 + fee_rate)
            if self.cash_usd + 1e-9 < required_cash:
                break
            quantity = self.quote_per_buy_usd / ask
            entry_fee = self.quote_per_buy_usd * fee_rate
            lot = SpotLot(
                entry_price=ask,
                quantity=quantity,
                cost_usd=required_cash,
                opened_time=tick_time,
                opened_msc=tick_msc,
            )
            self.cash_usd -= required_cash
            self.open_lots.append(lot)
            if emit:
                self._record_event(
                    event_path,
                    "open_lot",
                    tick,
                    entry_price=round(lot.entry_price, 8),
                    quantity=round(lot.quantity, 12),
                    cash_after=round(self.cash_usd, 6),
                    cost_usd=round(lot.cost_usd, 6),
                    entry_fee=round(entry_fee, 6),
                    fee_bps_per_side=round(self.taker_fee_bps, 4),
                    fee_model=self.fee_model,
                    fee_source=self.fee_source,
                    fee_tier=self.fee_tier,
                    fill_model=self.fill_model,
                )
            self.next_buy_level -= self.buy_step_px
            self.max_open_total = max(self.max_open_total, len(self.open_lots))

        closed_any = False
        remaining: list[SpotLot] = []
        for lot in self.open_lots:
            target = float(lot.entry_price) + self.profit_target_px
            held_long_enough = tick_time >= (int(lot.opened_time) + int(self.min_hold_seconds))
            if held_long_enough and bid >= target:
                proceeds = float(lot.quantity) * bid
                exit_fee = proceeds * fee_rate
                gross_pnl = (bid - float(lot.entry_price)) * float(lot.quantity)
                entry_fee = float(lot.cost_usd) - (float(lot.entry_price) * float(lot.quantity))
                total_fee = entry_fee + exit_fee
                pnl = proceeds - exit_fee - float(lot.cost_usd)
                self.cash_usd += proceeds - exit_fee
                self.realized_net_usd += pnl
                self.realized_closes += 1
                closed_any = True
                if emit:
                    self._record_event(
                        event_path,
                        "close_lot",
                        tick,
                        entry_price=round(lot.entry_price, 8),
                        exit_price=round(bid, 8),
                        quantity=round(lot.quantity, 12),
                        gross_pnl=round(gross_pnl, 6),
                        entry_fee=round(entry_fee, 6),
                        exit_fee=round(exit_fee, 6),
                        fee=round(total_fee, 6),
                        fee_bps_per_side=round(self.taker_fee_bps, 4),
                        fee_model=self.fee_model,
                        fee_source=self.fee_source,
                        fee_tier=self.fee_tier,
                        fill_model=self.fill_model,
                        realized_pnl=round(pnl, 6),
                        cash_after=round(self.cash_usd, 6),
                    )
            else:
                remaining.append(lot)
        self.open_lots = remaining

        if self.open_lots:
            self.next_sell_level = min(lot.entry_price + self.profit_target_px for lot in self.open_lots)
        else:
            self.next_sell_level = self.anchor + self.profit_target_px

        if not self.open_lots and abs(mid - float(self.anchor)) >= self.buy_step_px:
            self.anchor = mid
            self.next_buy_level = self.anchor - self.buy_step_px
            self.next_sell_level = self.anchor + self.profit_target_px
            self.anchor_resets += 1
            if emit and closed_any:
                self._record_event(event_path, "anchor_reset", tick, anchor=round(self.anchor, 8))

        self.last_tick_time = tick_time
        self.last_tick_msc = tick_msc
        self.last_bar_time = bucket_start(tick_time, self.timeframe_name)


def save_state(path: Path, engine: CoinbaseSpotPiranhaEngine, metadata: dict[str, Any], runner: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "metadata": metadata,
        "runner": runner or {},
        "symbols": {
            engine.product_id: engine.snapshot(),
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_state(path: Path, engine: CoinbaseSpotPiranhaEngine) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    snap = (payload.get("symbols") or {}).get(engine.product_id)
    if not snap:
        return False
    engine.load_snapshot(snap or {})
    return True


def fetch_coinbase_tick(client: CoinbaseAdvancedClient, product_id: str) -> dict[str, Any]:
    payload = client.best_bid_ask([product_id])
    books = payload.get("pricebooks") or []
    if not books:
        raise RuntimeError(f"No pricebook returned for {product_id}")
    book = books[0]
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        raise RuntimeError(f"Incomplete best bid/ask for {product_id}")
    now_msc = int(time.time() * 1000)
    now_sec = int(now_msc // 1000)
    return {
        "time": now_sec,
        "time_msc": now_msc,
        "bid": float(bids[0]["price"]),
        "ask": float(asks[0]["price"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coinbase Advanced spot piranha shadow runner.")
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--timeframe", default="M1", choices=["M1", "M5", "M15", "H1"])
    parser.add_argument("--buy-step", type=float, required=True)
    parser.add_argument("--profit-target", type=float, required=True)
    parser.add_argument("--quote-per-buy", type=float, default=5.0)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--max-lots", type=int, default=6)
    parser.add_argument("--taker-fee-bps", type=float, default=60.0)
    parser.add_argument("--min-hold-seconds", type=int, default=0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    product_id = str(args.product_id or "").upper()
    product = client.get_product(product_id)
    if str(product.get("product_type") or "").upper() != "SPOT":
        raise RuntimeError(f"{product_id} is not a SPOT product")
    engine = CoinbaseSpotPiranhaEngine(
        product_id=product_id,
        timeframe_name=str(args.timeframe).upper(),
        buy_step_px=float(args.buy_step),
        profit_target_px=float(args.profit_target),
        quote_per_buy_usd=float(args.quote_per_buy),
        starting_cash_usd=float(args.starting_cash),
        max_lots=int(args.max_lots),
        taker_fee_bps=float(args.taker_fee_bps),
        min_hold_seconds=int(args.min_hold_seconds),
    )
    fee_tier = resolve_spot_fee_tier(client, fallback_taker_bps=float(args.taker_fee_bps))
    engine.apply_fee_tier(fee_tier)
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    metadata = {
        "venue": "coinbase_advanced",
        "product_id": product_id,
        "product_type": str(product.get("product_type") or ""),
        "display_name": str(product.get("display_name") or ""),
        "timeframe": str(args.timeframe).upper(),
        "buy_step": float(args.buy_step),
        "profit_target": float(args.profit_target),
        "quote_per_buy": float(args.quote_per_buy),
        "starting_cash": float(args.starting_cash),
        "max_lots": int(args.max_lots),
        "taker_fee_bps": float(engine.taker_fee_bps),
        "fee_bps_per_side": float(engine.taker_fee_bps),
        "fee_model": engine.fee_model,
        "fee_source": engine.fee_source,
        "fee_tier": engine.fee_tier,
        "min_hold_seconds": int(args.min_hold_seconds),
        "tick_native": True,
        "shadow_only": True,
        "strategy_kind": "spot_piranha",
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
        "fee_bps_per_side": round(engine.taker_fee_bps, 4),
        "fee_source": engine.fee_source,
        "fee_tier": engine.fee_tier,
    }
    if state_path.exists() and not args.fresh_start:
        load_state(state_path, engine)
    else:
        tick = fetch_coinbase_tick(client, product_id)
        engine.prime((float(tick["bid"]) + float(tick["ask"])) / 2.0, int(tick["time"]))
        save_state(state_path, engine, metadata)
        append_jsonl(event_path, {"ts_utc": utc_now_iso(), "action": "fresh_start_prime" if args.fresh_start else "bootstrap_complete", "symbols": [product_id], **metadata})

    def run_once() -> None:
        tick = fetch_coinbase_tick(client, product_id)
        if int(tick["time_msc"]) <= int(engine.last_tick_msc or 0):
            runner_status["heartbeat_at"] = utc_now_iso()
            runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
            runner_status["consecutive_exceptions"] = 0
            save_state(state_path, engine, metadata, runner=runner_status)
            return
        engine.process_tick(tick, event_path=event_path, emit=True)
        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
        save_state(state_path, engine, metadata, runner=runner_status)

    try:
        run_once()
        if args.once:
            return 0
        while True:
            time.sleep(max(1.0, float(args.poll_seconds)))
            try:
                run_once()
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                save_state(state_path, engine, metadata, runner=runner_status)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    except Exception as exc:
        runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
        runner_status["last_exception_at"] = utc_now_iso()
        runner_status["last_exception_type"] = type(exc).__name__
        runner_status["last_exception_message"] = str(exc)
        save_state(state_path, engine, metadata, runner=runner_status)
        log_runner_exception(event_path, exc, phase="initial_run_once")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
