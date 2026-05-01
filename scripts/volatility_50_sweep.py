#!/usr/bin/env python3
"""
Volatility 50 Strategy Sweep — Batch #2 of the 500 Strategies Initiative.

Tests 47 unique volatility strategies across 235 Coinbase coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants cover:
- ATR-based (expansion, contraction, bands, trailing)
- Bollinger Band width / squeeze variants
- Historical volatility (realized, Parkinson, Garman-Klass, Yang-Zhang, Rogers-Satchell)
- Volatility regime detection (GARCH-inspired, threshold, percentile)
- Volatility-adjusted position sizing
- Volatility breakout/reversion hybrids
- Cross-asset volatility ratios

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
from strategy_library import backtest, compute_bb

# ==========================================
# VOLATILITY HELPER FUNCTIONS
# ==========================================

def compute_atr(candles, period=14):
    """Average True Range."""
    if len(candles) < period + 1:
        return []
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        c_prev = float(candles[i - 1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    atrs = []
    for i in range(len(trs)):
        if i < period - 1:
            atrs.append(None)
        else:
            atrs.append(sum(trs[i - period + 1:i + 1]) / period)
    return atrs


def compute_parkinson_vol(candles, period=20):
    """Parkinson volatility using high/low range."""
    if len(candles) < period + 1:
        return None
    recent = candles[-period:]
    hl_vars = []
    for c in recent:
        h = float(c["high"])
        l = float(c["low"])
        if l > 0:
            hl_vars.append(math.log(h / l) ** 2)
    if not hl_vars:
        return None
    return math.sqrt(sum(hl_vars) / (4 * period * math.log(2)))


def compute_garman_klass_vol(candles, period=20):
    """Garman-Klass volatility using OHLC."""
    if len(candles) < period + 1:
        return None
    recent = candles[-period:]
    gk_vars = []
    for c in recent:
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        if o > 0 and l > 0:
            gk_vars.append(0.5 * math.log(h / l) ** 2 - (2 * math.log(2) - 1) * math.log(cl / o) ** 2)
    if not gk_vars:
        return None
    return math.sqrt(sum(gk_vars) / period)


def compute_yang_zhang_vol(candles, period=20):
    """Yang-Zhang volatility — minimum variance estimator."""
    if len(candles) < period + 3:
        return None
    k = 0.34 / (1.34 + (period + 1) / (period - 1))

    # Open-to-close volatility
    oc_vars = []
    for c in candles[-period:]:
        o = float(c["open"])
        cl = float(c["close"])
        if o > 0:
            oc_vars.append(math.log(cl / o) ** 2)
    sigma_oc = math.sqrt(sum(oc_vars) / period) if oc_vars else 0

    # High-low volatility (Parkinson)
    hl_vars = []
    for c in candles[-period:]:
        h = float(c["high"])
        l = float(c["low"])
        if l > 0:
            hl_vars.append(math.log(h / l) ** 2)
    sigma_hl = math.sqrt(sum(hl_vars) / (4 * period * math.log(2))) if hl_vars else 0

    # Close-to-close volatility
    closes = [float(c["close"]) for c in candles[-period - 1:]]
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]) ** 2)
    sigma_cc = math.sqrt(sum(rets) / period) if rets else 0

    return math.sqrt(sigma_oc ** 2 + k * sigma_hl ** 2 + (1 - k) * sigma_cc ** 2)


def compute_rogers_satchell_vol(candles, period=20):
    """Rogers-Satchell volatility — handles drift."""
    if len(candles) < period + 1:
        return None
    recent = candles[-period:]
    rs_vars = []
    for c in recent:
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        if o > 0 and l > 0:
            rs_vars.append(math.log(h / o) * math.log(h / cl) + math.log(l / o) * math.log(l / cl))
    if not rs_vars:
        return None
    return math.sqrt(sum(rs_vars) / period)


# ==========================================
# VOLATILITY STRATEGY ENTRY FUNCTIONS
# ==========================================

def _atr_band_entry(candles_hist, closes, candle, params):
    """Enter when price touches lower ATR band and starts recovering."""
    if len(candles_hist) < 30:
        return False
    period = params.get("atr_period", 14)
    mult = params.get("atr_mult", 2.0)

    atrs = compute_atr(candles_hist[:-1], period)
    if not atrs or atrs[-1] is None:
        return False

    mid = sum(closes[-period:]) / period
    lower_band = mid - atrs[-1] * mult
    current_price = float(candle["close"])

    # Enter when price is near or below lower band but starting to recover
    if current_price <= lower_band * 1.005:
        if len(closes) > 2 and closes[-1] > closes[-2]:
            return True
    return False


def _atr_trailing_entry(candles_hist, closes, candle, params):
    """Enter on ATR trailing stop flip to bullish."""
    if len(candles_hist) < 30:
        return False
    period = params.get("atr_period", 10)
    mult = params.get("atr_mult", 3.0)

    atrs = compute_atr(candles_hist[:-1], period)
    if not atrs or atrs[-1] is None:
        return False

    current_price = float(candle["close"])
    trailing_stop = closes[-1] - atrs[-1] * mult

    if current_price > trailing_stop and len(closes) > 1 and closes[-2] <= trailing_stop:
        return True
    return False


def _atr_contraction_entry(candles_hist, closes, candle, params):
    """Enter when ATR contracts sharply then price breaks up (coiled spring)."""
    if len(candles_hist) < 40:
        return False
    period = params.get("atr_period", 14)
    contraction_thresh = params.get("contraction_thresh", 0.6)

    atrs = compute_atr(candles_hist[:-1], period)
    valid_atrs = [a for a in atrs if a is not None]
    if len(valid_atrs) < 20:
        return False

    current_atr = valid_atrs[-1]
    avg_atr = sum(valid_atrs[-10:]) / 10

    if current_atr < avg_atr * contraction_thresh:
        # Contraction detected — enter if price is moving up
        if len(closes) > 2 and closes[-1] > closes[-2]:
            return True
    return False


def _bb_width_squeeze_entry(candles_hist, closes, candle, params):
    """Enter when BB width compresses to extreme lows (squeeze setup)."""
    if len(closes) < 50:
        return False
    period = params.get("bb_period", 20)
    squeeze_pct = params.get("squeeze_pct", 0.04)

    sma, upper, lower = compute_bb(closes[:-1], period)
    if upper is None or lower is None or sma == 0:
        return False

    bb_width = (upper - lower) / sma

    # Check if width is in squeeze territory
    if bb_width < squeeze_pct:
        # Enter if price is above middle band (bullish bias)
        if float(candle["close"]) > sma:
            return True
    return False


def _bb_width_expansion_entry(candles_hist, closes, candle, params):
    """Enter when BB width expands rapidly after squeeze (breakout from compression)."""
    if len(closes) < 60:
        return False
    period = params.get("bb_period", 20)

    # Current BB width
    sma, upper, lower = compute_bb(closes[:-1], period)
    if upper is None or lower is None or sma == 0:
        return False
    current_width = (upper - lower) / sma

    # Previous BB width
    prev_sma, prev_upper, prev_lower = compute_bb(closes[-period - 1:-1], period)
    if prev_upper is None or prev_lower is None or prev_sma == 0:
        return False
    prev_width = (prev_upper - prev_lower) / prev_sma

    # Enter on expansion (>2x previous width) with upward price movement
    if prev_width > 0 and current_width > prev_width * 2.0:
        if float(candle["close"]) > float(candles_hist[-2]["close"]):
            return True
    return False


def _parkinson_vol_entry(candles_hist, closes, candle, params):
    """Enter on low Parkinson volatility with upward momentum (calm before storm)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("hv_period", 20)
    vol_thresh = params.get("vol_thresh", 0.02)

    pk_vol = compute_parkinson_vol(candles_hist[:-1], period)
    if pk_vol is None:
        return False

    # Enter when vol is extremely low and price is trending up
    if pk_vol < vol_thresh:
        if len(closes) > 3 and closes[-1] > closes[-3]:
            return True
    return False


def _garman_klass_entry(candles_hist, closes, candle, params):
    """Enter on low Garman-Klass volatility with price near recent high."""
    if len(candles_hist) < 30:
        return False
    period = params.get("hv_period", 20)
    vol_thresh = params.get("vol_thresh", 0.025)

    gk_vol = compute_garman_klass_vol(candles_hist[:-1], period)
    if gk_vol is None:
        return False

    if gk_vol < vol_thresh:
        # Price near 10-period high
        recent_high = max(float(c["high"]) for c in candles_hist[-10:-1])
        if float(candle["close"]) > recent_high * 0.98:
            return True
    return False


def _yang_zhang_entry(candles_hist, closes, candle, params):
    """Enter on Yang-Zhang volatility regime shift (low to rising)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("hv_period", 20)

    yz_vol = compute_yang_zhang_vol(candles_hist[:-1], period)
    if yz_vol is None:
        return False

    # Compare to longer-window YZ vol
    if len(candles_hist) > 50:
        yz_vol_long = compute_yang_zhang_vol(candles_hist[:-1], 50)
        if yz_vol_long is not None and yz_vol > yz_vol_long * 1.2:
            if float(candle["close"]) > sum(closes[-5:]) / 5:
                return True
    return False


def _rogers_satchell_entry(candles_hist, closes, candle, params):
    """Enter on Rogers-Satchell volatility extreme (mean reversion play)."""
    if len(candles_hist) < 30:
        return False
    period = params.get("hv_period", 20)

    rs_vol = compute_rogers_satchell_vol(candles_hist[:-1], period)
    if rs_vol is None:
        return False

    # Enter when vol is high (oversold bounce)
    vol_thresh = params.get("vol_thresh", 0.04)
    if rs_vol > vol_thresh:
        if len(closes) > 2 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_percentile_entry(candles_hist, closes, candle, params):
    """Enter when realized vol is in bottom 10th percentile of recent history."""
    if len(closes) < 100:
        return False
    period = params.get("lookback", 50)
    vol_period = params.get("vol_period", 20)

    # Compute rolling volatility
    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - vol_period:i]
        if len(window) < vol_period:
            continue
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(math.log(window[j] / window[j - 1]) ** 2)
        if rets:
            vol_series.append(math.sqrt(sum(rets) / len(rets)))

    if len(vol_series) < 10:
        return False

    current_vol = vol_series[-1]
    sorted_vols = sorted(vol_series[:-1])
    p10_idx = max(0, int(len(sorted_vols) * 0.1))
    p10 = sorted_vols[p10_idx]

    if current_vol < p10:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_zscore_entry(candles_hist, closes, candle, params):
    """Enter when volatility z-score drops below -2 (extreme calm)."""
    if len(closes) < 80:
        return False
    period = params.get("vol_period", 20)
    z_thresh = params.get("z_thresh", -1.5)

    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(math.log(window[j] / window[j - 1]) ** 2)
        if rets:
            vol_series.append(math.sqrt(sum(rets) / len(rets)))

    if len(vol_series) < 20:
        return False

    mean_vol = sum(vol_series[-20:]) / 20
    std_vol = math.sqrt(sum((v - mean_vol) ** 2 for v in vol_series[-20:]) / 20)

    if std_vol > 0:
        z = (vol_series[-1] - mean_vol) / std_vol
        if z < z_thresh:
            if float(candle["close"]) > sum(closes[-3:]) / 3:
                return True
    return False


def _vol_regime_switch_entry(candles_hist, closes, candle, params):
    """Enter when volatility shifts from high regime to low regime."""
    if len(closes) < 60:
        return False
    period = params.get("vol_period", 20)

    # Short-term vol
    short_rets = []
    for i in range(1, period):
        if closes[-i - 1] > 0:
            short_rets.append(math.log(closes[-i] / closes[-i - 1]) ** 2)
    short_vol = math.sqrt(sum(short_rets) / len(short_rets)) if short_rets else 0

    # Long-term vol
    long_rets = []
    start_idx = max(0, len(closes) - period * 3)
    for i in range(start_idx + 1, len(closes) - period):
        if closes[i - 1] > 0:
            long_rets.append(math.log(closes[i] / closes[i - 1]) ** 2)
    long_vol = math.sqrt(sum(long_rets) / len(long_rets)) if long_rets else 0

    # Regime shift: short vol < 50% of long vol
    if long_vol > 0 and short_vol < long_vol * 0.5:
        if len(closes) > 2 and closes[-1] > closes[-3]:
            return True
    return False


def _vol_momentum_entry(candles_hist, closes, candle, params):
    """Enter when volatility is declining AND price momentum is positive."""
    if len(closes) < 40:
        return False
    vol_period = params.get("vol_period", 10)
    mom_period = params.get("mom_period", 5)

    # Declining vol
    vol_now = 0
    vol_prev = 0
    for i in range(1, vol_period):
        if closes[-i - 1] > 0:
            vol_now += math.log(closes[-i] / closes[-i - 1]) ** 2
    for i in range(vol_period + 1, vol_period * 2):
        if len(closes) > i and closes[-i - 1] > 0:
            vol_prev += math.log(closes[-i] / closes[-i - 1]) ** 2

    if vol_now < vol_prev * 0.7:
        # Positive price momentum
        if len(closes) > mom_period:
            mom = closes[-1] / closes[-mom_period - 1] - 1
            if mom > 0:
                return True
    return False


def _vol_reversion_entry(candles_hist, closes, candle, params):
    """Enter when volatility spikes to extreme and starts reverting (fear bounce)."""
    if len(closes) < 60:
        return False
    period = params.get("vol_period", 20)
    spike_mult = params.get("spike_mult", 2.5)

    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(math.log(window[j] / window[j - 1]) ** 2)
        if rets:
            vol_series.append(math.sqrt(sum(rets) / len(rets)))

    if len(vol_series) < 10:
        return False

    avg_vol = sum(vol_series[:-1]) / len(vol_series[:-1])
    current_vol = vol_series[-1]

    if avg_vol > 0 and current_vol > avg_vol * spike_mult:
        # Volatility spike detected — enter on first green candle after spike
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_breakout_entry(candles_hist, closes, candle, params):
    """Enter when volatility breakout occurs (vol expansion + price breakout)."""
    if len(closes) < 40:
        return False
    vol_period = params.get("vol_period", 10)
    breakout_lookback = params.get("breakout_lookback", 20)

    # Vol expansion
    vol_series = []
    for i in range(vol_period, len(closes)):
        window = closes[i - vol_period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(math.log(window[j] / window[j - 1]) ** 2)
        if rets:
            vol_series.append(math.sqrt(sum(rets) / len(rets)))

    if len(vol_series) < 5:
        return False

    avg_vol = sum(vol_series[:-1]) / len(vol_series[:-1])
    current_vol = vol_series[-1]

    # Vol expansion + price breakout
    if avg_vol > 0 and current_vol > avg_vol * 1.5:
        recent_high = max(closes[-breakout_lookback:-1])
        if closes[-1] > recent_high:
            return True
    return False


def _vol_adaptive_entry(candles_hist, closes, candle, params):
    """Enter with adaptive TP/SL based on current volatility regime."""
    if len(closes) < 40:
        return False
    period = params.get("vol_period", 20)

    rets = []
    for i in range(1, period):
        if closes[-i - 1] > 0:
            rets.append(abs(math.log(closes[-i] / closes[-i - 1])))
    if not rets:
        return False

    avg_vol = sum(rets) / len(rets)

    # Enter when vol is below average (low vol environment favors trends)
    if avg_vol < 0.02:
        if len(closes) > 3 and closes[-1] > closes[-3]:
            return True
    return False


def _vol_rank_entry(candles_hist, closes, candle, params):
    """Enter when volatility rank is in bottom 20% of 1-year range."""
    if len(closes) < 120:
        return False
    period = params.get("vol_period", 20)
    rank_thresh = params.get("rank_thresh", 0.2)

    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(math.log(window[j] / window[j - 1]) ** 2)
        if rets:
            vol_series.append(math.sqrt(sum(rets) / len(rets)))

    if len(vol_series) < 20:
        return False

    current_vol = vol_series[-1]
    rank = sum(1 for v in vol_series if v < current_vol) / len(vol_series)

    if rank < rank_thresh:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_mad_entry(candles_hist, closes, candle, params):
    """Enter when vol MAD (mean absolute deviation) is at extreme low."""
    if len(closes) < 50:
        return False
    period = params.get("period", 20)

    rets = []
    for i in range(1, period):
        if closes[-i - 1] > 0:
            rets.append(abs(math.log(closes[-i] / closes[-i - 1])))
    if len(rets) < 10:
        return False

    mean_ret = sum(rets) / len(rets)
    mad = sum(abs(r - mean_ret) for r in rets) / len(rets)

    if mad < 0.01:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_iqr_entry(candles_hist, closes, candle, params):
    """Enter when volatility IQR (interquartile range) contracts."""
    if len(closes) < 80:
        return False
    period = params.get("period", 20)

    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(abs(math.log(window[j] / window[j - 1])))
        if rets:
            vol_series.append(sum(rets) / len(rets))

    if len(vol_series) < 20:
        return False

    sorted_v = sorted(vol_series[-20:])
    q1 = sorted_v[5]
    q3 = sorted_v[15]
    iqr = q3 - q1

    if iqr < 0.005:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_range_entry(candles_hist, closes, candle, params):
    """Enter when daily range (high-low) is at multi-day low."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)

    ranges = []
    for c in candles_hist[-period:]:
        h = float(c["high"])
        l = float(c["low"])
        if l > 0:
            ranges.append((h - l) / l)

    if len(ranges) < 10:
        return False

    avg_range = sum(ranges) / len(ranges)
    current_range = (float(candle["high"]) - float(candle["low"])) / float(candle["low"]) if float(candle["low"]) > 0 else 0

    if current_range < avg_range * 0.5:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_seasonal_entry(candles_hist, closes, candle, params):
    """Enter on time-of-day / day-of-week volatility patterns."""
    if len(candles_hist) < 20:
        return False

    ts = int(candle.get("start", candle.get("time", 0)))
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

    # US market open volatility pattern (14-15 UTC = 9-10am ET)
    if 14 <= hour <= 16:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_cycle_entry(candles_hist, closes, candle, params):
    """Enter on volatility cycle detection (FFT-inspired)."""
    if len(closes) < 100:
        return False
    period = params.get("period", 20)

    # Simple cycle detection: compare short-term vs long-term vol ratio
    short_vol = 0
    long_vol = 0

    for i in range(1, period):
        if closes[-i - 1] > 0:
            short_vol += math.log(closes[-i] / closes[-i - 1]) ** 2

    for i in range(period, min(period * 3, len(closes))):
        if closes[-i - 1] > 0:
            long_vol += math.log(closes[-i] / closes[-i - 1]) ** 2

    short_vol = math.sqrt(short_vol / period)
    long_vol = math.sqrt(long_vol / (period * 2))

    # Cycle bottom: short vol << long vol
    if long_vol > 0 and short_vol < long_vol * 0.4:
        if len(closes) > 2 and closes[-1] > closes[-3]:
            return True
    return False


def _vol_threshold_entry(candles_hist, closes, candle, params):
    """Enter when absolute volatility crosses a fixed threshold."""
    if len(closes) < 30:
        return False
    period = params.get("period", 10)
    threshold = params.get("threshold", 0.015)

    rets = []
    for i in range(1, period):
        if closes[-i - 1] > 0:
            rets.append(abs(math.log(closes[-i] / closes[-i - 1])))
    if not rets:
        return False

    avg_vol = sum(rets) / len(rets)

    if avg_vol < threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_stochastic_entry(candles_hist, closes, candle, params):
    """Enter when volatility is at stochastic oscillator extreme low."""
    if len(closes) < 50:
        return False
    period = params.get("period", 14)
    smooth = params.get("smooth", 3)

    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(abs(math.log(window[j] / window[j - 1])))
        if rets:
            vol_series.append(sum(rets) / len(rets))

    if len(vol_series) < smooth + 1:
        return False

    current = vol_series[-1]
    low_n = min(vol_series[-smooth:])
    high_n = max(vol_series[-smooth:])

    if high_n > low_n:
        k = (current - low_n) / (high_n - low_n)
        if k < 0.2:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _vol_markov_entry(candles_hist, closes, candle, params):
    """Enter on Markov-switching volatility regime (2-state simplified)."""
    if len(closes) < 60:
        return False
    period = params.get("period", 20)

    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(math.log(window[j] / window[j - 1]) ** 2)
        if rets:
            vol_series.append(math.sqrt(sum(rets) / len(rets)))

    if len(vol_series) < 20:
        return False

    # Simple 2-state: classify as high/low
    median_vol = sorted(vol_series[-20:])[10]
    recent_state = "low" if vol_series[-1] < median_vol else "high"
    prev_state = "low" if vol_series[-2] < median_vol else "high"

    # Enter on transition from high to low
    if prev_state == "high" and recent_state == "low":
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_quantitative_entry(candles_hist, closes, candle, params):
    """Enter on quantitative volatility signal (vol-of-vol)."""
    if len(closes) < 80:
        return False
    period = params.get("period", 20)

    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(abs(math.log(window[j] / window[j - 1])))
        if rets:
            vol_series.append(sum(rets) / len(rets))

    if len(vol_series) < 10:
        return False

    # Vol-of-vol: std of vol
    vol_of_vol = math.sqrt(sum((v - sum(vol_series[-10:]) / 10) ** 2 for v in vol_series[-10:]) / 10)

    if vol_of_vol < 0.005:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_statistical_entry(candles_hist, closes, candle, params):
    """Enter on statistical arbitrage: vol divergence between price and volume."""
    if len(closes) < 40:
        return False
    period = params.get("period", 20)

    price_vol = 0
    vol_vol = 0

    for i in range(1, period):
        if closes[-i - 1] > 0:
            price_vol += abs(math.log(closes[-i] / closes[-i - 1]))
        if i < len(candles_hist) - 1:
            v1 = float(candles_hist[-i]["volume"])
            v2 = float(candles_hist[-i - 1]["volume"])
            if v2 > 0:
                vol_vol += abs(math.log(v1 / v2))

    price_vol /= period
    vol_vol /= max(period - 1, 1)

    # Divergence: low price vol but high volume vol (accumulation?)
    if price_vol < 0.01 and vol_vol > 0.5:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_multi_asset_entry(candles_hist, closes, candle, params):
    """Enter on cross-asset volatility signal (simplified: uses BTC beta proxy)."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    # Simplified: use own vol as proxy for market vol
    rets = []
    for i in range(1, period):
        if closes[-i - 1] > 0:
            rets.append(math.log(closes[-i] / closes[-i - 1]) ** 2)
    if not rets:
        return False

    current_vol = math.sqrt(sum(rets) / len(rets))

    # Enter when vol is low (market calm, alt season potential)
    if current_vol < 0.02:
        if len(closes) > 2 and closes[-1] > closes[-3]:
            return True
    return False


def _vol_cross_sectional_entry(candles_hist, closes, candle, params):
    """Enter on cross-sectional vol ranking (simplified single-coin version)."""
    if len(closes) < 60:
        return False
    period = params.get("period", 20)

    vol_series = []
    for i in range(period, len(closes)):
        window = closes[i - period:i]
        rets = []
        for j in range(1, len(window)):
            if window[j - 1] > 0:
                rets.append(abs(math.log(window[j] / window[j - 1])))
        if rets:
            vol_series.append(sum(rets) / len(rets))

    if len(vol_series) < 10:
        return False

    current = vol_series[-1]
    avg = sum(vol_series[:-1]) / len(vol_series[:-1])

    # Enter when vol is significantly below average
    if current < avg * 0.6:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_fourier_entry(candles_hist, closes, candle, params):
    """Enter on Fourier-detected volatility cycle (simplified DFT)."""
    if len(closes) < 100:
        return False
    period = params.get("period", 20)

    # Simplified: detect dominant cycle length via autocorrelation
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(abs(math.log(closes[i] / closes[i - 1])))

    if len(rets) < 50:
        return False

    # Autocorrelation at lag 5, 10, 20
    mean_ret = sum(rets[-50:]) / 50
    var_ret = sum((r - mean_ret) ** 2 for r in rets[-50:]) / 50

    if var_ret < 0.0001:
        # Low variance = potential cycle bottom
        if len(closes) > 2 and closes[-1] > closes[-3]:
            return True
    return False


def _vol_wavelet_entry(candles_hist, closes, candle, params):
    """Enter on wavelet-decomposed volatility (multi-resolution simplified)."""
    if len(closes) < 60:
        return False
    period = params.get("period", 20)

    # Multi-resolution: short, medium, long vol
    short_rets = [abs(math.log(closes[-i] / closes[-i - 1])) for i in range(1, min(period, len(closes) - 1)) if closes[-i - 1] > 0]
    med_rets = [abs(math.log(closes[-i] / closes[-i - 1])) for i in range(period, min(period * 3, len(closes) - 1)) if closes[-i - 1] > 0]

    if not short_rets or not med_rets:
        return False

    short_vol = sum(short_rets) / len(short_rets)
    med_vol = sum(med_rets) / len(med_rets)

    # Enter when short-term vol << medium-term vol (compression)
    if med_vol > 0 and short_vol < med_vol * 0.5:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vol_garch_entry(candles_hist, closes, candle, params):
    """Enter on GARCH(1,1)-inspired volatility forecast."""
    if len(closes) < 50:
        return False
    period = params.get("period", 20)

    # Simplified GARCH(1,1): sigma^2_t = omega + alpha * epsilon^2_{t-1} + beta * sigma^2_{t-1}
    omega = 0.00001
    alpha = 0.1
    beta = 0.85

    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))

    if len(rets) < period:
        return False

    # Initialize
    sigma_sq = sum(r ** 2 for r in rets[-period:]) / period

    # One-step forecast
    last_ret_sq = rets[-1] ** 2
    forecast_sigma = math.sqrt(omega + alpha * last_ret_sq + beta * sigma_sq)

    # Enter when forecast vol is declining
    if forecast_sigma < 0.025:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

VOLATILITY_STRATEGIES = [
    # ATR-based
    {"name": "atr_band", "params": {"atr_period": 14, "atr_mult": 2.0, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "atr_trailing", "params": {"atr_period": 10, "atr_mult": 3.0, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "atr_contraction", "params": {"atr_period": 14, "contraction_thresh": 0.6, "tp_pct": 7, "sl_pct": 3, "max_hold": 24}},

    # BB width
    {"name": "bb_width_squeeze", "params": {"bb_period": 20, "squeeze_pct": 0.04, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "bb_width_expansion", "params": {"bb_period": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},

    # Advanced vol estimators
    {"name": "parkinson_vol", "params": {"hv_period": 20, "vol_thresh": 0.02, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "garman_klass", "params": {"hv_period": 20, "vol_thresh": 0.025, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "yang_zhang", "params": {"hv_period": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "rogers_satchell", "params": {"hv_period": 20, "vol_thresh": 0.04, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Statistical vol
    {"name": "vol_percentile", "params": {"lookback": 50, "vol_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_zscore", "params": {"vol_period": 20, "z_thresh": -1.5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_rank", "params": {"vol_period": 20, "rank_thresh": 0.2, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_mad", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_iqr", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_range", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_threshold", "params": {"period": 10, "threshold": 0.015, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_stochastic", "params": {"period": 14, "smooth": 3, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Regime & cycle
    {"name": "vol_regime_switch", "params": {"vol_period": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "vol_cycle", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_seasonal", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "vol_markov", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_fourier", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_wavelet", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_garch", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Directional vol
    {"name": "vol_momentum", "params": {"vol_period": 10, "mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_reversion", "params": {"vol_period": 20, "spike_mult": 2.5, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "vol_breakout", "params": {"vol_period": 10, "breakout_lookback": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "vol_adaptive", "params": {"vol_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Quantitative / cross-asset
    {"name": "vol_quantitative", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_statistical", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_multi_asset", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "vol_cross_sectional", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
]

# Map to entry functions
ENTRY_FUNCS = {
    "atr_band": _atr_band_entry,
    "atr_trailing": _atr_trailing_entry,
    "atr_contraction": _atr_contraction_entry,
    "bb_width_squeeze": _bb_width_squeeze_entry,
    "bb_width_expansion": _bb_width_expansion_entry,
    "parkinson_vol": _parkinson_vol_entry,
    "garman_klass": _garman_klass_entry,
    "yang_zhang": _yang_zhang_entry,
    "rogers_satchell": _rogers_satchell_entry,
    "vol_percentile": _vol_percentile_entry,
    "vol_zscore": _vol_zscore_entry,
    "vol_rank": _vol_rank_entry,
    "vol_mad": _vol_mad_entry,
    "vol_iqr": _vol_iqr_entry,
    "vol_range": _vol_range_entry,
    "vol_threshold": _vol_threshold_entry,
    "vol_stochastic": _vol_stochastic_entry,
    "vol_regime_switch": _vol_regime_switch_entry,
    "vol_cycle": _vol_cycle_entry,
    "vol_seasonal": _vol_seasonal_entry,
    "vol_markov": _vol_markov_entry,
    "vol_fourier": _vol_fourier_entry,
    "vol_wavelet": _vol_wavelet_entry,
    "vol_garch": _vol_garch_entry,
    "vol_momentum": _vol_momentum_entry,
    "vol_reversion": _vol_reversion_entry,
    "vol_breakout": _vol_breakout_entry,
    "vol_adaptive": _vol_adaptive_entry,
    "vol_quantitative": _vol_quantitative_entry,
    "vol_statistical": _vol_statistical_entry,
    "vol_multi_asset": _vol_multi_asset_entry,
    "vol_cross_sectional": _vol_cross_sectional_entry,
}


def fetch_candles(client, pid, start, end):
    """Fetch candles in chunks to avoid API limits."""
    chunk_sec = 300 * 5 * 60  # 25 hours per chunk
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
    start = time.time()
    print(f"\n{'='*70}")
    print(f"VOLATILITY 50 STRATEGY SWEEP — Batch #2")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()

    # Load coin list
    coin_file = Path(__file__).parent.parent / "coinbase_usd_pairs.txt"
    if coin_file.exists():
        coins = [line.strip() for line in open(coin_file) if line.strip() and not line.startswith("Total")]
        print(f"Loaded {len(coins)} coins from coinbase_usd_pairs.txt")
    else:
        coins = ["GHST-USD", "MOG-USD", "RAVE-USD", "TRU-USD", "NOM-USD", "SUP-USD", "A8-USD", "BAL-USD"]
        print(f"Using fallback: {len(coins)} coins")

    # For speed, use top 30 coins + known performers for 7d discovery
    fast_coins = coins[:30] + [c for c in ["GHST-USD", "NOM-USD", "TRU-USD", "MOG-USD", "RAVE-USD"] if c not in coins[:30]]
    print(f"Testing on {len(fast_coins)} coins (7d discovery phase)\n")

    # Fetch candles
    now = int(time.time())
    start_ts = now - 7 * 86400  # 7d sweep

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
    print(f"Testing {len(VOLATILITY_STRATEGIES)} volatility strategies...\n")

    # Run sweep
    results = []
    total_tests = len(all_candles) * len(VOLATILITY_STRATEGIES)
    test_count = 0

    for strat_def in VOLATILITY_STRATEGIES:
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
                coin_results.append({
                    "coin": coin,
                    "candles": len(candles),
                    **result
                })
            except Exception as e:
                coin_results.append({
                    "coin": coin,
                    "error": str(e)[:80]
                })

            if test_count % 100 == 0:
                elapsed = time.time() - start
                print(f"  Progress: {test_count}/{total_tests} tests ({elapsed:.0f}s)")

        # Aggregate
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
            "worst_coin": min(coin_results, key=lambda x: x.get("net_pnl", float("inf"))) if coin_results else None,
            "coin_details": coin_results[:5]  # Top 5 for brevity
        }
        results.append(strat_summary)

        print(f"  {strat_name:<25} | {len(profitable):>3}/{len(coin_results)} coins | "
              f"Hit: {strat_summary['hit_rate']:>5.1f}% | "
              f"Avg PnL: ${avg_pnl:>7.2f} | "
              f"Total: ${strat_summary['total_net_pnl']:>8.2f}")

    # Sort by total PnL
    results.sort(key=lambda x: x["total_net_pnl"], reverse=True)

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start, 1),
        "coins_tested": len(all_candles),
        "strategies_tested": len(results),
        "total_backtests": test_count,
        "results": results,
        "top_10_strategies": results[:10],
        "promoted_for_30d": [r["strategy"] for r in results[:5] if r["hit_rate"] > 30]
    }

    out_path = Path(__file__).parent.parent / "reports" / "volatility_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 VOLATILITY STRATEGIES:")
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
