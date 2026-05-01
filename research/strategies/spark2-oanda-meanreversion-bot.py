"""Spark-2 OANDA mean-reversion snapback bot.

Theme: overextension mean-reversion for fast-compounding FX attempts.
The bot looks for a pair stretched away from its rolling mean, then
requires an immediate reversal pulse before entering a concentrated trade.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime
from typing import Any

import requests

from oanda_config import get_oanda_config


OANDA = get_oanda_config()
ACCOUNT_ID = OANDA["account_id"]
BASE_URL = OANDA["api_base_v3"]
HEADERS = {
    "Authorization": f"Bearer {OANDA['api_token']}",
    "Content-Type": OANDA["content_type"],
}

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]
TIMEFRAME = "M1"

LOOKBACK_WINDOW = 24
MAX_CANDLES = 90

OVEREXTENSION_Z = 1.85
REBOUND_THRESHOLD = 0.03  # percent move back toward mean
TP_PCT = 0.0018
SL_PCT = 0.0010
TRAIL_TRIGGER = 0.0008
TRAIL_DROP = 0.0005
MAX_HOLD_CYCLES = 20

BASE_SIZE_PCT = 0.38
MAX_SIZE_PCT = 0.72
MIN_SIZE_PCT = 0.18
POLL_SECONDS = 20
TARGET_MULTIPLIER = 10.0

position_state: dict[str, dict[str, Any]] = {}
win_streak = 0
loss_streak = 0
cycle_count = 0


def get_account_summary() -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/summary",
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[ACCOUNT] {response.status_code}: {response.text[:120]}")
        return {}
    return response.json().get("account", {})


def get_open_positions() -> list[dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/openPositions",
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        return []
    return response.json().get("positions", [])


def get_price(pair: str) -> dict[str, float] | None:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/pricing",
        headers=HEADERS,
        params={"instruments": pair},
        timeout=10,
    )
    if response.status_code != 200:
        return None

    payload = response.json().get("prices", [])
    if not payload:
        return None

    try:
        bid = float(payload[0]["bids"][0]["price"])
        ask = float(payload[0]["asks"][0]["price"])
        return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def get_candles(pair: str, count: int = MAX_CANDLES) -> list[dict[str, float]]:
    response = requests.get(
        f"{BASE_URL}/instruments/{pair}/candles",
        headers=HEADERS,
        params={"granularity": TIMEFRAME, "count": count, "price": "M"},
        timeout=10,
    )
    if response.status_code != 200:
        return []

    candles: list[dict[str, float]] = []
    for candle in response.json().get("candles", []):
        if not candle.get("complete"):
            continue
        try:
            candles.append(
                {
                    "h": float(candle["mid"]["h"]),
                    "l": float(candle["mid"]["l"]),
                    "c": float(candle["mid"]["c"]),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return candles


def analyze_pair(pair: str, candles: list[dict[str, float]]) -> dict[str, Any] | None:
    closes = [c["c"] for c in candles]
    if len(closes) < LOOKBACK_WINDOW + 3:
        return None

    window = closes[-LOOKBACK_WINDOW:]
    mean = sum(window) / len(window)
    std = statistics.pstdev(window)
    if std <= 0:
        return None

    latest = closes[-1]
    prior = closes[-2]
    three_back = closes[-3]
    z_score = (latest - mean) / std
    rebound_1m = (latest - prior) / prior * 100
    rebound_3m = (latest - three_back) / three_back * 100
    atr = statistics.mean(c["h"] - c["l"] for c in candles[-LOOKBACK_WINDOW:])
    atr_pct = atr / latest if latest else 0.0

    return {
        "pair": pair,
        "latest": latest,
        "mean": mean,
        "z": z_score,
        "rebound_1m": rebound_1m,
        "rebound_3m": rebound_3m,
        "atr_pct": atr_pct,
    }


def detect_signal(pair: str) -> dict[str, Any] | None:
    candles = get_candles(pair)
    metrics = analyze_pair(pair, candles)
    if not metrics:
        return None

    # Oversold snapback -> long
    if metrics["z"] <= -OVEREXTENSION_Z and metrics["rebound_1m"] >= REBOUND_THRESHOLD:
        metrics["side"] = "long"
        metrics["strength"] = min(1.5, abs(metrics["z"]) / 2.0 + metrics["rebound_3m"] * 1.5)
        return metrics

    # Overbought fade -> short
    if metrics["z"] >= OVEREXTENSION_Z and metrics["rebound_1m"] <= -REBOUND_THRESHOLD:
        metrics["side"] = "short"
        metrics["strength"] = min(1.5, abs(metrics["z"]) / 2.0 + abs(metrics["rebound_3m"]) * 1.5)
        return metrics

    return None


def best_signal() -> dict[str, Any] | None:
    chosen = None
    best_score = 0.0
    for pair in PAIRS:
        signal = detect_signal(pair)
        if not signal:
            continue
        score = signal["strength"] * (1 + signal["atr_pct"] * 80)
        if score > best_score:
            chosen = signal
            best_score = score
    return chosen if chosen and best_score >= 0.9 else None


def allocation_pct() -> float:
    pct = BASE_SIZE_PCT
    if win_streak:
        pct = min(MAX_SIZE_PCT, pct + 0.09 * win_streak)
    if loss_streak:
        pct = max(MIN_SIZE_PCT, pct - 0.10 * loss_streak)
    return pct


def place_order(pair: str, units: int, side: str) -> bool:
    signed_units = units if side == "long" else -units
    response = requests.post(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
        headers=HEADERS,
        json={
            "order": {
                "instrument": pair,
                "units": str(signed_units),
                "type": "MARKET",
                "timeInForce": "FOK",
            }
        },
        timeout=10,
    )
    if response.status_code not in (200, 201):
        print(f"[ORDER FAIL] {pair} {side} {units}: {response.status_code} {response.text[:120]}")
        return False
    return True


def close_position(pair: str, side: str) -> bool:
    payload = {"longUnits": "ALL"} if side == "long" else {"shortUnits": "ALL"}
    response = requests.put(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/positions/{pair}/close",
        headers=HEADERS,
        json=payload,
        timeout=10,
    )
    if response.status_code not in (200, 201):
        print(f"[CLOSE FAIL] {pair}: {response.status_code} {response.text[:120]}")
        return False
    return True


def manage_open_positions() -> None:
    global win_streak, loss_streak

    for position in get_open_positions():
        pair = position.get("instrument")
        long_units = int(float(position.get("long", {}).get("units", 0)))
        short_units = int(float(position.get("short", {}).get("units", 0)))

        if long_units > 0:
            side = "long"
            entry = float(position.get("long", {}).get("averagePrice", 0))
            units = long_units
        elif short_units < 0:
            side = "short"
            entry = float(position.get("short", {}).get("averagePrice", 0))
            units = abs(short_units)
        else:
            continue

        price = get_price(pair)
        if not price:
            continue

        current = price["mid"]
        state = position_state.setdefault(
            pair,
            {"side": side, "entry_cycle": cycle_count, "best": current},
        )

        if side == "long":
            pnl_pct = (current - entry) / entry
            state["best"] = max(state["best"], current)
            trail_hit = pnl_pct >= TRAIL_TRIGGER and current < state["best"] * (1 - TRAIL_DROP)
        else:
            pnl_pct = (entry - current) / entry
            state["best"] = min(state["best"], current)
            trail_hit = pnl_pct >= TRAIL_TRIGGER and current > state["best"] * (1 + TRAIL_DROP)

        signal = detect_signal(pair)
        mean_reverted = signal is None

        exit_reason = None
        if pnl_pct >= TP_PCT:
            exit_reason = f"TP {pnl_pct*100:.2f}%"
        elif pnl_pct <= -SL_PCT:
            exit_reason = f"SL {pnl_pct*100:.2f}%"
        elif trail_hit:
            exit_reason = "TRAIL"
        elif cycle_count - state["entry_cycle"] >= MAX_HOLD_CYCLES and mean_reverted:
            exit_reason = "TIME/MEAN"

        if exit_reason and close_position(pair, side):
            print(f"[EXIT] {pair} {side} {units}u -> {exit_reason}")
            if pnl_pct > 0:
                win_streak += 1
                loss_streak = 0
            else:
                loss_streak += 1
                win_streak = 0
            position_state.pop(pair, None)


def maybe_open_trade(account: dict[str, Any]) -> None:
    existing_positions = get_open_positions()
    if any(int(float(pos.get("long", {}).get("units", 0))) != 0 or int(float(pos.get("short", {}).get("units", 0))) != 0 for pos in existing_positions):
        return

    signal = best_signal()
    if not signal:
        return

    balance = float(account.get("NAV", account.get("balance", 0)))
    if balance <= 0:
        return

    notional = balance * allocation_pct() * min(1.35, signal["strength"])
    units = int(max(1, notional / signal["latest"]))
    if place_order(signal["pair"], units, signal["side"]):
        position_state[signal["pair"]] = {
            "side": signal["side"],
            "entry_cycle": cycle_count,
            "best": signal["latest"],
        }
        print(
            f"[ENTRY] {signal['pair']} {signal['side']} {units}u "
            f"z={signal['z']:.2f} rebound={signal['rebound_1m']:.3f}%"
        )


def main() -> None:
    global cycle_count

    account = get_account_summary()
    if not account:
        raise RuntimeError("Unable to load OANDA account summary")

    start_nav = float(account.get("NAV", account.get("balance", 0)))
    print("=" * 72)
    print("SPARK-2 OANDA MEAN-REVERSION BOT")
    print(f"Start NAV: ${start_nav:.2f} | Target: ${start_nav * TARGET_MULTIPLIER:.2f}")
    print("=" * 72)

    while True:
        cycle_count += 1
        account = get_account_summary()
        nav = float(account.get("NAV", account.get("balance", 0)))
        now = datetime.now().strftime("%H:%M:%S")

        manage_open_positions()
        maybe_open_trade(account)

        print(
            f"[{now}] cycle={cycle_count} nav=${nav:.2f} "
            f"w_streak={win_streak} l_streak={loss_streak}"
        )

        if nav >= start_nav * TARGET_MULTIPLIER:
            print(f"[TARGET HIT] NAV reached ${nav:.2f}")
            break

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
