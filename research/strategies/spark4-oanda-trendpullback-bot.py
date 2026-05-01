"""Spark 4 - OANDA trend pullback continuation bot."""

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

PAIRS = ["EUR_USD", "USD_JPY", "GBP_USD", "AUD_USD", "USD_CAD"]
TIMEFRAME = "M1"
LOOKBACK = 140
EMA_FAST = 6
EMA_SLOW = 20
VOL_FAST = 8
VOL_BASE = 22
POLL_SECONDS = 20
ARM_MAX_AGE = 14
MAX_ALLOC = 0.88
MIN_ALLOC = 0.30


STATE: dict[str, dict[str, Any]] = {
    pair: {"armed": False, "side": None, "trigger": None, "arm_index": -1} for pair in PAIRS
}


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    avg: float | None = None
    for value in values[-period:]:
        if avg is None:
            avg = value
        else:
            avg = value * alpha + avg * (1 - alpha)
    return avg


def get_account_summary() -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/summary",
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[OANDA] account failed: {response.status_code} {response.text[:120]}")
        return {}
    return response.json().get("account", {})


def get_open_positions() -> list[dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/openPositions",
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[OANDA] openPositions failed: {response.status_code}")
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
    prices = response.json().get("prices", [])
    if not prices:
        return None
    try:
        bid = float(prices[0]["bids"][0]["price"])
        ask = float(prices[0]["asks"][0]["price"])
        return {"mid": (bid + ask) / 2}
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def get_candles(pair: str, count: int = LOOKBACK) -> list[dict[str, float]]:
    response = requests.get(
        f"{BASE_URL}/instruments/{pair}/candles",
        headers=HEADERS,
        params={"granularity": TIMEFRAME, "count": count, "price": "M"},
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[OANDA] candles failed {pair}: {response.status_code}")
        return []

    candles = response.json().get("candles", [])
    out: list[dict[str, float]] = []
    for candle in candles:
        if not candle.get("complete"):
            continue
        try:
            out.append(
                {
                    "h": float(candle["mid"]["h"]),
                    "l": float(candle["mid"]["l"]),
                    "o": float(candle["mid"]["o"]),
                    "c": float(candle["mid"]["c"]),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return out


def place_order(pair: str, units: int, side: str) -> bool:
    amount = units if side == "buy" else -units
    response = requests.post(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
        headers=HEADERS,
        json={"order": {"instrument": pair, "units": str(amount), "type": "MARKET", "timeInForce": "FOK"}},
        timeout=10,
    )
    if response.status_code not in (200, 201):
        print(f"[OANDA] order fail {pair} {side} {units}: {response.status_code} {response.text[:140]}")
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
        print(f"[OANDA] close fail {pair}: {response.status_code} {response.text[:120]}")
        return False
    return True


def reset_state(pair: str) -> None:
    STATE[pair].update({"armed": False, "side": None, "trigger": None, "arm_index": -1})


def detect_signal(pair: str, bars: list[dict[str, float]], idx: int) -> dict[str, Any] | None:
    if len(bars) < EMA_SLOW + VOL_BASE + 5:
        return None

    s = STATE[pair]
    closes = [bar["c"] for bar in bars]
    current = bars[-1]
    prior = bars[:-1]

    fast = ema(closes, EMA_FAST)
    slow = ema(closes, EMA_SLOW)
    if fast is None or slow is None:
        return None

    atr_fast = statistics.mean(c["h"] - c["l"] for c in prior[-VOL_FAST:])
    atr_base = statistics.mean(c["h"] - c["l"] for c in prior[-VOL_BASE:])
    if atr_base <= 0:
        return None
    vol_ratio = atr_fast / atr_base
    if vol_ratio < 1.04:
        return None

    pullback_window = max(0.0012, min(0.007, (atr_fast / current["c"]) * 1.35))
    trend_delta = (fast - slow) / slow

    if trend_delta > 0.00015:
        trend = "long"
    elif trend_delta < -0.00015:
        trend = "short"
    else:
        reset_state(pair)
        return None

    if not s["armed"]:
        if (
            trend == "long"
            and current["c"] < fast
            and current["c"] > fast * (1 - pullback_window)
            and current["c"] < current["o"]
        ):
            s.update({"armed": True, "side": "long", "trigger": current["c"], "arm_index": idx})
            return None
        if (
            trend == "short"
            and current["c"] > fast
            and current["c"] < fast * (1 + pullback_window)
            and current["c"] < current["o"]
        ):
            s.update({"armed": True, "side": "short", "trigger": current["c"], "arm_index": idx})
            return None

    if s["armed"]:
        if s["side"] != trend:
            reset_state(pair)
            return None
        if idx - s["arm_index"] > ARM_MAX_AGE:
            reset_state(pair)
            return None

        if trend == "long":
            cont = current["c"] > max(s["trigger"], fast) and current["c"] > current["o"]
            if current["c"] < fast * (1 - pullback_window * 1.2):
                reset_state(pair)
                return None
        else:
            cont = current["c"] < min(s["trigger"], fast) and current["c"] < current["o"]
            if current["c"] > fast * (1 + pullback_window * 1.2):
                reset_state(pair)
                return None

        if idx > s["arm_index"] and cont:
            atr_pct = atr_fast / current["c"]
            score = max(0.25, min(0.9, abs(trend_delta) * 280 + (vol_ratio - 1) * 2.0))
            alloc = max(MIN_ALLOC, min(MAX_ALLOC, 0.35 + score * 0.45))
            signal = {
                "pair": pair,
                "side": trend,
                "entry": current["c"],
                "alloc": alloc,
                "tp": max(0.0022, atr_pct * 2.4),
                "sl": max(0.0011, atr_pct * 1.05),
                "vol_ratio": vol_ratio,
                "trend": trend,
            }
            reset_state(pair)
            return signal

    return None


def detect_best_signal() -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for pair in PAIRS:
        bars = get_candles(pair)
        if len(bars) < EMA_SLOW + VOL_BASE + 5:
            continue
        signal = detect_signal(pair, bars, len(bars) - 1)
        if not signal:
            continue
        score = signal["vol_ratio"] * (1.0 + signal["alloc"] * 0.35)
        if score > best_score:
            best_score = score
            best = signal
    if best and best_score >= 1.05:
        return best
    return None


def parse_position(position: dict[str, Any]) -> tuple[str, float, float] | None:
    long_units = float(position.get("long", {}).get("units", 0))
    short_units = float(position.get("short", {}).get("units", 0))
    if long_units > 0:
        return "long", long_units, float(position.get("long", {}).get("averagePrice", 0))
    if short_units < 0:
        return "short", abs(short_units), float(position.get("short", {}).get("averagePrice", 0))
    return None


def make_default_plan(entry: float) -> dict[str, float]:
    return {"tp": 0.0024, "sl": 0.0012}


def main() -> None:
    print("=" * 78)
    print("SPARK 4 OANDA - TREND PULLBACK CONTINUATION")
    print("=" * 78)

    active_plan: dict[str, Any] | None = None
    wins = 0
    losses = 0
    closed = 0

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        account = get_account_summary()
        balance = float(account.get("balance", 50.0) or 50.0)

        open_positions = get_open_positions()
        active = next((p for p in open_positions if p.get("instrument") in PAIRS), None)

        if active is not None:
            pair = active["instrument"]
            parsed = parse_position(active)
            if not parsed:
                time.sleep(POLL_SECONDS)
                continue
            side, units, entry = parsed
            if units <= 0:
                time.sleep(POLL_SECONDS)
                continue

            price = get_price(pair)
            if not price:
                time.sleep(POLL_SECONDS)
                continue
            pnl = (price["mid"] - entry) / entry if side == "long" else (entry - price["mid"]) / entry

            if active_plan is None:
                active_plan = {"pair": pair, "side": side, **make_default_plan(entry)}

            print(
                f"[{now}] IN TRADE {pair} {side.upper()} units={units:.0f} "
                f"entry={entry:.5f} price={price['mid']:.5f} pnl={pnl*100:.3f}%"
            )

            signal = detect_best_signal()
            if pnl >= float(active_plan["tp"]):
                if close_position(pair, side):
                    wins += 1
                    closed += 1
                    print(f"[{now}] TP HIT {pair} +{pnl*100:.2f}%")
                    active_plan = None
                time.sleep(POLL_SECONDS)
                continue

            if pnl <= -float(active_plan["sl"]):
                if close_position(pair, side):
                    losses += 1
                    closed += 1
                    print(f"[{now}] SL HIT {pair} {pnl*100:.2f}%")
                    active_plan = None
                time.sleep(POLL_SECONDS)
                continue

            if signal is not None and signal["side"] != side and pnl > 0.001:
                if close_position(pair, side):
                    active_plan = None
                    closed += 1
                    losses += 1
                    print(f"[{now}] trend-shift close {pair}")
                time.sleep(POLL_SECONDS)
                continue

            time.sleep(POLL_SECONDS)
            continue

        signal = detect_best_signal()
        if signal is None:
            if closed:
                print(f"[{now}] HOLD balance=${balance:.2f} win_rate={(wins/closed)*100:.1f}%")
            else:
                print(f"[{now}] HOLD balance=${balance:.2f}")
            time.sleep(POLL_SECONDS)
            continue

        if balance < 10:
            print(f"[{now}] HOLD low balance {balance:.2f}")
            time.sleep(POLL_SECONDS)
            continue

        pair = signal["pair"]
        allocation = max(MIN_ALLOC, min(signal["alloc"], MAX_ALLOC))
        notional = balance * allocation
        units = int(notional / signal["entry"])
        if units < 1:
            print(f"[{now}] unit floor too small on {pair}")
            time.sleep(POLL_SECONDS)
            continue

        if signal["side"] == "long":
            if place_order(pair, units, "buy"):
                active_plan = {"pair": pair, "side": "long", "tp": signal["tp"], "sl": signal["sl"]}
                print(
                    f"[{now}] ENTRY LONG {pair} units={units} entry={signal['entry']:.5f} alloc={allocation*100:.1f}% "
                    f"vol={signal['vol_ratio']:.2f} tp={signal['tp']*100:.2f}% sl={signal['sl']*100:.2f}%"
                )
        else:
            if place_order(pair, units, "sell"):
                active_plan = {"pair": pair, "side": "short", "tp": signal["tp"], "sl": signal["sl"]}
                print(
                    f"[{now}] ENTRY SHORT {pair} units={units} entry={signal['entry']:.5f} alloc={allocation*100:.1f}% "
                    f"vol={signal['vol_ratio']:.2f} tp={signal['tp']*100:.2f}% sl={signal['sl']*100:.2f}%"
                )

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
