#!/usr/bin/env python3
"""
Leading Indicator Regime Detector — Weakness Score 0-100

The existing MTF regime detector (mtf_regime_detector.py) tells us WHERE we are
(uptrend/downtrend/extreme) using LAGGING indicators: EMA crosses, ADX levels, RSI extremes.

This detector tells us whether the trend is WEAKENING BEFORE it flips.

Leading signals (not lagging):
1. RSI divergence — price higher high, RSI lower high = momentum loss
2. Volume divergence — price makes new high but volume declines = distribution
3. Bar body shrinkage — candles get smaller near extremes = conviction loss
4. Wick expansion — longer wicks at extremes = rejection pressure
5. ADX slope change — ADX rolling over = trend momentum decaying

Output: weakness_score (0-100) per symbol
  - 0-30:  Trend healthy, no action needed
  - 31-60: Trend weakening, start monitoring
  - 61-80: Trend fragile, start Tier 0 offensive escape
  - 81-95: Trend breaking, widen old-side steps, rearm new side
  - 96-100: Trend flipped, execute asymmetry flip

Reads by: hungry_hippo_auto_flip.py, continuous_regime_monitor.py, HH runner escape logic
Writes to: reports/leading_regime_weakness.json
"""
import MetaTrader5 as mt5
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Core indicators ──────────────────────────────────────────────────────

def rsi(closes, period=14):
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


def _compute_adx(bars, period=14):
    if len(bars) < period + 1:
        return None
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(bars)):
        high, low = bars[i]["high"], bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        up_move = high - bars[i - 1]["high"]
        down_move = bars[i - 1]["low"] - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    if len(trs) < period:
        return None
    avg_tr = sum(trs[-period:]) / period
    avg_plus = sum(plus_dm[-period:]) / period
    avg_minus = sum(minus_dm[-period:]) / period
    if avg_tr == 0:
        return 0
    plus_di = (avg_plus / avg_tr) * 100
    minus_di = (avg_minus / avg_tr) * 100
    if plus_di + minus_di == 0:
        return 0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
    return dx


# ── Leading signal detectors ─────────────────────────────────────────────

def detect_rsi_divergence(bars, lookback=30):
    """
    RSI divergence: price makes higher high but RSI makes lower high = bearish divergence.
    Returns divergence_strength (0-1) and direction ('bearish' | 'bullish' | 'none').
    """
    if len(bars) < lookback + 14:
        return {"strength": 0.0, "direction": "none"}

    closes = [b["close"] for b in bars[-lookback:]]
    highs = [b["high"] for b in bars[-lookback:]]
    lows = [b["low"] for b in bars[-lookback:]]

    # Compute rolling RSI for each point in the lookback window
    all_closes = [b["close"] for b in bars[-(lookback + 14):]]
    rsi_values = []
    for i in range(14, len(all_closes) + 1):
        rsi_values.append(rsi(all_closes[:i]))

    if len(rsi_values) < lookback:
        return {"strength": 0.0, "direction": "none"}

    rsi_values = rsi_values[-lookback:]

    # Find local price peaks and their RSI
    price_peaks = []
    rsi_at_peaks = []
    for i in range(2, len(highs) - 1):
        if highs[i] >= highs[i - 1] and highs[i] > highs[i + 1]:
            price_peaks.append(highs[i])
            rsi_at_peaks.append(rsi_values[i])

    # Find local price troughs and their RSI
    price_troughs = []
    rsi_at_troughs = []
    for i in range(2, len(lows) - 1):
        if lows[i] <= lows[i - 1] and lows[i] < lows[i + 1]:
            price_troughs.append(lows[i])
            rsi_at_troughs.append(rsi_values[i])

    # Bearish divergence: higher high + lower RSI
    bearish_strength = 0.0
    if len(price_peaks) >= 2:
        for i in range(1, len(price_peaks)):
            if price_peaks[i] > price_peaks[i - 1] and rsi_at_peaks[i] < rsi_at_peaks[i - 1]:
                rsi_diff = rsi_at_peaks[i - 1] - rsi_at_peaks[i]
                bearish_strength = max(bearish_strength, min(1.0, rsi_diff / 20.0))

    # Bullish divergence: lower low + higher RSI
    bullish_strength = 0.0
    if len(price_troughs) >= 2:
        for i in range(1, len(price_troughs)):
            if price_troughs[i] < price_troughs[i - 1] and rsi_at_troughs[i] > rsi_at_troughs[i - 1]:
                rsi_diff = rsi_at_troughs[i] - rsi_at_troughs[i - 1]
                bullish_strength = max(bullish_strength, min(1.0, rsi_diff / 20.0))

    if bearish_strength > bullish_strength and bearish_strength > 0:
        return {"strength": bearish_strength, "direction": "bearish"}
    elif bullish_strength > 0:
        return {"strength": bullish_strength, "direction": "bullish"}
    return {"strength": 0.0, "direction": "none"}


def detect_volume_divergence(bars, lookback=30):
    """
    Volume divergence: price makes new high but tick_volume declines = distribution / exhaustion.
    """
    if len(bars) < lookback:
        return {"strength": 0.0, "direction": "none"}

    closes = [b["close"] for b in bars[-lookback:]]
    volumes = [float(b["tick_volume"]) for b in bars[-lookback:]]

    half = lookback // 2
    first_half_closes = closes[:half]
    second_half_closes = closes[half:]
    first_half_vol = volumes[:half]
    second_half_vol = volumes[half:]

    price_trend = (second_half_closes[-1] - first_half_closes[0]) / first_half_closes[0] if first_half_closes[0] else 0
    avg_vol_first = sum(first_half_vol) / len(first_half_vol) if first_half_vol else 0
    avg_vol_second = sum(second_half_vol) / len(second_half_vol) if second_half_vol else 0
    vol_change = (avg_vol_second - avg_vol_first) / avg_vol_first if avg_vol_first > 0 else 0

    # Bearish: price up, volume down
    if price_trend > 0.0005 and vol_change < -0.10:
        return {"strength": min(1.0, abs(vol_change) * 2), "direction": "bearish"}
    # Bullish: price down, volume down (selling exhaustion)
    if price_trend < -0.0005 and vol_change < -0.10:
        return {"strength": min(1.0, abs(vol_change) * 2), "direction": "bullish"}

    return {"strength": 0.0, "direction": "none"}


def detect_bar_body_shrinkage(bars, lookback=30):
    """
    Bar body shrinkage: candle bodies get smaller relative to total range = conviction loss.
    """
    if len(bars) < lookback:
        return {"ratio": 0.0, "conviction": "normal"}

    body_ratios = []
    for b in bars[-lookback:]:
        total_range = b["high"] - b["low"]
        if total_range == 0:
            body_ratios.append(1.0)
            continue
        body = abs(b["close"] - b["open"])
        body_ratios.append(body / total_range)

    recent_n = min(5, len(body_ratios))
    recent_avg = sum(body_ratios[-recent_n:]) / recent_n
    earlier = body_ratios[:-recent_n]
    earlier_avg = sum(earlier) / len(earlier) if earlier else recent_avg

    if earlier_avg == 0:
        return {"ratio": 0.0, "conviction": "normal"}

    shrinkage = max(0.0, min(1.0, 1.0 - (recent_avg / earlier_avg)))
    return {"ratio": shrinkage, "conviction": "weak" if shrinkage > 0.5 else "normal"}


def detect_wick_expansion(bars, lookback=30):
    """
    Wick expansion: wicks dominate bars near extremes = rejection pressure building.
    """
    if len(bars) < lookback:
        return {"ratio": 0.0}

    wick_ratios = []
    for b in bars[-lookback:]:
        total_range = b["high"] - b["low"]
        if total_range == 0:
            wick_ratios.append(0.0)
            continue
        body = abs(b["close"] - b["open"])
        wick = total_range - body
        wick_ratios.append(wick / total_range)

    recent_n = min(5, len(wick_ratios))
    recent_avg = sum(wick_ratios[-recent_n:]) / recent_n
    earlier = wick_ratios[:-recent_n]
    earlier_avg = sum(earlier) / len(earlier) if earlier else recent_avg

    expansion = max(0.0, min(1.0, recent_avg - earlier_avg))
    return {"ratio": expansion}


def detect_adx_weakening(bars):
    """
    ADX slope change: compare recent ADX vs earlier ADX.
    Declining ADX = trend momentum decaying.
    """
    if len(bars) < 56:
        return {"adx_recent": None, "adx_earlier": None, "change_pct": 0.0, "strength": 0.0}

    adx_recent = _compute_adx(bars[-28:])
    adx_earlier = _compute_adx(bars[-56:-28])

    if adx_recent is None or adx_earlier is None or adx_earlier == 0:
        return {"adx_recent": adx_recent, "adx_earlier": adx_earlier, "change_pct": 0.0, "strength": 0.0}

    change = (adx_earlier - adx_recent) / adx_earlier
    strength = max(0.0, min(1.0, change * 2.0))  # 50% ADX drop = max strength
    return {
        "adx_recent": round(adx_recent, 1),
        "adx_earlier": round(adx_earlier, 1),
        "change_pct": round(change * 100, 1),
        "strength": strength,
    }


# ── Composite weakness score ─────────────────────────────────────────────

WEIGHTS = {
    "rsi_divergence":   0.30,
    "volume_divergence": 0.25,
    "body_shrinkage":   0.20,
    "wick_expansion":   0.15,
    "adx_weakening":    0.10,
}


def compute_weakness_score(bars):
    """
    Compute weakness score 0-100.
    Combines all leading indicators with weighted scores.
    """
    score = 0.0
    details = {}

    # 1. RSI divergence
    rsi_div = detect_rsi_divergence(bars)
    score += rsi_div["strength"] * 100 * WEIGHTS["rsi_divergence"]
    details["rsi_divergence"] = {
        "strength": round(rsi_div["strength"], 3),
        "direction": rsi_div["direction"],
        "contribution": round(rsi_div["strength"] * 100 * WEIGHTS["rsi_divergence"], 1),
    }

    # 2. Volume divergence
    vol_div = detect_volume_divergence(bars)
    score += vol_div["strength"] * 100 * WEIGHTS["volume_divergence"]
    details["volume_divergence"] = {
        "strength": round(vol_div["strength"], 3),
        "direction": vol_div["direction"],
        "contribution": round(vol_div["strength"] * 100 * WEIGHTS["volume_divergence"], 1),
    }

    # 3. Body shrinkage
    shrinkage = detect_bar_body_shrinkage(bars)
    score += shrinkage["ratio"] * 100 * WEIGHTS["body_shrinkage"]
    details["body_shrinkage"] = {
        "ratio": round(shrinkage["ratio"], 3),
        "conviction": shrinkage["conviction"],
        "contribution": round(shrinkage["ratio"] * 100 * WEIGHTS["body_shrinkage"], 1),
    }

    # 4. Wick expansion
    wick = detect_wick_expansion(bars)
    score += wick["ratio"] * 100 * WEIGHTS["wick_expansion"]
    details["wick_expansion"] = {
        "ratio": round(wick["ratio"], 3),
        "contribution": round(wick["ratio"] * 100 * WEIGHTS["wick_expansion"], 1),
    }

    # 5. ADX weakening
    adx_w = detect_adx_weakening(bars)
    score += adx_w["strength"] * 100 * WEIGHTS["adx_weakening"]
    details["adx_weakening"] = {
        "adx_recent": adx_w["adx_recent"],
        "adx_earlier": adx_w["adx_earlier"],
        "change_pct": adx_w["change_pct"],
        "contribution": round(adx_w["strength"] * 100 * WEIGHTS["adx_weakening"], 1),
    }

    return {"weakness_score": round(score), "details": details}


def _recommend_action(weakness_score):
    if weakness_score <= 30:
        return "HOLD"
    elif weakness_score <= 60:
        return "MONITOR"
    elif weakness_score <= 80:
        return "OFFENSIVE_ESCAPE"
    elif weakness_score <= 95:
        return "PHASE_TRANSITION"
    else:
        return "FLIP"


def _action_label(action):
    labels = {
        "HOLD": "✅ Trend healthy",
        "MONITOR": "⚠️ Weakening",
        "OFFENSIVE_ESCAPE": "🟡 Close extremes",
        "PHASE_TRANSITION": "🟠 Widen old side",
        "FLIP": "🔴 FLIP NOW",
    }
    return labels.get(action, action)


# ── Main ─────────────────────────────────────────────────────────────────

DEFAULT_SYMBOLS = [
    "GBPUSD", "EURUSD", "USDJPY", "NZDUSD",
    "NAS100", "US30",
    "BTCUSD", "ETHUSD",
    "XAUUSD",
]


def detect_all_symbols(symbols=None):
    if symbols is None:
        symbols = list(DEFAULT_SYMBOLS)

    mt5.initialize()
    results = {}

    for sym in symbols:
        bars = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 100)
        if bars is None or len(bars) < 30:
            results[sym] = {"error": "insufficient data", "weakness_score": None}
            continue

        weakness = compute_weakness_score(bars)
        closes = [b["close"] for b in bars]
        current_price = closes[-1]
        price_change_20 = ((closes[-1] - closes[-20]) / closes[-20] * 100) if len(closes) >= 20 else 0.0

        action = _recommend_action(weakness["weakness_score"])

        results[sym] = {
            "weakness_score": weakness["weakness_score"],
            "price": round(current_price, 5),
            "price_change_20bars_pct": round(price_change_20, 2),
            "action": action,
            "details": weakness["details"],
            "detected_at": utc_now_iso(),
        }

    mt5.shutdown()
    return results


def main():
    symbols = list(DEFAULT_SYMBOLS)
    results = detect_all_symbols(symbols)

    print(f"{'Symbol':<10} {'Score':>6} {'Action':<20} {'20bar Δ':>9} {'Top Signal'}")
    print("-" * 80)

    for sym, data in sorted(results.items(), key=lambda x: x[1].get("weakness_score") or 0, reverse=True):
        if "error" in data:
            print(f"{sym:<10} {'ERR':>6} {'':<20} {'N/A':>9} {data['error']}")
            continue

        score = data["weakness_score"]
        action = data["action"]
        action_lbl = _action_label(action)
        pct = data["price_change_20bars_pct"]

        # Find top contributing signal
        top_signal = "—"
        max_contrib = 0
        for sig_name, sig_data in data["details"].items():
            contrib = sig_data.get("contribution", 0) or 0
            if contrib > max_contrib:
                max_contrib = contrib
                direction = sig_data.get("direction", "")
                top_signal = f"{sig_name}"
                if direction:
                    top_signal += f" ({direction})"

        print(f"{sym:<10} {score:>6} {action_lbl:<20} {pct:>+8.2f}% {top_signal}")

    # Write report
    report_dir = Path(__file__).parent.parent / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "leading_regime_weakness.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved to {report_path}")


if __name__ == "__main__":
    main()
