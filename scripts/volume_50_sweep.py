#!/usr/bin/env python3
"""
Volume 50 Strategy Sweep — Batch #3 of the 500 Strategies Initiative.

Tests 50 unique volume-based strategies across 35 Coinbase coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants cover:
- On-Balance Volume (OBV) based entries
- VWAP reversion and breakout
- Volume spike detection and follow-through
- Chaikin Money Flow (CMF) signals
- Money Flow Index (MFI) divergences
- Accumulation/Distribution line patterns
- Volume-price divergence detection
- Volume momentum and trend
- Volume profile / support-resistance
- Volume oscillator signals

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
# VOLUME HELPER FUNCTIONS
# ==========================================

def compute_obv(candles):
    """On-Balance Volume cumulative series."""
    obv = [0.0]
    for i in range(1, len(candles)):
        close = float(candles[i]["close"])
        prev_close = float(candles[i - 1]["close"])
        vol = float(candles[i]["volume"])
        if close > prev_close:
            obv.append(obv[-1] + vol)
        elif close < prev_close:
            obv.append(obv[-1] - vol)
        else:
            obv.append(obv[-1])
    return obv


def compute_vwap(candles, period=20):
    """Volume Weighted Average Price."""
    if len(candles) < period:
        return None
    recent = candles[-period:]
    cum_vol_price = sum(float(c["volume"]) * (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3 for c in recent)
    cum_vol = sum(float(c["volume"]) for c in recent)
    if cum_vol == 0:
        return None
    return cum_vol_price / cum_vol


def compute_cmf(candles, period=20):
    """Chaikin Money Flow."""
    if len(candles) < period + 1:
        return None
    recent = candles[-period:]
    mfv_sum = 0.0
    vol_sum = 0.0
    for c in recent:
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        v = float(c["volume"])
        if h != l:
            mfm = ((cl - l) - (h - cl)) / (h - l)
            mfv_sum += mfm * v
        vol_sum += v
    if vol_sum == 0:
        return None
    return mfv_sum / vol_sum


def compute_mfi(candles, period=14):
    """Money Flow Index."""
    if len(candles) < period + 2:
        return None, None
    typical_prices = []
    volumes = []
    for c in candles[-(period + 1):]:
        tc = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3
        typical_prices.append(tc)
        volumes.append(float(c["volume"]))

    pos_flow = 0.0
    neg_flow = 0.0
    for i in range(1, len(typical_prices)):
        raw_flow = typical_prices[i] * volumes[i]
        if typical_prices[i] > typical_prices[i - 1]:
            pos_flow += raw_flow
        else:
            neg_flow += raw_flow

    if neg_flow == 0:
        return 100.0, (pos_flow, neg_flow)
    mf_ratio = pos_flow / neg_flow
    mfi = 100 - 100 / (1 + mf_ratio)
    return mfi, (pos_flow, neg_flow)


def compute_ad_line(candles, period=20):
    """Accumulation/Distribution running total."""
    if len(candles) < period:
        return None
    ad = 0.0
    for c in candles[-period:]:
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        v = float(c["volume"])
        if h != l:
            clv = ((cl - l) - (h - cl)) / (h - l)
            ad += clv * v
    return ad


def compute_volume_ema(candles, period=20):
    """EMA of volume."""
    if len(candles) < period:
        return None
    vols = [float(c["volume"]) for c in candles[-period:]]
    multiplier = 2 / (period + 1)
    ema = sum(vols) / period
    return ema


def compute_volume_ratio(candles, short_period=5, long_period=20):
    """Short-term volume / long-term volume ratio."""
    if len(candles) < long_period:
        return None
    short_vols = [float(c["volume"]) for c in candles[-short_period:]]
    long_vols = [float(c["volume"]) for c in candles[-long_period:-short_period]]
    if not long_vols:
        return None
    short_avg = sum(short_vols) / len(short_vols)
    long_avg = sum(long_vols) / len(long_vols)
    if long_avg == 0:
        return None
    return short_avg / long_avg


# ==========================================
# VOLUME STRATEGY ENTRY FUNCTIONS
# ==========================================

def _obv_trend_entry(candles_hist, closes, candle, params):
    """Enter when OBV is trending up but price is flat (accumulation signal)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("obv_period", 20)

    obv = compute_obv(candles_hist)
    if len(obv) < period + 1:
        return False

    obv_recent = obv[-period:]
    obv_trend = obv_recent[-1] - obv_recent[0]

    price_change = closes[-1] / closes[-5] - 1 if len(closes) > 5 else 0

    # OBV up but price flat/down = smart money accumulating
    if obv_trend > 0 and price_change < 0.01:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _obv_breakout_entry(candles_hist, closes, candle, params):
    """Enter when OBV makes a new high (volume-confirmed breakout)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("lookback", 20)

    obv = compute_obv(candles_hist)
    if len(obv) < period + 1:
        return False

    current_obv = obv[-1]
    obv_high = max(obv[-period - 1:-1])

    if current_obv > obv_high:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _obv_divergence_entry(candles_hist, closes, candle, params):
    """Enter when OBV makes higher low while price makes lower low (bullish divergence)."""
    if len(candles_hist) < 50:
        return False
    lookback = params.get("lookback", 10)

    obv = compute_obv(candles_hist)
    if len(obv) < lookback + 5:
        return False

    # Price lower low
    if len(closes) > lookback * 2:
        recent_lows = closes[-lookback:]
        prev_lows = closes[-lookback * 2:-lookback]
        if not recent_lows or not prev_lows:
            return False
        price_ll = min(recent_lows)
        price_pl = min(prev_lows)

        # OBV higher low
        obv_recent = obv[-lookback:]
        obv_prev = obv[-lookback * 2:-lookback]
        if not obv_recent or not obv_prev:
            return False
        obv_ll = min(obv_recent)
        obv_pl = min(obv_prev)

        if price_ll < price_pl and obv_ll > obv_pl:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _vwap_reversion_entry(candles_hist, closes, candle, params):
    """Enter when price is far below VWAP and starting to revert."""
    if len(candles_hist) < 30:
        return False
    period = params.get("vwap_period", 20)
    dev_pct = params.get("dev_pct", 2.0)

    vwap = compute_vwap(candles_hist, period)
    if vwap is None:
        return False

    current_price = float(candle["close"])
    deviation = (current_price - vwap) / vwap * 100

    # Enter when price is below VWAP by threshold
    if deviation < -dev_pct:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vwap_breakout_entry(candles_hist, closes, candle, params):
    """Enter when price breaks above VWAP with volume confirmation."""
    if len(candles_hist) < 30:
        return False
    period = params.get("vwap_period", 20)

    vwap = compute_vwap(candles_hist, period)
    if vwap is None:
        return False

    current_price = float(candle["close"])
    prev_price = closes[-2] if len(closes) > 1 else current_price

    if prev_price <= vwap and current_price > vwap:
        # Volume confirmation
        vol_ratio = compute_volume_ratio(candles_hist)
        if vol_ratio is not None and vol_ratio > 1.2:
            return True
    return False


def _vol_spike_entry(candles_hist, closes, candle, params):
    """Enter on massive volume spike + green candle."""
    if len(candles_hist) < 30:
        return False
    vol_lookback = params.get("vol_lookback", 20)
    vol_mult = params.get("vol_mult", 3.0)

    recent_vols = [float(c["volume"]) for c in candles_hist[-vol_lookback - 1:-1]]
    avg_vol = sum(recent_vols) / vol_lookback

    current_vol = float(candle["volume"])
    current_close = float(candle["close"])
    current_open = float(candle["open"])

    if current_vol > avg_vol * vol_mult and current_close > current_open:
        return True
    return False


def _vol_surge_followthru_entry(candles_hist, closes, candle, params):
    """Enter on volume surge follow-through (spike was 2+ bars ago)."""
    if len(candles_hist) < 30:
        return False
    lookback = params.get("lookback", 10)

    vols = [float(c["volume"]) for c in candles_hist]
    avg_vol = sum(vols[-lookback * 2:-lookback]) / lookback if len(vols) > lookback * 2 else 0

    if avg_vol == 0:
        return False

    # Spike was 2-5 bars ago, price still going up
    for i in range(2, 6):
        if len(vols) > i and vols[-i] > avg_vol * 2.0:
            if len(closes) > i and closes[-1] > closes[-i]:
                return True
    return False


def _cmf_signal_entry(candles_hist, closes, candle, params):
    """Enter when CMF crosses above zero (accumulation starts)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("cmf_period", 20)
    threshold = params.get("threshold", 0.05)

    cmf = compute_cmf(candles_hist, period)
    if cmf is None:
        return False

    # CMF turning positive = smart money buying
    if cmf > threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _cmf_divergence_entry(candles_hist, closes, candle, params):
    """Enter when CMF rising but price falling (accumulation divergence)."""
    if len(candles_hist) < 50:
        return False
    period = params.get("period", 20)

    cmf_now = compute_cmf(candles_hist, period)
    if cmf_now is None:
        return False

    cmf_prev = compute_cmf(candles_hist[:-1], period)
    if cmf_prev is None:
        return False

    # CMF rising
    cmf_rising = cmf_now > cmf_prev
    # Price falling
    price_falling = len(closes) > 3 and closes[-1] < closes[-3]

    if cmf_rising and price_falling:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _mfi_oversold_entry(candles_hist, closes, candle, params):
    """Enter when MFI is oversold and turning up."""
    if len(candles_hist) < 20:
        return False
    period = params.get("mfi_period", 14)
    os_thresh = params.get("os_thresh", 20)

    mfi, _ = compute_mfi(candles_hist, period)
    if mfi is None:
        return False

    if mfi < os_thresh:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _mfi_divergence_entry(candles_hist, closes, candle, params):
    """Enter when MFI makes higher low while price makes lower low."""
    if len(candles_hist) < 50:
        return False
    period = params.get("mfi_period", 14)
    lookback = params.get("lookback", 10)

    mfi_now, _ = compute_mfi(candles_hist, period)
    mfi_prev_data, _ = compute_mfi(candles_hist[:-lookback], period)
    if mfi_now is None or mfi_prev_data is None:
        return False

    # Price making lower lows
    if len(closes) > lookback * 2:
        recent_low = min(closes[-lookback:])
        prev_low = min(closes[-lookback * 2:-lookback])

        if recent_low < prev_low and mfi_now > mfi_prev_data:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _ad_accumulation_entry(candles_hist, closes, candle, params):
    """Enter when A/D line is rising but price is flat (accumulation)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)

    ad_now = compute_ad_line(candles_hist, period)
    ad_prev = compute_ad_line(candles_hist[:-3], period)
    if ad_now is None or ad_prev is None:
        return False

    ad_rising = ad_now > ad_prev
    price_flat = abs(closes[-1] / closes[-5] - 1) < 0.02 if len(closes) > 5 else False

    if ad_rising and price_flat:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ad_distribution_entry(candles_hist, closes, candle, params):
    """Enter when A/D line divergence signals end of distribution."""
    if len(candles_hist) < 40:
        return False
    period = params.get("period", 20)

    ad = compute_ad_line(candles_hist, period)
    ad_prev = compute_ad_line(candles_hist[:-5], period)
    if ad is None or ad_prev is None:
        return False

    # A/D was falling (distribution) now stabilizing
    if ad > ad_prev and ad_prev < 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_momentum_entry(candles_hist, closes, candle, params):
    """Enter when volume momentum is increasing and price confirms."""
    if len(candles_hist) < 30:
        return False
    vol_period = params.get("vol_period", 5)
    mom_period = params.get("mom_period", 3)

    vols = [float(c["volume"]) for c in candles_hist]
    if len(vols) < vol_period + mom_period:
        return False

    recent_vol = sum(vols[-vol_period:]) / vol_period
    prev_vol = sum(vols[-vol_period - mom_period:-mom_period]) / vol_period

    if prev_vol > 0 and recent_vol > prev_vol * 1.3:
        if len(closes) > mom_period and closes[-1] > closes[-mom_period]:
            return True
    return False


def _vol_trend_entry(candles_hist, closes, candle, params):
    """Enter when volume is in sustained uptrend (trend confirmation)."""
    if len(candles_hist) < 40:
        return False
    period = params.get("period", 10)

    vols = [float(c["volume"]) for c in candles_hist]
    if len(vols) < period * 3:
        return False

    # Compare 3 consecutive volume averages
    avg1 = sum(vols[-period:]) / period
    avg2 = sum(vols[-period * 2:-period]) / period
    avg3 = sum(vols[-period * 3:-period * 2]) / period

    if avg1 > avg2 > avg3:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_profile_support_entry(candles_hist, closes, candle, params):
    """Enter when price bounces off high-volume node (volume profile support)."""
    if len(candles_hist) < 50:
        return False
    lookback = params.get("lookback", 30)
    num_bins = params.get("num_bins", 10)

    recent = candles_hist[-lookback:]
    prices = [(float(c["high"]) + float(c["low"])) / 2 for c in recent]
    vols = [float(c["volume"]) for c in recent]

    price_min = min(prices)
    price_max = max(prices)
    if price_max == price_min:
        return False

    bin_width = (price_max - price_min) / num_bins
    bin_volumes = [0.0] * num_bins

    for p, v in zip(prices, vols):
        bin_idx = min(int((p - price_min) / bin_width), num_bins - 1)
        bin_volumes[bin_idx] += v

    # Find highest volume bin (support)
    max_bin = bin_volumes.index(max(bin_volumes))
    support_price = price_min + (max_bin + 0.5) * bin_width

    current_price = float(candle["close"])
    if abs(current_price - support_price) / support_price < 0.02:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_oscillator_entry(candles_hist, closes, candle, params):
    """Enter when volume oscillator (short EMA - long EMA) turns positive."""
    if len(candles_hist) < 40:
        return False
    short_period = params.get("short_period", 5)
    long_period = params.get("long_period", 20)

    vols = [float(c["volume"]) for c in candles_hist]

    def ema(data, period):
        if len(data) < period:
            return None
        mult = 2 / (period + 1)
        e = sum(data[:period]) / period
        for x in data[period:]:
            e = (x - e) * mult + e
        return e

    short_ema = ema(vols[-long_period:], short_period)
    long_ema = ema(vols[-long_period:], long_period)

    if short_ema is None or long_ema is None or long_ema == 0:
        return False

    oscillator = (short_ema - long_ema) / long_ema * 100

    if oscillator > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_price_divergence_entry(candles_hist, closes, candle, params):
    """Enter when volume rises but price falls (accumulation during dip)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 10)

    vols = [float(c["volume"]) for c in candles_hist]
    if len(vols) < period * 2:
        return False

    recent_vol = sum(vols[-period:]) / period
    prev_vol = sum(vols[-period * 2:-period]) / period

    price_change = closes[-1] / closes[-period] - 1 if len(closes) > period else 0

    # Volume up, price down = accumulation
    if prev_vol > 0 and recent_vol > prev_vol * 1.2 and price_change < -0.01:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_cmf_extreme_entry(candles_hist, closes, candle, params):
    """Enter when CMF reaches extreme positive (>0.25)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("cmf_period", 20)
    extreme = params.get("extreme", 0.25)

    cmf = compute_cmf(candles_hist, period)
    if cmf is None:
        return False

    if cmf > extreme:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_cmf_extreme_neg_entry(candles_hist, closes, candle, params):
    """Enter on bounce from extreme negative CMF (<-0.25) capitulation."""
    if len(candles_hist) < 30:
        return False
    period = params.get("cmf_period", 20)
    extreme = params.get("extreme", -0.25)

    cmf = compute_cmf(candles_hist, period)
    if cmf is None:
        return False

    if cmf < extreme:
        # Capitulation bounce
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_cumulative_delta_entry(candles_hist, closes, candle, params):
    """Enter when cumulative volume delta (buying - selling) turns positive."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)

    delta = 0
    for i in range(max(0, len(candles_hist) - period), len(candles_hist)):
        c = candles_hist[i]
        cl = float(c["close"])
        op = float(c["open"])
        v = float(c["volume"])
        if cl > op:
            delta += v
        elif cl < op:
            delta -= v

    if delta > 0 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _vol_ease_of_movement_entry(candles_hist, closes, candle, params):
    """Enter on Ease of Movement indicator turning positive."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 14)

    eom_values = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        v = float(candles_hist[i]["volume"])
        dist = (h + l) / 2 - (float(candles_hist[i - 1]["high"]) + float(candles_hist[i - 1]["low"])) / 2
        if v > 0:
            eom_values.append(dist / v * 100000000)

    if len(eom_values) < period:
        return False

    eom = sum(eom_values[-period:]) / period

    if eom > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_force_index_entry(candles_hist, closes, candle, params):
    """Enter when Force Index turns positive (price change × volume)."""
    if len(candles_hist) < 20:
        return False
    period = params.get("period", 13)

    force_values = []
    for i in range(1, len(candles_hist)):
        price_change = closes[i] - closes[i - 1]
        vol = float(candles_hist[i]["volume"])
        force_values.append(price_change * vol)

    if len(force_values) < period:
        return False

    force_ema = sum(force_values[-period:]) / period

    if force_ema > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_klinger_entry(candles_hist, closes, candle, params):
    """Enter on Klinger Volume Oscillator signal."""
    if len(candles_hist) < 60:
        return False
    fast = params.get("fast", 34)
    slow = params.get("slow", 55)

    kvo_values = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        cl = float(candles_hist[i]["close"])
        ph = float(candles_hist[i - 1]["high"])
        pl = float(candles_hist[i - 1]["low"])
        pcl = float(candles_hist[i - 1]["close"])
        v = float(candles_hist[i]["volume"])

        trend = (h + l + cl) - (ph + pl + pcl)
        dm = 0
        if trend > 0:
            dm = 1
        elif trend < 0:
            dm = -1

        if v > 0:
            kvo_values.append(dm * v)

    if len(kvo_values) < slow + 1:
        return False

    fast_ema = sum(kvo_values[-fast:]) / fast
    slow_ema = sum(kvo_values[-slow:]) / slow

    if fast_ema > slow_ema:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_negative_volume_entry(candles_hist, closes, candle, params):
    """Enter on Negative Volume Index decline (smart money accumulation)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)

    nvi = 1000.0
    for i in range(1, len(candles_hist)):
        vol = float(candles_hist[i]["volume"])
        prev_vol = float(candles_hist[i - 1]["volume"])
        cl = float(candles_hist[i]["close"])
        prev_cl = float(candles_hist[i - 1]["close"])

        if vol < prev_vol:
            if prev_cl > 0:
                pct = (cl - prev_cl) / prev_cl
                nvi += nvi * pct

    if nvi < 1000:
        # NVI below baseline = accumulation phase
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_positive_volume_entry(candles_hist, closes, candle, params):
    """Enter on Positive Volume Index surge."""
    if len(candles_hist) < 30:
        return False

    pvi = 1000.0
    for i in range(1, len(candles_hist)):
        vol = float(candles_hist[i]["volume"])
        prev_vol = float(candles_hist[i - 1]["volume"])
        cl = float(candles_hist[i]["close"])
        prev_cl = float(candles_hist[i - 1]["close"])

        if vol > prev_vol:
            if prev_cl > 0:
                pct = (cl - prev_cl) / prev_cl
                pvi += pvi * pct

    if pvi > 1050:
        # PVI surged = volume-confirmed move
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_chaikin_osc_entry(candles_hist, closes, candle, params):
    """Enter on Chaikin Oscillator (ADL EMA diff) turning positive."""
    if len(candles_hist) < 40:
        return False
    fast = params.get("fast", 3)
    slow = params.get("slow", 10)

    adl_values = []
    adl = 0.0
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

    fast_ema = sum(adl_values[-fast:]) / fast
    slow_ema = sum(adl_values[-slow:]) / slow

    if fast_ema > slow_ema:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_on_balance_reversal_entry(candles_hist, closes, candle, params):
    """Enter when OBV reverses from declining to rising."""
    if len(candles_hist) < 40:
        return False
    period = params.get("period", 10)

    obv = compute_obv(candles_hist)
    if len(obv) < period * 2:
        return False

    obv_recent = obv[-period:]
    obv_prev = obv[-period * 2:-period]

    if not obv_recent or not obv_prev:
        return False

    recent_trend = obv_recent[-1] - obv_recent[0]
    prev_trend = obv_prev[-1] - obv_prev[0]

    if prev_trend < 0 and recent_trend > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_anomaly_entry(candles_hist, closes, candle, params):
    """Enter on volume anomaly (unusual volume pattern detection)."""
    if len(candles_hist) < 50:
        return False
    period = params.get("period", 20)
    anomaly_mult = params.get("anomaly_mult", 2.5)

    vols = [float(c["volume"]) for c in candles_hist]
    avg_vol = sum(vols[-period * 2:-period]) / period
    std_vol = math.sqrt(sum((v - avg_vol) ** 2 for v in vols[-period * 2:-period]) / period)

    if std_vol > 0:
        z_score = (vols[-1] - avg_vol) / std_vol
        if z_score > anomaly_mult:
            if float(candle["close"]) > float(candle["open"]):
                return True
    return False


def _vol_weighted_entry(candles_hist, closes, candle, params):
    """Enter on volume-weighted price breakout."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)

    vwap = compute_vwap(candles_hist, period)
    if vwap is None:
        return False

    current_price = float(candle["close"])
    if current_price > vwap * 1.02:
        # Volume confirmation
        vol = float(candle["volume"])
        avg_vol = sum(float(c["volume"]) for c in candles_hist[-period:]) / period
        if vol > avg_vol * 1.5:
            return True
    return False


def _vol_session_entry(candles_hist, closes, candle, params):
    """Enter on session-based volume pattern (US open volume surge)."""
    if len(candles_hist) < 10:
        return False

    ts = int(candle.get("start", candle.get("time", 0)))
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

    # US market open: 14:30 UTC (9:30 ET)
    if 14 <= hour <= 16:
        vol = float(candle["volume"])
        avg_vol = sum(float(c["volume"]) for c in candles_hist[-10:]) / 10
        if vol > avg_vol * 1.3:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _vol_relative_entry(candles_hist, closes, candle, params):
    """Enter on relative volume vs 20-period average."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    rv_thresh = params.get("rv_thresh", 1.5)

    vols = [float(c["volume"]) for c in candles_hist]
    if len(vols) < period:
        return False

    current_vol = vols[-1]
    avg_vol = sum(vols[-period - 1:-1]) / period

    if avg_vol > 0 and current_vol > avg_vol * rv_thresh:
        if float(candle["close"]) > float(candle["open"]):
            return True
    return False


def _vol_silhouette_entry(candles_hist, closes, candle, params):
    """Enter on volume silhouette pattern (volume profile shape analysis)."""
    if len(candles_hist) < 40:
        return False
    period = params.get("period", 20)

    recent = candles_hist[-period:]
    buys = sum(1 for c in recent if float(c["close"]) > float(c["open"]))
    buy_vol = sum(float(c["volume"]) for c in recent if float(c["close"]) > float(c["open"]))
    sell_vol = sum(float(c["volume"]) for c in recent if float(c["close"]) < float(c["open"]))

    total_vol = buy_vol + sell_vol
    if total_vol == 0:
        return False

    buy_ratio = buy_vol / total_vol

    # >60% of volume on up candles with >60% up candles
    if buy_ratio > 0.6 and buys > period * 0.6:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

VOLUME_STRATEGIES = [
    # OBV-based
    {"name": "obv_trend", "params": {"obv_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "obv_breakout", "params": {"lookback": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "obv_divergence", "params": {"lookback": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # VWAP-based
    {"name": "vwap_reversion", "params": {"vwap_period": 20, "dev_pct": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "vwap_breakout", "params": {"vwap_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Volume spike
    {"name": "vol_spike", "params": {"vol_lookback": 20, "vol_mult": 3.0, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "vol_surge_followthru", "params": {"lookback": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_anomaly", "params": {"period": 20, "anomaly_mult": 2.5, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},

    # CMF
    {"name": "cmf_signal", "params": {"cmf_period": 20, "threshold": 0.05, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cmf_divergence", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cmf_extreme", "params": {"cmf_period": 20, "extreme": 0.25, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cmf_extreme_neg", "params": {"cmf_period": 20, "extreme": -0.25, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},

    # MFI
    {"name": "mfi_oversold", "params": {"mfi_period": 14, "os_thresh": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "mfi_divergence", "params": {"mfi_period": 14, "lookback": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # A/D Line
    {"name": "ad_accumulation", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ad_distribution", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Volume momentum/trend
    {"name": "vol_momentum", "params": {"vol_period": 5, "mom_period": 3, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_trend", "params": {"period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_relative", "params": {"period": 20, "rv_thresh": 1.5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Volume oscillators
    {"name": "vol_oscillator", "params": {"short_period": 5, "long_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_force_index", "params": {"period": 13, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_ease_of_movement", "params": {"period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_klinger", "params": {"fast": 34, "slow": 55, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_chaikin_osc", "params": {"fast": 3, "slow": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Volume profile
    {"name": "vol_profile_support", "params": {"lookback": 30, "num_bins": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_weighted", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Volume-price relationships
    {"name": "vol_price_divergence", "params": {"period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_cumulative_delta", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # OBV patterns
    {"name": "obv_on_balance_reversal", "params": {"period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "obv_negative_volume", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "obv_positive_volume", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Session/time
    {"name": "vol_session", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},

    # Pattern
    {"name": "vol_silhouette", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "obv_trend": _obv_trend_entry,
    "obv_breakout": _obv_breakout_entry,
    "obv_divergence": _obv_divergence_entry,
    "vwap_reversion": _vwap_reversion_entry,
    "vwap_breakout": _vwap_breakout_entry,
    "vol_spike": _vol_spike_entry,
    "vol_surge_followthru": _vol_surge_followthru_entry,
    "vol_anomaly": _vol_anomaly_entry,
    "cmf_signal": _cmf_signal_entry,
    "cmf_divergence": _cmf_divergence_entry,
    "cmf_extreme": _vol_cmf_extreme_entry,
    "cmf_extreme_neg": _vol_cmf_extreme_neg_entry,
    "mfi_oversold": _mfi_oversold_entry,
    "mfi_divergence": _mfi_divergence_entry,
    "ad_accumulation": _ad_accumulation_entry,
    "ad_distribution": _ad_distribution_entry,
    "vol_momentum": _vol_momentum_entry,
    "vol_trend": _vol_trend_entry,
    "vol_relative": _vol_relative_entry,
    "vol_oscillator": _vol_oscillator_entry,
    "vol_force_index": _vol_force_index_entry,
    "vol_ease_of_movement": _vol_ease_of_movement_entry,
    "vol_klinger": _vol_klinger_entry,
    "vol_chaikin_osc": _vol_chaikin_osc_entry,
    "vol_profile_support": _vol_profile_support_entry,
    "vol_weighted": _vol_weighted_entry,
    "vol_price_divergence": _vol_price_divergence_entry,
    "vol_cumulative_delta": _vol_cumulative_delta_entry,
    "obv_on_balance_reversal": _vol_on_balance_reversal_entry,
    "obv_negative_volume": _vol_negative_volume_entry,
    "obv_positive_volume": _vol_positive_volume_entry,
    "vol_session": _vol_session_entry,
    "vol_silhouette": _vol_silhouette_entry,
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
    print(f"VOLUME 50 STRATEGY SWEEP — Batch #3")
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
    print(f"Testing {len(VOLUME_STRATEGIES)} volume strategies...\n")

    results = []
    total_tests = len(all_candles) * len(VOLUME_STRATEGIES)
    test_count = 0

    for strat_def in VOLUME_STRATEGIES:
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

        print(f"  {strat_name:<25} | {len(profitable):>3}/{len(coin_results)} coins | "
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

    out_path = Path(__file__).parent.parent / "reports" / "volume_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 VOLUME STRATEGIES:")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:>2}. {r['strategy']:<25} ${r['total_net_pnl']:>8.2f}  "
              f"Hit: {r['hit_rate']:>5.1f}%  "
              f"Profitable: {r['profitable_coins']}/{r['coins_tested']}")

    if output["promoted_for_30d"]:
        print(f"\nPROMOTED FOR 30D VALIDATION:")
        for s in output["promoted_for_30d"]:
            print(f"  ✅ {s}")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
