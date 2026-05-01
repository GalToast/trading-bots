"""Spark 1 - OANDA breakout bot with volatility expansion.

Strategy:
- Detect breakouts on forex candles using a rolling channel.
- Require volatility expansion before opening a new position.
- Scale position size dynamically with breakout strength and expand/shrink risk by signal quality.
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

LOOKBACK_BREAKOUT = 24
LOOKBACK_VOL_SHORT = 8
LOOKBACK_VOL_BASE = 24
MOMENTUM_LAG = 6
POLL_SECONDS = 20


def get_account_summary() -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/summary",
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[OANDA] account summary failed: {response.status_code} {response.text[:140]}")
        return {}
    return response.json().get("account", {})


def get_open_positions() -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/openPositions",
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[OANDA] open positions failed: {response.status_code}")
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

    pair_price = payload[0]
    try:
        bid = float(pair_price["bids"][0]["price"])
        ask = float(pair_price["asks"][0]["price"])
        return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def get_candles(pair: str, count: int = 90) -> list[dict[str, float]]:
    response = requests.get(
        f"{BASE_URL}/instruments/{pair}/candles",
        headers=HEADERS,
        params={"granularity": TIMEFRAME, "count": count, "price": "M"},
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[OANDA] candles failed for {pair}: {response.status_code}")
        return []

    raw = response.json().get("candles", [])
    candles = []
    for c in raw:
        if not c.get("complete"):
            continue
        try:
            candles.append(
                {
                    "h": float(c["mid"]["h"]),
                    "l": float(c["mid"]["l"]),
                    "c": float(c["mid"]["c"]),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return candles


def place_order(pair: str, units: int, side: str) -> bool:
    amount = str(units if side == "sell" else units)
    response = requests.post(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
        headers=HEADERS,
        json={"order": {"instrument": pair, "units": amount, "type": "MARKET", "timeInForce": "FOK"}},
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
        print(f"[OANDA] close failed {pair}: {response.status_code} {response.text[:120]}")
        return False
    return True


def compute_signal(pair: str, candles: list[dict[str, float]]) -> dict[str, Any] | None:
    if len(candles) < LOOKBACK_VOL_BASE + 2:
        return None

    closes = [c["c"] for c in candles]
    prior = candles[:-1]
    if len(prior) < LOOKBACK_BREAKOUT + 1:
        return None

    current = closes[-1]
    channel = prior[-LOOKBACK_BREAKOUT:]
    channel_hi = max(c["h"] for c in channel)
    channel_lo = min(c["l"] for c in channel)
    short_window = prior[-LOOKBACK_VOL_SHORT:]
    base_window = prior[-LOOKBACK_VOL_BASE:]

    atr_short = statistics.mean(c["h"] - c["l"] for c in short_window)
    atr_base = statistics.mean(c["h"] - c["l"] for c in base_window)
    if atr_base <= 0:
        return None

    vol_expansion = atr_short / atr_base
    if vol_expansion < 1.2:
        return None

    momentum = (current - closes[-MOMENTUM_LAG]) / closes[-MOMENTUM_LAG]
    atr_pct = atr_short / current

    if current > channel_hi and momentum > 0.0004:
        return {
            "pair": pair,
            "side": "long",
            "entry": current,
            "size_pct": min(0.55, 0.22 + min(0.20, momentum * 60)),
            "tp_pct": max(0.0018, atr_pct * 1.8),
            "sl_pct": max(0.0010, atr_pct * 1.0),
            "vol_expansion": vol_expansion,
            "momentum": momentum,
            "atr_pct": atr_pct,
            "channel": (channel_lo, channel_hi),
        }

    if current < channel_lo and momentum < -0.0004:
        return {
            "pair": pair,
            "side": "short",
            "entry": current,
            "size_pct": min(0.55, 0.24 + min(0.18, abs(momentum) * 60)),
            "tp_pct": max(0.0018, atr_pct * 1.7),
            "sl_pct": max(0.0010, atr_pct * 1.0),
            "vol_expansion": vol_expansion,
            "momentum": momentum,
            "atr_pct": atr_pct,
            "channel": (channel_lo, channel_hi),
        }

    return None


def detect_best_signal() -> dict[str, Any] | None:
    best_signal = None
    best_score = 0.0
    for pair in PAIRS:
        candles = get_candles(pair)
        signal = compute_signal(pair, candles)
        if not signal:
            continue

        score = abs(signal["momentum"]) * signal["vol_expansion"]
        if score > best_score:
            best_score = score
            best_signal = signal

    if best_signal and best_score > 0.0008:
        return best_signal
    return None


def parse_position(position: dict[str, Any]) -> tuple[str, float, float] | None:
    long_units = float(position.get("long", {}).get("units", 0))
    short_units = float(position.get("short", {}).get("units", 0))
    if long_units > 0:
        return "long", long_units, float(position.get("long", {}).get("averagePrice", 0))
    if short_units < 0:
        return "short", abs(short_units), float(position.get("short", {}).get("averagePrice", 0))
    return None


def main() -> None:
    print("=" * 84)
    print("SPARK 1 OANDA - BREAKOUT + VOLATILITY EXPANSION BOT")
    print("=" * 84)

    closed_trades = 0
    wins = 0
    losses = 0

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        account = get_account_summary()
        balance = float(account.get("balance", 50.0))
        open_positions = get_open_positions()

        if open_positions:
            # Manage one active position at a time; if multiple exist, handle the first only.
            active = next((p for p in open_positions if p["instrument"] in PAIRS), None)
            if active:
                parsed = parse_position(active)
                if parsed:
                    side, units, entry = parsed
                    pair = active["instrument"]
                    price = get_price(pair)
                    if price:
                        pnl_pct = (price["mid"] - entry) / entry if side == "long" else (entry - price["mid"]) / entry
                        signal = detect_best_signal()
                        if signal:
                            tp_pct = signal["tp_pct"]
                            sl_pct = signal["sl_pct"]
                        else:
                            tp_pct = 0.0015
                            sl_pct = 0.0010

                        print(
                            f"[{now}] OPEN {pair} {side} units={units:.0f} "
                            f"entry={entry:.5f} mid={price['mid']:.5f} pnl={pnl_pct*100:.3f}%"
                        )

                        if pnl_pct >= tp_pct:
                            if close_position(pair, side):
                                wins += 1
                                closed_trades += 1
                                print(f"[{now}] TP HIT -> closed {pair} {side}")
                        elif pnl_pct <= -sl_pct:
                            if close_position(pair, side):
                                losses += 1
                                closed_trades += 1
                                print(f"[{now}] SL HIT -> closed {pair} {side}")
                time.sleep(POLL_SECONDS)
                continue

        signal = detect_best_signal()
        if not signal:
            print(f"[{now}] HOLD | No expansion breakout. balance=${balance:.2f}")
            if closed_trades:
                win_rate = (wins / closed_trades) * 100
                print(f"[{now}] closed_trades={closed_trades} win_rate={win_rate:.1f}%")
            time.sleep(POLL_SECONDS)
            continue

        if balance < 10:
            print(f"[{now}] HOLD | balance too low (${balance:.2f}) for practical units.")
            time.sleep(POLL_SECONDS)
            continue

        alloc = min(balance * signal["size_pct"], balance * 0.9)
        pair = signal["pair"]
        if signal["side"] == "long":
            units = int(alloc / signal["entry"])
            if units < 1:
                print(f"[{now}] unit calc below minimum on {pair}, skipping")
                time.sleep(POLL_SECONDS)
                continue
            if place_order(pair, units, "buy"):
                print(
                    f"[{now}] ENTRY LONG {pair} | units={units} entry={signal['entry']:.5f} "
                    f"size={alloc:.2f} vol_exp={signal['vol_expansion']:.2f} mom={signal['momentum']*100:.3f}% "
                    f"channel={signal['channel'][0]:.5f}-{signal['channel'][1]:.5f}"
                )
        else:
            units = int(alloc / signal["entry"])
            if units < 1:
                print(f"[{now}] unit calc below minimum on {pair}, skipping")
                time.sleep(POLL_SECONDS)
                continue
            if place_order(pair, -units, "sell"):
                print(
                    f"[{now}] ENTRY SHORT {pair} | units={units} entry={signal['entry']:.5f} "
                    f"size={alloc:.2f} vol_exp={signal['vol_expansion']:.2f} mom={signal['momentum']*100:.3f}% "
                    f"channel={signal['channel'][0]:.5f}-{signal['channel'][1]:.5f}"
                )

        if closed_trades:
            win_rate = (wins / closed_trades) * 100
            print(f"[{now}] closed_trades={closed_trades} win_rate={win_rate:.1f}%")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
