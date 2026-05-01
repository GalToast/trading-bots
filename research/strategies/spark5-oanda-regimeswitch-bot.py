"""Spark 5 - Oanda regime-switch bot.

Core strategy:
- Evaluate multiple FX pairs on 1-minute candles.
- Classify each pair into trend, squeeze range, or transition regime.
- Enter only the strongest scored setup and scale notional with adaptive confidence,
  volatility regime and account momentum.
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

LOOKBACK = 230
EMA_FAST = 8
EMA_MID = 20
EMA_SLOW = 46
ATR_FAST = 8
ATR_BASE = 30
RSI_PERIOD = 14
CHANNEL_LOOKBACK = 18
RANGE_WINDOW = 34

BASE_RISK = {"trend": 0.56, "range": 0.32, "transition": 0.18}
TRADE_MIN_NOTIONAL = 8.0
MIN_SCORE = 0.42
POLL_SECONDS = 20

STATE: dict[str, Any] = {
    "wins": 0,
    "losses": 0,
    "active_plan": None,
    "balance_peak": 0.0,
}


def get_account_summary() -> dict[str, Any]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/summary",
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[OANDA] account failed: {response.status_code} {response.text[:140]}")
        return {}
    return response.json().get("account", {})


def get_open_positions() -> list[dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/accounts/{ACCOUNT_ID}/openPositions",
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[OANDA] openPositions failed: {response.status_code} {response.text[:140]}")
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
        print(f"[OANDA] order fail {pair} {side} {units}: {response.status_code} {response.text[:160]}")
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
        print(f"[OANDA] close fail {pair}: {response.status_code} {response.text[:160]}")
        return False
    return True


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    avg = None
    for value in values[-period:]:
        if avg is None:
            avg = value
            continue
        avg = value * alpha + avg * (1 - alpha)
    return avg


def calc_rsi(values: list[float], period: int = RSI_PERIOD) -> float | None:
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(-period, 0):
        delta = values[i] - values[i - 1]
        gains.append(max(0.0, delta))
        losses.append(max(0.0, -delta))
    avg_gain = statistics.mean(gains)
    avg_loss = statistics.mean(losses)
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def classify_regime(pair: str, bars: list[dict[str, float]]) -> dict[str, Any] | None:
    closes = [b["c"] for b in bars]
    current = closes[-1]
    fast = ema(closes, EMA_FAST)
    mid = ema(closes, EMA_MID)
    slow = ema(closes, EMA_SLOW)
    if fast is None or mid is None or slow is None:
        return None

    atr_fast = statistics.mean(b["h"] - b["l"] for b in bars[-ATR_FAST:])
    atr_base = statistics.mean(b["h"] - b["l"] for b in bars[-ATR_BASE:])
    if atr_base <= 0:
        return None

    atr_ratio = atr_fast / atr_base
    vol_pct = atr_fast / current
    trend_pressure = (fast - slow) / slow
    trend_conf = abs(trend_pressure) / max(vol_pct, 1e-9)
    recent_slice = closes[-RANGE_WINDOW:]
    ch_low = min(recent_slice)
    ch_high = max(recent_slice)
    ch_width = (ch_high - ch_low) / current
    rsi = calc_rsi(closes)
    drift = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] else 0.0

    if atr_ratio > 1.08 and trend_conf > 1.12 and abs(trend_pressure) > 0.00012:
        direction = "long" if trend_pressure > 0 else "short"
        channel = [b["c"] for b in bars[-CHANNEL_LOOKBACK:]]
        support = min(channel)
        resistance = max(channel)
        return {
            "pair": pair,
            "regime": "trend",
            "direction": direction,
            "conf": clamp(trend_conf / 2.3 + abs(trend_pressure) * 7000, 0.0, 1.0),
            "current": current,
            "atr_ratio": atr_ratio,
            "vol_pct": vol_pct,
            "trend_pressure": trend_pressure,
            "drift": drift,
            "support": support,
            "resistance": resistance,
            "rsi": rsi,
        }

    if atr_ratio < 0.95 and ch_width < 0.0069 and abs(drift) < 0.0011:
        return {
            "pair": pair,
            "regime": "range",
            "current": current,
            "atr_ratio": atr_ratio,
            "vol_pct": vol_pct,
            "channel": (ch_low, ch_high),
            "mid": (ch_low + ch_high) / 2.0,
            "rsi": rsi,
            "conf": max(0.22, min(0.85, (1.0 - atr_ratio) * 2.2)),
        }

    return {
        "pair": pair,
        "regime": "transition",
        "current": current,
        "conf": max(0.0, min(0.35, atr_ratio * 0.35)),
        "atr_ratio": atr_ratio,
        "vol_pct": vol_pct,
        "trend_pressure": trend_pressure,
    }


def regime_signal(regime: dict[str, Any], bars: list[dict[str, float]]) -> dict[str, Any] | None:
    if regime["regime"] == "transition":
        return None

    closes = [b["c"] for b in bars]
    current = regime["current"]

    if regime["regime"] == "trend":
        direction = regime["direction"]
        if direction == "long":
            ref = [b["c"] for b in bars[-CHANNEL_LOOKBACK:]]
            breakout = max(ref[:-1])
            pullback = closes[-2] < closes[-3]
            if pullback and current > breakout:
                tp = max(0.0023, regime["vol_pct"] * 2.7)
                sl = max(0.0011, regime["vol_pct"] * 1.2)
                conf = clamp(regime["conf"] * (1 + min(1.0, abs(regime["drift"]) * 220)), 0.0, 1.0)
                score = conf * (1 + abs(regime["trend_pressure"]) * 7000)
                return {
                    "pair": regime["pair"],
                    "regime": "trend",
                    "side": "long",
                    "entry": current,
                    "confidence": conf,
                    "score": score,
                    "tp": tp,
                    "sl": sl,
                    "vol_pct": regime["vol_pct"],
                    "atr_ratio": regime["atr_ratio"],
                }
        else:
            ref = [b["c"] for b in bars[-CHANNEL_LOOKBACK:]]
            breakout = min(ref[:-1])
            pullback = closes[-2] > closes[-3]
            if pullback and current < breakout:
                tp = max(0.0023, regime["vol_pct"] * 2.7)
                sl = max(0.0011, regime["vol_pct"] * 1.2)
                conf = clamp(regime["conf"] * (1 + min(1.0, abs(regime["drift"]) * 220)), 0.0, 1.0)
                score = conf * (1 + abs(regime["trend_pressure"]) * 7000)
                return {
                    "pair": regime["pair"],
                    "regime": "trend",
                    "side": "short",
                    "entry": current,
                    "confidence": conf,
                    "score": score,
                    "tp": tp,
                    "sl": sl,
                    "vol_pct": regime["vol_pct"],
                    "atr_ratio": regime["atr_ratio"],
                }
        return None

    channel_low, channel_high = regime["channel"]
    width = channel_high - channel_low
    if width <= 0:
        return None

    rsi = regime["rsi"]
    if rsi is None:
        return None

    band = width * 0.58
    if rsi >= 73 and current >= regime["mid"] + band:
        return {
            "pair": regime["pair"],
            "regime": "range",
            "side": "short",
            "entry": current,
            "confidence": regime["conf"] * ((rsi - 70) / 30),
            "score": regime["conf"] * abs(rsi - 50) / 50,
            "tp": max(0.0017, regime["vol_pct"] * 2.1),
            "sl": max(0.0009, regime["vol_pct"] * 1.05),
            "vol_pct": regime["vol_pct"],
            "atr_ratio": regime["atr_ratio"],
        }

    if rsi <= 27 and current <= regime["mid"] - band:
        return {
            "pair": regime["pair"],
            "regime": "range",
            "side": "long",
            "entry": current,
            "confidence": regime["conf"] * ((30 - rsi) / 30),
            "score": regime["conf"] * abs(rsi - 50) / 50,
            "tp": max(0.0017, regime["vol_pct"] * 2.1),
            "sl": max(0.0009, regime["vol_pct"] * 1.05),
            "vol_pct": regime["vol_pct"],
            "atr_ratio": regime["atr_ratio"],
        }

    return None


def select_signal() -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0

    for pair in PAIRS:
        bars = get_candles(pair)
        if not bars:
            continue

        regime = classify_regime(pair, bars)
        if not regime:
            continue

        signal = regime_signal(regime, bars)
        if not signal:
            continue

        multiplier = 1.0 if signal["regime"] == "trend" else 0.86
        weighted = signal["score"] * multiplier
        if weighted > best_score:
            best_score = weighted
            best = signal

    if best is None or best_score < MIN_SCORE:
        return None
    return best


def parse_position(position: dict[str, Any]) -> tuple[str, float, float] | None:
    long_units = float(position.get("long", {}).get("units", 0))
    short_units = float(position.get("short", {}).get("units", 0))
    if long_units > 0:
        return "long", long_units, float(position.get("long", {}).get("averagePrice", 0))
    if short_units < 0:
        return "short", abs(short_units), float(position.get("short", {}).get("averagePrice", 0))
    return None


def risk_size(balance: float, signal: dict[str, Any]) -> float:
    conf = clamp(signal["confidence"], 0.0, 1.0)
    base = BASE_RISK[signal["regime"]]

    streak_delta = STATE["wins"] - STATE["losses"]
    streak_adj = 1.0 + min(0.15, max(0.0, streak_delta) * 0.07)
    if streak_delta < -1:
        streak_adj = 0.66

    peak = STATE["balance_peak"] if STATE["balance_peak"] > 0 else balance
    if balance > peak:
        peak = balance
        STATE["balance_peak"] = peak
    drawdown = (peak - balance) / peak if peak > 0 else 0.0
    drawdown_adj = 1.0 - min(0.6, drawdown * 2.2)
    if drawdown > 0.18:
        drawdown_adj = max(0.38, drawdown_adj)

    vol_adj = 1.0
    if signal["regime"] == "trend":
        vol_adj = 1.0 + min(0.12, max(0.0, signal["atr_ratio"] - 1.0))
    else:
        vol_adj = max(0.7, min(1.05, 1.0 + (1.0 - signal["atr_ratio"]) * 0.3))

    size_pct = base * (0.5 + 0.5 * conf) * streak_adj * vol_adj * drawdown_adj
    return clamp(size_pct, 0.06, 0.80)


def make_plan(signal: dict[str, Any]) -> dict[str, Any]:
    if signal["regime"] == "trend":
        return {
            "pair": signal["pair"],
            "side": signal["side"],
            "regime": "trend",
            "tp": signal["tp"],
            "sl": signal["sl"],
            "trail": max(0.0009, signal["tp"] * 0.42),
            "strength": signal["score"],
        }
    return {
        "pair": signal["pair"],
        "side": signal["side"],
        "regime": "range",
        "tp": signal["tp"],
        "sl": signal["sl"],
        "trail": 0.0,
        "strength": signal["score"],
    }


def main() -> None:
    print("=" * 84)
    print("SPARK 5 OANDA - MULTI-PAIR REGIME SWITCH + ADAPTIVE RISK")
    print("=" * 84)

    wins = 0
    losses = 0
    closed = 0

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        account = get_account_summary()
        balance = float(account.get("balance", 50.0) or 50.0)

        if STATE["balance_peak"] <= 0:
            STATE["balance_peak"] = balance
        elif balance > STATE["balance_peak"]:
            STATE["balance_peak"] = balance

        open_positions = get_open_positions()
        active = next((position for position in open_positions if position.get("instrument") in PAIRS), None)
        signal = select_signal()

        if active is not None:
            pair = active["instrument"]
            parsed = parse_position(active)
            if parsed is None:
                time.sleep(POLL_SECONDS)
                continue
            side, units, entry = parsed
            if units <= 0:
                time.sleep(POLL_SECONDS)
                continue

            price = get_price(pair)
            if price is None:
                time.sleep(POLL_SECONDS)
                continue
            pnl_pct = (price["mid"] - entry) / entry if side == "long" else (entry - price["mid"]) / entry

            plan = STATE["active_plan"] or make_plan(
                {
                    "pair": pair,
                    "side": side,
                    "regime": "transition",
                    "tp": 0.0020,
                    "sl": 0.0010,
                    "score": 0.30,
                }
            )
            if plan["pair"] != pair:
                plan = make_plan(
                    {
                        "pair": pair,
                        "side": side,
                        "regime": "transition",
                        "tp": 0.0020,
                        "sl": 0.0010,
                        "score": 0.30,
                    }
                )

            print(
                f"[{now}] OPEN {pair} {side.upper()} units={units:.0f} entry={entry:.5f} mid={price['mid']:.5f} "
                f"pnl={pnl_pct*100:.3f}% tp={plan['tp']*100:.2f}% sl={plan['sl']*100:.2f}% regime={plan['regime']}"
            )

            if pnl_pct >= plan["tp"]:
                if close_position(pair, side):
                    STATE["active_plan"] = None
                    wins += 1
                    closed += 1
                    STATE["wins"] += 1
                    print(f"[{now}] TP HIT -> closed {pair} {side.upper()}")
                time.sleep(POLL_SECONDS)
                continue

            if pnl_pct <= -plan["sl"]:
                if close_position(pair, side):
                    STATE["active_plan"] = None
                    losses += 1
                    closed += 1
                    STATE["losses"] += 1
                    print(f"[{now}] SL HIT -> closed {pair} {side.upper()}")
                time.sleep(POLL_SECONDS)
                continue

            if signal and signal["regime"] == "trend" and signal["pair"] != pair and signal["score"] > plan["strength"] + 0.12 and pnl_pct > 0:
                if close_position(pair, side):
                    STATE["active_plan"] = None
                    print(
                        f"[{now}] SWITCH away from {pair} because stronger trend signal arrived on {signal['pair']} "
                        f"({signal['score']:.3f})"
                    )
                time.sleep(POLL_SECONDS)
                continue

            if plan["trail"] > 0 and pnl_pct >= plan["trail"] and signal and signal["regime"] != plan["regime"]:
                if close_position(pair, side):
                    STATE["active_plan"] = None
                    losses += 1
                    STATE["losses"] += 1
                    print(f"[{now}] regime shift close on {pair}")
            time.sleep(POLL_SECONDS)
            continue

        if not signal:
            print(f"[{now}] HOLD no strong signal | balance=${balance:.2f}")
            if closed:
                print(f"[{now}] closed={closed} win_rate={(wins / closed) * 100:.1f}%")
            time.sleep(POLL_SECONDS)
            continue

        notional = balance * risk_size(balance, signal)
        if notional < TRADE_MIN_NOTIONAL:
            print(f"[{now}] HOLD notional too small ${notional:.2f} for {signal['pair']} score={signal['score']:.3f}")
            time.sleep(POLL_SECONDS)
            continue

        pair = signal["pair"]
        units = int(notional / signal["entry"])
        if units < 1:
            print(f"[{now}] HOLD units floor < 1 for {pair}, signal score={signal['score']:.3f}")
            time.sleep(POLL_SECONDS)
            continue

        side = signal["side"]
        if place_order(pair, units, "buy" if side == "long" else "sell"):
            STATE["active_plan"] = make_plan(signal)
            print(
                f"[{now}] ENTRY {side.upper()} {pair} units={units} notional=${notional:.2f} "
                f"mode={signal['regime']} score={signal['score']:.3f} conf={signal['confidence']:.2f} "
                f"tp={signal['tp']*100:.2f}% sl={signal['sl']*100:.2f}%"
            )
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
