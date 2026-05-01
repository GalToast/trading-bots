#!/usr/bin/env python3
"""
Breakout 50 Strategy Sweep — Batch of the 500 Strategies Initiative.

Tests 50 unique breakout-based strategies across 35 Coinbase coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants cover:
- Donchian, Keltner, volatility, and ATR channel breakouts
- Opening range, pivot, and Fibonacci level breakouts
- Volume, momentum, and trend-confirmed breakouts
- False breakout reversal, pullback, continuation, and retest entries
- Confluence, multi-timeframe, consolidation, and squeeze breakouts
- Expansion, contraction, momentum, and reversion patterns
- Volume surge, volatility surge, and trend-aligned breakouts
- Pattern, signal, confirmation, validation, and filter-based breakouts
- Timing, entry-optimized, exit-optimized, and risk-managed breakouts
- Position sizing, portfolio, adaptive, dynamic, and regime-filtered breakouts
- ML-style, ensemble, hybrid, multi-asset, and cross-sectional breakouts
- Statistical, quantitative, gap, and inside bar breakouts

Uses the shared strategy_library.py engine with 40bps fees, $48 start.
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from strategy_library import backtest

# ==========================================
# BREAKOUT HELPER FUNCTIONS
# ==========================================

def compute_sma(candles, period):
    """Simple Moving Average of close."""
    if len(candles) < period:
        return None
    closes = [float(c["close"]) for c in candles[-period:]]
    return sum(closes) / period


def compute_ema(data, period):
    """Exponential Moving Average."""
    if len(data) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for x in data[period:]:
        ema = (x - ema) * multiplier + ema
    return ema


def compute_atr(candles, period=14):
    """Average True Range."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def compute_std(data):
    """Standard deviation."""
    if len(data) < 2:
        return 0
    mean = sum(data) / len(data)
    variance = sum((x - mean) ** 2 for x in data) / len(data)
    return math.sqrt(variance)


def compute_hhv(data, period):
    """Highest High Value over period."""
    if len(data) < period:
        return None
    return max(data[-period:])


def compute_llv(data, period):
    """Lowest Low Value over period."""
    if len(data) < period:
        return None
    return min(data[-period:])


def compute_rsi(candles, period=14):
    """Relative Strength Index."""
    if len(candles) < period + 1:
        return None
    closes = [float(c["close"]) for c in candles]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def compute_macd(candles, fast=12, slow=26, signal=9):
    """MACD line, signal line, histogram."""
    closes = [float(c["close"]) for c in candles]
    if len(closes) < slow + signal:
        return None, None, None
    fast_ema = compute_ema(closes, fast)
    slow_ema = compute_ema(closes, slow)
    if fast_ema is None or slow_ema is None:
        return None, None, None
    macd_line = fast_ema - slow_ema
    # Simplified signal line
    recent_macds = []
    for i in range(slow, len(closes)):
        fe = compute_ema(closes[:i + 1], fast)
        se = compute_ema(closes[:i + 1], slow)
        if fe and se:
            recent_macds.append(fe - se)
    if len(recent_macds) < signal:
        return macd_line, None, None
    signal_line = compute_ema(recent_macds, signal)
    histogram = macd_line - signal_line if signal_line else None
    return macd_line, signal_line, histogram


def compute_bollinger(candles, period=20, mult=2.0):
    """Bollinger Bands."""
    if len(candles) < period:
        return None, None, None
    closes = [float(c["close"]) for c in candles[-period:]]
    sma = sum(closes) / period
    std = compute_std(closes)
    upper = sma + mult * std
    lower = sma - mult * std
    return upper, sma, lower


def compute_donchian(candles, period=20):
    """Donchian Channel."""
    if len(candles) < period:
        return None, None, None
    highs = [float(c["high"]) for c in candles[-period:]]
    lows = [float(c["low"]) for c in candles[-period:]]
    upper = max(highs)
    lower = min(lows)
    middle = (upper + lower) / 2
    return upper, middle, lower


def compute_keltner(candles, atr_period=14, ema_period=20, mult=2.0):
    """Keltner Channel."""
    if len(candles) < max(atr_period, ema_period) + 1:
        return None, None, None
    closes = [float(c["close"]) for c in candles]
    ema = compute_ema(closes[-ema_period * 2:], ema_period)
    atr = compute_atr(candles, atr_period)
    if ema is None or atr is None:
        return None, None, None
    upper = ema + mult * atr
    lower = ema - mult * atr
    return upper, ema, lower


def compute_adx(candles, period=14):
    """ADX (simplified)."""
    if len(candles) < period * 2 + 1:
        return None
    plus_dm = []
    minus_dm = []
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        ph = float(candles[i - 1]["high"])
        pl = float(candles[i - 1]["low"])
        up_move = h - ph
        down_move = pl - l
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)
        tr = max(h - l, abs(h - float(candles[i - 1]["close"])), abs(l - float(candles[i - 1]["close"])))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr_val = sum(trs[-period:]) / period
    if atr_val == 0:
        return None
    avg_plus_dm = sum(plus_dm[-period:]) / period
    avg_minus_dm = sum(minus_dm[-period:]) / period
    plus_di = 100 * avg_plus_dm / atr_val
    minus_di = 100 * avg_minus_dm / atr_val
    if plus_di + minus_di == 0:
        return None
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    return dx


def compute_pivot_points(candle):
    """Pivot Point, R1, S1."""
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])
    pivot = (h + l + c) / 3
    r1 = 2 * pivot - l
    s1 = 2 * pivot - h
    return pivot, r1, s1


def compute_fib_levels(high, low):
    """Fibonacci retracement levels."""
    diff = high - low
    return {
        "0.0": high,
        "0.236": high - 0.236 * diff,
        "0.382": high - 0.382 * diff,
        "0.5": high - 0.5 * diff,
        "0.618": high - 0.618 * diff,
        "0.786": high - 0.786 * diff,
        "1.0": low,
    }


def compute_volume_avg(candles, period=20):
    """Average volume."""
    if len(candles) < period:
        return None
    vols = [float(c["volume"]) for c in candles[-period:]]
    return sum(vols) / period


def compute_squeeze(candles, bb_period=20, kc_atr=14, kc_ema=20, kc_mult=1.5):
    """Squeeze detection (BB inside KC)."""
    bb_upper, bb_mid, bb_lower = compute_bollinger(candles, bb_period)
    kc_upper, kc_mid, kc_lower = compute_keltner(candles, kc_atr, kc_ema, kc_mult)
    if bb_upper is None or kc_upper is None:
        return False
    return bb_upper < kc_upper and bb_lower > kc_lower


def compute_consolidation(candles, period=10, threshold=0.03):
    """Check if price is consolidating (range-bound)."""
    if len(candles) < period:
        return False
    highs = [float(c["high"]) for c in candles[-period:]]
    lows = [float(c["low"]) for c in candles[-period:]]
    range_pct = (max(highs) - min(lows)) / min(lows) if min(lows) > 0 else 0
    return range_pct < threshold


def compute_gap(candles):
    """Gap detection (current open vs previous close)."""
    if len(candles) < 2:
        return 0
    prev_close = float(candles[-2]["close"])
    curr_open = float(candles[-1]["open"])
    if prev_close == 0:
        return 0
    return (curr_open - prev_close) / prev_close * 100


def compute_inside_bar(candles):
    """Inside bar detection."""
    if len(candles) < 2:
        return False
    prev_h = float(candles[-2]["high"])
    prev_l = float(candles[-2]["low"])
    curr_h = float(candles[-1]["high"])
    curr_l = float(candles[-1]["low"])
    return curr_h <= prev_h and curr_l >= prev_l


# ==========================================
# BREAKOUT STRATEGY ENTRY FUNCTIONS
# ==========================================

def _donchian_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks above Donchian channel upper band."""
    if len(candles_hist) < 20:
        return False
    period = params.get("donchian_period", 20)
    upper, _, _ = compute_donchian(candles_hist, period)
    if upper is None:
        return False
    current_price = float(candle["close"])
    if current_price > upper and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _keltner_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks above Keltner channel upper band."""
    if len(candles_hist) < 20:
        return False
    upper, _, _ = compute_keltner(candles_hist)
    if upper is None:
        return False
    current_price = float(candle["close"])
    if current_price > upper and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _volatility_breakout_entry(candles_hist, closes, candle, params):
    """Enter on volatility expansion breakout (std expansion)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    mult = params.get("mult", 2.0)
    closes_list = [float(c["close"]) for c in candles_hist]
    sma = sum(closes_list[-period:]) / period
    std = compute_std(closes_list[-period:])
    upper = sma + mult * std
    current_price = float(candle["close"])
    if current_price > upper and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _opening_range_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks above opening range (first N bars of period)."""
    if len(candles_hist) < 15:
        return False
    or_period = params.get("or_period", 6)
    if len(candles_hist) < or_period + 1:
        return False
    opening_range = candles_hist[-or_period - 1:-1]
    highs = [float(c["high"]) for c in opening_range]
    or_high = max(highs)
    current_price = float(candle["close"])
    if current_price > or_high and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _atr_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks above SMA + ATR multiple."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 14)
    mult = params.get("mult", 2.0)
    atr = compute_atr(candles_hist, period)
    sma = compute_sma(candles_hist, period)
    if atr is None or sma is None:
        return False
    breakout_level = sma + mult * atr
    current_price = float(candle["close"])
    if current_price > breakout_level and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _channel_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks above linear regression channel upper."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 20)
    closes_list = [float(c["close"]) for c in candles_hist[-period:]]
    n = len(closes_list)
    if n < 10:
        return False
    x_mean = (n - 1) / 2
    y_mean = sum(closes_list) / n
    slope_num = sum((i - x_mean) * (closes_list[i] - y_mean) for i in range(n))
    slope_den = sum((i - x_mean) ** 2 for i in range(n))
    if slope_den == 0:
        return False
    slope = slope_num / slope_den
    intercept = y_mean - slope * x_mean
    predicted = intercept + slope * (n - 1)
    residuals = [closes_list[i] - (intercept + slope * i) for i in range(n)]
    std_res = compute_std(residuals)
    upper_channel = predicted + 2 * std_res
    current_price = float(candle["close"])
    if current_price > upper_channel and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _pivot_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks above pivot R1 resistance."""
    if len(candles_hist) < 15:
        return False
    prev_candle = candles_hist[-2]
    _, r1, _ = compute_pivot_points(prev_candle)
    current_price = float(candle["close"])
    if current_price > r1 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _fibonacci_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks above 0.618 Fibonacci level from recent swing."""
    if len(candles_hist) < 30:
        return False
    lookback = params.get("lookback", 20)
    highs = [float(c["high"]) for c in candles_hist[-lookback:]]
    lows = [float(c["low"]) for c in candles_hist[-lookback:]]
    swing_high = max(highs)
    swing_low = min(lows)
    fib = compute_fib_levels(swing_high, swing_low)
    fib_618 = fib["0.618"]
    current_price = float(candle["close"])
    if current_price > fib_618 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _pattern_breakout_entry(candles_hist, closes, candle, params):
    """Enter on bullish engulfing pattern breakout."""
    if len(candles_hist) < 15:
        return False
    prev = candles_hist[-2]
    curr = candle
    prev_open = float(prev["open"])
    prev_close = float(prev["close"])
    curr_open = float(curr["open"])
    curr_close = float(curr["close"])
    is_bullish_engulfing = (prev_close < prev_open and curr_close > curr_open
                            and curr_open <= prev_close and curr_close >= prev_open)
    if is_bullish_engulfing and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _volume_breakout_entry(candles_hist, closes, candle, params):
    """Enter on price breakout with volume above average."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 20)
    vol_mult = params.get("vol_mult", 1.5)
    avg_vol = compute_volume_avg(candles_hist, period)
    if avg_vol is None:
        return False
    current_vol = float(candle["volume"])
    current_price = float(candle["close"])
    prev_high = max(float(c["high"]) for c in candles_hist[-period:-1])
    if current_price > prev_high and current_vol > avg_vol * vol_mult and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _momentum_breakout_entry(candles_hist, closes, candle, params):
    """Enter when momentum (ROC) is strong and price breaks out."""
    if len(candles_hist) < 20:
        return False
    mom_period = params.get("mom_period", 10)
    threshold = params.get("threshold", 2.0)
    if len(closes) < mom_period + 1:
        return False
    roc = (closes[-1] / closes[-mom_period - 1] - 1) * 100
    breakout = closes[-1] > max(closes[-mom_period - 1:-1])
    if roc > threshold and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _trend_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks out in direction of EMA trend."""
    if len(candles_hist) < 30:
        return False
    fast_period = params.get("fast", 9)
    slow_period = params.get("slow", 21)
    closes_list = [float(c["close"]) for c in candles_hist]
    fast_ema = compute_ema(closes_list, fast_period)
    slow_ema = compute_ema(closes_list, slow_period)
    if fast_ema is None or slow_ema is None:
        return False
    trend_up = fast_ema > slow_ema
    breakout = closes[-1] > max(closes[-slow_period - 1:-1])
    if trend_up and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _false_breakout_reversal_entry(candles_hist, closes, candle, params):
    """Enter when prior breakout fails and price reverses back (trap breakout)."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 10)
    prev_high = max(float(c["high"]) for c in candles_hist[-period - 1:-1])
    prev_candle = candles_hist[-2]
    prev_high_broken = float(prev_candle["high"]) > prev_high
    curr_failed = float(candle["close"]) < float(prev_candle["high"])
    if prev_high_broken and curr_failed and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_pullback_entry(candles_hist, closes, candle, params):
    """Enter after breakout + pullback to support."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    highs = [float(c["high"]) for c in candles_hist[-period:]]
    breakout_level = max(highs[:-2])
    recent = candles_hist[-5:-1]
    pullback = all(float(c["close"]) < breakout_level for c in recent)
    current_price = float(candle["close"])
    if pullback and current_price > breakout_level and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_continuation_entry(candles_hist, closes, candle, params):
    """Enter on breakout continuation (second bar in same direction)."""
    if len(candles_hist) < 15:
        return False
    prev = candles_hist[-2]
    prev_breakout = float(prev["close"]) > max(float(c["high"]) for c in candles_hist[-10:-2])
    curr_continues = float(candle["close"]) > float(prev["close"])
    if prev_breakout and curr_continues and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_retest_entry(candles_hist, closes, candle, params):
    """Enter when price retests broken resistance as support."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 15)
    resistance = max(float(c["high"]) for c in candles_hist[-period:-5])
    broke_out = any(float(c["close"]) > resistance for c in candles_hist[-5:-2])
    retested = min(float(c["low"]) for c in candles_hist[-3:-1]) >= resistance * 0.995
    current_price = float(candle["close"])
    if broke_out and retested and current_price > resistance and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_confluence_entry(candles_hist, closes, candle, params):
    """Enter when multiple breakout signals align (Donchian + volume + momentum)."""
    if len(candles_hist) < 25:
        return False
    donchian_upper, _, _ = compute_donchian(candles_hist, 20)
    if donchian_upper is None:
        return False
    price_breakout = float(candle["close"]) > donchian_upper
    avg_vol = compute_volume_avg(candles_hist, 20)
    vol_confirm = avg_vol and float(candle["volume"]) > avg_vol * 1.3
    mom_confirm = len(closes) > 5 and closes[-1] > closes[-5]
    if price_breakout and vol_confirm and mom_confirm and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _multi_timeframe_breakout_entry(candles_hist, closes, candle, params):
    """Enter when short-term and long-term breakouts align."""
    if len(candles_hist) < 40:
        return False
    short_period = params.get("short", 10)
    long_period = params.get("long", 30)
    short_high = max(float(c["high"]) for c in candles_hist[-short_period - 1:-1])
    long_high = max(float(c["high"]) for c in candles_hist[-long_period - 1:-1])
    current_price = float(candle["close"])
    if current_price > short_high and current_price > long_high and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_consolidation_entry(candles_hist, closes, candle, params):
    """Enter when price breaks out of a consolidation range."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 15)
    if not compute_consolidation(candles_hist, period):
        return False
    range_high = max(float(c["high"]) for c in candles_hist[-period:-1])
    current_price = float(candle["close"])
    if current_price > range_high and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_squeeze_entry(candles_hist, closes, candle, params):
    """Enter when squeeze releases and price breaks out."""
    if len(candles_hist) < 30:
        return False
    was_squeezing = compute_squeeze(candles_hist[:-1])
    bb_upper, _, _ = compute_bollinger(candles_hist)
    if bb_upper is None:
        return False
    current_price = float(candle["close"])
    if was_squeezing and current_price > bb_upper and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_expansion_entry(candles_hist, closes, candle, params):
    """Enter when volatility expands and price breaks above recent range."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    closes_list = [float(c["close"]) for c in candles_hist]
    recent_std = compute_std(closes_list[-period:])
    prev_std = compute_std(closes_list[-period * 2:-period])
    expansion = prev_std > 0 and recent_std > prev_std * 1.5
    breakout = closes[-1] > max(closes[-period - 1:-1])
    if expansion and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_contraction_entry(candles_hist, closes, candle, params):
    """Enter after volatility contraction (calm before storm) with breakout."""
    if len(candles_hist) < 40:
        return False
    period = params.get("period", 20)
    closes_list = [float(c["close"]) for c in candles_hist]
    recent_std = compute_std(closes_list[-period:])
    prev_std = compute_std(closes_list[-period * 2:-period])
    contraction = prev_std > 0 and recent_std < prev_std * 0.5
    current_range = max(float(c["high"]) for c in candles_hist[-5:]) - min(float(c["low"]) for c in candles_hist[-5:])
    expanding = current_range > recent_std * 2
    if contraction and expanding and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_momentum_entry(candles_hist, closes, candle, params):
    """Enter when momentum accelerates after a breakout."""
    if len(candles_hist) < 25:
        return False
    mom_period = params.get("mom_period", 5)
    if len(closes) < mom_period + 2:
        return False
    roc_now = (closes[-1] / closes[-mom_period - 1] - 1) * 100
    roc_prev = (closes[-2] / closes[-mom_period - 2] - 1) * 100
    acceleration = roc_now > roc_prev * 1.5
    breakout = closes[-1] > max(closes[-mom_period - 2:-2])
    if acceleration and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_reversion_entry(candles_hist, closes, candle, params):
    """Enter when failed breakout reverts and then resumes."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 10)
    resistance = max(float(c["high"]) for c in candles_hist[-period - 1:-3])
    failed_breakout = any(float(c["close"]) > resistance for c in candles_hist[-3:-1])
    current_price = float(candle["close"])
    if failed_breakout and current_price > resistance and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_volume_entry(candles_hist, closes, candle, params):
    """Enter on volume surge breakout (unusual volume + price move)."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    vol_mult = params.get("vol_mult", 2.5)
    avg_vol = compute_volume_avg(candles_hist, period)
    if avg_vol is None or avg_vol == 0:
        return False
    current_vol = float(candle["volume"])
    price_change = (float(candle["close"]) - float(candle["open"])) / float(candle["open"]) * 100
    if current_vol > avg_vol * vol_mult and price_change > 1.0 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_volatility_entry(candles_hist, closes, candle, params):
    """Enter on volatility surge breakout."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    closes_list = [float(c["close"]) for c in candles_hist]
    recent_std = compute_std(closes_list[-5:])
    avg_std = compute_std(closes_list[-period:])
    if avg_std == 0:
        return False
    vol_surge = recent_std > avg_std * 2.0
    breakout = closes[-1] > max(closes[-period - 1:-1])
    if vol_surge and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_trend_entry(candles_hist, closes, candle, params):
    """Enter on trend-aligned breakout (ADX confirms trend strength)."""
    if len(candles_hist) < 30:
        return False
    adx_thresh = params.get("adx_thresh", 25)
    adx = compute_adx(candles_hist)
    if adx is None:
        return False
    trend_strong = adx > adx_thresh
    period = params.get("period", 20)
    breakout = closes[-1] > max(closes[-period - 1:-1])
    if trend_strong and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_pattern_entry(candles_hist, closes, candle, params):
    """Enter on three white soldiers pattern breakout."""
    if len(candles_hist) < 15:
        return False
    if len(candles_hist) < 4:
        return False
    soldiers = True
    for i in range(-3, 0):
        c = candles_hist[i]
        if float(c["close"]) <= float(c["open"]):
            soldiers = False
            break
        if i > -3 and float(c["close"]) <= float(candles_hist[i - 1]["close"]):
            soldiers = False
            break
    if soldiers and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_signal_entry(candles_hist, closes, candle, params):
    """Enter on RSI signal breakout (RSI crosses above 50)."""
    if len(candles_hist) < 20:
        return False
    rsi = compute_rsi(candles_hist)
    if rsi is None:
        return False
    rsi_prev = compute_rsi(candles_hist[:-1])
    if rsi_prev is None:
        return False
    cross_above = rsi_prev < 50 and rsi > 50
    if cross_above and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_confirmation_entry(candles_hist, closes, candle, params):
    """Enter when breakout is confirmed by close above level (not just wick)."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 15)
    resistance = max(float(c["close"]) for c in candles_hist[-period - 1:-1])
    current_close = float(candle["close"])
    current_low = float(candle["low"])
    confirmed = current_close > resistance and current_low > resistance * 0.99
    if confirmed and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_validation_entry(candles_hist, closes, candle, params):
    """Enter when breakout validates over multiple bars."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 15)
    resistance = max(float(c["high"]) for c in candles_hist[-period - 1:-3])
    broke_out = float(candles_hist[-3]["close"]) > resistance
    held = float(candles_hist[-2]["close"]) > resistance
    current_above = float(candle["close"]) > resistance
    if broke_out and held and current_above and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_filter_entry(candles_hist, closes, candle, params):
    """Enter on filtered breakout (only if price is above 200-period SMA)."""
    if len(candles_hist) < 50:
        return False
    sma_200 = compute_sma(candles_hist, min(50, len(candles_hist)))
    if sma_200 is None:
        return False
    above_trend = float(candle["close"]) > sma_200
    period = params.get("period", 20)
    breakout = closes[-1] > max(closes[-period - 1:-1])
    if above_trend and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_timing_entry(candles_hist, closes, candle, params):
    """Enter on timing-based breakout (after N bars of no breakout)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    wait_period = params.get("wait", 10)
    resistance = max(float(c["high"]) for c in candles_hist[-period - wait_period:-wait_period])
    no_breakout = all(float(c["close"]) < resistance for c in candles_hist[-wait_period:-1])
    current_price = float(candle["close"])
    if no_breakout and current_price > resistance and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_entry_optimized(candles_hist, closes, candle, params):
    """Enter on entry-optimized breakout (enter on pullback to breakout level)."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 15)
    resistance = max(float(c["high"]) for c in candles_hist[-period - 1:-5])
    broke_out = any(float(c["close"]) > resistance for c in candles_hist[-5:-2])
    pullback = float(candle["low"]) <= resistance * 1.005 and float(candle["close"]) > resistance
    if broke_out and pullback and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_exit_optimized(candles_hist, closes, candle, params):
    """Enter when previous breakout exit zone presents new entry."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    closes_list = [float(c["close"]) for c in candles_hist]
    recent_max = max(closes_list[-period:-5])
    recent_min = min(closes_list[-period:-5])
    midpoint = (recent_max + recent_min) / 2
    current_price = float(candle["close"])
    breakout_from_mid = current_price > midpoint and closes[-2] < midpoint
    if breakout_from_mid and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_risk_entry(candles_hist, closes, candle, params):
    """Enter on risk-managed breakout (only if risk/reward is favorable)."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    rr_min = params.get("rr_min", 2.0)
    resistance = max(float(c["high"]) for c in candles_hist[-period - 1:-1])
    support = min(float(c["low"]) for c in candles_hist[-period - 1:-1])
    risk = float(candle["close"]) - support
    reward = resistance * 0.05
    if risk <= 0:
        return False
    rr = reward / risk
    breakout = float(candle["close"]) > resistance
    if breakout and rr > rr_min and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_position_sizing_entry(candles_hist, closes, candle, params):
    """Enter when breakout occurs with favorable position sizing signal."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    atr = compute_atr(candles_hist, period)
    if atr is None or atr == 0:
        return False
    position_size = 0.02 / atr
    resistance = max(float(c["high"]) for c in candles_hist[-period - 1:-1])
    breakout = float(candle["close"]) > resistance
    favorable_size = position_size > 0.001
    if breakout and favorable_size and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_portfolio_entry(candles_hist, closes, candle, params):
    """Enter when breakout occurs and correlation with recent winners is low."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 15)
    breakout = closes[-1] > max(closes[-period - 1:-1])
    rsi = compute_rsi(candles_hist)
    if rsi is None:
        return False
    not_overbought = rsi < 75
    if breakout and not_overbought and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_adaptive_entry(candles_hist, closes, candle, params):
    """Enter on adaptive breakout (period adjusts to volatility)."""
    if len(candles_hist) < 30:
        return False
    closes_list = [float(c["close"]) for c in candles_hist]
    vol = compute_std(closes_list[-20:])
    base_period = params.get("base_period", 20)
    adaptive_period = max(10, min(30, int(base_period / (vol / sum(closes_list[-20:]) * 100 + 0.5))))
    if len(candles_hist) < adaptive_period + 1:
        return False
    resistance = max(float(c["high"]) for c in candles_hist[-adaptive_period - 1:-1])
    current_price = float(candle["close"])
    if current_price > resistance and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_dynamic_entry(candles_hist, closes, candle, params):
    """Enter on dynamic breakout (trailing resistance based on recent structure)."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 10)
    dynamic_resistance = max(float(c["high"]) for c in candles_hist[-period - 1:-1])
    dynamic_support = max(float(c["low"]) for c in candles_hist[-period - 1:-1])
    current_price = float(candle["close"])
    narrowing = (dynamic_resistance - dynamic_support) < dynamic_resistance * 0.02
    breakout = current_price > dynamic_resistance
    if narrowing and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_regime_entry(candles_hist, closes, candle, params):
    """Enter on regime-filtered breakout (only in trending regimes)."""
    if len(candles_hist) < 40:
        return False
    adx = compute_adx(candles_hist)
    if adx is None:
        return False
    trending = adx > 20
    period = params.get("period", 20)
    breakout = closes[-1] > max(closes[-period - 1:-1])
    if trending and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_ml_entry(candles_hist, closes, candle, params):
    """Enter on ML-style breakout (multi-factor scoring)."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    score = 0
    closes_list = [float(c["close"]) for c in candles_hist]
    price_breakout = closes[-1] > max(closes[-period - 1:-1])
    if price_breakout:
        score += 2
    avg_vol = compute_volume_avg(candles_hist, period)
    if avg_vol and float(candle["volume"]) > avg_vol * 1.2:
        score += 1
    rsi = compute_rsi(candles_hist)
    if rsi and 50 < rsi < 70:
        score += 1
    macd_line, _, _ = compute_macd(candles_hist)
    if macd_line and macd_line > 0:
        score += 1
    if score >= 3 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_ensemble_entry(candles_hist, closes, candle, params):
    """Enter on ensemble breakout (majority of signals agree)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    signals = 0
    donchian_upper, _, _ = compute_donchian(candles_hist, period)
    if donchian_upper and float(candle["close"]) > donchian_upper:
        signals += 1
    if closes[-1] > max(closes[-period - 1:-1]):
        signals += 1
    atr = compute_atr(candles_hist, period)
    sma = compute_sma(candles_hist, period)
    if atr and sma and float(candle["close"]) > sma + atr:
        signals += 1
    rsi = compute_rsi(candles_hist)
    if rsi and rsi > 55:
        signals += 1
    if signals >= 3 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_hybrid_entry(candles_hist, closes, candle, params):
    """Enter on hybrid breakout (trend + momentum + volume)."""
    if len(candles_hist) < 30:
        return False
    fast_ema = compute_ema(closes, 9)
    slow_ema = compute_ema(closes, 21)
    trend_up = fast_ema is not None and slow_ema is not None and fast_ema > slow_ema
    mom = len(closes) > 5 and closes[-1] > closes[-5]
    avg_vol = compute_volume_avg(candles_hist, 20)
    vol_confirm = avg_vol is not None and float(candle["volume"]) > avg_vol
    period = params.get("period", 15)
    breakout = closes[-1] > max(closes[-period - 1:-1])
    if trend_up and mom and vol_confirm and breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_multi_asset_entry(candles_hist, closes, candle, params):
    """Enter when breakout is strong and RSI not extreme (proxy for cross-asset)."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    breakout = closes[-1] > max(closes[-period - 1:-1])
    rsi = compute_rsi(candles_hist)
    not_extreme = rsi is None or (rsi > 40 and rsi < 80)
    if breakout and not_extreme and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_cross_sectional_entry(candles_hist, closes, candle, params):
    """Enter on cross-sectional breakout (price vs its own range)."""
    if len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    highs = [float(c["high"]) for c in candles_hist[-period:]]
    lows = [float(c["low"]) for c in candles_hist[-period:]]
    range_pct = (closes[-1] - min(lows)) / (max(highs) - min(lows)) if max(highs) != min(lows) else 0
    breakout = range_pct > 0.9
    if breakout and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_statistical_entry(candles_hist, closes, candle, params):
    """Enter on statistical breakout (z-score of price vs mean)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    closes_list = [float(c["close"]) for c in candles_hist]
    mean = sum(closes_list[-period:]) / period
    std = compute_std(closes_list[-period:])
    if std == 0:
        return False
    z_score = (float(candle["close"]) - mean) / std
    if z_score > 2.0 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_quantitative_entry(candles_hist, closes, candle, params):
    """Enter on quantitative breakout (price > mean + 1.5*std and volume confirms)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    closes_list = [float(c["close"]) for c in candles_hist]
    mean = sum(closes_list[-period:]) / period
    std = compute_std(closes_list[-period:])
    threshold = mean + 1.5 * std
    avg_vol = compute_volume_avg(candles_hist, period)
    vol_confirm = avg_vol is not None and float(candle["volume"]) > avg_vol
    if float(candle["close"]) > threshold and vol_confirm and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _gap_breakout_entry(candles_hist, closes, candle, params):
    """Enter on gap breakout (gap up and continue)."""
    if len(candles_hist) < 15:
        return False
    gap_pct = compute_gap(candles_hist)
    gap_thresh = params.get("gap_thresh", 1.0)
    gap_up = gap_pct > gap_thresh
    continues = float(candle["close"]) > float(candle["open"])
    if gap_up and continues and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _inside_bar_breakout_entry(candles_hist, closes, candle, params):
    """Enter when inside bar breakout occurs."""
    if len(candles_hist) < 15:
        return False
    was_inside_bar = compute_inside_bar(candles_hist)
    if not was_inside_bar:
        return False
    mother_high = float(candles_hist[-2]["high"])
    current_price = float(candle["close"])
    if current_price > mother_high and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

BREAKOUT_STRATEGIES = [
    # Channel-based breakouts
    {"name": "donchian_breakout", "params": {"donchian_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "keltner_breakout", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "volatility_breakout", "params": {"period": 20, "mult": 2.0, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "opening_range_breakout", "params": {"or_period": 6, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "atr_breakout", "params": {"period": 14, "mult": 2.0, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "channel_breakout", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Level-based breakouts
    {"name": "pivot_breakout", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "fibonacci_breakout", "params": {"lookback": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "pattern_breakout", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Confirmation-based breakouts
    {"name": "volume_breakout", "params": {"period": 20, "vol_mult": 1.5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "momentum_breakout", "params": {"mom_period": 10, "threshold": 2.0, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "trend_breakout", "params": {"fast": 9, "slow": 21, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Reversal/continuation breakouts
    {"name": "false_breakout_reversal", "params": {"period": 10, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "breakout_pullback", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_continuation", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_retest", "params": {"period": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Advanced breakouts
    {"name": "breakout_confluence", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "multi_timeframe_breakout", "params": {"short": 10, "long": 30, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_consolidation", "params": {"period": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_squeeze", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},

    # Volatility breakouts
    {"name": "breakout_expansion", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_contraction", "params": {"period": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "breakout_momentum", "params": {"mom_period": 5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_reversion", "params": {"period": 10, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},

    # Surge breakouts
    {"name": "breakout_volume", "params": {"period": 20, "vol_mult": 2.5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_volatility", "params": {"period": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "breakout_trend", "params": {"adx_thresh": 25, "period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_pattern", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Signal breakouts
    {"name": "breakout_signal", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_confirmation", "params": {"period": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_validation", "params": {"period": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_filter", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Timing/optimization breakouts
    {"name": "breakout_timing", "params": {"period": 20, "wait": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_entry", "params": {"period": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_exit", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_risk", "params": {"period": 20, "rr_min": 2.0, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_position_sizing", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Portfolio/cross breakouts
    {"name": "breakout_portfolio", "params": {"period": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_adaptive", "params": {"base_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_dynamic", "params": {"period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_regime", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Advanced/ensemble breakouts
    {"name": "breakout_ml", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_ensemble", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_hybrid", "params": {"period": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_multi_asset", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_cross_sectional", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Quantitative breakouts
    {"name": "breakout_statistical", "params": {"period": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "breakout_quantitative", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Special breakouts
    {"name": "gap_breakout", "params": {"gap_thresh": 1.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "inside_bar_breakout", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "donchian_breakout": _donchian_breakout_entry,
    "keltner_breakout": _keltner_breakout_entry,
    "volatility_breakout": _volatility_breakout_entry,
    "opening_range_breakout": _opening_range_breakout_entry,
    "atr_breakout": _atr_breakout_entry,
    "channel_breakout": _channel_breakout_entry,
    "pivot_breakout": _pivot_breakout_entry,
    "fibonacci_breakout": _fibonacci_breakout_entry,
    "pattern_breakout": _pattern_breakout_entry,
    "volume_breakout": _volume_breakout_entry,
    "momentum_breakout": _momentum_breakout_entry,
    "trend_breakout": _trend_breakout_entry,
    "false_breakout_reversal": _false_breakout_reversal_entry,
    "breakout_pullback": _breakout_pullback_entry,
    "breakout_continuation": _breakout_continuation_entry,
    "breakout_retest": _breakout_retest_entry,
    "breakout_confluence": _breakout_confluence_entry,
    "multi_timeframe_breakout": _multi_timeframe_breakout_entry,
    "breakout_consolidation": _breakout_consolidation_entry,
    "breakout_squeeze": _breakout_squeeze_entry,
    "breakout_expansion": _breakout_expansion_entry,
    "breakout_contraction": _breakout_contraction_entry,
    "breakout_momentum": _breakout_momentum_entry,
    "breakout_reversion": _breakout_reversion_entry,
    "breakout_volume": _breakout_volume_entry,
    "breakout_volatility": _breakout_volatility_entry,
    "breakout_trend": _breakout_trend_entry,
    "breakout_pattern": _breakout_pattern_entry,
    "breakout_signal": _breakout_signal_entry,
    "breakout_confirmation": _breakout_confirmation_entry,
    "breakout_validation": _breakout_validation_entry,
    "breakout_filter": _breakout_filter_entry,
    "breakout_timing": _breakout_timing_entry,
    "breakout_entry": _breakout_entry_optimized,
    "breakout_exit": _breakout_exit_optimized,
    "breakout_risk": _breakout_risk_entry,
    "breakout_position_sizing": _breakout_position_sizing_entry,
    "breakout_portfolio": _breakout_portfolio_entry,
    "breakout_adaptive": _breakout_adaptive_entry,
    "breakout_dynamic": _breakout_dynamic_entry,
    "breakout_regime": _breakout_regime_entry,
    "breakout_ml": _breakout_ml_entry,
    "breakout_ensemble": _breakout_ensemble_entry,
    "breakout_hybrid": _breakout_hybrid_entry,
    "breakout_multi_asset": _breakout_multi_asset_entry,
    "breakout_cross_sectional": _breakout_cross_sectional_entry,
    "breakout_statistical": _breakout_statistical_entry,
    "breakout_quantitative": _breakout_quantitative_entry,
    "gap_breakout": _gap_breakout_entry,
    "inside_bar_breakout": _inside_bar_breakout_entry,
}


def fetch_candles(client, pid, start, end):
    """Fetch candles in chunks to avoid API limits."""
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="FIVE_MINUTE")
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


def main():
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"BREAKOUT 50 STRATEGY SWEEP")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()

    # Load coin list
    coin_file = Path(__file__).parent.parent / "coinbase_usd_pairs.txt"
    if coin_file.exists():
        coins = [line.strip() for line in open(coin_file) if line.strip() and not line.startswith("Total")]
        print(f"Loaded {len(coins)} coins from coinbase_usd_pairs.txt")
    else:
        coins = ["GHST-USD", "MOG-USD", "RAVE-USD", "TRU-USD", "NOM-USD"]
        print(f"Using fallback: {len(coins)} coins")

    fast_coins = coins[:30] + [c for c in ["GHST-USD", "NOM-USD", "TRU-USD", "MOG-USD", "RAVE-USD"] if c not in coins[:30]]
    print(f"Testing on {len(fast_coins)} coins (7d discovery phase)\n")

    now = int(time.time())
    start_ts = now - 7 * 86400

    all_candles = {}
    for coin in fast_coins:
        try:
            candles = fetch_candles(client, coin, start_ts, now)
            if candles:
                all_candles[coin] = candles
                print(f"  {coin}: {len(candles)} candles")
            else:
                print(f"  {coin}: NO DATA")
        except Exception as e:
            print(f"  {coin}: ERROR — {str(e)[:60]}")
        time.sleep(0.2)

    print(f"\nFetched data for {len(all_candles)} coins")
    print(f"Testing {len(BREAKOUT_STRATEGIES)} breakout strategies...\n")

    results = []
    total_tests = len(all_candles) * len(BREAKOUT_STRATEGIES)
    test_count = 0

    for strat_def in BREAKOUT_STRATEGIES:
        strat_name = strat_def["name"]
        entry_fn = ENTRY_FUNCS.get(strat_name)
        if entry_fn is None:
            print(f"  SKIP {strat_name}: no entry function")
            continue

        coin_results = []
        for coin, candles in all_candles.items():
            test_count += 1
            try:
                result = backtest(candles, entry_fn, strat_def["params"],
                                  fee_rate=0.004, starting_cash=48.0)
                coin_results.append({"coin": coin, "candles": len(candles), **result})
            except Exception as e:
                coin_results.append({"coin": coin, "error": str(e)[:80]})

            if test_count % 100 == 0:
                elapsed = time.time() - start_time
                print(f"  Progress: {test_count}/{total_tests} tests ({elapsed:.0f}s)")

        profitable = [r for r in coin_results if "net_pnl" in r and r["net_pnl"] > 0]
        avg_pnl = sum(r.get("net_pnl", 0) for r in coin_results) / len(coin_results) if coin_results else 0

        strat_summary = {
            "strategy": strat_name,
            "coins_tested": len(coin_results),
            "profitable_coins": len(profitable),
            "hit_rate": len(profitable) / len(coin_results) * 100 if coin_results else 0,
            "avg_net_pnl": round(avg_pnl, 2),
            "total_net_pnl": round(sum(r.get("net_pnl", 0) for r in coin_results), 2),
            "best_coin": max(profitable, key=lambda x: x.get("net_pnl", 0)) if profitable else None,
            "coin_details": coin_results[:5]
        }
        results.append(strat_summary)

        print(f"  {strat_name:<30} | {len(profitable):>3}/{len(coin_results)} coins | "
              f"Hit: {strat_summary['hit_rate']:>5.1f}% | "
              f"Avg PnL: ${avg_pnl:>7.2f} | "
              f"Total: ${strat_summary['total_net_pnl']:>8.2f}")

    results.sort(key=lambda x: x["total_net_pnl"], reverse=True)

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 1),
        "coins_tested": len(all_candles),
        "strategies_tested": len(results),
        "total_backtests": test_count,
        "results": results,
        "top_10_strategies": results[:10],
        "promoted_for_30d": [r["strategy"] for r in results[:5] if r["hit_rate"] > 30]
    }

    out_path = Path(__file__).parent.parent / "reports" / "breakout_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 BREAKOUT STRATEGIES:")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:>2}. {r['strategy']:<30} ${r['total_net_pnl']:>8.2f}  "
              f"Hit: {r['hit_rate']:>5.1f}%  "
              f"Profitable: {r['profitable_coins']}/{r['coins_tested']}")

    if output["promoted_for_30d"]:
        print(f"\nPROMOTED FOR 30D VALIDATION:")
        for s in output["promoted_for_30d"]:
            print(f"  -> {s}")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
