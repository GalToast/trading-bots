#!/usr/bin/env python3
"""
Momentum 50 Strategy Sweep — Batch of the 500 Strategies Initiative.

Tests 50 unique momentum-based strategies across 35 Coinbase coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants cover:
- Oscillator-based momentum (MACD, TSI, TRIX, KST, etc.)
- Trend-following momentum (MA crossovers, EMA ribbons, Guppy, etc.)
- Adaptive/dynamic momentum (KAMA, Hull MA, regime-filtered, etc.)
- Volume-weighted momentum (VWAP, force index, etc.)
- Statistical momentum (linear regression, residual, beta-adjusted, etc.)
- Path-dependent and asymmetric momentum
- Specialized indicators (Supertrend, PSAR, Ichimoku, Alligator, etc.)

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
# MOMENTUM HELPER FUNCTIONS
# ==========================================

def compute_ema(data, period):
    """Exponential Moving Average."""
    if len(data) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema = sum(data[:period]) / period
    for x in data[period:]:
        ema = (x - ema) * multiplier + ema
    return ema


def compute_sma(data, period):
    """Simple Moving Average."""
    if len(data) < period:
        return None
    return sum(data[-period:]) / period


def compute_rsi(closes, period=14):
    """Relative Strength Index."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
        if len(gains) > period:
            gains = gains[-period:]
            losses = losses[-period:]
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_std(data, period):
    """Standard deviation."""
    if len(data) < period:
        return None
    subset = data[-period:]
    mean = sum(subset) / len(subset)
    variance = sum((x - mean) ** 2 for x in subset) / period
    return math.sqrt(variance)


def compute_linear_regression_slope(data, period):
    """Linear regression slope of data over period."""
    if len(data) < period:
        return None
    subset = data[-period:]
    n = len(subset)
    x_mean = (n - 1) / 2.0
    y_mean = sum(subset) / n
    numerator = sum((i - x_mean) * (subset[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    if denominator == 0:
        return 0.0
    return numerator / denominator


# ==========================================
# MOMENTUM STRATEGY ENTRY FUNCTIONS
# ==========================================

def _macd_momentum_entry(candles_hist, closes, candle, params):
    """Enter when MACD line crosses above signal line."""
    if len(closes) < 50:
        return False
    fast = params.get("fast", 12)
    slow = params.get("slow", 26)
    signal = params.get("signal", 9)

    fast_ema = compute_ema(closes, fast)
    slow_ema = compute_ema(closes, slow)
    if fast_ema is None or slow_ema is None:
        return False
    macd_line = fast_ema - slow_ema

    # Compute MACD values over recent history for signal line
    macd_vals = []
    for i in range(slow + signal, len(closes) + 1):
        f = compute_ema(closes[:i], fast)
        s = compute_ema(closes[:i], slow)
        if f is not None and s is not None:
            macd_vals.append(f - s)

    if len(macd_vals) < signal + 1:
        return False
    sig = compute_ema(macd_vals, signal)
    prev_sig_data = macd_vals[:-1]
    if len(prev_sig_data) >= signal:
        prev_sig = compute_ema(prev_sig_data, signal)
    else:
        prev_sig = sig

    if sig is not None and macd_line > (sig or 0):
        if prev_sig is not None and (macd_vals[-2] if len(macd_vals) > 1 else macd_line) <= prev_sig:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _adx_momentum_entry(candles_hist, closes, candle, params):
    """Enter when ADX above 25 (strong trend) and price trending up."""
    if len(candles_hist) < 30:
        return False
    period = params.get("adx_period", 14)
    threshold = params.get("threshold", 25)

    tr_values = []
    plus_dm = []
    minus_dm = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        ph = float(candles_hist[i - 1]["high"])
        pl = float(candles_hist[i - 1]["low"])
        pc = float(candles_hist[i - 1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_values.append(tr)
        h_diff = h - ph
        l_diff = pl - l
        plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
        minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)

    if len(tr_values) < period * 2:
        return False

    smoothed_tr = sum(tr_values[-period:]) / period
    smoothed_plus = sum(plus_dm[-period:]) / period
    smoothed_minus = sum(minus_dm[-period:]) / period

    if smoothed_tr == 0:
        return False

    plus_di = (smoothed_plus / smoothed_tr) * 100
    minus_di = (smoothed_minus / smoothed_tr) * 100

    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0

    if dx > threshold and plus_di > minus_di:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _supertrend_entry(candles_hist, closes, candle, params):
    """Enter when Supertrend flips bullish."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 10)
    multiplier = params.get("multiplier", 3.0)

    atr_vals = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        pc = float(candles_hist[i - 1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        atr_vals.append(tr)

    if len(atr_vals) < period:
        return False

    atr = sum(atr_vals[-period:]) / period
    hl2 = (float(candle["high"]) + float(candle["low"])) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    current_price = float(candle["close"])
    if current_price > lower_band and current_price < upper_band:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _psar_momentum_entry(candles_hist, closes, candle, params):
    """Enter when Parabolic SAR flips below price (bullish)."""
    if len(candles_hist) < 30:
        return False
    af_start = params.get("af_start", 0.02)
    af_step = params.get("af_step", 0.02)
    af_max = params.get("af_max", 0.2)

    af = af_start
    ep = float(candles_hist[0]["high"])
    sar = float(candles_hist[0]["low"])
    uptrend = True

    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        prev_sar = sar

        if uptrend:
            sar = prev_sar + af * (ep - prev_sar)
            if l < sar:
                sar = ep
                uptrend = False
                af = af_start
                ep = l
            else:
                if h > ep:
                    ep = h
                    af = min(af + af_step, af_max)
                sar = min(sar, float(candles_hist[i - 1]["low"]) if i > 0 else sar,
                          float(candles_hist[i - 2]["low"]) if i > 1 else sar)
        else:
            sar = prev_sar - af * (prev_sar - ep)
            if h > sar:
                sar = ep
                uptrend = True
                af = af_start
                ep = h
            else:
                if l < ep:
                    ep = l
                    af = min(af + af_step, af_max)
                sar = max(sar, float(candles_hist[i - 1]["high"]) if i > 0 else sar,
                          float(candles_hist[i - 2]["high"]) if i > 1 else sar)

    current_price = float(candle["close"])
    if uptrend and current_price > sar:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ichimoku_entry(candles_hist, closes, candle, params):
    """Enter when Ichimoku cloud breakout (price above cloud, TK cross bullish)."""
    if len(candles_hist) < 60:
        return False
    tenkan_period = params.get("tenkan", 9)
    kijun_period = params.get("kijun", 26)

    highs = [float(c["high"]) for c in candles_hist]
    lows = [float(c["low"]) for c in candles_hist]

    tenkan = (max(highs[-tenkan_period:]) + min(lows[-tenkan_period:])) / 2
    kijun = (max(highs[-kijun_period:]) + min(lows[-kijun_period:])) / 2

    current_price = float(candle["close"])
    if current_price > tenkan > kijun:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _dmi_momentum_entry(candles_hist, closes, candle, params):
    """Enter when DMI +DI above -DI (directional movement bullish)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 14)

    plus_dm = []
    minus_dm = []
    tr_values = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        ph = float(candles_hist[i - 1]["high"])
        pl = float(candles_hist[i - 1]["low"])
        pc = float(candles_hist[i - 1]["close"])

        up_move = h - ph
        down_move = pl - l
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
        tr_values.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(plus_dm) < period:
        return False

    smooth_plus = sum(plus_dm[-period:]) / period
    smooth_minus = sum(minus_dm[-period:]) / period
    smooth_tr = sum(tr_values[-period:]) / period

    if smooth_tr == 0:
        return False

    plus_di = (smooth_plus / smooth_tr) * 100
    minus_di = (smooth_minus / smooth_tr) * 100

    if plus_di > minus_di:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _aroon_momentum_entry(candles_hist, closes, candle, params):
    """Enter when Aroon Up above 70 (strong uptrend)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 25)
    threshold = params.get("threshold", 70)

    if len(closes) < period + 1:
        return False

    recent_highs = [float(c["high"]) for c in candles_hist[-period:]]
    recent_lows = [float(c["low"]) for c in candles_hist[-period:]]

    high_idx = recent_highs.index(max(recent_highs))
    low_idx = recent_lows.index(min(recent_lows))

    aroon_up = ((period - 1 - high_idx) / (period - 1)) * 100
    aroon_down = ((period - 1 - low_idx) / (period - 1)) * 100

    if aroon_up > threshold and aroon_down < 30:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _tsi_momentum_entry(candles_hist, closes, candle, params):
    """Enter when True Strength Index turns positive."""
    if len(closes) < 50:
        return False
    long_period = params.get("long", 25)
    short_period = params.get("short", 13)

    price_changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    if len(price_changes) < long_period + short_period:
        return None

    # Double-smooth the price changes
    first_ema_data = price_changes
    ema1 = compute_ema(first_ema_data, long_period)
    if ema1 is None:
        return False

    # Build EMA series for absolute changes too
    abs_changes = [abs(c) for c in price_changes]

    # Second smoothing on the raw changes
    ema2_vals = []
    for i in range(long_period, len(price_changes) + 1):
        e = compute_ema(price_changes[:i], short_period)
        if e is not None:
            ema2_vals.append(e)

    ema2_abs_vals = []
    for i in range(long_period, len(abs_changes) + 1):
        e = compute_ema(abs_changes[:i], short_period)
        if e is not None:
            ema2_abs_vals.append(e)

    if not ema2_vals or not ema2_abs_vals or ema2_abs_vals[-1] == 0:
        return False

    tsi = (ema2_vals[-1] / ema2_abs_vals[-1]) * 100

    if tsi > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ultimate_oscillator_entry(candles_hist, closes, candle, params):
    """Enter when Ultimate Oscillator above 50."""
    if len(candles_hist) < 30:
        return False
    p1 = params.get("p1", 7)
    p2 = params.get("p2", 14)
    p3 = params.get("p3", 28)

    bp_values = []
    tr_values = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        pc = float(candles_hist[i - 1]["close"])
        bp = float(candle["close"]) - min(l, pc)
        tr = max(h, pc) - min(l, pc)
        bp_values.append(bp)
        tr_values.append(tr)

    if len(bp_values) < p3 or len(tr_values) < p3:
        return False

    def avg_bp(start, end):
        bp_sum = sum(bp_values[start:end])
        tr_sum = sum(tr_values[start:end])
        return bp_sum / tr_sum if tr_sum > 0 else 50

    avg7 = avg_bp(-p1, None)
    avg14 = avg_bp(-p2, None)
    avg28 = avg_bp(-p3, None)

    uo = (4 * avg7 + 2 * avg14 + avg28) / 7 * 100

    if uo > 50:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _chaikin_oscillator_entry(candles_hist, closes, candle, params):
    """Enter when Chaikin Oscillator turns positive."""
    if len(candles_hist) < 30:
        return False
    fast = params.get("fast", 3)
    slow = params.get("slow", 10)

    adl = 0.0
    adl_values = []
    for c in candles_hist:
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        v = float(c["volume"])
        if h != l:
            clv = ((cl - l) - (h - cl)) / (h - l)
            adl += clv * v
        adl_values.append(adl)

    if len(adl_values) < slow:
        return False

    fast_ema_val = compute_ema(adl_values, fast)
    slow_ema_val = compute_ema(adl_values, slow)

    if fast_ema_val is None or slow_ema_val is None:
        return False

    chaikin_osc = fast_ema_val - slow_ema_val

    if chaikin_osc > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _force_index_entry(candles_hist, closes, candle, params):
    """Enter when Force Index above zero."""
    if len(closes) < 15:
        return False
    period = params.get("period", 13)

    fi_values = []
    for i in range(1, len(closes)):
        price_change = closes[i] - closes[i - 1]
        vol = float(candles_hist[i]["volume"])
        fi_values.append(price_change * vol)

    if len(fi_values) < period:
        return False

    fi_ema = compute_ema(fi_values, period)
    if fi_ema is None:
        return False

    if fi_ema > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _rate_of_change_entry(candles_hist, closes, candle, params):
    """Enter when ROC above zero and rising."""
    if len(closes) < 20:
        return False
    period = params.get("period", 12)

    if len(closes) < period + 2:
        return False

    roc_now = (closes[-1] - closes[-period]) / closes[-period] * 100
    roc_prev = (closes[-2] - closes[-period - 1]) / closes[-period - 1] * 100

    if roc_now > 0 and roc_now > roc_prev:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _detrended_price_entry(candles_hist, closes, candle, params):
    """Enter when Detrended Price Oscillator turns up."""
    if len(closes) < 40:
        return False
    period = params.get("period", 21)

    sma_val = compute_sma(closes, period)
    if sma_val is None:
        return False

    dpo_now = closes[-1] - sma_val
    dpo_prev = closes[-2] - compute_sma(closes[:-1], period) if len(closes) > period + 1 else dpo_now

    if dpo_now > dpo_prev and dpo_now < 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _trix_momentum_entry(candles_hist, closes, candle, params):
    """Enter when TRIX above zero."""
    if len(closes) < 60:
        return False
    period = params.get("period", 15)

    ema1 = compute_ema(closes, period)
    if ema1 is None:
        return False

    ema1_series = []
    for i in range(period, len(closes) + 1):
        e = compute_ema(closes[:i], period)
        if e is not None:
            ema1_series.append(e)

    if len(ema1_series) < period:
        return False

    ema2 = compute_ema(ema1_series, period)
    if ema2 is None:
        return False

    ema2_series = []
    for i in range(period, len(ema1_series) + 1):
        e = compute_ema(ema1_series[:i], period)
        if e is not None:
            ema2_series.append(e)

    if len(ema2_series) < period:
        return False

    ema3 = compute_ema(ema2_series, period)
    if ema3 is None or ema3 == 0:
        return False

    trix = (ema3 - ema2_series[-2]) / ema2_series[-2] * 100 if len(ema2_series) > 1 else 0

    if trix > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _kst_momentum_entry(candles_hist, closes, candle, params):
    """Enter when Know Sure Thing turns positive."""
    if len(closes) < 60:
        return False
    r1 = params.get("r1", 10)
    r2 = params.get("r2", 15)
    r3 = params.get("r3", 20)
    r4 = params.get("r4", 30)
    s1 = params.get("s1", 10)
    s2 = params.get("s2", 10)
    s3 = params.get("s3", 10)
    s4 = params.get("s4", 15)

    def roc(data, lookback):
        if len(data) <= lookback:
            return None
        return (data[-1] - data[-lookback - 1]) / data[-lookback - 1] * 100

    rcs = []
    for data_set in [
        (closes, r1, s1),
        (closes, r2, s2),
        (closes, r3, s3),
        (closes, r4, s4)
    ]:
        c, r, s = data_set
        if len(c) < r + s + 2:
            return False
        roc_val = roc(c, r)
        if roc_val is None:
            return False
        rcs.append(roc_val)

    kst = rcs[0] * 1 + rcs[1] * 2 + rcs[2] * 3 + rcs[3] * 4

    if kst > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _schaff_trend_cycle_entry(candles_hist, closes, candle, params):
    """Enter when Schaff Trend Cycle above 25."""
    if len(closes) < 60:
        return False
    fast = params.get("fast", 23)
    slow = params.get("slow", 50)
    cycle = params.get("cycle", 10)

    fast_ema_val = compute_ema(closes, fast)
    slow_ema_val = compute_ema(closes, slow)
    if fast_ema_val is None or slow_ema_val is None:
        return False

    macd_val = fast_ema_val - slow_ema_val

    # Simplified Schaff TC
    if macd_val > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _rainbow_oscillator_entry(candles_hist, closes, candle, params):
    """Enter when Rainbow Oscillator shows bullish alignment."""
    if len(closes) < 50:
        return False
    periods = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]

    emas = []
    for p in periods:
        e = compute_ema(closes, p)
        if e is None:
            return False
        emas.append(e)

    # Bullish: shortest EMA > longest EMA
    if emas[0] > emas[-1]:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _true_strength_entry(candles_hist, closes, candle, params):
    """Enter when TSI momentum with signal cross turns bullish."""
    if len(closes) < 50:
        return False
    long_p = params.get("long", 25)
    short_p = params.get("short", 13)
    signal_p = params.get("signal", 9)

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    abs_changes = [abs(c) for c in changes]

    if len(changes) < long_p + short_p:
        return False

    # Double smooth numerator
    num_series = changes
    for p in [long_p, short_p]:
        num_ema = compute_ema(num_series, p)
        if num_ema is None:
            return False
        num_series = [num_ema]

    # Double smooth denominator
    den_series = abs_changes
    for p in [long_p, short_p]:
        den_ema = compute_ema(den_series, p)
        if den_ema is None:
            return False
        den_series = [den_ema]

    if den_series[0] == 0:
        return False

    tsi = (num_series[0] / den_series[0]) * 100

    if tsi > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _price_momentum_entry(candles_hist, closes, candle, params):
    """Enter on simple price rate of change (positive momentum)."""
    if len(closes) < 15:
        return False
    period = params.get("period", 10)

    if len(closes) < period + 1:
        return False

    roc = (closes[-1] - closes[-period]) / closes[-period] * 100

    if roc > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _volume_momentum_entry(candles_hist, closes, candle, params):
    """Enter on volume-weighted price momentum."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 14)

    if len(closes) < period + 1:
        return False

    weighted_prices = []
    for i in range(-period, 0):
        idx = len(candles_hist) + i
        if 0 <= idx < len(candles_hist):
            c = candles_hist[idx]
            v = float(c["volume"])
            p = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3
            weighted_prices.append(p * v)

    if not weighted_prices:
        return False

    momentum = weighted_prices[-1] - weighted_prices[0]

    if momentum > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _volatility_momentum_entry(candles_hist, closes, candle, params):
    """Enter on volatility-adjusted momentum."""
    if len(closes) < 30:
        return False
    mom_period = params.get("mom_period", 10)
    vol_period = params.get("vol_period", 20)

    if len(closes) < mom_period + vol_period:
        return False

    mom = (closes[-1] - closes[-mom_period]) / closes[-mom_period]
    vol = compute_std(closes, vol_period)

    if vol is None or vol == 0:
        return False

    vol_adj_momentum = mom / vol

    if vol_adj_momentum > 0.01:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _trend_following_entry(candles_hist, closes, candle, params):
    """Enter on basic trend following (price > MA)."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    ma = compute_sma(closes, period)
    if ma is None:
        return False

    current_price = float(candle["close"])
    if current_price > ma:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _moving_avg_crossover_entry(candles_hist, closes, candle, params):
    """Enter when fast MA crosses above slow MA."""
    if len(closes) < 50:
        return False
    fast = params.get("fast", 10)
    slow = params.get("slow", 30)

    fast_ma = compute_sma(closes, fast)
    slow_ma = compute_sma(closes, slow)
    fast_ma_prev = compute_sma(closes[:-1], fast) if len(closes) > fast + 1 else fast_ma
    slow_ma_prev = compute_sma(closes[:-1], slow) if len(closes) > slow + 1 else slow_ma

    if fast_ma is None or slow_ma is None or fast_ma_prev is None or slow_ma_prev is None:
        return False

    if fast_ma_prev <= slow_ma_prev and fast_ma > slow_ma:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ema_ribbon_entry(candles_hist, closes, candle, params):
    """Enter when EMA ribbon alignment turns bullish."""
    if len(closes) < 50:
        return False
    periods = params.get("periods", [10, 15, 20, 25, 30, 35])

    emas = []
    for p in periods:
        e = compute_ema(closes, p)
        if e is None:
            return False
        emas.append(e)

    # Bullish: all EMAs in ascending order
    bullish = all(emas[i] < emas[i + 1] for i in range(len(emas) - 1))

    if bullish:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _guppy_mma_entry(candles_hist, closes, candle, params):
    """Enter when Guppy Multiple MA alignment is bullish."""
    if len(closes) < 60:
        return False
    short_periods = [3, 5, 8, 10, 12, 15]
    long_periods = [30, 35, 40, 45, 50, 60]

    short_emas = [compute_ema(closes, p) for p in short_periods]
    long_emas = [compute_ema(closes, p) for p in long_periods]

    if any(e is None for e in short_emas + long_emas):
        return False

    # Short group above long group
    short_avg = sum(short_emas) / len(short_emas)
    long_avg = sum(long_emas) / len(long_emas)

    if short_avg > long_avg:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _alligator_entry(candles_hist, closes, candle, params):
    """Enter when Williams Alligator mouth opens bullish."""
    if len(closes) < 60:
        return False
    jaw_period = params.get("jaw", 13)
    teeth_period = params.get("teeth", 8)
    lips_period = params.get("lips", 5)

    jaw = compute_sma(closes, jaw_period)
    teeth = compute_sma(closes, teeth_period)
    lips = compute_sma(closes, lips_period)

    if jaw is None or teeth is None or lips is None:
        return False

    # Bullish: lips > teeth > jaw
    if lips > teeth > jaw:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _fractal_momentum_entry(candles_hist, closes, candle, params):
    """Enter on fractal-based momentum (recent fractal low confirmed)."""
    if len(candles_hist) < 40:
        return False
    lookback = params.get("lookback", 5)

    # Find recent swing lows (fractal pattern: low with higher lows on each side)
    lows = [float(c["low"]) for c in candles_hist]
    fractal_lows = []
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
            fractal_lows.append((i, lows[i]))

    if not fractal_lows:
        return False

    last_fractal_idx, last_fractal_low = fractal_lows[-1]
    current_price = float(candle["close"])

    # Price moved up from last fractal low
    if current_price > last_fractal_low * 1.02:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _adaptive_momentum_entry(candles_hist, closes, candle, params):
    """Enter on adaptive lookback momentum (lookback adjusts to volatility)."""
    if len(closes) < 40:
        return False
    base_period = params.get("base_period", 14)

    # Adjust period based on recent volatility
    vol = compute_std(closes, 20)
    if vol is None or vol == 0:
        return False

    avg_price = sum(closes[-20:]) / 20
    vol_ratio = vol / avg_price if avg_price > 0 else 0.01

    # Higher volatility -> shorter lookback, lower -> longer
    adaptive_period = max(5, min(30, int(base_period / (vol_ratio * 10 + 0.5))))

    if len(closes) < adaptive_period + 1:
        return False

    roc = (closes[-1] - closes[-adaptive_period]) / closes[-adaptive_period] * 100

    if roc > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _dynamic_momentum_entry(candles_hist, closes, candle, params):
    """Enter on dynamic threshold momentum."""
    if len(closes) < 40:
        return False
    period = params.get("period", 14)
    threshold_mult = params.get("threshold_mult", 1.0)

    if len(closes) < period + 1:
        return False

    roc = (closes[-1] - closes[-period]) / closes[-period] * 100
    roc_std = compute_std([(closes[i] - closes[i - period]) / closes[i - period] * 100 for i in range(period, len(closes) - 1)], period)

    if roc_std is None or roc_std == 0:
        return False

    dynamic_threshold = threshold_mult * roc_std

    if roc > dynamic_threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _regime_momentum_entry(candles_hist, closes, candle, params):
    """Enter on regime-filtered momentum (only trade in trending regime)."""
    if len(closes) < 50:
        return False
    mom_period = params.get("mom_period", 12)
    trend_period = params.get("trend_period", 50)

    # Determine regime using ADX-like measure
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    if len(returns) < trend_period:
        return False

    avg_return = sum(returns[-trend_period:]) / trend_period
    std_return = compute_std(returns, trend_period)

    if std_return is None or std_return == 0:
        return False

    # Trending regime if |mean/std| > threshold
    sharpe_ratio = avg_return / std_return

    if len(closes) < mom_period + 1:
        return False

    mom = (closes[-1] - closes[-mom_period]) / closes[-mom_period] * 100

    # Trade only if trending and momentum positive
    if abs(sharpe_ratio) > 0.3 and mom > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ml_momentum_entry(candles_hist, closes, candle, params):
    """Enter on ML-style momentum (linear regression slope positive)."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    slope = compute_linear_regression_slope(closes, period)
    if slope is None:
        return False

    # Normalize slope by price level
    avg_price = sum(closes[-period:]) / period
    if avg_price == 0:
        return False

    normalized_slope = slope / avg_price

    if normalized_slope > 0.0001:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ensemble_momentum_entry(candles_hist, closes, candle, params):
    """Enter on ensemble of 3 momentum signals (ROC, RSI, MA)."""
    if len(closes) < 40:
        return False
    roc_period = params.get("roc_period", 10)
    rsi_period = params.get("rsi_period", 14)
    ma_period = params.get("ma_period", 20)

    signals = 0

    # Signal 1: ROC positive
    if len(closes) > roc_period:
        roc = (closes[-1] - closes[-roc_period]) / closes[-roc_period] * 100
        if roc > 0:
            signals += 1

    # Signal 2: RSI above 50
    rsi = compute_rsi(closes, rsi_period)
    if rsi is not None and rsi > 50:
        signals += 1

    # Signal 3: Price above MA
    ma = compute_sma(closes, ma_period)
    if ma is not None and float(candle["close"]) > ma:
        signals += 1

    # Need at least 2 of 3 signals
    if signals >= 2:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _volatility_adjusted_momentum_entry(candles_hist, closes, candle, params):
    """Enter on momentum / volatility ratio."""
    if len(closes) < 40:
        return False
    mom_period = params.get("mom_period", 12)
    vol_period = params.get("vol_period", 20)

    if len(closes) < mom_period + 1:
        return False

    mom = (closes[-1] - closes[-mom_period]) / closes[-mom_period]
    vol = compute_std(closes, vol_period)

    if vol is None or vol == 0:
        return False

    ratio = mom / vol

    if ratio > 0.05:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _volume_weighted_momentum_entry(candles_hist, closes, candle, params):
    """Enter on VWAP momentum (price above VWAP and rising)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)

    if len(candles_hist) < period:
        return False

    recent = candles_hist[-period:]
    cum_vol_price = sum(float(c["volume"]) * (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3 for c in recent)
    cum_vol = sum(float(c["volume"]) for c in recent)

    if cum_vol == 0:
        return False

    vwap = cum_vol_price / cum_vol
    current_price = float(candle["close"])

    if current_price > vwap:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _correlation_momentum_entry(candles_hist, closes, candle, params):
    """Enter on cross-correlation momentum (price vs time correlation)."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    if len(closes) < period:
        return False

    subset = closes[-period:]
    x = list(range(period))

    x_mean = sum(x) / period
    y_mean = sum(subset) / period

    numerator = sum((x[i] - x_mean) * (subset[i] - y_mean) for i in range(period))
    denom_x = math.sqrt(sum((xi - x_mean) ** 2 for xi in x))
    denom_y = math.sqrt(sum((yi - y_mean) ** 2 for yi in subset))

    if denom_x == 0 or denom_y == 0:
        return False

    correlation = numerator / (denom_x * denom_y)

    if correlation > 0.3:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _beta_momentum_entry(candles_hist, closes, candle, params):
    """Enter on beta-adjusted momentum (momentum relative to market)."""
    if len(closes) < 40:
        return False
    period = params.get("period", 20)

    # Use recent closes as proxy for "market" (self-referential beta)
    if len(closes) < period + 1:
        return False

    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]

    if len(returns) < period:
        return False

    avg_return = sum(returns[-period:]) / period
    std_return = compute_std(returns, period)

    if std_return is None or std_return == 0:
        return False

    beta = avg_return / std_return

    if beta > 0.1:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _residual_momentum_entry(candles_hist, closes, candle, params):
    """Enter on residual momentum (from regression, positive residual)."""
    if len(closes) < 40:
        return False
    period = params.get("period", 20)

    if len(closes) < period:
        return False

    subset = closes[-period:]
    x = list(range(period))

    # Fit linear regression
    x_mean = sum(x) / period
    y_mean = sum(subset) / period

    slope_num = sum((x[i] - x_mean) * (subset[i] - y_mean) for i in range(period))
    slope_den = sum((xi - x_mean) ** 2 for xi in x)

    if slope_den == 0:
        return False

    slope = slope_num / slope_den
    intercept = y_mean - slope * x_mean

    # Residual of last point
    predicted = slope * (period - 1) + intercept
    actual = closes[-1]
    residual = actual - predicted

    if residual > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _path_dependent_momentum_entry(candles_hist, closes, candle, params):
    """Enter on path-dependent momentum (recent high vs low path matters)."""
    if len(closes) < 30:
        return False
    lookback = params.get("lookback", 15)

    if len(closes) < lookback + 1:
        return False

    recent = closes[-lookback:]
    high_idx = recent.index(max(recent))
    low_idx = recent.index(min(recent))

    # Bullish: high came after low (uptrend path)
    if high_idx > low_idx:
        # And momentum is positive from low to now
        low_price = recent[low_idx]
        if low_price > 0 and (closes[-1] - low_price) / low_price > 0.02:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _asymmetric_momentum_entry(candles_hist, closes, candle, params):
    """Enter on asymmetric up/down momentum (up moves bigger than down)."""
    if len(closes) < 30:
        return False
    period = params.get("period", 14)

    if len(closes) < period + 1:
        return False

    up_moves = []
    down_moves = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            up_moves.append(diff / closes[i - 1])
        else:
            down_moves.append(abs(diff) / closes[i - 1])

    if not up_moves or not down_moves:
        return False

    avg_up = sum(up_moves[-period:]) / min(len(up_moves), period)
    avg_down = sum(down_moves[-period:]) / min(len(down_moves), period)

    if avg_down == 0:
        return False

    asymmetry = avg_up / avg_down

    if asymmetry > 1.2:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _signed_momentum_entry(candles_hist, closes, candle, params):
    """Enter on signed momentum (direction matters, not magnitude)."""
    if len(closes) < 25:
        return False
    period = params.get("period", 10)

    if len(closes) < period + 1:
        return False

    signs = []
    for i in range(1, len(closes)):
        signs.append(1 if closes[i] > closes[i - 1] else -1)

    recent_signs = signs[-period:]
    signed_sum = sum(recent_signs)

    if signed_sum > period * 0.3:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _directional_momentum_entry(candles_hist, closes, candle, params):
    """Enter on Directional Movement Index bullish."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 14)

    plus_dm = []
    minus_dm = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        ph = float(candles_hist[i - 1]["high"])
        pl = float(candles_hist[i - 1]["low"])

        up_move = h - ph
        down_move = pl - l

        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)

    if len(plus_dm) < period:
        return False

    smooth_plus = sum(plus_dm[-period:]) / period
    smooth_minus = sum(minus_dm[-period:]) / period

    if smooth_minus == 0:
        return False

    dx = (smooth_plus - smooth_minus) / (smooth_plus + smooth_minus) * 100

    if dx > 10:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _mass_index_entry(candles_hist, closes, candle, params):
    """Enter on Mass Index reversal signal."""
    if len(candles_hist) < 40:
        return False
    period = params.get("period", 25)
    ema_period = params.get("ema_period", 9)

    tr_values = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        pc = float(candles_hist[i - 1]["close"])
        tr_values.append(max(h - l, abs(h - pc), abs(l - pc)))

    if len(tr_values) < ema_period * 2:
        return False

    tr_ema1 = compute_ema(tr_values, ema_period)
    if tr_ema1 is None:
        return False

    # Build series of EMA differences
    ema_diffs = []
    for i in range(ema_period, len(tr_values)):
        e1 = compute_ema(tr_values[:i + 1], ema_period)
        if e1 is None:
            continue
        e2 = compute_ema(tr_values[:i + 1], ema_period * 2)
        if e2 is None or e2 == 0:
            continue
        ema_diffs.append(e1 / e2)

    if len(ema_diffs) < period:
        return False

    mass_index = sum(ema_diffs[-period:]) / period

    if mass_index > 27:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vortex_entry(candles_hist, closes, candle, params):
    """Enter on Vortex indicator bullish cross."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 14)

    tr_values = []
    vm_plus = []
    vm_minus = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        pc = float(candles_hist[i - 1]["close"])
        ph = float(candles_hist[i - 1]["high"])
        pl = float(candles_hist[i - 1]["low"])

        tr_values.append(max(h - l, abs(h - pc), abs(l - pc)))
        vm_plus.append(abs(h - pl))
        vm_minus.append(abs(l - ph))

    if len(tr_values) < period:
        return False

    sum_tr = sum(tr_values[-period:])
    if sum_tr == 0:
        return False

    vi_plus = sum(vm_plus[-period:]) / sum_tr
    vi_minus = sum(vm_minus[-period:]) / sum_tr

    if vi_plus > vi_minus:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _kama_entry(candles_hist, closes, candle, params):
    """Enter on Kaufman Adaptive MA momentum."""
    if len(closes) < 40:
        return False
    period = params.get("period", 10)

    if len(closes) < period + 1:
        return False

    # Efficiency ratio
    change = abs(closes[-1] - closes[-period])
    volatility = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - period, len(closes)))

    if volatility == 0:
        return False

    er = change / volatility

    # Fast and slow smoothing constants
    fast_sc = 2.0 / (2 + 1)
    slow_sc = 2.0 / (period + 1)
    ssc = er * (fast_sc - slow_sc) + slow_sc

    # Simplified KAMA
    kama = closes[-period]
    for i in range(len(closes) - period, len(closes)):
        kama = kama + ssc * ssc * (closes[i] - kama)

    if closes[-1] > kama:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _hull_ma_entry(candles_hist, closes, candle, params):
    """Enter on Hull MA momentum (price above HMA)."""
    if len(closes) < 40:
        return False
    period = params.get("period", 21)

    half = period // 2
    sqrt_period = int(math.sqrt(period))

    if len(closes) < period:
        return False

    # HMA = WMA(2*WMA(n/2) - WMA(n)), sqrt(n))
    # Simplified: use the last WMA values
    wma_half = compute_wma(closes, half)
    wma_full = compute_wma(closes, period)

    if wma_half is None or wma_full is None:
        return False

    # Build the difference series
    diff_data = []
    for i in range(period, len(closes) + 1):
        wh = compute_wma(closes[:i], half)
        wf = compute_wma(closes[:i], period)
        if wh is not None and wf is not None:
            diff_data.append(2 * wh - wf)

    if len(diff_data) < sqrt_period:
        return False

    hull = compute_wma_raw(diff_data, sqrt_period)
    if hull is None:
        return False

    if closes[-1] > hull:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def compute_wma(closes, period):
    """Weighted Moving Average (return last value)."""
    if len(closes) < period:
        return None
    subset = closes[-period:]
    weights = list(range(1, period + 1))
    weighted_sum = sum(subset[i] * weights[i] for i in range(period))
    weight_sum = sum(weights)
    return weighted_sum / weight_sum


def compute_wma_raw(data, period):
    """WMA on arbitrary data series."""
    if len(data) < period:
        return None
    subset = data[-period:]
    weights = list(range(1, period + 1))
    weighted_sum = sum(subset[i] * weights[i] for i in range(period))
    weight_sum = sum(weights)
    return weighted_sum / weight_sum


def _tema_entry(candles_hist, closes, candle, params):
    """Enter on Triple EMA momentum."""
    if len(closes) < 50:
        return False
    period = params.get("period", 21)

    ema1 = compute_ema(closes, period)
    if ema1 is None:
        return False

    ema1_series = []
    for i in range(period, len(closes) + 1):
        e = compute_ema(closes[:i], period)
        if e is not None:
            ema1_series.append(e)

    if len(ema1_series) < period:
        return False

    ema2 = compute_ema(ema1_series, period)
    if ema2 is None:
        return False

    ema2_series = []
    for i in range(period, len(ema1_series) + 1):
        e = compute_ema(ema1_series[:i], period)
        if e is not None:
            ema2_series.append(e)

    if len(ema2_series) < period:
        return False

    ema3 = compute_ema(ema2_series, period)
    if ema3 is None:
        return False

    tema = 3 * ema1 - 3 * ema2 + ema3

    if closes[-1] > tema:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _dema_entry(candles_hist, closes, candle, params):
    """Enter on Double EMA momentum."""
    if len(closes) < 40:
        return False
    period = params.get("period", 21)

    ema1 = compute_ema(closes, period)
    if ema1 is None:
        return False

    ema1_series = []
    for i in range(period, len(closes) + 1):
        e = compute_ema(closes[:i], period)
        if e is not None:
            ema1_series.append(e)

    if len(ema1_series) < period:
        return False

    ema2 = compute_ema(ema1_series, period)
    if ema2 is None:
        return False

    dema = 2 * ema1 - ema2

    if closes[-1] > dema:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _wma_entry(candles_hist, closes, candle, params):
    """Enter on Weighted MA momentum."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    wma = compute_wma(closes, period)
    if wma is None:
        return False

    if closes[-1] > wma:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _smma_entry(candles_hist, closes, candle, params):
    """Enter on Smoothed MA momentum."""
    if len(closes) < 40:
        return False
    period = params.get("period", 20)

    if len(closes) < period:
        return False

    # SMMA: each value is (prev_smma * (n-1) + close) / n
    smma = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        smma = (smma * (period - 1) + closes[i]) / period

    if closes[-1] > smma:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _laguerre_entry(candles_hist, closes, candle, params):
    """Enter on Laguerre filter momentum."""
    if len(closes) < 30:
        return False
    gamma = params.get("gamma", 0.5)

    # Laguerre filter: L0 = (1-gamma)*price + gamma*L0[1]
    # L1 = -gamma*L0 + L0[1] + gamma*L1[1]
    # Simplified: single-pole Laguerre
    l0 = closes[0]
    for i in range(1, len(closes)):
        l0 = (1 - gamma) * closes[i] + gamma * l0

    if closes[-1] > l0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

MOMENTUM_STRATEGIES = [
    # Oscillator-based
    {"name": "macd_momentum", "params": {"fast": 12, "slow": 26, "signal": 9, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "adx_momentum", "params": {"adx_period": 14, "threshold": 25, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "supertrend", "params": {"period": 10, "multiplier": 3.0, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "psar_momentum", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ichimoku", "params": {"tenkan": 9, "kijun": 26, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "dmi_momentum", "params": {"period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "aroon_momentum", "params": {"period": 25, "threshold": 70, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "tsi_momentum", "params": {"long": 25, "short": 13, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ultimate_oscillator", "params": {"p1": 7, "p2": 14, "p3": 28, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "chaikin_oscillator", "params": {"fast": 3, "slow": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Price/volume momentum
    {"name": "force_index", "params": {"period": 13, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "rate_of_change", "params": {"period": 12, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "detrended_price", "params": {"period": 21, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "trix_momentum", "params": {"period": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "kst_momentum", "params": {"r1": 10, "r2": 15, "r3": 20, "r4": 30, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "schaff_trend_cycle", "params": {"fast": 23, "slow": 50, "cycle": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "rainbow_oscillator", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "true_strength", "params": {"long": 25, "short": 13, "signal": 9, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "price_momentum", "params": {"period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "volume_momentum", "params": {"period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Trend/MA-based
    {"name": "volatility_momentum", "params": {"mom_period": 10, "vol_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "trend_following", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "moving_avg_crossover", "params": {"fast": 10, "slow": 30, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ema_ribbon", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "guppy_mma", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "alligator", "params": {"jaw": 13, "teeth": 8, "lips": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "fractal_momentum", "params": {"lookback": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "adaptive_momentum", "params": {"base_period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "dynamic_momentum", "params": {"period": 14, "threshold_mult": 1.0, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "regime_momentum", "params": {"mom_period": 12, "trend_period": 50, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Statistical/ensemble
    {"name": "ml_momentum", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ensemble_momentum", "params": {"roc_period": 10, "rsi_period": 14, "ma_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "volatility_adjusted_momentum", "params": {"mom_period": 12, "vol_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "volume_weighted_momentum", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "correlation_momentum", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "beta_momentum", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "residual_momentum", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "path_dependent_momentum", "params": {"lookback": 15, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "asymmetric_momentum", "params": {"period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "signed_momentum", "params": {"period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Specialized indicators
    {"name": "directional_momentum", "params": {"period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "mass_index", "params": {"period": 25, "ema_period": 9, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "vortex", "params": {"period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "kama", "params": {"period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hull_ma", "params": {"period": 21, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "tema", "params": {"period": 21, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "dema", "params": {"period": 21, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "wma", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "smma", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "laguerre", "params": {"gamma": 0.5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "macd_momentum": _macd_momentum_entry,
    "adx_momentum": _adx_momentum_entry,
    "supertrend": _supertrend_entry,
    "psar_momentum": _psar_momentum_entry,
    "ichimoku": _ichimoku_entry,
    "dmi_momentum": _dmi_momentum_entry,
    "aroon_momentum": _aroon_momentum_entry,
    "tsi_momentum": _tsi_momentum_entry,
    "ultimate_oscillator": _ultimate_oscillator_entry,
    "chaikin_oscillator": _chaikin_oscillator_entry,
    "force_index": _force_index_entry,
    "rate_of_change": _rate_of_change_entry,
    "detrended_price": _detrended_price_entry,
    "trix_momentum": _trix_momentum_entry,
    "kst_momentum": _kst_momentum_entry,
    "schaff_trend_cycle": _schaff_trend_cycle_entry,
    "rainbow_oscillator": _rainbow_oscillator_entry,
    "true_strength": _true_strength_entry,
    "price_momentum": _price_momentum_entry,
    "volume_momentum": _volume_momentum_entry,
    "volatility_momentum": _volatility_momentum_entry,
    "trend_following": _trend_following_entry,
    "moving_avg_crossover": _moving_avg_crossover_entry,
    "ema_ribbon": _ema_ribbon_entry,
    "guppy_mma": _guppy_mma_entry,
    "alligator": _alligator_entry,
    "fractal_momentum": _fractal_momentum_entry,
    "adaptive_momentum": _adaptive_momentum_entry,
    "dynamic_momentum": _dynamic_momentum_entry,
    "regime_momentum": _regime_momentum_entry,
    "ml_momentum": _ml_momentum_entry,
    "ensemble_momentum": _ensemble_momentum_entry,
    "volatility_adjusted_momentum": _volatility_adjusted_momentum_entry,
    "volume_weighted_momentum": _volume_weighted_momentum_entry,
    "correlation_momentum": _correlation_momentum_entry,
    "beta_momentum": _beta_momentum_entry,
    "residual_momentum": _residual_momentum_entry,
    "path_dependent_momentum": _path_dependent_momentum_entry,
    "asymmetric_momentum": _asymmetric_momentum_entry,
    "signed_momentum": _signed_momentum_entry,
    "directional_momentum": _directional_momentum_entry,
    "mass_index": _mass_index_entry,
    "vortex": _vortex_entry,
    "kama": _kama_entry,
    "hull_ma": _hull_ma_entry,
    "tema": _tema_entry,
    "dema": _dema_entry,
    "wma": _wma_entry,
    "smma": _smma_entry,
    "laguerre": _laguerre_entry,
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
    print(f"MOMENTUM 50 STRATEGY SWEEP")
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
    print(f"Testing {len(MOMENTUM_STRATEGIES)} momentum strategies...\n")

    results = []
    total_tests = len(all_candles) * len(MOMENTUM_STRATEGIES)
    test_count = 0

    for strat_def in MOMENTUM_STRATEGIES:
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

        print(f"  {strat_name:<28} | {len(profitable):>3}/{len(coin_results)} coins | "
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

    out_path = Path(__file__).parent.parent / "reports" / "momentum_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 MOMENTUM STRATEGIES:")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:>2}. {r['strategy']:<28} ${r['total_net_pnl']:>8.2f}  "
              f"Hit: {r['hit_rate']:>5.1f}%  "
              f"Profitable: {r['profitable_coins']}/{r['coins_tested']}")

    if output["promoted_for_30d"]:
        print(f"\nPROMOTED FOR 30D VALIDATION:")
        for s in output["promoted_for_30d"]:
            print(f"  * {s}")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
