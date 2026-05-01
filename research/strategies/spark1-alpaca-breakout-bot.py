"""Spark 1 - Alpaca breakout bot with volatility expansion.

Core idea:
- Detect a breakout from a rolling channel on ETH/USD 1-minute candles.
- Confirm the breakout only when short-term volatility is expanding.
- Trade directionally with adaptive size/exit based on momentum + ATR regime.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime

import requests
from alpaca_config import get_alpaca_config

ALPACA = get_alpaca_config()
BASE_URL = ALPACA["base_url"]
DATA_URL = ALPACA["data_url"]

HEADERS = {
    "APCA-API-KEY-ID": ALPACA["api_key"],
    "APCA-API-SECRET-KEY": ALPACA["secret_key"],
}

TRADE_SYMBOL = "ETHUSD"
DATA_SYMBOL = "ETH/USD"
TIMEFRAME = "1Min"

START_CAPITAL = 50.0
LOOKBACK_BREAKOUT = 24
LOOKBACK_VOL_SHORT = 8
LOOKBACK_VOL_BASE = 24
MOMENTUM_LAG = 6
POLL_SECONDS = 20


def get_account() -> dict:
    response = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        print(f"[ALPACA] account call failed: {response.status_code} {response.text[:140]}")
        return {}
    return response.json()


def get_positions() -> list[dict]:
    response = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
    if response.status_code != 200:
        print(f"[ALPACA] positions call failed: {response.status_code}")
        return []
    return response.json()


def get_bars(limit: int = 90) -> list[dict]:
    response = requests.get(
        f"{DATA_URL}/bars",
        headers=HEADERS,
        params={"symbols": DATA_SYMBOL, "timeframe": TIMEFRAME, "limit": limit},
        timeout=10,
    )
    if response.status_code != 200:
        print(f"[ALPACA] bars call failed: {response.status_code} {response.text[:140]}")
        return []

    payload = response.json()
    bars_by_symbol = payload.get("bars", {})
    raw_bars = bars_by_symbol.get(DATA_SYMBOL, []) if isinstance(bars_by_symbol, dict) else []

    bars = []
    for bar in raw_bars:
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


def get_open_position() -> dict | None:
    for position in get_positions():
        if position.get("symbol") == TRADE_SYMBOL:
            return position
    return None


def place_order(side: str, qty: float) -> bool:
    response = requests.post(
        f"{BASE_URL}/v2/orders",
        headers=HEADERS,
        json={
            "symbol": TRADE_SYMBOL,
            "qty": f"{qty:.6f}",
            "side": side,
            "type": "market",
            "time_in_force": "ioc",
        },
        timeout=10,
    )
    if response.status_code not in (200, 201):
        print(f"[ALPACA][ORDER FAIL] {side} {qty:.6f} -> {response.status_code} {response.text[:160]}")
        return False
    return True


def compute_signal(bars: list[dict]) -> dict | None:
    if len(bars) < LOOKBACK_VOL_BASE + 2:
        return None

    closes = [bar["c"] for bar in bars]
    prior = bars[:-1]
    if len(prior) < LOOKBACK_BREAKOUT + 1:
        return None

    current = closes[-1]
    channel = prior[-LOOKBACK_BREAKOUT:]
    channel_hi = max(bar["h"] for bar in channel)
    channel_lo = min(bar["l"] for bar in channel)

    short_window = prior[-LOOKBACK_VOL_SHORT:]
    base_window = prior[-LOOKBACK_VOL_BASE:]
    atr_short = statistics.mean(bar["h"] - bar["l"] for bar in short_window)
    atr_base = statistics.mean(bar["h"] - bar["l"] for bar in base_window)
    if atr_base <= 0:
        return None

    vol_expansion = atr_short / atr_base
    if vol_expansion < 1.2:
        return None

    momentum = (current - closes[-MOMENTUM_LAG]) / closes[-MOMENTUM_LAG]
    atr_pct = atr_short / current

    if current > channel_hi and momentum > 0.0004:
        return {
            "side": "long",
            "entry": current,
            "size_pct": min(0.55, 0.24 + min(0.18, momentum * 60)),
            "tp_pct": max(0.0018, atr_pct * 1.8),
            "sl_pct": max(0.0010, atr_pct * 1.0),
            "vol_expansion": vol_expansion,
            "momentum": momentum,
            "atr_pct": atr_pct,
            "channel": (channel_lo, channel_hi),
        }

    if current < channel_lo and momentum < -0.0004:
        return {
            "side": "short",
            "entry": current,
            "size_pct": min(0.55, 0.20 + min(0.20, abs(momentum) * 60)),
            "tp_pct": max(0.0018, atr_pct * 1.7),
            "sl_pct": max(0.0010, atr_pct * 1.0),
            "vol_expansion": vol_expansion,
            "momentum": momentum,
            "atr_pct": atr_pct,
            "channel": (channel_lo, channel_hi),
        }

    return None


def main() -> None:
    print("=" * 78)
    print("SPARK 1 ALPACA - BREAKOUT + VOLATILITY EXPANSION BOT")
    print("=" * 78)
    print("Intent: capture ETH/USD breakout momentum with tight adaptive exits.")

    wins = 0
    losses = 0
    closed_trades = 0

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        account = get_account()
        cash = float(account.get("cash", 0))
        equity = float(account.get("equity", START_CAPITAL if cash <= 0 else cash))

        bars = get_bars()
        if len(bars) < LOOKBACK_VOL_BASE + 2:
            print(f"[{now}] warming up bars: {len(bars)}")
            time.sleep(POLL_SECONDS)
            continue

        current = bars[-1]["c"]
        signal = compute_signal(bars)
        position = get_open_position()

        if position:
            side = position.get("side", "long")
            avg_entry = float(position.get("avg_entry_price", current))
            qty = float(position.get("qty", 0))
            if qty <= 0:
                time.sleep(POLL_SECONDS)
                continue

            if side == "long":
                pnl_pct = (current - avg_entry) / avg_entry
            else:
                pnl_pct = (avg_entry - current) / avg_entry

            print(
                f"[{now}] IN TRADE | side={side} qty={qty:.6f} "
                f"entry={avg_entry:.2f} price={current:.2f} pnl={pnl_pct*100:.3f}%"
            )

            if signal:
                tp_pct = signal["tp_pct"]
                sl_pct = signal["sl_pct"]
                if pnl_pct >= tp_pct:
                    if place_order("sell" if side == "long" else "buy", qty):
                        wins += 1
                        closed_trades += 1
                        print(f"[{now}] TP HIT - closed {side} @ {current:.2f} (+{pnl_pct*100:.2f}%)")
                elif pnl_pct <= -sl_pct:
                    if place_order("sell" if side == "long" else "buy", qty):
                        losses += 1
                        closed_trades += 1
                        print(f"[{now}] SL HIT - closed {side} @ {current:.2f} ({pnl_pct*100:.2f}%)")

            time.sleep(POLL_SECONDS)
            continue

        if not signal:
            print(f"[{now}] HOLD | price={current:.2f} cash=${cash:.2f} eq=${equity:.2f}")
            time.sleep(POLL_SECONDS)
            continue

        if cash < 15:
            print(f"[{now}] HOLD | cash too small (${cash:.2f}) for meaningful exposure.")
            time.sleep(POLL_SECONDS)
            continue

        if signal["side"] == "long":
            order_qty = (equity * signal["size_pct"]) / signal["entry"]
            order_side = "buy"
        else:
            order_qty = (equity * signal["size_pct"]) / signal["entry"]
            order_side = "sell"

        if order_qty < 0.0002:
            print(f"[{now}] computed qty too small ({order_qty:.6f}), skipping")
            time.sleep(POLL_SECONDS)
            continue

        channel_lo, channel_hi = signal["channel"]
        print(
            f"[{now}] ENTRY | {signal['side'].upper()} @ {signal['entry']:.2f} "
            f"size={signal['size_pct']*100:.1f}% vol_exp={signal['vol_expansion']:.2f} "
            f"mom={signal['momentum']*100:.3f}% atr={signal['atr_pct']*100:.3f}% channel={channel_lo:.2f}-{channel_hi:.2f}"
        )

        if place_order(order_side, order_qty):
            print(f"[{now}] ORDER SENT | {order_side} {order_qty:.6f}")
            if closed_trades:
                win_rate = (wins / closed_trades) * 100
                print(f"[{now}] trade_count={closed_trades} win_rate={win_rate:.1f}%")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
