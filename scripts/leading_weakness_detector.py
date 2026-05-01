#!/usr/bin/env python3
"""Leading Indicator Weakness Score Detector — detects trend weakening BEFORE the flip.

Unlike ADX and Hurst (lagging), this uses leading signals:
1. RSI divergence — price higher high, RSI lower high = momentum loss
2. Volume divergence — price higher high, volume declining = distribution
3. Position ratio — sell_opens/buy_opens > 0.5 in uptrend = weakening
4. Price acceleration — decelerating moves = trend fatigue

Output: reports/weakness_scores_live.json

Score interpretation:
  0-30:   Trend healthy — no action needed
  30-60:  Trend weakening — tighten escape thresholds
  60-80:  Trend deteriorating — prepare for flip (Tier 0 escape)
  80-100: Trend dying — flip asymmetry, close against-trend positions

Usage:
    python scripts/leading_weakness_detector.py --symbols NAS100 US30 EURUSD
    python scripts/leading_weakness_detector.py --watch --interval 60
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_JSON = ROOT / "reports" / "weakness_scores_live.json"

DEFAULT_SYMBOLS = ["NAS100", "US30", "EURUSD", "GBPUSD", "ETHUSD", "BTCUSD", "XAUUSD"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def detect_rsi_divergence(bars: list, lookback: int = 20) -> float:
    """Detect RSI divergence — price HH with RSI LH (bearish) or price LL with RSI HL (bullish).

    Returns:
        -1.0 to 1.0: negative = bearish divergence (price up, RSI down),
                      positive = bullish divergence (price down, RSI up),
                      0 = no divergence
    """
    if len(bars) < lookback * 2:
        return 0.0

    closes = [b["close"] for b in bars]
    recent = closes[-lookback:]
    older = closes[-lookback * 2:-lookback]

    # Price change
    price_recent = (recent[-1] - recent[0]) / recent[0] if recent[0] > 0 else 0
    price_older = (older[-1] - older[0]) / older[0] if older[0] > 0 else 0

    # RSI change
    rsi_recent = rsi(closes[-lookback - 14:])
    rsi_older = rsi(closes[-lookback * 2 - 14:-lookback + 1])

    if rsi_recent is None or rsi_older is None:
        return 0.0

    rsi_change = (rsi_recent - rsi_older) / 100  # Normalize to -1 to 1

    # Divergence: price and RSI moving in opposite directions
    divergence = price_recent - rsi_change

    # Score: how much is RSI disagreeing with price?
    # If price is up 2% but RSI is down 10 points, that's bearish divergence
    if price_recent > 0 and rsi_change < 0:
        return min(1.0, abs(divergence) * 5)  # Bearish (price up, RSI down)
    elif price_recent < 0 and rsi_change > 0:
        return -min(1.0, abs(divergence) * 5)  # Bullish (price down, RSI up)
    else:
        return 0.0  # No divergence


def detect_volume_divergence(bars: list, lookback: int = 20) -> float:
    """Detect volume divergence — price higher high with declining volume = distribution.

    Returns:
        0-1: 1 = strong distribution signal (price up, volume down)
        0 = no divergence
    """
    if len(bars) < lookback * 2:
        return 0.0

    closes = [b["close"] for b in bars]
    volumes = [b["tick_volume"] for b in bars]

    recent = closes[-lookback:]
    older = closes[-lookback * 2:-lookback]
    vol_recent = volumes[-lookback:]
    vol_older = volumes[-lookback * 2:-lookback]

    # Price change
    price_recent_high = max(recent)
    price_older_high = max(older)
    price_making_high = price_recent_high > price_older_high

    # Volume change
    avg_vol_recent = sum(vol_recent) / len(vol_recent)
    avg_vol_older = sum(vol_older) / len(vol_older)
    volume_declining = avg_vol_recent < avg_vol_older

    if price_making_high and volume_declining:
        # Distribution signal
        vol_ratio = avg_vol_older / avg_vol_recent if avg_vol_recent > 0 else 1
        return min(1.0, (vol_ratio - 1) * 0.5)
    elif not price_making_high and not volume_declining:
        # Accumulation (price down or flat, volume up)
        return 0.0
    else:
        return 0.0


def detect_price_acceleration(bars: list, lookback: int = 10) -> float:
    """Detect deceleration in price moves — trend fatigue.

    Returns:
        0-1: 1 = strong deceleration (trend dying)
        0 = accelerating or steady
    """
    if len(bars) < lookback * 2:
        return 0.0

    closes = [b["close"] for b in bars]

    # Compute price changes per bar
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    # Recent changes vs older changes
    recent_changes = changes[-lookback:]
    older_changes = changes[-lookback * 2:-lookback]

    # Average absolute change (volatility)
    recent_avg = sum(abs(c) for c in recent_changes) / len(recent_changes)
    older_avg = sum(abs(c) for c in older_changes) / len(older_changes)

    if older_avg == 0:
        return 0.0

    # Deceleration: recent moves are smaller than older moves
    decel = 1 - (recent_avg / older_avg)
    return max(0, min(1.0, decel))


def detect_trend_direction(bars: list, period: int = 20) -> str:
    """Simple trend direction: UP, DOWN, or FLAT."""
    if len(bars) < period:
        return "FLAT"

    closes = [b["close"] for b in bars[-period:]]
    price_change = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0

    if price_change > 0.005:  # 0.5% up
        return "UP"
    elif price_change < -0.005:  # 0.5% down
        return "DOWN"
    else:
        return "FLAT"


def compute_weakness_score(symbol: str, bars: list, open_buy: int = 0, open_sell: int = 0) -> dict:
    """Compute 0-100 weakness score for a symbol.

    Components (each weighted):
    - RSI divergence (30%): price HH, RSI LH = momentum loss
    - Volume divergence (25%): price HH, volume declining = distribution
    - Price acceleration (25%): decelerating moves = trend fatigue
    - Position ratio (20%): sell_opens/buy_opens > 0.5 in uptrend = weakening
    """
    if len(bars) < 40:
        return {
            "symbol": symbol,
            "score": 50,
            "trend": "UNKNOWN",
            "components": {},
            "note": "Insufficient data",
        }

    trend = detect_trend_direction(bars)
    rsi_div = detect_rsi_divergence(bars)
    vol_div = detect_volume_divergence(bars)
    accel = detect_price_acceleration(bars)

    # Position ratio signal
    # In an uptrend, if sell_opens are catching up to buy_opens, trend is weakening
    position_score = 0.0
    if trend == "UP" and (open_buy + open_sell) > 0:
        sell_ratio = open_sell / (open_buy + 1)
        if sell_ratio > 0.5:
            position_score = min(1.0, sell_ratio)
    elif trend == "DOWN" and (open_buy + open_sell) > 0:
        buy_ratio = open_buy / (open_sell + 1)
        if buy_ratio > 0.5:
            position_score = min(1.0, buy_ratio)

    # Weighted combination
    weakness = (
        0.30 * max(0, rsi_div) +      # RSI divergence (bearish only)
        0.25 * vol_div +                # Volume divergence
        0.25 * accel +                   # Price deceleration
        0.20 * position_score            # Position ratio
    )

    score = round(weakness * 100, 1)
    score = max(0, min(100, score))

    return {
        "symbol": symbol,
        "score": score,
        "trend": trend,
        "components": {
            "rsi_divergence": round(rsi_div, 3),
            "volume_divergence": round(vol_div, 3),
            "price_acceleration": round(accel, 3),
            "position_ratio": round(position_score, 3),
        },
        "open_positions": {"buy": open_buy, "sell": open_sell},
        "updated_at": utc_now_iso(),
    }


def run_detection(symbols: list[str]) -> list[dict]:
    """Run weakness detection for all symbols."""
    mt5.initialize()
    results = []

    for symbol in symbols:
        bars = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 200)
        if bars is None or len(bars) < 40:
            results.append({
                "symbol": symbol,
                "score": 50,
                "trend": "UNKNOWN",
                "components": {},
                "note": "Insufficient bar data",
                "updated_at": utc_now_iso(),
            })
            continue

        result = compute_weakness_score(symbol, list(bars))
        results.append(result)

    mt5.shutdown()
    return results


def write_output(results: list[dict]) -> None:
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": utc_now_iso(),
        "symbols": {r["symbol"]: r for r in results},
        "alert_summary": [
            r for r in results
            if r.get("score", 0) >= 60
        ],
    }
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_once(symbols: list[str]) -> None:
    results = run_detection(symbols)
    write_output(results)

    for r in results:
        score = r.get("score", 50)
        trend = r.get("trend", "?")
        symbol = r.get("symbol", "?")

        if score >= 80:
            emoji = "🚨"
        elif score >= 60:
            emoji = "⚠️"
        elif score >= 30:
            emoji = "📊"
        else:
            emoji = "✅"

        print(f"  {emoji} {symbol}: {score}/100 (trend={trend})")


def run_watch(symbols: list[str], interval: int = 60) -> None:
    print(f"Starting weakness detector — polling every {interval}s for {len(symbols)} symbols")
    print(f"Output: {OUTPUT_JSON}")
    print()

    poll_count = 0
    while True:
        poll_count += 1
        try:
            results = run_detection(symbols)
            write_output(results)

            timestamp = utc_now_iso()
            alerts = [r for r in results if r.get("score", 0) >= 60]

            if alerts:
                for r in alerts:
                    print(f"[{timestamp}] 🚨 ALERT {r['symbol']}: {r['score']}/100 (trend={r['trend']})")
            else:
                scores = ", ".join(f"{r['symbol']}={r['score']}" for r in results[:5])
                print(f"[{timestamp}] Poll #{poll_count} — no alerts — {scores}")

        except Exception as e:
            print(f"[{utc_now_iso()}] ERROR on poll #{poll_count}: {e}")

        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Leading Indicator Weakness Score Detector")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Symbols to monitor")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--watch", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.once or not args.watch:
        run_once(args.symbols)
    else:
        run_watch(args.symbols, interval=args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
