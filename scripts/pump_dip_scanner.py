#!/usr/bin/env python3
"""
Pump & Dip Detection Scanner — Comprehensive market state detection.

Scans ALL Coinbase coins for:
1. PUMP signals — early detection of parabolic moves (ride 2% → 20-50%)
2. DIP signals — capitulation detection for bounce plays (buy the bottom)

Backtests each signal type to validate predictive power.
Runs live scan to find current opportunities.

Output: reports/pump_dip_scanner_results.json
"""
import json
import os
import sys
import time
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "pump_dip_scanner_results.json"

# Universe of coins to scan (microcaps + majors for comparison)
COIN_UNIVERSE = [
    # Microcaps (RAVE-like)
    "RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD", "MOG-USD",
    "FARTCOIN-USD", "A8-USD", "VVV-USD", "PRL-USD", "COMP-USD",
    # Mid-caps
    "SOL-USD", "DOGE-USD", "XRP-USD", "PEPE-USD", "WIF-USD",
    "AAVE-USD", "LINK-USD", "UNI-USD", "AVAX-USD", "NEAR-USD",
    "FET-USD", "RENDER-USD", "TIA-USD", "SEI-USD", "SUI-USD",
    # Majors (for baseline comparison)
    "BTC-USD", "ETH-USD",
]

BTC = "BTC-USD"
WINDOW_HOURS = 48  # Lookback for signal detection
CANDLE_GRANULARITY = "FIVE_MINUTE"  # 5-min candles


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_candles(client, pid, start, end, granularity=CANDLE_GRANULARITY):
    """Fetch candles with chunking to handle API limits."""
    chunk_sec = 300 * 5 * 60  # 25 hours per chunk
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def fetch_best_bid_ask(client, pids):
    """Get current bid/ask spread for order book analysis."""
    try:
        resp = client.best_bid_ask(pids)
        return resp.get("pricebooks", [])
    except Exception:
        return []


# ============================================================
# SIGNAL DETECTION FUNCTIONS
# ============================================================

def compute_rsi(closes, period):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


def compute_bollinger(closes, period=20, num_std=2):
    if len(closes) < period:
        return None
    recent = closes[-period:]
    sma = statistics.mean(recent)
    std = statistics.stdev(recent) if len(recent) > 1 else 0
    upper = sma + num_std * std
    lower = sma - num_std * std
    width = (upper - lower) / sma * 100 if sma > 0 else 0
    return {"sma": sma, "upper": upper, "lower": lower, "width": width}


def compute_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return statistics.mean(trs[-period:])


# ============================================================
# PUMP SIGNALS (5 components)
# ============================================================

def detect_pump_signals(candles, lookback=48):
    """
    Detect early pump signals. Returns score 0-100 and component breakdown.

    Signals:
    1. Volume spike — current volume > 3x average
    2. Price acceleration — consecutive green bars, increasing body size
    3. Volatility breakout — Bollinger expansion after squeeze
    4. Momentum surge — rate of change accelerating
    5. Break of resistance — price above recent high
    """
    if len(candles) < lookback + 5:
        return {"score": 0, "reason": "insufficient_data", "components": {}}

    closes = [float(c["close"]) for c in candles]
    volumes = [float(c["volume"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    opens = [float(c["open"]) for c in candles]

    current_price = closes[-1]
    components = {}

    # 1. VOLUME SPIKE (0-20 points)
    avg_vol = statistics.mean(volumes[-lookback:-5]) if len(volumes) > lookback else statistics.mean(volumes[-20:])
    recent_vol = statistics.mean(volumes[-5:])
    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

    if vol_ratio >= 5.0:
        vol_score = 20
    elif vol_ratio >= 3.0:
        vol_score = 16
    elif vol_ratio >= 2.0:
        vol_score = 12
    elif vol_ratio >= 1.5:
        vol_score = 8
    else:
        vol_score = 4

    components["volume_spike"] = {
        "score": vol_score,
        "vol_ratio": round(vol_ratio, 2),
        "avg_vol": round(avg_vol, 0),
        "recent_vol": round(recent_vol, 0),
    }

    # 2. PRICE ACCELERATION (0-20 points)
    # Count consecutive green bars and check body size increase
    green_streak = 0
    bodies = []
    for i in range(-1, -11, -1):  # Last 10 bars
        o = opens[i]
        c = closes[i]
        body = abs(c - o)
        bodies.append(body)
        if c > o:
            green_streak += 1
        else:
            break

    if green_streak >= 5:
        accel_score = 20
    elif green_streak >= 4:
        accel_score = 16
    elif green_streak >= 3:
        accel_score = 12
    elif green_streak >= 2:
        accel_score = 8
    else:
        accel_score = 4

    # Bonus if bodies are increasing
    if len(bodies) >= 3 and bodies[0] < bodies[1] < bodies[2]:
        accel_score = min(20, accel_score + 4)

    components["price_acceleration"] = {
        "score": accel_score,
        "green_streak": green_streak,
        "current_body": round(bodies[0], 6) if bodies else 0,
    }

    # 3. VOLATILITY BREAKOUT (0-20 points)
    bb_current = compute_bollinger(closes[-20:], 20, 2)
    bb_previous = compute_bollinger(closes[-40:-20], 20, 2)

    if bb_current and bb_previous:
        vol_expansion = bb_current["width"] / bb_previous["width"] if bb_previous["width"] > 0 else 1.0

        # Expansion after squeeze is the key pump signal
        if bb_previous["width"] < 5 and bb_current["width"] > 10:
            vol_breakout_score = 20  # Squeeze → explosion
        elif vol_expansion >= 2.0:
            vol_breakout_score = 16
        elif vol_expansion >= 1.5:
            vol_breakout_score = 12
        elif bb_current["width"] > 15:
            vol_breakout_score = 8
        else:
            vol_breakout_score = 4

        # Price breaking above upper band
        if current_price > bb_current["upper"]:
            vol_breakout_score = min(20, vol_breakout_score + 4)
    else:
        vol_breakout_score = 5
        vol_expansion = 1.0

    components["volatility_breakout"] = {
        "score": vol_breakout_score,
        "bb_width": round(bb_current["width"], 2) if bb_current else 0,
        "vol_expansion": round(vol_expansion, 2),
        "above_upper": current_price > bb_current["upper"] if bb_current else False,
    }

    # 4. MOMENTUM SURGE (0-20 points)
    # Rate of change: 5-bar ROC vs 20-bar average ROC
    roc_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if closes[-6] > 0 else 0
    rocs_20 = [(closes[i] - closes[i - 6]) / closes[i - 6] * 100
               for i in range(-25, -5) if closes[i - 6] > 0]
    avg_roc = statistics.mean(rocs_20) if rocs_20 else 0

    if roc_5 > avg_roc * 3 and roc_5 > 2:
        momentum_score = 20
    elif roc_5 > avg_roc * 2 and roc_5 > 1:
        momentum_score = 16
    elif roc_5 > avg_roc * 1.5 and roc_5 > 0.5:
        momentum_score = 12
    elif roc_5 > 0:
        momentum_score = 8
    else:
        momentum_score = 2

    components["momentum_surge"] = {
        "score": momentum_score,
        "roc_5bar": round(roc_5, 2),
        "avg_roc_20bar": round(avg_roc, 2),
        "acceleration": round(roc_5 - avg_roc, 2),
    }

    # 5. RESISTANCE BREAK (0-20 points)
    recent_high = max(highs[-lookback:-5]) if len(highs) > lookback else max(highs[-20:])
    if current_price > recent_high:
        break_score = 20
    elif current_price > recent_high * 0.98:
        break_score = 14
    elif current_price > recent_high * 0.95:
        break_score = 8
    else:
        break_score = 4

    components["resistance_break"] = {
        "score": break_score,
        "current_price": round(current_price, 6),
        "recent_high": round(recent_high, 6),
        "pct_from_high": round((current_price - recent_high) / recent_high * 100, 2),
    }

    # Total score
    total_score = sum(c["score"] for c in components.values())

    return {
        "score": total_score,
        "components": components,
        "current_price": round(current_price, 6),
    }


# ============================================================
# DIP/REBOUND SIGNALS (5 components)
# ============================================================

def detect_dip_signals(candles, lookback=48):
    """
    Detect dip/rebound signals. Returns score 0-100 and component breakdown.

    Signals:
    1. RSI extreme oversold — RSI(3) < 15 or RSI(6) < 25
    2. Volume climax — capitulation volume at the bottom
    3. Bullish divergence — price lower low, RSI higher low
    4. Long lower wick — rejection of lows
    5. Mean reversion — price > 2 std below MA
    """
    if len(candles) < lookback + 5:
        return {"score": 0, "reason": "insufficient_data", "components": {}}

    closes = [float(c["close"]) for c in candles]
    volumes = [float(c["volume"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    opens = [float(c["open"]) for c in candles]

    current_price = closes[-1]
    components = {}

    # 1. RSI EXTREME OVERSOLD (0-20 points)
    rsi_3 = compute_rsi(closes, 3)
    rsi_6 = compute_rsi(closes, 6)
    rsi_14 = compute_rsi(closes, 14)

    if rsi_3 < 10:
        rsi_score = 20
    elif rsi_3 < 15:
        rsi_score = 18
    elif rsi_3 < 20:
        rsi_score = 14
    elif rsi_3 < 25:
        rsi_score = 10
    elif rsi_6 < 25:
        rsi_score = 8
    else:
        rsi_score = 2

    components["rsi_extreme_oversold"] = {
        "score": rsi_score,
        "rsi_3": round(rsi_3, 1),
        "rsi_6": round(rsi_6, 1),
        "rsi_14": round(rsi_14, 1),
    }

    # 2. VOLUME CLIMAX (0-20 points)
    # Capitulation = extreme volume at the bottom (panic selling)
    avg_vol = statistics.mean(volumes[-lookback:-5]) if len(volumes) > lookback else statistics.mean(volumes[-20:])
    last_vol = volumes[-1]
    vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0

    # Volume climax is more convincing if price is also down
    price_drop = (closes[-1] - closes[-6]) / closes[-6] * 100 if closes[-6] > 0 else 0

    if vol_ratio >= 5.0 and price_drop < -3:
        climax_score = 20
    elif vol_ratio >= 4.0 and price_drop < -2:
        climax_score = 16
    elif vol_ratio >= 3.0 and price_drop < -1:
        climax_score = 12
    elif vol_ratio >= 2.0:
        climax_score = 8
    else:
        climax_score = 4

    components["volume_climax"] = {
        "score": climax_score,
        "vol_ratio": round(vol_ratio, 2),
        "price_drop_5bar": round(price_drop, 2),
    }

    # 3. BULLISH DIVERGENCE (0-20 points)
    # Price makes lower low, but RSI makes higher low
    # Compare last 20 bars for divergence
    if len(closes) >= 30:
        # Find last two significant lows
        recent_lows = []
        recent_rsis = []
        for i in range(-20, -2):
            if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
                recent_lows.append((i, lows[i]))
                sub_closes = closes[:i + 1]
                if len(sub_closes) >= 4:
                    recent_rsis.append((i, compute_rsi(sub_closes, 3)))

        # Check for divergence: lower low in price, higher low in RSI
        if len(recent_lows) >= 2 and len(recent_rsis) >= 2:
            last_low = recent_lows[-1]
            prev_low = recent_lows[-2]
            last_rsi = recent_rsis[-1]
            prev_rsi = recent_rsis[-2]

            if last_low[1] < prev_low[1] and last_rsi[1] > prev_rsi[1]:
                div_score = 20  # Classic bullish divergence
            elif last_low[1] < prev_low[1] and last_rsi[1] >= prev_rsi[1]:
                div_score = 14
            elif last_low[1] <= prev_low[1] * 1.02:
                div_score = 8  # Near-equal lows (potential double bottom)
            else:
                div_score = 4
        else:
            # Not enough data for divergence, give partial credit for extreme oversold
            div_score = 8 if rsi_3 < 20 else 4
    else:
        div_score = 4

    components["bullish_divergence"] = {
        "score": div_score,
    }

    # 4. LONG LOWER WICK (0-20 points)
    # Rejection of lows = long wick relative to body
    last_candle = candles[-1]
    last_open = float(last_candle["open"])
    last_close = float(last_candle["close"])
    last_low = float(last_candle["low"])
    last_high = float(last_candle["high"])

    body_size = abs(last_close - last_open)
    lower_wick = min(last_open, last_close) - last_low
    total_range = last_high - last_low

    if total_range > 0:
        wick_ratio = lower_wick / total_range
        body_ratio = body_size / total_range if body_size > 0 else 0

        # Hammer candlestick pattern: long lower wick, small body
        if wick_ratio >= 0.6 and body_ratio <= 0.3:
            wick_score = 20
        elif wick_ratio >= 0.5 and body_ratio <= 0.4:
            wick_score = 16
        elif wick_ratio >= 0.4:
            wick_score = 12
        elif wick_ratio >= 0.3:
            wick_score = 8
        else:
            wick_score = 4
    else:
        wick_score = 4

    components["long_lower_wick"] = {
        "score": wick_score,
        "wick_ratio": round(wick_ratio, 2) if total_range > 0 else 0,
        "body_ratio": round(body_ratio, 2) if total_range > 0 else 0,
        "is_hammer": wick_ratio >= 0.6 and body_ratio <= 0.3 if total_range > 0 else False,
    }

    # 5. MEAN REVERSION (0-20 points)
    # Price > 2 std below moving average
    bb = compute_bollinger(closes[-20:], 20, 2)

    if bb:
        distance_from_lower = (current_price - bb["lower"]) / bb["sma"] * 100 if bb["sma"] > 0 else 0

        if distance_from_lower < -5:
            mr_score = 20  # Extremely oversold
        elif distance_from_lower < -3:
            mr_score = 16
        elif distance_from_lower < -1:
            mr_score = 12
        elif distance_from_lower < 0:
            mr_score = 8  # Below average but not extreme
        else:
            mr_score = 4
    else:
        mr_score = 4
        distance_from_lower = 0

    components["mean_reversion"] = {
        "score": mr_score,
        "distance_from_lower_bb": round(distance_from_lower, 2),
        "bb_sma": round(bb["sma"], 6) if bb else 0,
        "bb_lower": round(bb["lower"], 6) if bb else 0,
    }

    # Total score
    total_score = sum(c["score"] for c in components.values())

    return {
        "score": total_score,
        "components": components,
        "current_price": round(current_price, 6),
    }


# ============================================================
# BACKTEST FUNCTIONS
# ============================================================

def backtest_pump_signals(candles):
    """
    Backtest pump signals: when pump score > threshold, measure subsequent price excursion.

    For each bar where pump score >= 50:
    - Record entry price
    - Measure max price in next 24h (48 bars of 5min)
    - Calculate max excursion %
    """
    if len(candles) < 100:
        return {"error": "insufficient_data"}

    signals = []
    forward_bars = 48  # 4 hours of 5-min bars

    for i in range(50, len(candles) - forward_bars):
        window = candles[:i + 1]
        pump = detect_pump_signals(window, lookback=48)

        if pump["score"] >= 50:
            entry_price = float(candles[i]["close"])
            future_candles = candles[i + 1:i + 1 + forward_bars]
            future_highs = [float(c["high"]) for c in future_candles]
            future_lows = [float(c["low"]) for c in future_candles]

            if future_highs:
                max_price = max(future_highs)
                min_price = min(future_lows)
                max_excursion = (max_price - entry_price) / entry_price * 100
                max_drawdown = (min_price - entry_price) / entry_price * 100

                signals.append({
                    "bar_index": i,
                    "entry_price": round(entry_price, 6),
                    "pump_score": pump["score"],
                    "max_excursion_pct": round(max_excursion, 2),
                    "max_drawdown_pct": round(max_drawdown, 2),
                    "hit_tp_5": max_excursion >= 5,
                    "hit_tp_10": max_excursion >= 10,
                    "hit_tp_20": max_excursion >= 20,
                    "hit_tp_40": max_excursion >= 40,
                })

    if not signals:
        return {"total_signals": 0}

    return {
        "total_signals": len(signals),
        "avg_max_excursion": round(statistics.mean(s["max_excursion_pct"] for s in signals), 2),
        "avg_max_drawdown": round(statistics.mean(s["max_drawdown_pct"] for s in signals), 2),
        "hit_rate_5pct": round(sum(1 for s in signals if s["hit_tp_5"]) / len(signals) * 100, 1),
        "hit_rate_10pct": round(sum(1 for s in signals if s["hit_tp_10"]) / len(signals) * 100, 1),
        "hit_rate_20pct": round(sum(1 for s in signals if s["hit_tp_20"]) / len(signals) * 100, 1),
        "hit_rate_40pct": round(sum(1 for s in signals if s["hit_tp_40"]) / len(signals) * 100, 1),
        "avg_pump_score": round(statistics.mean(s["pump_score"] for s in signals), 1),
        "best_excursion": round(max(s["max_excursion_pct"] for s in signals), 2),
        "worst_drawdown": round(min(s["max_drawdown_pct"] for s in signals), 2),
    }


def backtest_dip_signals(candles):
    """
    Backtest dip signals: when dip score > threshold, measure subsequent bounce.

    For each bar where dip score >= 50:
    - Record entry price
    - Measure max price in next 24h
    - Calculate bounce return %
    """
    if len(candles) < 100:
        return {"error": "insufficient_data"}

    signals = []
    forward_bars = 48

    for i in range(50, len(candles) - forward_bars):
        window = candles[:i + 1]
        dip = detect_dip_signals(window, lookback=48)

        if dip["score"] >= 50:
            entry_price = float(candles[i]["close"])
            future_candles = candles[i + 1:i + 1 + forward_bars]
            future_highs = [float(c["high"]) for c in future_candles]
            future_lows = [float(c["low"]) for c in future_candles]

            if future_highs:
                max_price = max(future_highs)
                min_price = min(future_lows)
                bounce_return = (max_price - entry_price) / entry_price * 100
                further_drop = (min_price - entry_price) / entry_price * 100

                signals.append({
                    "bar_index": i,
                    "entry_price": round(entry_price, 6),
                    "dip_score": dip["score"],
                    "bounce_return_pct": round(bounce_return, 2),
                    "further_drop_pct": round(further_drop, 2),
                    "bounce_2pct": bounce_return >= 2,
                    "bounce_5pct": bounce_return >= 5,
                    "bounce_10pct": bounce_return >= 10,
                    "bounce_20pct": bounce_return >= 20,
                })

    if not signals:
        return {"total_signals": 0}

    return {
        "total_signals": len(signals),
        "avg_bounce_return": round(statistics.mean(s["bounce_return_pct"] for s in signals), 2),
        "avg_further_drop": round(statistics.mean(s["further_drop_pct"] for s in signals), 2),
        "hit_rate_2pct": round(sum(1 for s in signals if s["bounce_2pct"]) / len(signals) * 100, 1),
        "hit_rate_5pct": round(sum(1 for s in signals if s["bounce_5pct"]) / len(signals) * 100, 1),
        "hit_rate_10pct": round(sum(1 for s in signals if s["bounce_10pct"]) / len(signals) * 100, 1),
        "hit_rate_20pct": round(sum(1 for s in signals if s["bounce_20pct"]) / len(signals) * 100, 1),
        "avg_dip_score": round(statistics.mean(s["dip_score"] for s in signals), 1),
        "best_bounce": round(max(s["bounce_return_pct"] for s in signals), 2),
        "worst_drop": round(min(s["further_drop_pct"] for s in signals), 2),
    }


# ============================================================
# MAIN
# ============================================================

def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_HOURS * 3600

    print(f"=" * 70, flush=True)
    print(f"PUMP & DIP DETECTION SCANNER", flush=True)
    print(f"Scanning {len(COIN_UNIVERSE)} coins, {WINDOW_HOURS}h lookback", flush=True)
    print(f"=" * 70, flush=True)

    # Phase 1: Live scan
    print(f"\n[1/3] LIVE SCAN — Fetching candles for all coins...", flush=True)
    coin_data = {}
    for idx, coin in enumerate(COIN_UNIVERSE, 1):
        candles = fetch_candles(client, coin, start, now)
        if len(candles) >= 50:
            coin_data[coin] = candles
            print(f"  [{idx}/{len(COIN_UNIVERSE)}] {coin}: {len(candles)} candles ✓", flush=True)
        else:
            print(f"  [{idx}/{len(COIN_UNIVERSE)}] {coin}: {len(candles)} candles ✗ (skipped)", flush=True)

    # Phase 2: Signal detection
    print(f"\n[2/3] SIGNAL DETECTION — Scanning for pump and dip signals...", flush=True)

    pump_opportunities = []
    dip_opportunities = []

    for coin, candles in coin_data.items():
        pump = detect_pump_signals(candles, lookback=48)
        dip = detect_dip_signals(candles, lookback=48)

        pump_entry = {
            "coin": coin,
            "pump_score": pump["score"],
            "current_price": pump.get("current_price"),
            "components": pump.get("components", {}),
        }

        dip_entry = {
            "coin": coin,
            "dip_score": dip["score"],
            "current_price": dip.get("current_price"),
            "components": dip.get("components", {}),
        }

        pump_opportunities.append(pump_entry)
        dip_opportunities.append(dip_entry)

    # Sort by score
    pump_opportunities.sort(key=lambda x: x["pump_score"], reverse=True)
    dip_opportunities.sort(key=lambda x: x["dip_score"], reverse=True)

    # Phase 3: Backtest on coins with sufficient data
    print(f"\n[3/3] BACKTEST VALIDATION — Testing signal predictive power...", flush=True)

    backtest_results = {}
    for coin, candles in coin_data.items():
        if len(candles) >= 200:
            pump_bt = backtest_pump_signals(candles)
            dip_bt = backtest_dip_signals(candles)
            backtest_results[coin] = {
                "pump_backtest": pump_bt,
                "dip_backtest": dip_bt,
            }
            print(f"  {coin}: pump={pump_bt.get('total_signals', 0)} signals, "
                  f"dip={dip_bt.get('total_signals', 0)} signals", flush=True)

    # Print results
    print(f"\n{'='*70}", flush=True)
    print("TOP PUMP OPPORTUNITIES (score >= 50)", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'Rank':>4} | {'Coin':<16} | {'Score':>5} | {'Price':>10} | {'Vol Ratio':>9} | {'Green Streak':>10} | {'Momentum':>9}", flush=True)
    print(f"{'-'*4}-+-{'-'*16}-+-{'-'*5}-+-{'-'*10}-+-{'-'*9}-+-{'-'*10}-+-{'-'*9}", flush=True)

    for i, p in enumerate(pump_opportunities[:15], 1):
        vol_comp = p["components"].get("volume_spike", {})
        accel_comp = p["components"].get("price_acceleration", {})
        mom_comp = p["components"].get("momentum_surge", {})
        print(f"{i:>4} | {p['coin']:<16} | {p['pump_score']:>5} | "
              f"${p['current_price']:>9.6f} | {vol_comp.get('vol_ratio', 'N/A'):>8.1f}x | "
              f"{accel_comp.get('green_streak', 'N/A'):>10} | "
              f"{mom_comp.get('roc_5bar', 'N/A'):>7.1f}%", flush=True)

    print(f"\n{'='*70}", flush=True)
    print("TOP DIP/BOUNCE OPPORTUNITIES (score >= 50)", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'Rank':>4} | {'Coin':<16} | {'Score':>5} | {'Price':>10} | {'RSI(3)':>6} | {'Vol Climax':>10} | {'Hammer':>6}", flush=True)
    print(f"{'-'*4}-+-{'-'*16}-+-{'-'*5}-+-{'-'*10}-+-{'-'*6}-+-{'-'*10}-+-{'-'*6}", flush=True)

    for i, d in enumerate(dip_opportunities[:15], 1):
        rsi_comp = d["components"].get("rsi_extreme_oversold", {})
        vol_comp = d["components"].get("volume_climax", {})
        wick_comp = d["components"].get("long_lower_wick", {})
        print(f"{i:>4} | {d['coin']:<16} | {d['dip_score']:>5} | "
              f"${d['current_price']:>9.6f} | {rsi_comp.get('rsi_3', 'N/A'):>5.1f} | "
              f"{vol_comp.get('vol_ratio', 'N/A'):>9.1f}x | "
              f"{'✓' if wick_comp.get('is_hammer') else '✗':>6}", flush=True)

    # Backtest summary
    print(f"\n{'='*70}", flush=True)
    print("BACKTEST VALIDATION SUMMARY", flush=True)
    print(f"{'='*70}", flush=True)

    for coin, bt in backtest_results.items():
        pump_bt = bt["pump_backtest"]
        dip_bt = bt["dip_backtest"]

        print(f"\n  {coin}:", flush=True)

        if pump_bt.get("total_signals", 0) > 0:
            print(f"    PUMP signals: {pump_bt['total_signals']}", flush=True)
            print(f"      Avg max excursion: {pump_bt['avg_max_excursion']:.2f}%", flush=True)
            print(f"      Hit rates: 5%={pump_bt['hit_rate_5pct']}%, "
                  f"10%={pump_bt['hit_rate_10pct']}%, "
                  f"20%={pump_bt['hit_rate_20pct']}%, "
                  f"40%={pump_bt.get('hit_rate_40pct', 0)}%", flush=True)
        else:
            print(f"    PUMP signals: none", flush=True)

        if dip_bt.get("total_signals", 0) > 0:
            print(f"    DIP signals:  {dip_bt['total_signals']}", flush=True)
            print(f"      Avg bounce return: {dip_bt['avg_bounce_return']:.2f}%", flush=True)
            print(f"      Hit rates: 2%={dip_bt['hit_rate_2pct']}%, "
                  f"5%={dip_bt['hit_rate_5pct']}%, "
                  f"10%={dip_bt['hit_rate_10pct']}%, "
                  f"20%={dip_bt['hit_rate_20pct']}%", flush=True)
        else:
            print(f"    DIP signals:  none", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_hours": WINDOW_HOURS,
        "coins_scanned": len(coin_data),
        "total_coins": len(COIN_UNIVERSE),
        "pump_opportunities": pump_opportunities,
        "dip_opportunities": dip_opportunities,
        "backtest_results": backtest_results,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\n\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
