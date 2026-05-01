#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coinbase_advanced_client import CoinbaseAdvancedClient, CoinbaseAdvancedClientError
from coinbase_rate_limit import safe_market_candles
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "lattice_warp_grinder_strict_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "lattice_warp_grinder_strict_shadow_events.jsonl"
DEFAULT_PRODUCTS = ["IOTX-USD", "BAL-USD", "BLUR-USD"]
KRAKEN_BTC_TICKER = "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD"
EXECUTION_MODEL = "warp_shadow_candle_confirmed_v1"
ENTRY_TTL_SECONDS = 75.0
DEFAULT_QUOTE_SIZE = 50.0
DEFAULT_TARGET_MULTIPLE = 1.006
DEFAULT_STOP_MULTIPLE = 0.985
DEFAULT_MIN_SPREAD_PCT = 0.85
DEFAULT_WARP_THRESHOLD_USD = 5.0


def maker_fee_rate(total_volume: float) -> float:
    if total_volume >= 100000:
        return 0.0010
    if total_volume >= 50000:
        return 0.0015
    if total_volume >= 10000:
        return 0.0025
    return 0.0040


def floor_minute_epoch(ts: float) -> int:
    return int(float(ts) // 60 * 60)


def effective_target_multiple(base_target_multiple: float, fee_rate: float) -> float:
    return max(float(base_target_multiple), 1.0 + (float(fee_rate) * 2.0) + 0.001)


def normalize_candle(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    try:
        return {
            "start": int(row.get("start") or 0),
            "open": float(row.get("open") or 0.0),
            "high": float(row.get("high") or 0.0),
            "low": float(row.get("low") or 0.0),
            "close": float(row.get("close") or 0.0),
            "volume": float(row.get("volume") or 0.0),
        }
    except Exception:
        return None


class StrictLatticeWarpShadow:
    def __init__(
        self,
        *,
        starting_cash: float = 324.0,
        products: list[str] | None = None,
        quote_size: float = DEFAULT_QUOTE_SIZE,
        target_multiple: float = DEFAULT_TARGET_MULTIPLE,
        stop_multiple: float = DEFAULT_STOP_MULTIPLE,
        min_spread_pct: float = DEFAULT_MIN_SPREAD_PCT,
        warp_threshold_usd: float = DEFAULT_WARP_THRESHOLD_USD,
    ) -> None:
        self.execution_model = EXECUTION_MODEL
        self.products = [str(product).upper() for product in (products or DEFAULT_PRODUCTS)]
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.realized_net = 0.0
        self.realized_closes = 0
        self.realized_wins = 0
        self.realized_losses = 0
        self.total_volume = 0.0
        self.total_fees = 0.0
        self.quote_size = float(quote_size)
        self.target_multiple = float(target_multiple)
        self.stop_multiple = float(stop_multiple)
        self.min_spread_pct = float(min_spread_pct)
        self.warp_threshold_usd = float(warp_threshold_usd)
        self.positions: dict[str, dict[str, Any]] = {}
        self.pending_entries: dict[str, dict[str, Any]] = {}
        self.pending_exits: dict[str, dict[str, Any]] = {}
        self.market_state: dict[str, dict[str, Any]] = {}
        self.kraken_state = {
            "last_price": 0.0,
            "warp_velocity": 0.0,
            "last_signal_at": 0.0,
        }
        self.reset_notice: dict[str, Any] | None = None

    @classmethod
    def from_state(
        cls,
        payload: dict[str, Any] | None,
        *,
        default_products: list[str] | None = None,
        default_starting_cash: float = 324.0,
    ) -> "StrictLatticeWarpShadow":
        engine = payload.get("engine") if isinstance(payload, dict) else None
        if not isinstance(engine, dict):
            return cls(starting_cash=default_starting_cash, products=default_products)

        products = [str(product).upper() for product in (engine.get("products") or default_products or DEFAULT_PRODUCTS)]
        starting_cash = float(engine.get("starting_cash") or default_starting_cash)
        if str(engine.get("execution_model") or "") != EXECUTION_MODEL:
            restored = cls(starting_cash=starting_cash, products=products)
            restored.reset_notice = {
                "reason": "execution_model_reset",
                "prior_execution_model": str(engine.get("execution_model") or "legacy_snapshot_fill"),
                "prior_cash": float(engine.get("cash") or starting_cash),
                "prior_realized_net": float(engine.get("realized_net") or 0.0),
                "prior_realized_closes": int(engine.get("realized_closes") or engine.get("closes") or 0),
                "prior_total_volume": float(engine.get("total_volume") or engine.get("vol") or 0.0),
            }
            return restored

        restored = cls(
            starting_cash=starting_cash,
            products=products,
            quote_size=float(engine.get("quote_size") or DEFAULT_QUOTE_SIZE),
            target_multiple=float(engine.get("target_multiple") or DEFAULT_TARGET_MULTIPLE),
            stop_multiple=float(engine.get("stop_multiple") or DEFAULT_STOP_MULTIPLE),
            min_spread_pct=float(engine.get("min_spread_pct") or DEFAULT_MIN_SPREAD_PCT),
            warp_threshold_usd=float(engine.get("warp_threshold_usd") or DEFAULT_WARP_THRESHOLD_USD),
        )
        restored.cash = float(engine.get("cash") or restored.starting_cash)
        restored.realized_net = float(engine.get("realized_net") or 0.0)
        restored.realized_closes = int(engine.get("realized_closes") or 0)
        restored.realized_wins = int(engine.get("realized_wins") or 0)
        restored.realized_losses = int(engine.get("realized_losses") or 0)
        restored.total_volume = float(engine.get("total_volume") or 0.0)
        restored.total_fees = float(engine.get("total_fees") or 0.0)
        kraken = engine.get("kraken_state") or {}
        restored.kraken_state = {
            "last_price": float(kraken.get("last_price") or 0.0),
            "warp_velocity": float(kraken.get("warp_velocity") or 0.0),
            "last_signal_at": float(kraken.get("last_signal_at") or 0.0),
        }
        for row in engine.get("positions") or []:
            product = str(row.get("product_id") or "").upper()
            if not product:
                continue
            restored.positions[product] = {
                "product_id": product,
                "entry_price": float(row.get("entry_price") or 0.0),
                "units": float(row.get("units") or 0.0),
                "quote_size": float(row.get("quote_size") or 0.0),
                "entry_fee": float(row.get("entry_fee") or 0.0),
                "fee_rate": float(row.get("fee_rate") or 0.0),
                "opened_at": str(row.get("opened_at") or ""),
                "last_bid": float(row.get("last_bid") or 0.0),
                "last_ask": float(row.get("last_ask") or 0.0),
                "signal_velocity": float(row.get("signal_velocity") or 0.0),
                "triggered_at": float(row.get("triggered_at") or 0.0),
            }
        for row in engine.get("pending_entries") or []:
            product = str(row.get("product_id") or "").upper()
            if not product:
                continue
            restored.pending_entries[product] = {
                "product_id": product,
                "limit_price": float(row.get("limit_price") or 0.0),
                "quote_size": float(row.get("quote_size") or 0.0),
                "fee_rate": float(row.get("fee_rate") or 0.0),
                "placed_at": float(row.get("placed_at") or 0.0),
                "eligible_after_candle_start": int(row.get("eligible_after_candle_start") or 0),
                "expires_at": float(row.get("expires_at") or 0.0),
                "spread_pct": float(row.get("spread_pct") or 0.0),
                "signal_velocity": float(row.get("signal_velocity") or 0.0),
                "kraken_price": float(row.get("kraken_price") or 0.0),
            }
        for row in engine.get("pending_exits") or []:
            product = str(row.get("product_id") or "").upper()
            if not product:
                continue
            restored.pending_exits[product] = {
                "product_id": product,
                "limit_price": float(row.get("limit_price") or 0.0),
                "fee_rate": float(row.get("fee_rate") or 0.0),
                "placed_at": float(row.get("placed_at") or 0.0),
                "eligible_after_candle_start": int(row.get("eligible_after_candle_start") or 0),
                "target_multiple": float(row.get("target_multiple") or DEFAULT_TARGET_MULTIPLE),
            }
        for row in engine.get("market_state") or []:
            product = str(row.get("product_id") or "").upper()
            if not product:
                continue
            restored.market_state[product] = {
                "product_id": product,
                "last_candle_start": int(row.get("last_candle_start") or 0),
                "last_candle_open": float(row.get("last_candle_open") or 0.0),
                "last_candle_high": float(row.get("last_candle_high") or 0.0),
                "last_candle_low": float(row.get("last_candle_low") or 0.0),
                "last_candle_close": float(row.get("last_candle_close") or 0.0),
                "last_candle_volume": float(row.get("last_candle_volume") or 0.0),
                "last_candle_poll_minute": int(row.get("last_candle_poll_minute") or 0),
            }
        return restored

    def _market_row(self, product_id: str) -> dict[str, Any]:
        product = str(product_id).upper()
        row = self.market_state.get(product)
        if not isinstance(row, dict):
            row = {
                "product_id": product,
                "last_candle_start": 0,
                "last_candle_open": 0.0,
                "last_candle_high": 0.0,
                "last_candle_low": 0.0,
                "last_candle_close": 0.0,
                "last_candle_volume": 0.0,
                "last_candle_poll_minute": 0,
            }
            self.market_state[product] = row
        return row

    def record_completed_candle(self, product_id: str, candle: dict[str, Any] | None) -> bool:
        row = normalize_candle(candle)
        if row is None or int(row["start"]) <= 0:
            return False
        market = self._market_row(product_id)
        if int(row["start"]) <= int(market.get("last_candle_start") or 0):
            return False
        market["last_candle_start"] = int(row["start"])
        market["last_candle_open"] = float(row["open"])
        market["last_candle_high"] = float(row["high"])
        market["last_candle_low"] = float(row["low"])
        market["last_candle_close"] = float(row["close"])
        market["last_candle_volume"] = float(row["volume"])
        return True

    def note_kraken_price(self, price: float | None, *, now_ts: float) -> dict[str, Any] | None:
        if price is None or price <= 0:
            return None
        previous = float(self.kraken_state.get("last_price") or 0.0)
        velocity = 0.0
        if previous > 0:
            velocity = float(price) - previous
        self.kraken_state["last_price"] = float(price)
        self.kraken_state["warp_velocity"] = velocity
        if velocity >= self.warp_threshold_usd:
            last_signal_at = float(self.kraken_state.get("last_signal_at") or 0.0)
            if now_ts - last_signal_at >= 1.0:
                self.kraken_state["last_signal_at"] = float(now_ts)
                return {
                    "ts_utc": utc_now_iso(),
                    "action": "warp_signal_detected",
                    "kraken_price": round(float(price), 4),
                    "warp_velocity": round(float(velocity), 4),
                }
        return None

    def snapshot(self) -> dict[str, Any]:
        positions = []
        for product_id in sorted(self.positions):
            row = dict(self.positions[product_id])
            positions.append(
                {
                    "product_id": product_id,
                    "entry_price": round(float(row.get("entry_price") or 0.0), 10),
                    "units": round(float(row.get("units") or 0.0), 10),
                    "quote_size": round(float(row.get("quote_size") or 0.0), 4),
                    "entry_fee": round(float(row.get("entry_fee") or 0.0), 4),
                    "fee_rate": round(float(row.get("fee_rate") or 0.0), 6),
                    "opened_at": str(row.get("opened_at") or ""),
                    "last_bid": round(float(row.get("last_bid") or 0.0), 10),
                    "last_ask": round(float(row.get("last_ask") or 0.0), 10),
                    "signal_velocity": round(float(row.get("signal_velocity") or 0.0), 4),
                    "triggered_at": round(float(row.get("triggered_at") or 0.0), 3),
                }
            )
        pending_entries = []
        for product_id in sorted(self.pending_entries):
            row = dict(self.pending_entries[product_id])
            pending_entries.append(
                {
                    "product_id": product_id,
                    "limit_price": round(float(row.get("limit_price") or 0.0), 10),
                    "quote_size": round(float(row.get("quote_size") or 0.0), 4),
                    "fee_rate": round(float(row.get("fee_rate") or 0.0), 6),
                    "placed_at": round(float(row.get("placed_at") or 0.0), 3),
                    "eligible_after_candle_start": int(row.get("eligible_after_candle_start") or 0),
                    "expires_at": round(float(row.get("expires_at") or 0.0), 3),
                    "spread_pct": round(float(row.get("spread_pct") or 0.0), 4),
                    "signal_velocity": round(float(row.get("signal_velocity") or 0.0), 4),
                    "kraken_price": round(float(row.get("kraken_price") or 0.0), 4),
                }
            )
        pending_exits = []
        for product_id in sorted(self.pending_exits):
            row = dict(self.pending_exits[product_id])
            pending_exits.append(
                {
                    "product_id": product_id,
                    "limit_price": round(float(row.get("limit_price") or 0.0), 10),
                    "fee_rate": round(float(row.get("fee_rate") or 0.0), 6),
                    "placed_at": round(float(row.get("placed_at") or 0.0), 3),
                    "eligible_after_candle_start": int(row.get("eligible_after_candle_start") or 0),
                    "target_multiple": round(float(row.get("target_multiple") or 0.0), 6),
                }
            )
        market_rows = []
        for product_id in sorted(self.market_state):
            row = dict(self.market_state[product_id])
            market_rows.append(
                {
                    "product_id": product_id,
                    "last_candle_start": int(row.get("last_candle_start") or 0),
                    "last_candle_open": round(float(row.get("last_candle_open") or 0.0), 10),
                    "last_candle_high": round(float(row.get("last_candle_high") or 0.0), 10),
                    "last_candle_low": round(float(row.get("last_candle_low") or 0.0), 10),
                    "last_candle_close": round(float(row.get("last_candle_close") or 0.0), 10),
                    "last_candle_volume": round(float(row.get("last_candle_volume") or 0.0), 4),
                    "last_candle_poll_minute": int(row.get("last_candle_poll_minute") or 0),
                }
            )
        return {
            "mode": "lattice_warp_grinder_strict_shadow",
            "execution_model": self.execution_model,
            "products": self.products,
            "starting_cash": round(self.starting_cash, 4),
            "cash": round(self.cash, 4),
            "realized_net": round(self.realized_net, 4),
            "realized_closes": self.realized_closes,
            "realized_wins": self.realized_wins,
            "realized_losses": self.realized_losses,
            "total_volume": round(self.total_volume, 4),
            "total_fees": round(self.total_fees, 4),
            "current_fee_rate": maker_fee_rate(self.total_volume),
            "quote_size": round(self.quote_size, 4),
            "target_multiple": round(self.target_multiple, 6),
            "stop_multiple": round(self.stop_multiple, 6),
            "min_spread_pct": round(self.min_spread_pct, 4),
            "warp_threshold_usd": round(self.warp_threshold_usd, 4),
            "open_positions": len(self.positions),
            "pending_entry_count": len(self.pending_entries),
            "pending_exit_count": len(self.pending_exits),
            "positions": positions,
            "pending_entries": pending_entries,
            "pending_exits": pending_exits,
            "market_state": market_rows,
            "kraken_state": {
                "last_price": round(float(self.kraken_state.get("last_price") or 0.0), 4),
                "warp_velocity": round(float(self.kraken_state.get("warp_velocity") or 0.0), 4),
                "last_signal_at": round(float(self.kraken_state.get("last_signal_at") or 0.0), 3),
            },
        }

    def process_book(
        self,
        product_id: str,
        *,
        bid: float,
        ask: float,
        warp_active: bool,
        signal_velocity: float,
        kraken_price: float,
        completed_candle: dict[str, Any] | None = None,
        now_ts: float | None = None,
    ) -> list[dict[str, Any]]:
        product = str(product_id).upper()
        if bid <= 0 or ask <= 0 or ask < bid:
            return []

        now_ts = float(now_ts if now_ts is not None else time.time())
        events: list[dict[str, Any]] = []
        spread_pct = (ask - bid) / bid * 100.0
        candle = normalize_candle(completed_candle)
        if candle is not None:
            self.record_completed_candle(product, candle)

        position = self.positions.get(product)
        if position:
            position["last_bid"] = bid
            position["last_ask"] = ask

        pending_entry = self.pending_entries.get(product)
        if pending_entry and product not in self.positions:
            if now_ts >= float(pending_entry.get("expires_at") or 0.0):
                del self.pending_entries[product]
                events.append(
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "cancel_entry_order",
                        "product_id": product,
                        "limit_price": round(float(pending_entry["limit_price"]), 10),
                        "quote_size": round(float(pending_entry["quote_size"]), 4),
                        "reason": "entry_ttl_expired",
                    }
                )
            elif candle is not None and int(candle["start"]) > int(pending_entry["eligible_after_candle_start"]):
                if float(candle["low"]) <= float(pending_entry["limit_price"]):
                    quote_size = float(pending_entry["quote_size"])
                    fee_rate = float(pending_entry["fee_rate"])
                    total_cost = quote_size * (1.0 + fee_rate)
                    if self.cash >= total_cost:
                        units = quote_size / float(pending_entry["limit_price"])
                        entry_fee = quote_size * fee_rate
                        self.cash -= total_cost
                        self.total_volume += quote_size
                        self.total_fees += entry_fee
                        self.positions[product] = {
                            "product_id": product,
                            "entry_price": float(pending_entry["limit_price"]),
                            "units": units,
                            "quote_size": quote_size,
                            "entry_fee": entry_fee,
                            "fee_rate": fee_rate,
                            "opened_at": utc_now_iso(),
                            "last_bid": bid,
                            "last_ask": ask,
                            "signal_velocity": float(pending_entry.get("signal_velocity") or 0.0),
                            "triggered_at": float(pending_entry.get("placed_at") or now_ts),
                        }
                        del self.pending_entries[product]
                        events.append(
                            {
                                "ts_utc": utc_now_iso(),
                                "action": "fill_entry_order",
                                "product_id": product,
                                "entry_price": round(float(self.positions[product]["entry_price"]), 10),
                                "units": round(units, 10),
                                "entry_fee": round(entry_fee, 4),
                                "fee_rate": round(fee_rate, 6),
                                "signal_velocity": round(float(self.positions[product]["signal_velocity"]), 4),
                                "fill_confirmed_by_candle_start": int(candle["start"]),
                                "fill_confirmed_by_candle_low": round(float(candle["low"]), 10),
                                "cash_after": round(self.cash, 4),
                            }
                        )
                        realized_target_multiple = effective_target_multiple(self.target_multiple, maker_fee_rate(self.total_volume))
                        limit_price = float(self.positions[product]["entry_price"]) * realized_target_multiple
                        self.pending_exits[product] = {
                            "product_id": product,
                            "limit_price": limit_price,
                            "fee_rate": maker_fee_rate(self.total_volume),
                            "placed_at": now_ts,
                            "eligible_after_candle_start": floor_minute_epoch(now_ts),
                            "target_multiple": realized_target_multiple,
                        }
                        events.append(
                            {
                                "ts_utc": utc_now_iso(),
                                "action": "place_exit_order",
                                "product_id": product,
                                "limit_price": round(limit_price, 10),
                                "fee_rate": round(float(self.pending_exits[product]["fee_rate"]), 6),
                                "target_multiple": round(realized_target_multiple, 6),
                            }
                        )
                        return events

        position = self.positions.get(product)
        pending_exit = self.pending_exits.get(product)
        if position:
            entry_price = float(position["entry_price"])
            units = float(position["units"])
            quote_size = float(position["quote_size"])
            entry_fee = float(position["entry_fee"])

            if pending_exit and candle is not None and int(candle["start"]) > int(pending_exit["eligible_after_candle_start"]):
                if float(candle["high"]) >= float(pending_exit["limit_price"]):
                    exit_price = float(pending_exit["limit_price"])
                    exit_fee_rate = float(pending_exit["fee_rate"])
                    exit_notional = units * exit_price
                    exit_fee = exit_notional * exit_fee_rate
                    proceeds = exit_notional - exit_fee
                    net = proceeds - (quote_size + entry_fee)
                    self.cash += proceeds
                    self.realized_net += net
                    self.realized_closes += 1
                    self.total_volume += exit_notional
                    self.total_fees += exit_fee
                    if net >= 0:
                        self.realized_wins += 1
                    else:
                        self.realized_losses += 1
                    del self.positions[product]
                    del self.pending_exits[product]
                    events.append(
                        {
                            "ts_utc": utc_now_iso(),
                            "action": "fill_exit_order",
                            "product_id": product,
                            "entry_price": round(entry_price, 10),
                            "exit_price": round(exit_price, 10),
                            "units": round(units, 10),
                            "quote_size": round(quote_size, 4),
                            "entry_fee": round(entry_fee, 4),
                            "exit_fee": round(exit_fee, 4),
                            "fee_rate": round(exit_fee_rate, 6),
                            "fill_confirmed_by_candle_start": int(candle["start"]),
                            "fill_confirmed_by_candle_high": round(float(candle["high"]), 10),
                            "close_reason": "target",
                            "net_pnl": round(net, 4),
                            "cash_after": round(self.cash, 4),
                            "total_volume_after": round(self.total_volume, 4),
                        }
                    )
                    return events

            if bid < entry_price * self.stop_multiple:
                exit_price = bid
                exit_fee_rate = 0.0060
                exit_notional = units * exit_price
                exit_fee = exit_notional * exit_fee_rate
                proceeds = exit_notional - exit_fee
                net = proceeds - (quote_size + entry_fee)
                self.cash += proceeds
                self.realized_net += net
                self.realized_closes += 1
                self.total_volume += exit_notional
                self.total_fees += exit_fee
                if net >= 0:
                    self.realized_wins += 1
                else:
                    self.realized_losses += 1
                del self.positions[product]
                if product in self.pending_exits:
                    del self.pending_exits[product]
                events.append(
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "panic_exit",
                        "product_id": product,
                        "entry_price": round(entry_price, 10),
                        "exit_price": round(exit_price, 10),
                        "units": round(units, 10),
                        "quote_size": round(quote_size, 4),
                        "entry_fee": round(entry_fee, 4),
                        "exit_fee": round(exit_fee, 4),
                        "fee_rate": round(exit_fee_rate, 6),
                        "close_reason": "panic_stop",
                        "net_pnl": round(net, 4),
                        "cash_after": round(self.cash, 4),
                        "total_volume_after": round(self.total_volume, 4),
                    }
                )
                return events

            if product not in self.pending_exits:
                realized_target_multiple = effective_target_multiple(self.target_multiple, maker_fee_rate(self.total_volume))
                limit_price = entry_price * realized_target_multiple
                self.pending_exits[product] = {
                    "product_id": product,
                    "limit_price": limit_price,
                    "fee_rate": maker_fee_rate(self.total_volume),
                    "placed_at": now_ts,
                    "eligible_after_candle_start": floor_minute_epoch(now_ts),
                    "target_multiple": realized_target_multiple,
                }
                events.append(
                    {
                        "ts_utc": utc_now_iso(),
                        "action": "place_exit_order",
                        "product_id": product,
                        "limit_price": round(limit_price, 10),
                        "fee_rate": round(float(self.pending_exits[product]["fee_rate"]), 6),
                        "target_multiple": round(realized_target_multiple, 6),
                    }
                )
                return events

        fee_rate = maker_fee_rate(self.total_volume)
        total_cost = self.quote_size * (1.0 + fee_rate)
        if (
            warp_active
            and product not in self.positions
            and product not in self.pending_entries
            and self.cash >= total_cost
            and spread_pct >= self.min_spread_pct
        ):
            self.pending_entries[product] = {
                "product_id": product,
                "limit_price": bid,
                "quote_size": self.quote_size,
                "fee_rate": fee_rate,
                "placed_at": now_ts,
                "eligible_after_candle_start": floor_minute_epoch(now_ts),
                "expires_at": now_ts + ENTRY_TTL_SECONDS,
                "spread_pct": spread_pct,
                "signal_velocity": signal_velocity,
                "kraken_price": kraken_price,
            }
            events.append(
                {
                    "ts_utc": utc_now_iso(),
                    "action": "place_entry_order",
                    "product_id": product,
                    "limit_price": round(bid, 10),
                    "ask_price": round(ask, 10),
                    "quote_size": round(self.quote_size, 4),
                    "fee_rate": round(fee_rate, 6),
                    "spread_pct": round(spread_pct, 4),
                    "signal_velocity": round(signal_velocity, 4),
                    "kraken_price": round(kraken_price, 4),
                    "expires_at": round(now_ts + ENTRY_TTL_SECONDS, 3),
                }
            )
        return events


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def save_state(path: Path, engine: StrictLatticeWarpShadow, runner: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "runner": runner,
        "engine": engine.snapshot(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def fetch_kraken_btc() -> float | None:
    try:
        with urllib.request.urlopen(KRAKEN_BTC_TICKER, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return float(data["result"]["XXBTZUSD"]["c"][0])
    except Exception:
        return None


def fetch_latest_completed_candle(
    client: CoinbaseAdvancedClient,
    product_id: str,
    *,
    last_seen_start: int,
    now_ts: float,
) -> dict[str, Any] | None:
    end = int(now_ts)
    start = max(0, end - 180)
    response = safe_market_candles(
        client,
        product_id,
        start=start,
        end=end,
        granularity="ONE_MINUTE",
        retries=4,
        base_delay=1.0,
    )
    if response is None:
        return None
    candles = [normalize_candle(row) for row in (response.get("candles") or [])]
    candles = [row for row in candles if row is not None]
    if not candles:
        return None
    completed_cutoff = floor_minute_epoch(now_ts) - 60
    latest: dict[str, Any] | None = None
    for row in sorted(candles, key=lambda item: int(item["start"])):
        start_ts = int(row["start"])
        if start_ts <= int(last_seen_start or 0):
            continue
        if start_ts > completed_cutoff:
            continue
        latest = row
    return latest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict lattice-warp grinder shadow")
    parser.add_argument("--starting-cash", type=float, default=324.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    parser.add_argument("--quote-size", type=float, default=DEFAULT_QUOTE_SIZE)
    parser.add_argument("--target-multiple", type=float, default=DEFAULT_TARGET_MULTIPLE)
    parser.add_argument("--stop-multiple", type=float, default=DEFAULT_STOP_MULTIPLE)
    parser.add_argument("--min-spread-pct", type=float, default=DEFAULT_MIN_SPREAD_PCT)
    parser.add_argument("--warp-threshold-usd", type=float, default=DEFAULT_WARP_THRESHOLD_USD)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    prior_payload = load_json(state_path)
    engine = StrictLatticeWarpShadow.from_state(
        prior_payload,
        default_products=[str(product).upper() for product in args.products],
        default_starting_cash=float(args.starting_cash),
    )
    if not isinstance(prior_payload, dict):
        engine.starting_cash = float(args.starting_cash)
        engine.cash = float(args.starting_cash)
    engine.quote_size = float(args.quote_size)
    engine.target_multiple = float(args.target_multiple)
    engine.stop_multiple = float(args.stop_multiple)
    engine.min_spread_pct = float(args.min_spread_pct)
    engine.warp_threshold_usd = float(args.warp_threshold_usd)

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

    if engine.reset_notice:
        append_jsonl(
            event_path,
            {
                "ts_utc": utc_now_iso(),
                "action": "execution_model_reset",
                **engine.reset_notice,
                "new_execution_model": EXECUTION_MODEL,
            },
        )
        save_state(state_path, engine, runner_status)

    def run_once() -> None:
        now_ts = time.time()
        poll_minute = floor_minute_epoch(now_ts)
        kraken_price = fetch_kraken_btc()
        if kraken_price is None:
            append_jsonl(
                event_path,
                {
                    "ts_utc": utc_now_iso(),
                    "action": "kraken_tick_unavailable",
                },
            )
        else:
            signal_event = engine.note_kraken_price(kraken_price, now_ts=now_ts)
            if signal_event:
                append_jsonl(event_path, signal_event)
        warp_velocity = float(engine.kraken_state.get("warp_velocity") or 0.0)
        warp_active = kraken_price is not None and warp_velocity >= float(engine.warp_threshold_usd)

        for product in engine.products:
            try:
                market = engine._market_row(product)
                completed_candle = None
                if int(market.get("last_candle_poll_minute") or 0) != poll_minute:
                    completed_candle = fetch_latest_completed_candle(
                        client,
                        product,
                        last_seen_start=int(market.get("last_candle_start") or 0),
                        now_ts=now_ts,
                    )
                    market["last_candle_poll_minute"] = poll_minute
                    if completed_candle is None:
                        append_jsonl(
                            event_path,
                            {
                                "ts_utc": utc_now_iso(),
                                "action": "rate_limit_skip_live_fetch",
                                "product_id": product,
                                "granularity": "ONE_MINUTE",
                            },
                        )
                resp = client.best_bid_ask([product])
                pricebooks = resp.get("pricebooks") or []
                if not pricebooks:
                    continue
                book = pricebooks[0]
                bid = float(book["bids"][0]["price"])
                ask = float(book["asks"][0]["price"])
                events = engine.process_book(
                    product,
                    bid=bid,
                    ask=ask,
                    warp_active=bool(warp_active),
                    signal_velocity=warp_velocity,
                    kraken_price=float(kraken_price or 0.0),
                    completed_candle=completed_candle,
                    now_ts=now_ts,
                )
                for event in events:
                    append_jsonl(event_path, event)
                time.sleep(0.25)
            except CoinbaseAdvancedClientError as exc:
                if "429" in str(exc):
                    append_jsonl(
                        event_path,
                        {
                            "ts_utc": utc_now_iso(),
                            "action": "rate_limit_skip_live_fetch",
                            "product_id": product,
                            "error": str(exc),
                        },
                    )
                    time.sleep(3.0)
                    continue
                raise

        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
        runner_status["last_exception_at"] = None
        runner_status["last_exception_type"] = ""
        runner_status["last_exception_message"] = ""
        save_state(state_path, engine, runner_status)

    try:
        run_once()
        snapshot = engine.snapshot()
        print(
            f"[{utc_now_iso()}] Strict warp shadow cash=${snapshot['cash']:.2f} "
            f"net=${snapshot['realized_net']:.2f} vol=${snapshot['total_volume']:.2f} "
            f"open={snapshot['open_positions']} pending={snapshot['pending_entry_count']}/{snapshot['pending_exit_count']} "
            f"vel=${snapshot['kraken_state']['warp_velocity']:.2f}",
            flush=True,
        )
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
                save_state(state_path, engine, runner_status)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    except Exception as exc:
        runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
        runner_status["last_exception_at"] = utc_now_iso()
        runner_status["last_exception_type"] = type(exc).__name__
        runner_status["last_exception_message"] = str(exc)
        save_state(state_path, engine, runner_status)
        log_runner_exception(event_path, exc, phase="initial_run_once")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
