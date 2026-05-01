"""Spark-7 Alpaca quote-pulse fade bot.

Competition mode:
- New HFT family separate from spark3 burst-chasing and spark6 recycling.
- Buys sharp micro selloffs after a short pulse down and tries to catch the snapback.
- Quote driven, one position at a time, long-only for clean paper-account heats.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from typing import Any

import requests

from alpaca_config import get_alpaca_config

ALPACA = get_alpaca_config()
BASE_URL = ALPACA["base_url"]
DATA_URL = ALPACA["data_url"]

HEADERS = {
    "APCA-API-KEY-ID": ALPACA["api_key"],
    "APCA-API-SECRET-KEY": ALPACA["secret_key"],
}

SYMBOLS = [
    ("BTCUSD", "BTC/USD"),
    ("ETHUSD", "ETH/USD"),
]

POLL_SECONDS = 0.55
ACCOUNT_REFRESH_SECONDS = 5.0
QUOTE_WINDOW = 40
FAST_LOOKBACK = 2
SLOW_LOOKBACK = 6
ENTRY_SPREAD_MAX = 0.00145
MIN_PULSE_DOWN_PCT = 0.00010
MAX_PULSE_DOWN_PCT = 0.00110
MAX_RANGE_PCT = 0.00240
MAX_RANGE_POS = 0.34
MIN_BID_IMBALANCE = 1.03
MIN_REBOUND_DRIFT = 0.00001
MIN_SCORE = 0.78
TP_PCT = 0.00030
SL_PCT = 0.00027
EXIT_DECAY_PCT = -0.00003
MAX_HOLD_SECONDS = 4.2
WIN_COOLDOWN_SECONDS = 0.25
LOSS_COOLDOWN_SECONDS = 1.9
BASE_SIZE_PCT = 0.36
MAX_SIZE_PCT = 0.54
MIN_CASH = 3.0
MIN_FILL_NOTIONAL = 8.0
MIN_POSITION_NOTIONAL = 4.0
DUST_QTY = 1e-6
MAX_ENTRY_SLIP_PCT = 0.00105

quote_tape: dict[str, deque[dict[str, float]]] = {
    data_symbol: deque(maxlen=QUOTE_WINDOW) for _, data_symbol in SYMBOLS
}
cooldowns: dict[str, float] = {trade_symbol: 0.0 for trade_symbol, _ in SYMBOLS}
runtime: dict[str, Any] = {
    "started_at": time.time(),
    "last_trade_at": time.time(),
    "entries": 0,
    "closes": 0,
    "no_fills": 0,
    "status_tick": 0,
    "last_account_refresh": 0.0,
    "account_cache": None,
    "position_state": None,
}


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def trade_events_per_hour() -> float:
    elapsed = max(1.0, time.time() - runtime["started_at"])
    return ((runtime["entries"] + runtime["closes"]) / elapsed) * 3600


def get_account(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if not force and runtime["account_cache"] and now - runtime["last_account_refresh"] < ACCOUNT_REFRESH_SECONDS:
        return runtime["account_cache"]

    response = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        print(f"[ALPACA] account failed: {response.status_code} {response.text[:140]}")
        return runtime["account_cache"] or {}

    account = response.json()
    runtime["account_cache"] = account
    runtime["last_account_refresh"] = now
    return account


def get_positions() -> list[dict[str, Any]]:
    response = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        print(f"[ALPACA] positions failed: {response.status_code} {response.text[:120]}")
        return []
    positions = response.json()
    return positions if isinstance(positions, list) else []


def trade_symbol_to_data(trade_symbol: str) -> str | None:
    for ts, data_symbol in SYMBOLS:
        if ts == trade_symbol:
            return data_symbol
    return None


def get_live_quotes() -> dict[str, dict[str, float]]:
    response = requests.get(
        f"{DATA_URL}/latest/quotes",
        headers=HEADERS,
        params={"symbols": ",".join(data_symbol for _, data_symbol in SYMBOLS)},
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[ALPACA] quotes failed: {response.status_code} {response.text[:140]}")
        return {}

    raw = response.json().get("quotes", {})
    parsed: dict[str, dict[str, float]] = {}
    ts = time.time()
    for _, data_symbol in SYMBOLS:
        quote = raw.get(data_symbol, {})
        try:
            bid = float(quote.get("bp", 0) or 0)
            ask = float(quote.get("ap", 0) or 0)
            bid_size = float(quote.get("bs", 0) or 0)
            ask_size = float(quote.get("as", 0) or 0)
        except (TypeError, ValueError):
            continue
        if bid <= 0 or ask <= 0 or ask <= bid:
            continue
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid if mid > 0 else 0.0
        imbalance = bid_size / max(ask_size, 1.0)
        entry = {
            "ts": ts,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread_pct": spread_pct,
            "imbalance": imbalance,
        }
        parsed[data_symbol] = entry
        quote_tape[data_symbol].append(entry)
    return parsed


def detect_open_position() -> dict[str, Any] | None:
    for position in get_positions():
        try:
            qty = abs(float(position.get("qty", 0) or 0))
            entry = float(position.get("avg_entry_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        notional = qty * entry
        if qty <= DUST_QTY or notional < MIN_POSITION_NOTIONAL:
            continue
        trade_symbol = position.get("symbol")
        data_symbol = trade_symbol_to_data(trade_symbol)
        if not data_symbol:
            continue
        return {
            "trade_symbol": trade_symbol,
            "data_symbol": data_symbol,
            "qty": qty,
            "entry": entry,
            "opened_at": time.time(),
        }
    return None


def flatten_symbol(trade_symbol: str) -> bool:
    response = requests.delete(f"{BASE_URL}/v2/positions/{trade_symbol}", headers=HEADERS, timeout=10)
    if response.status_code not in (200, 201, 204):
        print(f"[ALPACA] flatten fail {trade_symbol}: {response.status_code} {response.text[:140]}")
        return False
    runtime["closes"] += 1
    return True


def get_order(order_id: str | None) -> dict[str, Any] | None:
    if not order_id:
        return None
    response = requests.get(f"{BASE_URL}/v2/orders/{order_id}", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        return None
    return response.json()


def order_has_meaningful_fill(order: dict[str, Any] | None) -> bool:
    if not order:
        return False
    try:
        filled_qty = abs(float(order.get("filled_qty", 0) or 0))
        filled_price = float(order.get("filled_avg_price", 0) or 0)
    except (TypeError, ValueError):
        return False
    status = order.get("status", "")
    return (
        filled_qty > DUST_QTY
        and filled_qty * filled_price >= MIN_FILL_NOTIONAL
        and status in {"filled", "partially_filled"}
    )


def place_entry(trade_symbol: str, qty: float, quote: dict[str, float]) -> bool:
    limit_price = quote["ask"] * (1 + MAX_ENTRY_SLIP_PCT)
    response = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={
            "symbol": trade_symbol,
            "qty": f"{qty:.8f}".rstrip("0").rstrip("."),
            "side": "buy",
            "type": "limit",
            "time_in_force": "ioc",
            "limit_price": f"{limit_price:.8f}".rstrip("0").rstrip("."),
        },
        timeout=10,
    )
    if response.status_code not in (200, 201):
        print(f"[ALPACA] entry fail buy {trade_symbol}: {response.status_code} {response.text[:140]}")
        return False

    order = response.json()
    if order_has_meaningful_fill(order):
        runtime["entries"] += 1
        return True

    order_id = order.get("id")
    for _ in range(4):
        time.sleep(0.25)
        latest = get_order(order_id)
        if order_has_meaningful_fill(latest):
            runtime["entries"] += 1
            return True
        if latest and latest.get("status") in {"canceled", "expired", "done_for_day", "rejected"}:
            break

    runtime["no_fills"] += 1
    latest = get_order(order_id) if order_id else None
    status = (latest or order).get("status", "unknown")
    filled_qty = (latest or order).get("filled_qty", "0")
    print(f"[ALPACA] no fill buy {trade_symbol} status={status} filled_qty={filled_qty}")
    return False


def choose_signal(quotes: dict[str, dict[str, float]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    idle_seconds = time.time() - runtime["last_trade_at"]
    score_boost = min(0.55, max(0.0, idle_seconds - 12.0) / 35.0)

    for trade_symbol, data_symbol in SYMBOLS:
        tape = quote_tape[data_symbol]
        if len(tape) < SLOW_LOOKBACK + 2:
            continue
        quote = quotes.get(data_symbol)
        if not quote:
            continue
        if quote["spread_pct"] > ENTRY_SPREAD_MAX or time.time() < cooldowns[trade_symbol]:
            continue

        current = tape[-1]["mid"]
        fast_base = tape[-1 - FAST_LOOKBACK]["mid"]
        slow_base = tape[-1 - SLOW_LOOKBACK]["mid"]
        fast_momentum = (current - fast_base) / fast_base
        slow_momentum = (current - slow_base) / slow_base
        drift = (current - tape[-2]["mid"]) / tape[-2]["mid"]

        recent_window = list(tape)[-SLOW_LOOKBACK:]
        recent_high = max(item["mid"] for item in recent_window)
        recent_low = min(item["mid"] for item in recent_window)
        range_pct = (recent_high - recent_low) / current if current > 0 else 0.0
        range_pos = (current - recent_low) / (recent_high - recent_low) if recent_high > recent_low else 0.5
        pulse_down_pct = (recent_high - current) / recent_high if recent_high > 0 else 0.0
        imbalance = quote["imbalance"]

        if pulse_down_pct < MIN_PULSE_DOWN_PCT or pulse_down_pct > MAX_PULSE_DOWN_PCT:
            continue
        if range_pct > MAX_RANGE_PCT:
            continue
        if range_pos > MAX_RANGE_POS:
            continue
        if imbalance < MIN_BID_IMBALANCE:
            continue
        if drift < MIN_REBOUND_DRIFT:
            continue

        score = (
            pulse_down_pct * 11000
            + max(0.0, -fast_momentum) * 7000
            + max(0.0, imbalance - 1.0) * 4.0
            + max(0.0, 0.30 - range_pos) * 2200
            + max(0.0, drift) * 7000
            - abs(slow_momentum) * 7000
            - quote["spread_pct"] * 3000
            + score_boost
        )
        if score < MIN_SCORE:
            continue

        size_pct = min(MAX_SIZE_PCT, BASE_SIZE_PCT + max(0.0, score) * 0.006)
        signal = {
            "trade_symbol": trade_symbol,
            "data_symbol": data_symbol,
            "quote": quote,
            "score": score,
            "drift": drift,
            "range_pct": range_pct,
            "range_pos": range_pos,
            "pulse_down_pct": pulse_down_pct,
            "imbalance": imbalance,
            "size_pct": size_pct,
        }
        if best is None or signal["score"] > best["score"]:
            best = signal

    return best


def should_exit(position: dict[str, Any], quote: dict[str, float] | None) -> tuple[bool, str, float]:
    if not quote:
        return False, "NO_QUOTE", 0.0

    current = quote["mid"]
    pnl_pct = (current - position["entry"]) / position["entry"]
    held = time.time() - position["opened_at"]
    tape = quote_tape[position["data_symbol"]]
    decay = 0.0
    if len(tape) >= FAST_LOOKBACK + 1:
        decay_base = tape[-1 - FAST_LOOKBACK]["mid"]
        decay = (current - decay_base) / decay_base

    if pnl_pct >= TP_PCT:
        return True, "TP", pnl_pct
    if pnl_pct <= -SL_PCT:
        return True, "SL", pnl_pct
    if held >= MAX_HOLD_SECONDS:
        return True, "TIME", pnl_pct
    if held >= 1.8 and decay <= EXIT_DECAY_PCT:
        return True, "DECAY", pnl_pct
    return False, "", pnl_pct


def log_status(now: str, equity: float, cash: float, signal: dict[str, Any] | None) -> None:
    runtime["status_tick"] += 1
    if runtime["status_tick"] % 4 != 0:
        return
    tph = trade_events_per_hour()
    if signal:
        print(
            f"[{now}] WATCH {signal['trade_symbol']} score={signal['score']:.2f} "
            f"pulse={signal['pulse_down_pct']*100:.3f}% imb={signal['imbalance']:.2f} "
            f"drift={signal['drift']*100:.3f}% pos={signal['range_pos']:.2f} tph={tph:.1f}"
        )
    else:
        print(f"[{now}] HOLD equity=${equity:.2f} cash=${cash:.2f} tph={tph:.1f} no_fill={runtime['no_fills']}")


def main() -> None:
    print("=" * 88)
    print("SPARK-7 ALPACA QUOTE-PULSE FADE")
    print("=" * 88)

    while True:
        now = now_str()
        account = get_account()
        cash = float(account.get("cash", 0) or 0)
        equity = float(account.get("equity", cash) or cash)

        quotes = get_live_quotes()
        if not quotes:
            time.sleep(POLL_SECONDS)
            continue

        live_position = detect_open_position()
        if live_position:
            existing = runtime["position_state"]
            if not existing or existing.get("trade_symbol") != live_position["trade_symbol"]:
                live_position["opened_at"] = time.time()
                runtime["position_state"] = live_position
            else:
                live_position["opened_at"] = existing["opened_at"]
                runtime["position_state"] = live_position

            quote = quotes.get(live_position["data_symbol"])
            should_close, reason, pnl_pct = should_exit(live_position, quote)
            current = quote["mid"] if quote else live_position["entry"]
            print(
                f"[{now}] OPEN LONG {live_position['trade_symbol']} qty={live_position['qty']:.6f} "
                f"entry={live_position['entry']:.2f} px={current:.2f} pnl={pnl_pct*100:.3f}% "
                f"tph={trade_events_per_hour():.1f}"
            )
            if should_close and flatten_symbol(live_position["trade_symbol"]):
                runtime["last_trade_at"] = time.time()
                cooldown_seconds = WIN_COOLDOWN_SECONDS if reason == "TP" else LOSS_COOLDOWN_SECONDS
                cooldowns[live_position["trade_symbol"]] = time.time() + cooldown_seconds
                print(f"[{now}] EXIT {reason} {live_position['trade_symbol']} pnl={pnl_pct*100:.3f}%")
                runtime["position_state"] = None
                get_account(force=True)
            time.sleep(POLL_SECONDS)
            continue

        runtime["position_state"] = None
        if cash < MIN_CASH:
            print(f"[{now}] HOLD cash too low ${cash:.2f}")
            time.sleep(POLL_SECONDS)
            continue

        signal = choose_signal(quotes)
        if not signal:
            log_status(now, equity, cash, None)
            time.sleep(POLL_SECONDS)
            continue

        position_value = min(cash * 0.9, equity * signal["size_pct"])
        qty = position_value / signal["quote"]["ask"]
        if qty * signal["quote"]["ask"] < MIN_FILL_NOTIONAL:
            log_status(now, equity, cash, None)
            time.sleep(POLL_SECONDS)
            continue

        if place_entry(signal["trade_symbol"], qty, signal["quote"]):
            runtime["last_trade_at"] = time.time()
            runtime["position_state"] = {
                "trade_symbol": signal["trade_symbol"],
                "data_symbol": signal["data_symbol"],
                "qty": qty,
                "entry": signal["quote"]["ask"],
                "opened_at": time.time(),
            }
            print(
                f"[{now}] ENTRY LONG {signal['trade_symbol']} qty={qty:.6f} "
                f"entry={signal['quote']['ask']:.2f} score={signal['score']:.2f} "
                f"pulse={signal['pulse_down_pct']*100:.3f}% imb={signal['imbalance']:.2f} "
                f"drift={signal['drift']*100:.3f}% pos={signal['range_pos']:.2f} "
                f"tph={trade_events_per_hour():.1f}"
            )
            get_account(force=True)
        else:
            log_status(now, equity, cash, signal)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
