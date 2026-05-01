"""Spark 5 - Alpaca regime-switch bot.

Core strategy:
- Classify live ETH/USD action into trend or range regimes from EMA stack + ATR regime.
- Trade trend breakouts only after pullback confirmation and reversion trades only in stable
  range conditions.
- Scale leverage allocation dynamically from regime confidence, regime volatility, account
  drawdown, and recent win/loss streak.
"""

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

SYMBOL = "ETHUSD"
DATA_SYMBOL = "ETH/USD"
TIMEFRAME = "1Min"

LOOKBACK = 180
EMA_FAST = 6
EMA_MID = 13
EMA_SLOW = 34
ATR_FAST = 5
ATR_BASE = 18
RSI_PERIOD = 14
CHANNEL_LOOKBACK = 12
RANGE_WINDOW = 24

BASE_RISK = {
    "trend": 0.98,
    "range": 0.85,
    "transition": 0.35,
}

MIN_QTY = 0.0002
MIN_ENTRY_EQ = 12.0
MIN_SIGNAL_SCORE = 0.08
POLL_SECONDS = 4
DUST_QTY = 1e-5
MIN_FILL_NOTIONAL = 5.0
MIN_POSITION_NOTIONAL = 1.0
MAX_ENTRY_SLIP_PCT = 0.0025
MAX_EXIT_SLIP_PCT = 0.0035

STATE: dict[str, Any] = {
    "wins": 0,
    "losses": 0,
    "active_plan": None,
    "equity_peak": 0.0,
}


def get_account() -> dict[str, Any]:
    response = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        print(f"[ALPACA] account failed: {response.status_code} {response.text[:140]}")
        return {}
    return response.json()


def get_positions() -> list[dict[str, Any]]:
    response = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        print(f"[ALPACA] positions failed: {response.status_code}")
        return []
    return response.json()


def get_bars(limit: int = LOOKBACK) -> list[dict[str, float]]:
    response = requests.get(
        f"{DATA_URL}/bars",
        headers=HEADERS,
        params={"symbols": DATA_SYMBOL, "timeframe": TIMEFRAME, "limit": limit},
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[ALPACA] bars failed: {response.status_code} {response.text[:140]}")
        return []

    rows = response.json().get("bars", {}).get(DATA_SYMBOL, [])
    bars: list[dict[str, float]] = []
    for row in rows:
        try:
            bars.append(
                {
                    "o": float(row["o"]),
                    "h": float(row["h"]),
                    "l": float(row["l"]),
                    "c": float(row["c"]),
                }
            )
        except (TypeError, ValueError, KeyError):
            continue
    return bars


def get_latest_quote() -> dict[str, float] | None:
    response = requests.get(
        f"{DATA_URL}/latest/quotes",
        headers=HEADERS,
        params={"symbols": DATA_SYMBOL},
        timeout=10,
    )
    if response.status_code != 200:
        return None
    quote = response.json().get("quotes", {}).get(DATA_SYMBOL, {})
    try:
        bid = float(quote.get("bp", 0) or 0)
        ask = float(quote.get("ap", 0) or 0)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    spread_pct = (ask - bid) / mid if mid > 0 else 0.0
    return {"bid": bid, "ask": ask, "mid": mid, "spread_pct": spread_pct}


def get_open_position() -> dict[str, Any] | None:
    for position in get_positions():
        try:
            qty = abs(float(position.get("qty", 0) or 0))
            entry = float(position.get("avg_entry_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        notional = qty * entry
        if position.get("symbol") == SYMBOL and qty > DUST_QTY and notional >= MIN_POSITION_NOTIONAL:
            return position
    return None


def place_order(side: str, qty: float, reference_price: float, max_slip_pct: float) -> bool:
    if qty <= 0:
        return False
    if reference_price <= 0:
        return False
    quote = get_latest_quote()
    quote_price = quote["ask"] if quote and side == "buy" else quote["bid"] if quote else reference_price
    limit_price = quote_price * (1 + max_slip_pct if side == "buy" else 1 - max_slip_pct)
    response = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={
            "symbol": SYMBOL,
            "qty": f"{qty:.8f}".rstrip("0").rstrip("."),
            "side": side,
            "type": "limit",
            "time_in_force": "ioc",
            "limit_price": f"{limit_price:.8f}".rstrip("0").rstrip("."),
        },
        timeout=10,
    )
    if response.status_code not in (200, 201):
        print(f"[ALPACA][ORDER FAIL] {side} {qty:.6f} -> {response.status_code} {response.text[:160]}")
        return False
    order = response.json()
    order_id = order.get("id")
    if order_has_fill(order):
        return True
    for _ in range(4):
        time.sleep(0.35)
        latest = get_order(order_id)
        if not latest:
            break
        if order_has_fill(latest):
            return True
        if latest.get("status") in {"canceled", "expired", "done_for_day", "rejected"}:
            break
    latest = get_order(order_id) if order_id else None
    status = (latest or order).get("status", "unknown")
    filled_qty = (latest or order).get("filled_qty", "0")
    print(f"[ALPACA][ORDER NO FILL] {side} status={status} filled_qty={filled_qty}")
    return False


def get_order(order_id: str | None) -> dict[str, Any] | None:
    if not order_id:
        return None
    response = requests.get(f"{BASE_URL}/v2/orders/{order_id}", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        return None
    return response.json()


def order_has_fill(order: dict[str, Any] | None) -> bool:
    if not order:
        return False
    try:
        filled_qty = abs(float(order.get("filled_qty", 0) or 0))
        filled_price = float(order.get("filled_avg_price", 0) or 0)
    except (TypeError, ValueError):
        return False
    status = order.get("status", "")
    filled_notional = filled_qty * filled_price
    return (
        filled_qty > DUST_QTY
        and filled_notional >= MIN_FILL_NOTIONAL
        and status in {"filled", "partially_filled"}
    )


def close_position(side: str, qty: float, reference_price: float) -> bool:
    if abs(qty) <= DUST_QTY or abs(qty) * reference_price < MIN_POSITION_NOTIONAL:
        return False
    safe_qty = max(0.0, abs(qty) - DUST_QTY)
    if safe_qty <= 0:
        return False
    return place_order("sell" if side == "long" else "buy", safe_qty, reference_price, MAX_EXIT_SLIP_PCT)


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    current = None
    for value in values[-period:]:
        if current is None:
            current = value
            continue
        current = value * alpha + current * (1 - alpha)
    return current


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


def classify_regime(bars: list[dict[str, float]], quote: dict[str, float] | None) -> dict[str, Any]:
    closes = [b["c"] for b in bars]
    current = quote["mid"] if quote else closes[-1]
    closes_for_signal = closes[:-1] + [current]
    prior = closes_for_signal[-6:]

    fast = ema(closes_for_signal, EMA_FAST)
    mid = ema(closes_for_signal, EMA_MID)
    slow = ema(closes_for_signal, EMA_SLOW)
    if fast is None or mid is None or slow is None:
        return {"regime": "transition", "conf": 0.0}

    atr_fast = statistics.mean(b["h"] - b["l"] for b in bars[-ATR_FAST:])
    atr_base = statistics.mean(b["h"] - b["l"] for b in bars[-ATR_BASE:])
    if atr_base <= 0:
        return {"regime": "transition", "conf": 0.0, "current": current}

    atr_ratio = atr_fast / atr_base
    vol_pct = atr_fast / current

    trend_pressure = (fast - slow) / slow
    mid_pressure = (fast - mid) / mid
    trend_conf = abs(trend_pressure) / max(vol_pct, 1e-9)
    last_channel = [b["h"] for b in bars[-RANGE_WINDOW:]]
    channel_width = (max(last_channel) - min(last_channel)) / current
    micro_chop = abs((prior[-1] - prior[0]) / prior[0]) if prior[0] else 0.0
    rsi = calc_rsi(closes_for_signal)

    if atr_ratio > 0.995 and trend_conf > 0.55 and abs(trend_pressure) > 0.00003:
        return {
            "regime": "trend",
            "direction": "long" if trend_pressure > 0 else "short",
            "conf": clamp(trend_conf / 2.35 + abs(mid_pressure) * 8.0, 0.0, 1.0),
            "current": current,
            "atr_ratio": atr_ratio,
            "vol_pct": vol_pct,
            "trend_pressure": trend_pressure,
            "micro_chop": micro_chop,
            "rsi": rsi,
        }

    if atr_ratio < 1.06 and channel_width < 0.0140 and micro_chop < 0.0035:
        channel_low = min(b["l"] for b in bars[-RANGE_WINDOW:])
        channel_high = max(b["h"] for b in bars[-RANGE_WINDOW:])
        return {
            "regime": "range",
            "current": current,
            "atr_ratio": atr_ratio,
            "vol_pct": vol_pct,
            "channel": (channel_low, channel_high),
            "mid": (channel_low + channel_high) / 2.0,
            "rsi": rsi,
            "conf": max(0.22, min(0.9, (1 - atr_ratio) * 1.8)),
        }

    return {
        "regime": "transition",
        "current": current,
        "conf": max(0.0, min(0.4, atr_ratio * 0.35)),
        "atr_ratio": atr_ratio,
        "vol_pct": vol_pct,
        "trend_pressure": trend_pressure,
    }


def generate_signal(regime: dict[str, Any], bars: list[dict[str, float]]) -> dict[str, Any] | None:
    if regime["regime"] == "transition":
        return None

    closes = [b["c"] for b in bars]
    current = regime["current"]
    if regime["regime"] == "trend":
        direction = regime["direction"]
        channel = [b["c"] for b in bars[-CHANNEL_LOOKBACK:]]
        last_break_high = max(channel[:-1])
        last_break_low = min(channel[:-1])
        pullback_confirm = closes[-2] < closes[-3] if direction == "long" else closes[-2] > closes[-3]

        if direction == "long" and current > last_break_high and (pullback_confirm or closes[-1] > closes[-2]):
            raw_conf = regime["conf"] * (1 + min(1.0, regime["micro_chop"] * 240))
            return {
                "regime": "trend",
                "side": "long",
                "entry": current,
                "conf": clamp(raw_conf, 0.0, 1.0),
            "score": regime["conf"] * 2.4 + 0.45,
            "tp": max(0.0014, regime["vol_pct"] * 1.8),
            "sl": max(0.0014, regime["vol_pct"] * 1.6),
                "atr_ratio": regime["atr_ratio"],
            }

        if direction == "short" and current < last_break_low and (pullback_confirm or closes[-1] < closes[-2]):
            raw_conf = regime["conf"] * (1 + min(1.0, regime["micro_chop"] * 220))
            return {
                "regime": "trend",
                "side": "short",
                "entry": current,
                "conf": clamp(raw_conf, 0.0, 1.0),
            "score": regime["conf"] * 2.4 + 0.45,
            "tp": max(0.0014, regime["vol_pct"] * 1.8),
            "sl": max(0.0014, regime["vol_pct"] * 1.6),
                "atr_ratio": regime["atr_ratio"],
            }
        return None

    channel_low, channel_high = regime["channel"]
    channel_width = channel_high - channel_low
    if channel_width <= 0:
        return None
    band = channel_width * 0.58
    rsi = regime["rsi"]
    if rsi is None:
        return None

    if rsi >= 60 and current >= regime["mid"] + band * 0.35:
        return {
            "regime": "range",
            "side": "short",
            "entry": current,
            "conf": max(0.22, regime["conf"] * max(0.6, (rsi - 50) / 20)),
            "score": regime["conf"] * abs(rsi - 50) / 35,
            "tp": max(0.0011, regime["vol_pct"] * 1.5),
            "sl": max(0.0011, regime["vol_pct"] * 1.4),
            "atr_ratio": regime["atr_ratio"],
        }

    if rsi <= 40 and current <= regime["mid"] - band * 0.35:
        return {
            "regime": "range",
            "side": "long",
            "entry": current,
            "conf": max(0.22, regime["conf"] * max(0.6, (50 - rsi) / 20)),
            "score": regime["conf"] * abs(rsi - 50) / 35,
            "tp": max(0.0011, regime["vol_pct"] * 1.5),
            "sl": max(0.0011, regime["vol_pct"] * 1.4),
            "atr_ratio": regime["atr_ratio"],
        }

    if abs(current - regime["mid"]) / current > 0.0008:
        fallback_side = "long" if current < regime["mid"] else "short"
        return {
            "regime": "range",
            "side": fallback_side,
            "entry": current,
            "conf": max(0.18, regime["conf"]),
            "score": max(0.12, regime["conf"] * 0.9),
            "tp": max(0.0010, regime["vol_pct"] * 1.3),
            "sl": max(0.0012, regime["vol_pct"] * 1.5),
            "atr_ratio": regime["atr_ratio"],
        }

    return None


def adjust_size_pct(equity: float, signal: dict[str, Any]) -> float:
    conf = clamp(signal["conf"], 0.0, 1.0)
    regime = signal["regime"]
    base = BASE_RISK[regime]
    trend_boost = 1.0
    if regime == "trend":
        trend_boost += min(0.22, max(0.0, signal["atr_ratio"] - 1.0))

    vol_adj = 1.0
    if regime == "range":
        vol_adj = max(0.6, min(1.2, signal["atr_ratio"] * 1.2))
    elif signal["vol_pct"] > 0.0024:
        vol_adj = 1.0 + min(0.16, signal["vol_pct"] * 30)

    streak_delta = STATE["wins"] - STATE["losses"]
    streak_adj = 1.0 + min(0.2, max(0.0, streak_delta) * 0.08)
    if streak_delta < -1:
        streak_adj = 0.62

    peak = STATE["equity_peak"] if STATE["equity_peak"] > 0 else equity
    if equity > peak:
        peak = equity
        STATE["equity_peak"] = peak
    drawdown = (peak - equity) / peak if peak > 0 else 0.0
    drawdown_adj = 1.0 - min(0.55, drawdown * 2.2)
    if drawdown > 0.24:
        drawdown_adj = max(0.32, drawdown_adj)

    size = base * (0.45 + conf * 0.55) * trend_boost * vol_adj * streak_adj * drawdown_adj
    STATE["equity_peak"] = peak
    return clamp(size, 0.25, 0.99)


def build_plan(signal: dict[str, Any]) -> dict[str, Any]:
    if signal["regime"] == "trend":
        return {
            "side": signal["side"],
            "regime": "trend",
            "tp": signal["tp"],
            "sl": signal["sl"],
            "trail": max(0.0006, signal["tp"] * 0.30),
        }
    return {"side": signal["side"], "regime": "range", "tp": signal["tp"], "sl": signal["sl"], "trail": 0.0}


def plan_from_position(side: str) -> dict[str, Any]:
    if STATE["active_plan"] is not None and STATE["active_plan"]["side"] == side:
        return STATE["active_plan"]
    return {"side": side, "tp": 0.0020, "sl": 0.0010, "trail": 0.0, "regime": "transition"}


def main() -> None:
    print("=" * 86)
    print("SPARK 5 ALPACA - REGIME SWITCH + ADAPTIVE-RISK (ETH/USD)")
    print("=" * 86)

    closed = 0
    wins = 0
    losses = 0

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        account = get_account()
        equity = float(account.get("equity", 50.0) or 50.0)
        cash = float(account.get("cash", equity) or equity)
        if STATE["equity_peak"] <= 0:
            STATE["equity_peak"] = equity
        elif equity > STATE["equity_peak"]:
            STATE["equity_peak"] = equity

        bars = get_bars()
        if len(bars) < LOOKBACK:
            print(f"[{now}] WARMUP bars={len(bars)}")
            time.sleep(POLL_SECONDS)
            continue

        quote = get_latest_quote()
        if quote and quote["spread_pct"] > 0.004:
            print(f"[{now}] HOLD spread too wide {quote['spread_pct']*100:.3f}%")
            time.sleep(POLL_SECONDS)
            continue

        regime = classify_regime(bars, quote)
        signal = generate_signal(regime, bars)
        position = get_open_position()

        if position is not None:
            side = position.get("side", "long")
            qty = float(position.get("qty", 0))
            if qty <= 0:
                time.sleep(POLL_SECONDS)
                continue

            entry = float(position.get("avg_entry_price", regime["current"]))
            pnl_pct = (regime["current"] - entry) / entry if side == "long" else (entry - regime["current"]) / entry
            plan = plan_from_position(side)

            print(
                f"[{now}] IN_TRADE {side.upper()} qty={qty:.6f} entry={entry:.2f} px={regime['current']:.2f} "
                f"pnl={pnl_pct*100:.2f}% mode={plan['regime']} tp={plan['tp']*100:.2f}% sl={plan['sl']*100:.2f}%"
            )

            if pnl_pct >= plan["tp"]:
                if close_position(side, qty, regime["current"]):
                    STATE["active_plan"] = None
                    wins += 1
                    closed += 1
                    STATE["wins"] += 1
                    print(f"[{now}] TP HIT, closed {side.upper()} with +{pnl_pct*100:.2f}%")
                time.sleep(POLL_SECONDS)
                continue

            if pnl_pct <= -plan["sl"]:
                if close_position(side, qty, regime["current"]):
                    STATE["active_plan"] = None
                    losses += 1
                    closed += 1
                    STATE["losses"] += 1
                    print(f"[{now}] SL HIT, closed {side.upper()} with {pnl_pct*100:.2f}%")
                time.sleep(POLL_SECONDS)
                continue

            if plan["trail"] > 0 and pnl_pct >= plan["trail"] and signal is not None and signal["regime"] != plan["regime"]:
                if close_position(side, qty, regime["current"]):
                    STATE["active_plan"] = None
                    losses += 1
                    STATE["losses"] += 1
                    print(f"[{now}] regime-shift close for safety")
            time.sleep(POLL_SECONDS)
            continue

        if not signal:
            print(f"[{now}] HOLD mode={regime['regime']} equity=${equity:.2f} cash=${cash:.2f}")
            if closed:
                print(f"[{now}] closed={closed} win_rate={(wins / closed) * 100:.1f}%")
            time.sleep(POLL_SECONDS)
            continue

        if signal["score"] < MIN_SIGNAL_SCORE:
            print(f"[{now}] HOLD weak signal score={signal['score']:.3f} regime={signal['regime']}")
            time.sleep(POLL_SECONDS)
            continue

        if equity < MIN_ENTRY_EQ:
            print(f"[{now}] HOLD low equity ${equity:.2f}, skipping entries")
            time.sleep(POLL_SECONDS)
            continue

        alloc = adjust_size_pct(equity, signal)
        order_qty = (equity * alloc) / signal["entry"]
        order_qty = max(order_qty, MIN_QTY)

        if order_qty * signal["entry"] < MIN_ENTRY_EQ:
            print(
                f"[{now}] SKIP tiny notional ${order_qty * signal['entry']:.2f} "
                f"alloc={alloc*100:.1f}% score={signal['score']:.3f}"
            )
            time.sleep(POLL_SECONDS)
            continue

        side = "buy" if signal["side"] == "long" else "sell"
        if place_order(side, order_qty, signal["entry"], MAX_ENTRY_SLIP_PCT):
            STATE["active_plan"] = build_plan(signal)
            print(
                f"[{now}] ENTRY {signal['side'].upper()} mode={signal['regime']} qty={order_qty:.6f} "
                f"alloc={alloc*100:.1f}% score={signal['score']:.3f} conf={signal['conf']:.2f} "
                f"tp={signal['tp']*100:.2f}% sl={signal['sl']*100:.2f}%"
            )
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
