"""Spark 4 - Alpaca trend pullback continuation bot."""

from __future__ import annotations

import statistics
import time
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

SYMBOL = "BTCUSD"
DATA_SYMBOL = "BTC/USD"
TIMEFRAME = "1Min"

LOOKBACK = 150
EMA_FAST = 6
EMA_SLOW = 20
VOL_FAST = 7
VOL_BASE = 22

POLL_SECONDS = 20
ARM_MAX_AGE = 14
MAX_ALLOC = 0.88
MIN_ALLOC = 0.30


STATE = {
    "armed": False,
    "side": None,
    "trigger": None,
    "arm_index": -1,
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


def get_account() -> dict[str, Any]:
    response = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        print(f"[ALPACA] account failed: {response.status_code} {response.text[:120]}")
        return {}
    return response.json()


def get_positions() -> list[dict[str, str]]:
    response = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        print(f"[ALPACA] positions failed: {response.status_code}")
        return []
    return response.json()


def get_open_position() -> dict[str, str] | None:
    for position in get_positions():
        if position.get("symbol") == SYMBOL:
            return position
    return None


def get_bars() -> list[dict[str, float]]:
    response = requests.get(
        f"{DATA_URL}/bars",
        headers=HEADERS,
        params={"symbols": DATA_SYMBOL, "timeframe": TIMEFRAME, "limit": LOOKBACK},
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[ALPACA] bars failed: {response.status_code} {response.text[:140]}")
        return []

    raw = response.json().get("bars", {}).get(DATA_SYMBOL, [])
    bars: list[dict[str, float]] = []
    for bar in raw:
        try:
            bars.append(
                {
                    "h": float(bar["h"]),
                    "l": float(bar["l"]),
                    "o": float(bar["o"]),
                    "c": float(bar["c"]),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return bars


def place_market(side: str, qty: float) -> bool:
    response = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={
            "symbol": SYMBOL,
            "qty": f"{qty:.6f}",
            "side": side,
            "type": "market",
            "time_in_force": "ioc",
        },
        timeout=10,
    )
    if response.status_code not in (200, 201):
        print(f"[ALPACA] order fail {side} qty={qty:.6f}: {response.status_code} {response.text[:140]}")
        return False
    return True


def close_position(side: str, qty: float) -> bool:
    return place_market("sell" if side == "long" else "buy", qty)


def reset_state() -> None:
    STATE["armed"] = False
    STATE["side"] = None
    STATE["trigger"] = None
    STATE["arm_index"] = -1


def detect_signal(bars: list[dict[str, float]], idx: int) -> dict[str, Any] | None:
    if len(bars) < EMA_SLOW + VOL_BASE + 5:
        return None

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
    trend = None
    trend_delta = (fast - slow) / slow

    if trend_delta > 0.00015:
        trend = "long"
    elif trend_delta < -0.00015:
        trend = "short"
    else:
        reset_state()
        return None

    if not STATE["armed"]:
        if (
            trend == "long"
            and current["c"] < fast
            and current["c"] > fast * (1 - pullback_window)
            and current["c"] < current["o"]
        ):
            STATE["armed"] = True
            STATE["side"] = "long"
            STATE["trigger"] = current["c"]
            STATE["arm_index"] = idx
            return None

        if (
            trend == "short"
            and current["c"] > fast
            and current["c"] < fast * (1 + pullback_window)
            and current["c"] < current["o"]
        ):
            STATE["armed"] = True
            STATE["side"] = "short"
            STATE["trigger"] = current["c"]
            STATE["arm_index"] = idx
            return None

    if STATE["armed"]:
        if STATE["side"] != trend:
            reset_state()
            return None
        if idx - STATE["arm_index"] > ARM_MAX_AGE:
            reset_state()
            return None

        if trend == "long":
            cont = current["c"] > max(STATE["trigger"], fast) and current["c"] > current["o"]
            if current["c"] < fast * (1 - pullback_window * 1.2):
                reset_state()
                return None
        else:
            cont = current["c"] < min(STATE["trigger"], fast) and current["c"] < current["o"]
            if current["c"] > fast * (1 + pullback_window * 1.2):
                reset_state()
                return None

        if idx > STATE["arm_index"] and cont:
            atr_pct = atr_fast / current["c"]
            score = max(0.25, min(0.9, abs(trend_delta) * 280 + (vol_ratio - 1) * 2.0))
            alloc = max(MIN_ALLOC, min(MAX_ALLOC, 0.35 + score * 0.45))
            signal = {
                "side": trend,
                "entry": current["c"],
                "alloc": alloc,
                "tp": max(0.0022, atr_pct * 2.4),
                "sl": max(0.0011, atr_pct * 1.05),
                "vol_ratio": vol_ratio,
            }
            reset_state()
            return signal

    return None


def main() -> None:
    print("=" * 72)
    print("SPARK 4 ALPACA - TREND PULLBACK CONTINUATION")
    print("=" * 72)

    plan = None
    wins = 0
    losses = 0
    closed = 0

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        account = get_account()
        equity = float(account.get("equity", 50.0) or 50.0)
        cash = float(account.get("cash", equity) or equity)

        bars = get_bars()
        if len(bars) < EMA_SLOW + VOL_BASE + 5:
            print(f"[{now}] warm-up bars={len(bars)}")
            time.sleep(POLL_SECONDS)
            continue

        current = bars[-1]["c"]
        signal = detect_signal(bars, len(bars) - 1)
        position = get_open_position()

        if position:
            side = position.get("side", "long")
            qty = float(position.get("qty", 0))
            if qty <= 0:
                time.sleep(POLL_SECONDS)
                continue

            entry = float(position.get("avg_entry_price", current))
            pnl = (current - entry) / entry if side == "long" else (entry - current) / entry

            if plan is None:
                atr_pct = (bars[-1]["h"] - bars[-1]["l"]) / entry
                plan = {"side": side, "tp": max(0.0024, atr_pct * 2.2), "sl": max(0.0011, atr_pct * 1.05)}

            print(f"[{now}] IN TRADE {side.upper()} qty={qty:.6f} entry={entry:.2f} price={current:.2f} pnl={pnl * 100:.3f}%")

            if pnl >= plan["tp"]:
                if close_position(side, qty):
                    wins += 1
                    closed += 1
                    print(f"[{now}] TP HIT +{pnl*100:.2f}%")
                    plan = None
            elif pnl <= -plan["sl"]:
                if close_position(side, qty):
                    losses += 1
                    closed += 1
                    print(f"[{now}] SL HIT {pnl*100:.2f}%")
                    plan = None
            elif signal is not None and signal["side"] != side and pnl > 0.001:
                if close_position(side, qty):
                    closed += 1
                    losses += 1
                    print(f"[{now}] trend-shift close to switch")
                    plan = None

            time.sleep(POLL_SECONDS)
            continue

        if signal is None:
            if closed:
                print(f"[{now}] HOLD cash={cash:.2f} eq={equity:.2f} win_rate={(wins / closed) * 100:.1f}%")
            else:
                print(f"[{now}] HOLD cash={cash:.2f} eq={equity:.2f}")
            time.sleep(POLL_SECONDS)
            continue

        if equity < 12:
            print(f"[{now}] HOLD low equity ${equity:.2f}")
            time.sleep(POLL_SECONDS)
            continue

        alloc = max(MIN_ALLOC, min(signal["alloc"], MAX_ALLOC))
        notional = equity * alloc
        qty = notional / signal["entry"]
        if qty < 0.0002:
            print(f"[{now}] SKIP tiny qty {qty:.6f}")
            time.sleep(POLL_SECONDS)
            continue

        side = signal["side"]
        if place_market("buy" if side == "long" else "sell", qty):
            plan = {"side": side, "tp": signal["tp"], "sl": signal["sl"]}
            print(
                f"[{now}] ENTRY {side.upper()} qty={qty:.6f} entry={signal['entry']:.2f} alloc={alloc*100:.1f}% "
                f"vol={signal['vol_ratio']:.2f} tp={signal['tp']*100:.2f}% sl={signal['sl']*100:.2f}%"
            )

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
