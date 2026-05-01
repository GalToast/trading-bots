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

from coinbase_advanced_client import CoinbaseAdvancedClient
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "predatory_shadow_monitor_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "predatory_shadow_monitor_events.jsonl"
DEFAULT_PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
KRAKEN_BTC_TICKER = "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD"
ICEBERG_RELOAD_MULTIPLE = 10.0
FAKE_FLOOR_PULL_FRACTION = 0.1
FAKE_FLOOR_VOLUME_MULTIPLE = 5.0

TRACKED_ACTIONS = {
    "iceberg_buy_reload_detected": 1,
    "iceberg_sell_reload_detected": -1,
    "fake_floor_pull_detected": -1,
    "magnetic_wall_touch_detected": 0,
    "kraken_warp_surge_detected": 1,
    "kraken_warp_flush_detected": -1,
}


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def save_state(path: Path, monitor: "PredatoryShadowMonitor", runner: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "runner": runner,
        "monitor": monitor.snapshot(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def fetch_kraken_btc() -> float | None:
    try:
        with urllib.request.urlopen(KRAKEN_BTC_TICKER, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return float(data["result"]["XXBTZUSD"]["c"][0])
    except Exception:
        return None


def same_price(current_price: float, previous_price: float) -> bool:
    if current_price <= 0 or previous_price <= 0:
        return False
    tolerance = max(0.00000001, abs(previous_price) * 0.000001)
    return abs(current_price - previous_price) <= tolerance


def detect_predatory_events(
    product_id: str,
    previous: dict[str, float] | None,
    current: dict[str, float],
    *,
    ts_utc: str,
    kraken_move: float = 0.0,
) -> list[dict[str, Any]]:
    if not previous:
        return []

    events: list[dict[str, Any]] = []
    current_price = float(current.get("price") or 0.0)
    previous_price = float(previous.get("price") or 0.0)
    current_bid_size = float(current.get("bid_size") or 0.0)
    current_ask_size = float(current.get("ask_size") or 0.0)
    previous_bid_size = float(previous.get("bid_size") or 0.0)
    previous_ask_size = float(previous.get("ask_size") or 0.0)
    vol_24h = float(current.get("vol_24h") or 0.0)

    # Include bid/ask for spread feature engineering
    base_event = {
        "ts_utc": ts_utc,
        "product_id": product_id,
        "price": round(current_price, 10),
        "bid": round(float(current.get("bid") or 0.0), 10),
        "ask": round(float(current.get("ask") or 0.0), 10),
    }

    # 1. Iceberg Reloads
    if same_price(current_price, previous_price):
        if previous_ask_size > 0 and current_ask_size >= previous_ask_size * ICEBERG_RELOAD_MULTIPLE:
            ev = base_event.copy()
            ev.update({
                "action": "iceberg_sell_reload_detected",
                "previous_ask_size": round(previous_ask_size, 6),
                "current_ask_size": round(current_ask_size, 6),
                "reload_multiple": round(current_ask_size / previous_ask_size, 4),
            })
            events.append(ev)
        if previous_bid_size > 0 and current_bid_size >= previous_bid_size * ICEBERG_RELOAD_MULTIPLE:
            ev = base_event.copy()
            ev.update({
                "action": "iceberg_buy_reload_detected",
                "previous_bid_size": round(previous_bid_size, 6),
                "current_bid_size": round(current_bid_size, 6),
                "reload_multiple": round(current_bid_size / previous_bid_size, 4),
            })
            events.append(ev)

    # 2. Fake Floors
    if previous_bid_size > (vol_24h / 1440.0 * FAKE_FLOOR_VOLUME_MULTIPLE):
        if current_bid_size < (previous_bid_size * FAKE_FLOOR_PULL_FRACTION) and current_price <= previous_price:
            ev = base_event.copy()
            ev.update({
                "action": "fake_floor_pull_detected",
                "previous_price": round(previous_price, 10),
                "previous_bid_size": round(previous_bid_size, 6),
                "current_bid_size": round(current_bid_size, 6),
                "vol_24h": round(vol_24h, 6),
            })
            events.append(ev)

    # 3. Magnetic Walls
    if current_price > 0:
        mag_level = round(current_price * 20.0) / 20.0
        if mag_level >= 0.05 and abs(current_price - mag_level) / mag_level <= 0.0005:
            ev = base_event.copy()
            ev.update({
                "action": "magnetic_wall_touch_detected",
                "mag_level": round(mag_level, 4)
            })
            events.append(ev)

    # 4. Kraken Warp Gates
    if kraken_move >= 5.0:
        ev = base_event.copy()
        ev.update({
            "action": "kraken_warp_surge_detected",
            "move_usd": round(kraken_move, 4)
        })
        events.append(ev)
    elif kraken_move <= -5.0:
        ev = base_event.copy()
        ev.update({
            "action": "kraken_warp_flush_detected",
            "move_usd": round(kraken_move, 4)
        })
        events.append(ev)

    return events


class PredatoryShadowMonitor:
    def __init__(self, products: list[str], *, prior_payload: dict[str, Any] | None = None) -> None:
        self.products = [str(product).upper() for product in products]
        self.last_book: dict[str, dict[str, float]] = {}
        self.event_counts = {k: 0 for k in TRACKED_ACTIONS}
        self.kraken_state = {
            "last_price": 0.0,
            "last_move_usd": 0.0,
        }
        if isinstance(prior_payload, dict):
            monitor = prior_payload.get("monitor") or {}
            for row in monitor.get("last_book") or []:
                product_id = row.get("product_id") or ""
                self.last_book[product_id.upper()] = {
                    "price": float(row.get("price") or 0.0),
                    "bid": float(row.get("bid") or 0.0),
                    "ask": float(row.get("ask") or 0.0),
                    "bid_size": float(row.get("bid_size") or 0.0),
                    "ask_size": float(row.get("ask_size") or 0.0),
                    "vol_24h": float(row.get("vol_24h") or 0.0),
                }
            counts = monitor.get("event_counts") or {}
            for key in self.event_counts:
                self.event_counts[key] = int(counts.get(key) or 0)
            kraken = monitor.get("kraken_state") or {}
            self.kraken_state["last_price"] = float(kraken.get("last_price") or 0.0)
            self.kraken_state["last_move_usd"] = float(kraken.get("last_move_usd") or 0.0)

    def snapshot(self) -> dict[str, Any]:
        books = []
        for product_id in sorted(self.last_book):
            row = self.last_book[product_id]
            books.append(
                {
                    "product_id": product_id,
                    "price": round(float(row.get("price") or 0.0), 10),
                    "bid": round(float(row.get("bid") or 0.0), 10),
                    "ask": round(float(row.get("ask") or 0.0), 10),
                    "bid_size": round(float(row.get("bid_size") or 0.0), 6),
                    "ask_size": round(float(row.get("ask_size") or 0.0), 6),
                    "vol_24h": round(float(row.get("vol_24h") or 0.0), 6),
                }
            )
        return {
            "mode": "predatory_shadow_monitor",
            "products": self.products,
            "event_counts": dict(self.event_counts),
            "kraken_state": {
                "last_price": round(float(self.kraken_state.get("last_price") or 0.0), 4),
                "last_move_usd": round(float(self.kraken_state.get("last_move_usd") or 0.0), 4),
            },
            "last_book": books,
        }

    def note_kraken_btc(self, price: float | None) -> dict[str, Any] | None:
        if price is None or price <= 0:
            return None
        previous = float(self.kraken_state.get("last_price") or 0.0)
        move = 0.0 if previous <= 0 else float(price) - previous
        self.kraken_state["last_price"] = float(price)
        self.kraken_state["last_move_usd"] = move
        return {
            "ts_utc": utc_now_iso(),
            "action": "kraken_btc_snapshot",
            "price": round(float(price), 4),
            "move_usd": round(float(move), 4),
        }

    def process_snapshot(self, product_id: str, current: dict[str, float], *, ts_utc: str) -> list[dict[str, Any]]:
        product = str(product_id).upper()
        previous = self.last_book.get(product)
        kraken_move = float(self.kraken_state.get("last_move_usd") or 0.0)
        events = detect_predatory_events(product, previous, current, ts_utc=ts_utc, kraken_move=kraken_move)
        for event in events:
            action = str(event.get("action") or "")
            if action in self.event_counts:
                self.event_counts[action] += 1
        self.last_book[product] = dict(current)
        return events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Structured predatory shadow monitor")
    parser.add_argument("--products", nargs="*", default=DEFAULT_PRODUCTS)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    prior_payload = load_json(state_path)
    monitor = PredatoryShadowMonitor([str(product).upper() for product in args.products], prior_payload=prior_payload)
    client = CoinbaseAdvancedClient()

    runner_status = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": utc_now_iso(),
        "poll_seconds": max(0.5, float(args.poll_seconds)),
        "heartbeat_at": None,
        "last_successful_run_at": None,
        "consecutive_exceptions": 0,
        "last_exception_at": None,
        "last_exception_type": "",
        "last_exception_message": "",
    }

    def run_once() -> None:
        kraken_event = monitor.note_kraken_btc(fetch_kraken_btc())
        if kraken_event:
            append_jsonl(event_path, kraken_event)

        for product in monitor.products:
            ticker = client.get_product(product)
            current_price = float(ticker.get("price") or 0.0)
            vol_24h = float(ticker.get("volume_24h") or 0.0)
            resp = client.best_bid_ask([product])
            pricebooks = resp.get("pricebooks") or []
            if not pricebooks:
                continue
            book = pricebooks[0]
            bid = float(book["bids"][0]["price"])
            ask = float(book["asks"][0]["price"])
            bid_size = float(book["bids"][0]["size"])
            ask_size = float(book["asks"][0]["size"])
            current = {
                "price": current_price,
                "bid": bid,
                "ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "vol_24h": vol_24h,
            }
            for event in monitor.process_snapshot(product, current, ts_utc=utc_now_iso()):
                append_jsonl(event_path, event)

        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
        runner_status["last_exception_at"] = None
        runner_status["last_exception_type"] = ""
        runner_status["last_exception_message"] = ""
        save_state(state_path, monitor, runner_status)

    try:
        run_once()
        if args.once:
            return 0
        while True:
            time.sleep(max(0.5, float(args.poll_seconds)))
            try:
                run_once()
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                save_state(state_path, monitor, runner_status)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    except Exception as exc:
        runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
        runner_status["last_exception_at"] = utc_now_iso()
        runner_status["last_exception_type"] = type(exc).__name__
        runner_status["last_exception_message"] = str(exc)
        save_state(state_path, monitor, runner_status)
        log_runner_exception(event_path, exc, phase="initial_run_once")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
