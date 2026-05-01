"""Spark-3 OANDA micro-momentum scalper for fast compounding.

Core idea:
- Scan a small pair universe every cycle with 1-minute candles.
- Build a micro-momentum score from EMA slope + bar expansion.
- Enter only one active trade, sized aggressively relative to current NAV.
- Use strict TP/SL plus an adaptive trailing stop once in profit.
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

PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD"]
TIMEFRAME = "M1"
TIMEOUT_SECONDS = 10

EMA_FAST = 6
EMA_MID = 14
EMA_SLOW = 30
MOM_LAG = 4

BASE_SIZE_PCT = 0.45
MAX_SIZE_PCT = 0.82
MIN_SIZE_PCT = 0.22
WIN_UPSIZE = 0.08
LOSS_DOWN = 0.06

TP_BASE = 0.0021
SL_BASE = 0.0016
TRAIL_TRIGGER = 0.0010
TRAIL_DROP = 0.0006

POLL_SECONDS = 12
CYCLE_TARGET = 10.0


def get_summary() -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/summary",
        headers=HEADERS,
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"[OANDA] summary failed: {response.status_code} {response.text[:140]}")
        return {}
    return response.json().get("account", {})


def get_open_positions() -> list[dict]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/openPositions",
        headers=HEADERS,
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"[OANDA] positions failed: {response.status_code} {response.text[:140]}")
        return []
    return response.json().get("positions", [])


def get_price(pair: str) -> dict[str, float] | None:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/pricing",
        headers=HEADERS,
        params={"instruments": pair},
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        return None

    payload = response.json().get("prices", [])
    if not payload:
        return None

    try:
        p = payload[0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def get_candles(pair: str, count: int = 120) -> list[dict[str, float]]:
    response = requests.get(
        f"{BASE_URL}/instruments/{pair}/candles",
        headers=HEADERS,
        params={"granularity": TIMEFRAME, "count": count, "price": "M"},
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        return []

    raw = response.json().get("candles", [])
    out = []
    for candle in raw:
        if not candle.get("complete"):
            continue
        try:
            out.append(
                {
                    "h": float(candle["mid"]["h"]),
                    "l": float(candle["mid"]["l"]),
                    "c": float(candle["mid"]["c"]),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return out


def ema(values: list[float], period: int) -> list[float] | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    seq = [seed]
    for value in values[period:]:
        seq.append(value * k + seq[-1] * (1 - k))
    return seq


def compute_pair_signal(pair: str, win_streak: int, loss_streak: int, nav: float) -> dict[str, Any] | None:
    candles = get_candles(pair)
    if len(candles) < EMA_SLOW + MOM_LAG + 2:
        return None

    closes = [c["c"] for c in candles]
    ema_fast = ema(closes, EMA_FAST)
    ema_mid = ema(closes, EMA_MID)
    ema_slow = ema(closes, EMA_SLOW)
    if ema_fast is None or ema_mid is None or ema_slow is None:
        return None

    ranges = [c["h"] - c["l"] for c in candles]
    atr_fast = statistics.mean(ranges[-EMA_FAST:])
    atr_slow = statistics.mean(ranges[-EMA_SLOW:])
    if atr_slow <= 0:
        return None

    price = closes[-1]
    prior_price = closes[-MOM_LAG - 1]
    momentum = (price - prior_price) / prior_price
    vol_ratio = atr_fast / atr_slow
    shape = abs(momentum) * vol_ratio

    if shape < 0.0002 or vol_ratio < 1.02:
        return None

    if ema_fast[-1] > ema_mid[-1] > ema_slow[-1] and momentum > 0:
        side = "long"
    elif ema_fast[-1] < ema_mid[-1] < ema_slow[-1] and momentum < 0:
        side = "short"
    else:
        return None

    size_pct = BASE_SIZE_PCT + min(0.30, win_streak * WIN_UPSIZE)
    if loss_streak:
        size_pct = max(MIN_SIZE_PCT, size_pct - loss_streak * LOSS_DOWN)
    size_pct = min(MAX_SIZE_PCT, size_pct)

    quality = min(1.5, 1.0 + shape * 900)
    notional = nav * size_pct * quality

    tp = max(TP_BASE, 0.0013 * (1 + vol_ratio))
    sl = min(0.0034, SL_BASE * max(1.0, vol_ratio))
    return {
        "pair": pair,
        "side": side,
        "price": price,
        "size_pct": size_pct,
        "notional": notional,
        "tp_pct": tp,
        "sl_pct": sl,
        "quality": quality,
        "vol_ratio": vol_ratio,
    }


def best_signal(win_streak: int, loss_streak: int, nav: float) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for pair in PAIRS:
        signal = compute_pair_signal(pair, win_streak, loss_streak, nav)
        if not signal:
            continue
        # Score uses directional edge and expansion strength.
        score = signal["quality"] * signal["vol_ratio"] * 1000
        if score > best_score:
            best = signal
            best_score = score
    return best if best and best_score >= 1.6 else None


def place_order(pair: str, units: int, side: str) -> bool:
    body_units = str(units if side == "long" else -units)
    response = requests.post(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders",
        headers=HEADERS,
        json={
            "order": {
                "instrument": pair,
                "units": body_units,
                "type": "MARKET",
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        },
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code not in (200, 201):
        print(f"[OANDA] order failed {pair} {side} {units}: {response.status_code}")
        print(response.text[:160])
        return False
    return True


def close_position(pair: str, side: str) -> bool:
    payload = {"longUnits": "ALL"} if side == "long" else {"shortUnits": "ALL"}
    response = requests.put(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/positions/{pair}/close",
        headers=HEADERS,
        json=payload,
        timeout=TIMEOUT_SECONDS,
    )
    if response.status_code not in (200, 201):
        print(f"[OANDA] close failed {pair}: {response.status_code}")
        return False
    return True


def parse_position(p: dict[str, Any]) -> tuple[str, float, float] | None:
    long_units = float(p.get("long", {}).get("units", 0))
    short_units = float(p.get("short", {}).get("units", 0))
    if long_units > 0:
        return "long", long_units, float(p.get("long", {}).get("averagePrice", 0))
    if short_units < 0:
        return "short", abs(short_units), float(p.get("short", {}).get("averagePrice", 0))
    return None


def main() -> None:
    print("=" * 84)
    print("SPARK-3 OANDA MICRO-MOMENTUM SCALPER")
    print("=" * 84)

    start = get_summary()
    start_nav = float(start.get("NAV", 0))
    if start_nav <= 0:
        raise RuntimeError("Could not load starting NAV from OANDA summary.")

    wins = 0
    losses = 0
    closed_trades = 0

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        account = get_summary()
        if not account:
            time.sleep(POLL_SECONDS)
            continue

        nav = float(account.get("NAV", 0))
        balance = float(account.get("balance", 0))
        positions = get_open_positions()
        active = next((p for p in positions if p.get("instrument") in PAIRS), None)

        if active:
            parsed = parse_position(active)
            if parsed:
                side, units, entry = parsed
                pair = active["instrument"]
                price = get_price(pair)
                if price:
                    mid = price["mid"]
                    pnl_pct = (mid - entry) / entry if side == "long" else (entry - mid) / entry
                    signal = best_signal(wins, losses, nav)
                    tp_pct = signal["tp_pct"] if signal else TP_BASE
                    sl_pct = signal["sl_pct"] if signal else SL_BASE

                    trailing = pnl_pct > TRAIL_TRIGGER and pnl_pct < TRAIL_TRIGGER - TRAIL_DROP
                    hit_tp = pnl_pct >= tp_pct
                    hit_sl = pnl_pct <= -sl_pct

                    print(
                        f"[{now}] OPEN {pair} {side} units={units:.0f} entry={entry:.5f} "
                        f"mid={mid:.5f} pnl={pnl_pct*100:.3f}% tp={tp_pct*100:.2f}% sl={sl_pct*100:.2f}%"
                    )

                    if hit_tp or hit_sl or trailing:
                        reason = "TP" if hit_tp else "SL" if hit_sl else "TRAIL"
                        if close_position(pair, side):
                            closed_trades += 1
                            if hit_tp:
                                wins += 1
                            else:
                                losses += 1
                            print(f"[{now}] EXIT {reason} {pair} {side} pnl={pnl_pct*100:.2f}%")
                else:
                    print(f"[{now}] missing mid price for {pair}, skipping management step")

            time.sleep(POLL_SECONDS)
            continue

        signal = best_signal(wins, losses, nav)
        if not signal:
            if closed_trades:
                print(
                    f"[{now}] HOLD | NAV={nav:.2f} w/l={wins}/{losses} "
                    f"mult={nav/start_nav:.2f}x"
                )
            else:
                print(f"[{now}] HOLD | NAV={nav:.2f} waiting for momentum")
            time.sleep(POLL_SECONDS)
            continue

        pair = signal["pair"]
        units = int(signal["notional"] / signal["price"])
        if units < 1:
            print(f"[{now}] UNIT too small for {pair}, skip")
            time.sleep(POLL_SECONDS)
            continue

        if place_order(pair, units, signal["side"]):
            print(
                f"[{now}] ENTRY {signal['side'].upper()} {pair} units={units} entry={signal['price']:.5f} "
                f"size={signal['notional']:.2f} tp={signal['tp_pct']*100:.2f}% sl={signal['sl_pct']*100:.2f}% "
                f"quality={signal['quality']:.2f} vol_ratio={signal['vol_ratio']:.2f}"
            )

        mult = nav / start_nav
        if mult >= CYCLE_TARGET:
            print("=" * 84)
            print(f"TARGET REACHED: {mult:.2f}x NAV {nav:.2f}")
            print("=" * 84)
            break

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
