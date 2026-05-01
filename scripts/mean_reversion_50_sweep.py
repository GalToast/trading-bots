#!/usr/bin/env python3
"""
Mean Reversion 50 Strategy Sweep — Mean Reversion batch of the 500 Strategies Initiative.

Tests 50 unique mean-reversion strategies across 35 Coinbase coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants cover:
- Bollinger Band reversion
- Z-score and statistical extremes
- Oscillator-based reversion (Stochastic, CCI, Williams %R)
- Rate of Change and momentum reversion
- Pairs trading and cointegration
- Kalman filter and state-space models
- Ornstein-Uhlenbeck process
- Hurst exponent regime detection
- Distance and cross-sectional reversion
- Adaptive and regime-switching methods
- Volume-confirmed reversion
- RSI/MACD/EMA/SMA/VWAP reversion
- Statistical deviation methods (std, MAD, percentile, rank)
- Beta, correlation, spread, ratio reversion
- Time-decay and weighted methods
- Pattern and seasonal reversion
- Cycle-based (wavelet, Fourier) reversion
- Entropy-based reversion
- Hybrid approaches (momentum filter, false breakout, gap fill)
- Exhaustion, capitulation, panic reversion
- Snap-back extreme deviation

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
# MEAN REVERSION HELPER FUNCTIONS
# ==========================================

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
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def compute_sma(data, period):
    """Simple Moving Average."""
    if len(data) < period:
        return None
    return sum(data[-period:]) / period


def compute_ema(data, period):
    """Exponential Moving Average."""
    if len(data) < period:
        return None
    mult = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for x in data[period:]:
        ema = (x - ema) * mult + ema
    return ema


def compute_std(data, period):
    """Standard deviation over a window."""
    if len(data) < period:
        return None
    window = data[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    return math.sqrt(variance)


def compute_bollinger(closes, period=20, num_std=2):
    """Bollinger Bands: returns (lower, middle, upper)."""
    if len(closes) < period:
        return None, None, None
    sma = compute_sma(closes, period)
    std = compute_std(closes, period)
    if std is None or sma is None:
        return None, None, None
    return sma - num_std * std, sma, sma + num_std * std


def compute_stochastic(candles, k_period=14):
    """Stochastic %K."""
    if len(candles) < k_period:
        return None
    recent = candles[-k_period:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    current_close = float(candles[-1]["close"])
    hh = max(highs)
    ll = min(lows)
    if hh == ll:
        return 50.0
    return (current_close - ll) / (hh - ll) * 100


def compute_cci(candles, period=20):
    """Commodity Channel Index."""
    if len(candles) < period:
        return None
    recent = candles[-period:]
    tps = [(float(c["high"]) + float(c["low"]) + float(c["close"])) / 3 for c in recent]
    mean_tp = sum(tps) / len(tps)
    mean_dev = sum(abs(tp - mean_tp) for tp in tps) / len(tps)
    if mean_dev == 0:
        return 0.0
    current_tp = (float(candles[-1]["high"]) + float(candles[-1]["low"]) + float(candles[-1]["close"])) / 3
    return (current_tp - mean_tp) / (0.015 * mean_dev)


def compute_williams_r(candles, period=14):
    """Williams %R."""
    if len(candles) < period:
        return None
    recent = candles[-period:]
    highs = [float(c["high"]) for c in recent]
    lows = [float(c["low"]) for c in recent]
    current_close = float(candles[-1]["close"])
    hh = max(highs)
    ll = min(lows)
    if hh == ll:
        return -50.0
    return (hh - current_close) / (hh - ll) * -100


def compute_roc(closes, period=10):
    """Rate of Change."""
    if len(closes) < period + 1:
        return None
    return (closes[-1] - closes[-period - 1]) / closes[-period - 1] * 100


def compute_zscore(data):
    """Z-score of the last value relative to the window."""
    if len(data) < 20:
        return None
    window = data[:-1]
    mean = sum(window) / len(window)
    std = math.sqrt(sum((x - mean) ** 2 for x in window) / len(window))
    if std == 0:
        return 0.0
    return (data[-1] - mean) / std


def compute_mad(data):
    """Mean Absolute Deviation."""
    if len(data) < 20:
        return None
    window = data[:-1]
    mean = sum(window) / len(window)
    mad = sum(abs(x - mean) for x in window) / len(window)
    if mad == 0:
        return 0.0
    return (data[-1] - mean) / mad


def compute_macd(closes, fast=12, slow=26, signal=9):
    """MACD line, signal line, histogram."""
    if len(closes) < slow + 1:
        return None, None, None
    fast_ema = compute_ema(closes, fast)
    slow_ema = compute_ema(closes, slow)
    if fast_ema is None or slow_ema is None:
        return None, None, None
    macd_line = fast_ema - slow_ema
    # Simplified signal as EMA of recent MACD approximations
    return macd_line, None, None


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


def compute_hurst(closes, min_lag=4, max_lag=20):
    """Simplified Hurst exponent via R/S analysis."""
    if len(closes) < max_lag * 2:
        return None
    lags = range(min_lag, max_lag + 1)
    tau = []
    for lag in lags:
        window = closes[-lag * 2:]
        if len(window) < lag * 2:
            continue
        diffs = [window[i + lag] - window[i] for i in range(len(window) - lag)]
        if not diffs:
            continue
        mean_d = sum(diffs) / len(diffs)
        std_d = math.sqrt(sum((d - mean_d) ** 2 for d in diffs) / len(diffs))
        if std_d > 0:
            tau.append(std_d)
    if len(tau) < 3:
        return None
    # Simplified: estimate Hurst from slope of log-log regression
    log_lags = [math.log(l) for l in lags[:len(tau)]]
    log_tau = [math.log(t) for t in tau]
    n = len(log_lags)
    mean_x = sum(log_lags) / n
    mean_y = sum(log_tau) / n
    num = sum((log_lags[i] - mean_x) * (log_tau[i] - mean_y) for i in range(n))
    den = sum((x - mean_x) ** 2 for x in log_lags)
    if den == 0:
        return 0.5
    slope = num / den
    hurst = slope  # approximated
    return hurst


def compute_kalman_estimate(closes):
    """Simplified 1D Kalman filter estimate of price level."""
    if len(closes) < 10:
        return None, None
    x = closes[0]  # state estimate
    P = 1.0  # estimation error covariance
    Q = 0.001  # process noise
    R = 0.1  # measurement noise

    for z in closes[1:]:
        # Predict
        x_pred = x
        P_pred = P + Q
        # Update
        K = P_pred / (P_pred + R)  # Kalman gain
        x = x_pred + K * (z - x_pred)
        P = (1 - K) * P_pred

    return x, math.sqrt(P)


def compute_ou_params(closes, lookback=30):
    """Ornstein-Uhlenbeck process parameters via simplified OLS."""
    if len(closes) < lookback + 1:
        return None, None, None
    window = closes[-lookback - 1:]
    deltas = [window[i + 1] - window[i] for i in range(len(window) - 1)]
    levels = window[:-1]
    mean_level = sum(levels) / len(levels)

    # OLS: delta = theta * (mu - level) + epsilon
    # Simplified: estimate mean-reversion speed
    x_centered = [l - mean_level for l in levels]
    num = sum(deltas[i] * x_centered[i] for i in range(len(deltas)))
    den = sum(x * x for x in x_centered)
    if den == 0:
        return None, None, None
    theta = -num / den  # mean reversion speed
    sigma = math.sqrt(sum(d ** 2 for d in deltas) / len(deltas))
    return theta, mean_level, sigma


def compute_rolling_mean(closes, period=20):
    """Rolling mean of recent closes."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def compute_percentile_rank(data, period=30):
    """Percentile rank of last value in recent window."""
    if len(data) < period + 1:
        return None
    window = data[-period:-1]
    current = data[-1]
    count_below = sum(1 for v in window if v < current)
    return count_below / len(window) * 100


def compute_entropy(closes, period=20, bins=10):
    """Shannon entropy of price returns."""
    if len(closes) < period + 1:
        return None
    returns = [closes[i] - closes[i - 1] for i in range(max(1, len(closes) - period), len(closes))]
    if not returns:
        return None
    min_r = min(returns)
    max_r = max(returns)
    if max_r == min_r:
        return 0.0
    bin_width = (max_r - min_r) / bins
    counts = [0] * bins
    for r in returns:
        idx = min(int((r - min_r) / bin_width), bins - 1)
        counts[idx] += 1
    total = len(returns)
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log(p)
    return entropy


def compute_fourier_dominant_cycle(closes, period=40):
    """Find dominant cycle length via simplified DFT."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / len(window)
    centered = [x - mean for x in window]
    n = len(centered)

    best_power = 0
    best_cycle = None
    for k in range(2, n // 2):
        re = sum(centered[t] * math.cos(2 * math.pi * k * t / n) for t in range(n))
        im = sum(centered[t] * math.sin(2 * math.pi * k * t / n) for t in range(n))
        power = re * re + im * im
        if power > best_power:
            best_power = power
            best_cycle = n / k
    return best_cycle


def compute_wavelet_detail(closes, level=1):
    """Simplified Haar wavelet detail coefficients (last value)."""
    if len(closes) < 8:
        return None
    data = closes[-8:]
    # One level of Haar decomposition
    detail = []
    for i in range(0, len(data) - 1, 2):
        detail.append((data[i] - data[i + 1]) / 2)
    if not detail:
        return None
    return detail[-1]


def compute_beta(coin_closes, benchmark_closes, period=20):
    """Beta of coin vs benchmark (simplified)."""
    if len(coin_closes) < period + 1 or len(benchmark_closes) < period + 1:
        return None
    coin_rets = [coin_closes[i] / coin_closes[i - 1] - 1 for i in range(len(coin_closes) - period, len(coin_closes))]
    bench_rets = [benchmark_closes[i] / benchmark_closes[i - 1] - 1 for i in range(len(benchmark_closes) - period, len(benchmark_closes))]
    n = min(len(coin_rets), len(bench_rets))
    if n < 5:
        return None
    coin_rets = coin_rets[-n:]
    bench_rets = bench_rets[-n:]
    mean_c = sum(coin_rets) / n
    mean_b = sum(bench_rets) / n
    cov = sum((coin_rets[i] - mean_c) * (bench_rets[i] - mean_b) for i in range(n)) / n
    var_b = sum((b - mean_b) ** 2 for b in bench_rets) / n
    if var_b == 0:
        return None
    return cov / var_b


# ==========================================
# MEAN REVERSION STRATEGY ENTRY FUNCTIONS
# ==========================================

def _bb_reversion_entry(candles_hist, closes, candle, params):
    """Bollinger Band lower touch + RSI oversold."""
    if len(closes) < 20:
        return False
    period = params.get("bb_period", 20)
    rsi_period = params.get("rsi_period", 14)
    rsi_thresh = params.get("rsi_thresh", 35)

    lower, mid, upper = compute_bollinger(closes, period)
    if lower is None:
        return False
    rsi = compute_rsi(closes, rsi_period)
    if rsi is None:
        return False

    current_price = float(candle["close"])
    if current_price <= lower and rsi < rsi_thresh:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _zscore_reversion_entry(candles_hist, closes, candle, params):
    """Z-score of price below -2, enter on revert."""
    if len(closes) < 25:
        return False
    threshold = params.get("zscore_thresh", -2.0)

    z = compute_zscore(closes)
    if z is None:
        return False
    if z < threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _stochastic_reversion_entry(candles_hist, closes, candle, params):
    """Stochastic %K below 20, enter on cross up."""
    if len(candles_hist) < 20:
        return False
    k_period = params.get("k_period", 14)
    threshold = params.get("threshold", 20)

    k_now = compute_stochastic(candles_hist, k_period)
    k_prev = compute_stochastic(candles_hist[:-1], k_period)
    if k_now is None or k_prev is None:
        return False

    if k_prev < threshold and k_now >= threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _cci_reversion_entry(candles_hist, closes, candle, params):
    """CCI below -100, enter on cross back above."""
    if len(candles_hist) < 25:
        return False
    period = params.get("cci_period", 20)
    threshold = params.get("threshold", -100)

    cci_now = compute_cci(candles_hist, period)
    cci_prev = compute_cci(candles_hist[:-1], period)
    if cci_now is None or cci_prev is None:
        return False

    if cci_prev < threshold and cci_now >= threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _williams_r_reversion_entry(candles_hist, closes, candle, params):
    """Williams %R below -80, enter on recovery."""
    if len(candles_hist) < 20:
        return False
    period = params.get("wr_period", 14)
    threshold = params.get("threshold", -80)

    wr_now = compute_williams_r(candles_hist, period)
    wr_prev = compute_williams_r(candles_hist[:-1], period)
    if wr_now is None or wr_prev is None:
        return False

    if wr_prev < threshold and wr_now >= threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _roc_reversion_entry(candles_hist, closes, candle, params):
    """Rate of Change extremely negative, enter on bounce."""
    if len(closes) < 15:
        return False
    period = params.get("roc_period", 10)
    threshold = params.get("threshold", -5.0)

    roc = compute_roc(closes, period)
    if roc is None:
        return False

    if roc < threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _pairs_trading_entry(candles_hist, closes, candle, params):
    """Price vs rolling mean divergence, enter on convergence."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)
    dev_mult = params.get("dev_mult", 2.0)

    mean = compute_rolling_mean(closes, period)
    std = compute_std(closes, period)
    if mean is None or std is None or std == 0:
        return False

    current = closes[-1]
    deviation = (current - mean) / std
    if deviation < -dev_mult:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _cointegration_entry(candles_hist, closes, candle, params):
    """Cointegration residual mean reversion (simplified vs BTC proxy)."""
    if len(closes) < 40:
        return False
    period = params.get("period", 30)
    threshold = params.get("threshold", 2.0)

    # Use mean as proxy for "fair value" cointegration level
    mean = compute_rolling_mean(closes, period)
    std = compute_std(closes, period)
    if mean is None or std is None or std == 0:
        return False

    residual = closes[-1] - mean
    if residual / std < -threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _kalman_reversion_entry(candles_hist, closes, candle, params):
    """Kalman filter estimate, enter when price below estimate."""
    if len(closes) < 15:
        return False
    dev_mult = params.get("dev_mult", 2.0)

    estimate, uncertainty = compute_kalman_estimate(closes)
    if estimate is None or uncertainty is None:
        return False

    current = closes[-1]
    if current < estimate - dev_mult * uncertainty:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ou_reversion_entry(candles_hist, closes, candle, params):
    """Ornstein-Uhlenbeck process, enter at -2 sigma."""
    if len(closes) < 35:
        return False
    lookback = params.get("lookback", 30)
    sigma_mult = params.get("sigma_mult", 2.0)

    theta, mean_level, sigma = compute_ou_params(closes, lookback)
    if theta is None or mean_level is None or sigma is None or sigma == 0:
        return False

    current = closes[-1]
    deviation = (current - mean_level) / sigma
    if deviation < -sigma_mult:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _hurst_reversion_entry(candles_hist, closes, candle, params):
    """Hurst exponent < 0.5 (mean reverting regime), enter on oversold."""
    if len(closes) < 50:
        return False
    hurst_thresh = params.get("hurst_thresh", 0.45)

    hurst = compute_hurst(closes)
    if hurst is None:
        return False

    # Mean reverting regime + price dip
    if hurst < hurst_thresh:
        rsi = compute_rsi(closes, 14)
        if rsi is not None and rsi < 35:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _distance_reversion_entry(candles_hist, closes, candle, params):
    """Distance from MA > 2 std, enter on revert."""
    if len(closes) < 30:
        return False
    ma_period = params.get("ma_period", 20)
    std_period = params.get("std_period", 20)
    dev_mult = params.get("dev_mult", 2.0)

    ma = compute_sma(closes, ma_period)
    std = compute_std(closes, std_period)
    if ma is None or std is None or std == 0:
        return False

    current = closes[-1]
    distance = (current - ma) / std
    if distance < -dev_mult:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _cross_sectional_reversion_entry(candles_hist, closes, candle, params):
    """Coin vs peer group relative performance reversion."""
    if len(closes) < 20:
        return False
    period = params.get("period", 10)
    threshold = params.get("threshold", -3.0)

    # Simplified: use own return vs rolling avg return as proxy
    if len(closes) < period + 1:
        return False
    roc = (closes[-1] - closes[-period - 1]) / closes[-period - 1] * 100
    avg_roc = sum((closes[-i] - closes[-i - period]) / closes[-i - period] * 100 for i in range(1, min(period + 1, len(closes) - period))) / min(period, len(closes) - period - 1) if len(closes) > period + 1 else 0

    if roc - avg_roc < threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _sector_reversion_entry(candles_hist, closes, candle, params):
    """Sector-relative mean reversion proxy."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    threshold = params.get("threshold", -2.5)

    # Proxy: short-term return vs medium-term return
    short_ret = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) > 5 else 0
    med_ret = (closes[-1] - closes[-period]) / closes[-period] * 100 if len(closes) > period else 0

    if short_ret < threshold and med_ret < 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _factor_reversion_entry(candles_hist, closes, candle, params):
    """Factor-based reversion signal."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    # Factor: momentum factor reversion (short-term reversal)
    returns_1 = (closes[-1] - closes[-2]) / closes[-2] if len(closes) > 2 else 0
    returns_5 = (closes[-1] - closes[-6]) / closes[-6] if len(closes) > 6 else 0
    returns_20 = (closes[-1] - closes[-period - 1]) / closes[-period - 1] if len(closes) > period + 1 else 0

    # Reversion: negative momentum over multiple horizons
    if returns_5 < -0.03 and returns_20 < -0.05 and returns_1 > -0.01:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _adaptive_reversion_entry(candles_hist, closes, candle, params):
    """Adaptive lookback based on volatility."""
    if len(closes) < 30:
        return False
    base_period = params.get("base_period", 20)
    dev_mult = params.get("dev_mult", 2.0)

    # Adaptive: scale period by recent vol
    recent_std = compute_std(closes, 10)
    long_std = compute_std(closes, 30)
    if recent_std is None or long_std is None or long_std == 0:
        return False

    vol_ratio = recent_std / long_std
    adaptive_period = max(10, int(base_period / vol_ratio))

    ma = compute_sma(closes, adaptive_period)
    std = compute_std(closes, adaptive_period)
    if ma is None or std is None or std == 0:
        return False

    if (closes[-1] - ma) / std < -dev_mult:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _dynamic_reversion_entry(candles_hist, closes, candle, params):
    """Dynamic threshold based on regime."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    ma = compute_sma(closes, period)
    std = compute_std(closes, period)
    if ma is None or std is None or std == 0:
        return False

    # Dynamic threshold: tighten in low vol, widen in high vol
    recent_std = compute_std(closes, 5)
    if recent_std is None:
        return False
    vol_ratio = recent_std / std
    threshold = 1.5 + vol_ratio  # adaptive

    if (closes[-1] - ma) / std < -threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _regime_reversion_entry(candles_hist, closes, candle, params):
    """Regime-switching mean reversion."""
    if len(closes) < 40:
        return False
    period = params.get("period", 20)

    # Detect regime: compare short vs long MA spread
    short_ma = compute_sma(closes, 5)
    long_ma = compute_sma(closes, period)
    if short_ma is None or long_ma is None:
        return False

    # Mean reversion regime: price far below both MAs
    std = compute_std(closes, period)
    if std is None or std == 0:
        return False

    if closes[-1] < short_ma and closes[-1] < long_ma:
        deviation = (closes[-1] - long_ma) / std
        if deviation < -1.5:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _volume_reversion_entry(candles_hist, closes, candle, params):
    """Mean reversion confirmed by volume drying up."""
    if len(closes) < 25 or len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    vol_period = params.get("vol_period", 10)

    ma = compute_sma(closes, period)
    std = compute_std(closes, period)
    if ma is None or std is None or std == 0:
        return False

    # Volume drying up: recent vol below average
    vols = [float(c["volume"]) for c in candles_hist]
    recent_vol = sum(vols[-vol_period:]) / vol_period
    avg_vol = sum(vols[-period:]) / period
    if avg_vol == 0:
        return False

    vol_drying = recent_vol < avg_vol * 0.7

    if (closes[-1] - ma) / std < -1.5 and vol_drying:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _volatility_reversion_entry(candles_hist, closes, candle, params):
    """Mean reversion in high vol regime."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    std = compute_std(closes, period)
    long_std = compute_std(closes, 40) if len(closes) >= 40 else std
    if std is None or long_std is None or long_std == 0:
        return False

    # High vol regime
    if std > long_std * 1.3:
        ma = compute_sma(closes, period)
        if ma is None:
            return False
        if (closes[-1] - ma) / std < -1.5:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _rsi_divergence_entry(candles_hist, closes, candle, params):
    """RSI divergence (price lower low, RSI higher low)."""
    if len(closes) < 30:
        return False
    lookback = params.get("lookback", 10)
    rsi_period = params.get("rsi_period", 14)

    if len(closes) < lookback * 2:
        return False

    # Price lower low
    recent_low = min(closes[-lookback:])
    prev_low = min(closes[-lookback * 2:-lookback])

    # RSI higher low
    rsi_recent_data = [compute_rsi(closes[:i], rsi_period) for i in range(len(closes) - lookback, len(closes))]
    rsi_prev_data = [compute_rsi(closes[:i], rsi_period) for i in range(len(closes) - lookback * 2, len(closes) - lookback)]
    rsi_recent_vals = [r for r in rsi_recent_data if r is not None]
    rsi_prev_vals = [r for r in rsi_prev_data if r is not None]

    if not rsi_recent_vals or not rsi_prev_vals:
        return False

    rsi_recent_low = min(rsi_recent_vals)
    rsi_prev_low = min(rsi_prev_vals)

    if recent_low < prev_low and rsi_recent_low > rsi_prev_low:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _macd_reversion_entry(candles_hist, closes, candle, params):
    """MACD histogram turning positive from extreme."""
    if len(closes) < 35:
        return False

    # Approximate MACD histogram turning
    macd_now, _, _ = compute_macd(closes)
    macd_prev, _, _ = compute_macd(closes[:-3])
    if macd_now is None or macd_prev is None:
        return False

    # Extreme negative MACD turning up
    if macd_prev < -0.01 * closes[-1] and macd_now > macd_prev:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ema_reversion_entry(candles_hist, closes, candle, params):
    """Price far below EMA, enter on snap-back."""
    if len(closes) < 30:
        return False
    period = params.get("ema_period", 20)
    dev_pct = params.get("dev_pct", 3.0)

    ema = compute_ema(closes, period)
    if ema is None or ema == 0:
        return False

    deviation = (closes[-1] - ema) / ema * 100
    if deviation < -dev_pct:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _sma_reversion_entry(candles_hist, closes, candle, params):
    """Price far below SMA, enter on snap-back."""
    if len(closes) < 25:
        return False
    period = params.get("sma_period", 20)
    dev_pct = params.get("dev_pct", 3.0)

    sma = compute_sma(closes, period)
    if sma is None or sma == 0:
        return False

    deviation = (closes[-1] - sma) / sma * 100
    if deviation < -dev_pct:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _vwap_reversion_entry(candles_hist, closes, candle, params):
    """Price far below VWAP, enter on revert."""
    if len(candles_hist) < 25:
        return False
    period = params.get("vwap_period", 20)
    dev_pct = params.get("dev_pct", 2.0)

    vwap = compute_vwap(candles_hist, period)
    if vwap is None or vwap == 0:
        return False

    deviation = (closes[-1] - vwap) / vwap * 100
    if deviation < -dev_pct:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _anchored_vwap_reversion_entry(candles_hist, closes, candle, params):
    """Reversion to anchored VWAP (from window start)."""
    if len(candles_hist) < 30:
        return False
    anchor_bars = params.get("anchor_bars", 30)
    dev_pct = params.get("dev_pct", 2.5)

    if len(candles_hist) < anchor_bars:
        return False
    anchored = candles_hist[-anchor_bars:]
    cum_vol_price = sum(float(c["volume"]) * (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3 for c in anchored)
    cum_vol = sum(float(c["volume"]) for c in anchored)
    if cum_vol == 0:
        return False
    avwap = cum_vol_price / cum_vol

    if avwap == 0:
        return False
    deviation = (closes[-1] - avwap) / avwap * 100
    if deviation < -dev_pct:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _std_dev_reversion_entry(candles_hist, closes, candle, params):
    """Price > 2 std below mean, enter on revert."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    num_std = params.get("num_std", 2.0)

    mean = compute_sma(closes, period)
    std = compute_std(closes, period)
    if mean is None or std is None or std == 0:
        return False

    if (closes[-1] - mean) / std < -num_std:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _mad_reversion_entry(candles_hist, closes, candle, params):
    """Mean absolute deviation extreme."""
    if len(closes) < 25:
        return False
    threshold = params.get("threshold", -2.5)

    mad_score = compute_mad(closes)
    if mad_score is None:
        return False

    if mad_score < threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _percentile_channel_entry(candles_hist, closes, candle, params):
    """Price below 10th percentile of channel."""
    if len(closes) < 35:
        return False
    period = params.get("period", 30)
    pct_thresh = params.get("pct_thresh", 10)

    if len(closes) < period + 1:
        return False
    window = closes[-period:]
    current = closes[-1]
    rank = sum(1 for v in window if v <= current) / len(window) * 100

    if rank < pct_thresh:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _rank_reversion_entry(candles_hist, closes, candle, params):
    """Price rank in recent window at bottom."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    rank_thresh = params.get("rank_thresh", 3)

    if len(closes) < period:
        return False
    window = closes[-period:]
    current = closes[-1]
    rank = sum(1 for v in window if v < current)

    if rank < rank_thresh:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _beta_reversion_entry(candles_hist, closes, candle, params):
    """Beta-adjusted reversion signal."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)
    threshold = params.get("threshold", -2.0)

    # Use own rolling mean as benchmark proxy
    beta = compute_beta(closes, closes, period)
    if beta is None:
        return False

    ma = compute_sma(closes, period)
    std = compute_std(closes, period)
    if ma is None or std is None or std == 0:
        return False

    # Beta-adjusted deviation
    adj_dev = (closes[-1] - ma) / (std * max(abs(beta), 0.5))
    if adj_dev < threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _correlation_reversion_entry(candles_hist, closes, candle, params):
    """Correlation breakdown reversion."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)
    threshold = params.get("threshold", -2.5)

    # Correlation with own lagged series as proxy for breakdown
    if len(closes) < period * 2:
        return False
    series1 = closes[-period:]
    series2 = closes[-period * 2:-period]

    mean1 = sum(series1) / len(series1)
    mean2 = sum(series2) / len(series2)
    std1 = math.sqrt(sum((x - mean1) ** 2 for x in series1) / len(series1))
    std2 = math.sqrt(sum((x - mean2) ** 2 for x in series2) / len(series2))
    if std1 == 0 or std2 == 0:
        return False

    cov = sum((series1[i] - mean1) * (series2[i] - mean2) for i in range(period)) / period
    corr = cov / (std1 * std2)

    # Low correlation regime + oversold
    if corr < 0.3:
        ma = compute_sma(closes, period)
        if ma is None:
            return False
        std = compute_std(closes, period)
        if std is None or std == 0:
            return False
        if (closes[-1] - ma) / std < threshold:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _spread_reversion_entry(candles_hist, closes, candle, params):
    """Spread from fair value reversion."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    spread_thresh = params.get("spread_thresh", 3.0)

    # Fair value = EMA
    fair = compute_ema(closes, period)
    if fair is None or fair == 0:
        return False

    spread_pct = (closes[-1] - fair) / fair * 100
    if spread_pct < -spread_thresh:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _ratio_reversion_entry(candles_hist, closes, candle, params):
    """Price ratio reversion (ratio to MA)."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    ratio_thresh = params.get("ratio_thresh", 0.95)

    ma = compute_sma(closes, period)
    if ma is None or ma == 0:
        return False

    ratio = closes[-1] / ma
    if ratio < ratio_thresh:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _residual_reversion_entry(candles_hist, closes, candle, params):
    """Regression residual mean reversion."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    threshold = params.get("threshold", -2.0)

    # Simple linear regression residual
    window = closes[-period:]
    n = len(window)
    x_mean = (n - 1) / 2
    y_mean = sum(window) / n
    num = sum((i - x_mean) * (window[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return False
    slope = num / den
    intercept = y_mean - slope * x_mean

    predicted = slope * (n - 1) + intercept
    residual = closes[-1] - predicted
    std = compute_std(closes, period)
    if std is None or std == 0:
        return False

    if residual / std < threshold:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _time_decay_reversion_entry(candles_hist, closes, candle, params):
    """Time-weighted mean reversion."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    dev_mult = params.get("dev_mult", 2.0)

    # Time-weighted mean (more weight to recent)
    window = closes[-period:]
    weights = [i + 1 for i in range(len(window))]
    w_sum = sum(weights)
    tw_mean = sum(w * v for w, v in zip(weights, window)) / w_sum

    std = compute_std(closes, period)
    if std is None or std == 0:
        return False

    if (closes[-1] - tw_mean) / std < -dev_mult:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _weighted_reversion_entry(candles_hist, closes, candle, params):
    """Volume-weighted mean reversion."""
    if len(closes) < 25 or len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    dev_mult = params.get("dev_mult", 2.0)

    recent = candles_hist[-period:]
    vols = [float(c["volume"]) for c in recent]
    prices = [float(c["close"]) for c in recent]
    total_vol = sum(vols)
    if total_vol == 0:
        return False

    vw_mean = sum(v * p for v, p in zip(vols, prices)) / total_vol
    std = compute_std(closes, period)
    if std is None or std == 0:
        return False

    if (closes[-1] - vw_mean) / std < -dev_mult:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _pattern_reversion_entry(candles_hist, closes, candle, params):
    """Candle pattern + mean reversion."""
    if len(closes) < 5:
        return False

    # Hammer-like pattern: long lower wick, small body at top
    c = candle
    body = abs(float(c["close"]) - float(c["open"]))
    range_c = float(c["high"]) - float(c["low"])
    lower_wick = min(float(c["close"]), float(c["open"])) - float(c["low"])

    is_hammer = range_c > 0 and lower_wick > body * 2 and body < range_c * 0.3

    # Also check: recent downtrend
    is_downtrend = len(closes) > 3 and closes[-1] < closes[-3]

    if is_hammer and is_downtrend:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _seasonal_reversion_entry(candles_hist, closes, candle, params):
    """Seasonal mean reversion (intra-day / intra-week pattern)."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    ts = int(candle.get("start", candle.get("time", 0)))
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

    # Simplified: check if price is below mean during typically bullish hours
    ma = compute_sma(closes, period)
    if ma is None:
        return False

    # During accumulation hours (0-6 UTC), expect reversion
    if 0 <= hour <= 6:
        if closes[-1] < ma * 0.97:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _cycle_reversion_entry(candles_hist, closes, candle, params):
    """Cycle-based mean reversion."""
    if len(closes) < 40:
        return False
    period = params.get("period", 20)

    # Estimate cycle position via zero-crossing count
    ma = compute_sma(closes, period)
    if ma is None:
        return False

    deviations = [closes[-period + i] - ma for i in range(period) if len(closes) >= period]
    if len(deviations) < 4:
        return False

    zero_crossings = sum(1 for i in range(1, len(deviations)) if deviations[i] * deviations[i - 1] < 0)

    # Near cycle trough: even number of crossings suggests we're at bottom
    if zero_crossings >= 2 and deviations[-1] < 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _wavelet_reversion_entry(candles_hist, closes, candle, params):
    """Wavelet-decomposed mean reversion."""
    if len(closes) < 15:
        return False
    threshold = params.get("threshold", -0.02)

    detail = compute_wavelet_detail(closes)
    if detail is None:
        return False

    # Negative detail coefficient suggests mean-reverting move
    if detail < threshold * closes[-1]:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _fourier_reversion_entry(candles_hist, closes, candle, params):
    """Fourier-based cycle reversion."""
    if len(closes) < 45:
        return False
    period = params.get("period", 40)

    dominant_cycle = compute_fourier_dominant_cycle(closes, period)
    if dominant_cycle is None:
        return False

    # If cycle detected, check if we're near trough
    ma = compute_sma(closes, int(dominant_cycle))
    if ma is None:
        return False

    if closes[-1] < ma * 0.97:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _entropy_reversion_entry(candles_hist, closes, candle, params):
    """Entropy-based mean reversion."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    entropy_thresh = params.get("entropy_thresh", 1.5)

    entropy = compute_entropy(closes, period)
    if entropy is None:
        return False

    # Low entropy = trending, high entropy = choppy/reverting
    if entropy > entropy_thresh:
        ma = compute_sma(closes, period)
        std = compute_std(closes, period)
        if ma is None or std is None or std == 0:
            return False
        if (closes[-1] - ma) / std < -1.5:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _momentum_reversion_hybrid_entry(candles_hist, closes, candle, params):
    """Momentum filter + mean reversion entry."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    # Longer-term momentum up (trend filter)
    mom_20 = (closes[-1] - closes[-period - 1]) / closes[-period - 1] if len(closes) > period + 1 else 0

    # Short-term pullback (reversion entry)
    pullback = (closes[-1] - closes[-5]) / closes[-5] if len(closes) > 5 else 0

    ma = compute_sma(closes, 10)
    if ma is None:
        return False
    std = compute_std(closes, 10)
    if std is None or std == 0:
        return False

    # Uptrend + dip = reversion entry
    if mom_20 > 0.02 and pullback < -0.02 and (closes[-1] - ma) / std < -1.0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _breakout_reversion_hybrid_entry(candles_hist, closes, candle, params):
    """False breakout reversion."""
    if len(closes) < 30:
        return False
    period = params.get("period", 20)

    # Recent high (potential breakout level)
    recent_high = max(closes[-period:-1])
    current = closes[-1]

    # False breakout: price went above recent high but fell back
    has_broken_out = any(c > recent_high for c in closes[-period:])
    false_breakout = has_broken_out and current < recent_high

    if false_breakout:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _gap_reversion_entry(candles_hist, closes, candle, params):
    """Gap fill reversion."""
    if len(closes) < 5:
        return False
    gap_thresh = params.get("gap_thresh", 2.0)

    # Gap: open far from previous close
    op = float(candle["open"])
    prev_cl = closes[-2] if len(closes) > 1 else op

    if prev_cl == 0:
        return False
    gap_pct = abs(op - prev_cl) / prev_cl * 100

    if gap_pct > gap_thresh:
        # Gap down, starting to fill
        if op < prev_cl and closes[-1] > op:
            return True
    return False


def _exhaustion_reversion_entry(candles_hist, closes, candle, params):
    """Exhaustion bar + mean reversion."""
    if len(closes) < 5:
        return False

    c = candle
    body = float(c["close"]) - float(c["open"])
    range_c = float(c["high"]) - float(c["low"])

    # Exhaustion: large range, small close position (indecision after move)
    if range_c == 0:
        return False
    close_position = abs(float(c["close"]) - float(c["low"])) / range_c

    # Large range bar after downtrend
    avg_range = sum(float(candles_hist[-i]["high"]) - float(candles_hist[-i]["low"]) for i in range(1, min(6, len(candles_hist)))) / min(5, len(candles_hist) - 1)
    large_range = range_c > avg_range * 1.5

    is_downtrend = len(closes) > 3 and closes[-1] < closes[-4]

    if large_range and is_downtrend and 0.3 < close_position < 0.7:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _capitulation_reversion_entry(candles_hist, closes, candle, params):
    """Capitulation volume + reversion."""
    if len(closes) < 25 or len(candles_hist) < 25:
        return False
    period = params.get("period", 20)
    vol_mult = params.get("vol_mult", 3.0)

    vols = [float(c["volume"]) for c in candles_hist]
    avg_vol = sum(vols[-period:-1]) / max(1, len(vols) - period - 1)
    if avg_vol == 0:
        return False

    # Capitulation: huge volume + large red candle
    current_vol = float(candle["volume"])
    body = float(candle["close"]) - float(candle["open"])

    if current_vol > avg_vol * vol_mult and body < 0:
        # Red candle with massive volume = capitulation
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _panic_reversion_entry(candles_hist, closes, candle, params):
    """Panic selling + mean reversion."""
    if len(closes) < 20:
        return False
    period = params.get("period", 10)
    drop_thresh = params.get("drop_thresh", 4.0)

    # Sharp drop over few bars
    drop = (closes[-1] - closes[-period]) / closes[-period] * 100 if len(closes) > period else 0

    if drop < -drop_thresh:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _snap_back_entry(candles_hist, closes, candle, params):
    """Extreme deviation + snap back entry."""
    if len(closes) < 25:
        return False
    period = params.get("period", 20)
    dev_mult = params.get("dev_mult", 3.0)

    ma = compute_sma(closes, period)
    std = compute_std(closes, period)
    if ma is None or std is None or std == 0:
        return False

    z = (closes[-1] - ma) / std
    if z < -dev_mult:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

MEAN_REVERSION_STRATEGIES = [
    # Statistical / Band-based
    {"name": "bb_reversion", "params": {"bb_period": 20, "rsi_period": 14, "rsi_thresh": 35, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "zscore_reversion", "params": {"zscore_thresh": -2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "std_dev_reversion", "params": {"period": 20, "num_std": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "mad_reversion", "params": {"threshold": -2.5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Oscillator-based
    {"name": "stochastic_reversion", "params": {"k_period": 14, "threshold": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "cci_reversion", "params": {"cci_period": 20, "threshold": -100, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "williams_r_reversion", "params": {"wr_period": 14, "threshold": -80, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "roc_reversion", "params": {"roc_period": 10, "threshold": -5.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Pairs / Cointegration
    {"name": "pairs_trading", "params": {"period": 20, "dev_mult": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "cointegration", "params": {"period": 30, "threshold": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # State-space / Process models
    {"name": "kalman_reversion", "params": {"dev_mult": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "ou_reversion", "params": {"lookback": 30, "sigma_mult": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Regime / Adaptive
    {"name": "hurst_reversion", "params": {"hurst_thresh": 0.45, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "adaptive_reversion", "params": {"base_period": 20, "dev_mult": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "dynamic_reversion", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "regime_reversion", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Distance / Relative
    {"name": "distance_reversion", "params": {"ma_period": 20, "std_period": 20, "dev_mult": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_sectional_reversion", "params": {"period": 10, "threshold": -3.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "sector_reversion", "params": {"period": 20, "threshold": -2.5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "factor_reversion", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Volume / Volatility confirmed
    {"name": "volume_reversion", "params": {"period": 20, "vol_period": 10, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "volatility_reversion", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Indicator divergence
    {"name": "rsi_divergence", "params": {"lookback": 10, "rsi_period": 14, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "macd_reversion", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Moving Average reversion
    {"name": "ema_reversion", "params": {"ema_period": 20, "dev_pct": 3.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "sma_reversion", "params": {"sma_period": 20, "dev_pct": 3.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # VWAP based
    {"name": "vwap_reversion", "params": {"vwap_period": 20, "dev_pct": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "anchored_vwap_reversion", "params": {"anchor_bars": 30, "dev_pct": 2.5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Percentile / Rank
    {"name": "percentile_channel", "params": {"period": 30, "pct_thresh": 10, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "rank_reversion", "params": {"period": 20, "rank_thresh": 3, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Beta / Correlation / Spread / Ratio
    {"name": "beta_reversion", "params": {"period": 20, "threshold": -2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "correlation_reversion", "params": {"period": 20, "threshold": -2.5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "spread_reversion", "params": {"period": 20, "spread_thresh": 3.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "ratio_reversion", "params": {"period": 20, "ratio_thresh": 0.95, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Regression / Residual
    {"name": "residual_reversion", "params": {"period": 20, "threshold": -2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Time / Volume weighted
    {"name": "time_decay_reversion", "params": {"period": 20, "dev_mult": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "weighted_reversion", "params": {"period": 20, "dev_mult": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Pattern / Seasonal / Cycle
    {"name": "pattern_reversion", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "seasonal_reversion", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "cycle_reversion", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Signal processing
    {"name": "wavelet_reversion", "params": {"threshold": -0.02, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "fourier_reversion", "params": {"period": 40, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Entropy
    {"name": "entropy_reversion", "params": {"period": 20, "entropy_thresh": 1.5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Hybrid approaches
    {"name": "momentum_reversion_hybrid", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_reversion_hybrid", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "gap_reversion", "params": {"gap_thresh": 2.0, "tp_pct": 5, "sl_pct": 3, "max_hold": 12}},
    {"name": "exhaustion_reversion", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "capitulation_reversion", "params": {"period": 20, "vol_mult": 3.0, "tp_pct": 8, "sl_pct": 4, "max_hold": 24}},
    {"name": "panic_reversion", "params": {"period": 10, "drop_thresh": 4.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "snap_back", "params": {"period": 20, "dev_mult": 3.0, "tp_pct": 8, "sl_pct": 4, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "bb_reversion": _bb_reversion_entry,
    "zscore_reversion": _zscore_reversion_entry,
    "stochastic_reversion": _stochastic_reversion_entry,
    "cci_reversion": _cci_reversion_entry,
    "williams_r_reversion": _williams_r_reversion_entry,
    "roc_reversion": _roc_reversion_entry,
    "pairs_trading": _pairs_trading_entry,
    "cointegration": _cointegration_entry,
    "kalman_reversion": _kalman_reversion_entry,
    "ou_reversion": _ou_reversion_entry,
    "hurst_reversion": _hurst_reversion_entry,
    "distance_reversion": _distance_reversion_entry,
    "cross_sectional_reversion": _cross_sectional_reversion_entry,
    "sector_reversion": _sector_reversion_entry,
    "factor_reversion": _factor_reversion_entry,
    "adaptive_reversion": _adaptive_reversion_entry,
    "dynamic_reversion": _dynamic_reversion_entry,
    "regime_reversion": _regime_reversion_entry,
    "volume_reversion": _volume_reversion_entry,
    "volatility_reversion": _volatility_reversion_entry,
    "rsi_divergence": _rsi_divergence_entry,
    "macd_reversion": _macd_reversion_entry,
    "ema_reversion": _ema_reversion_entry,
    "sma_reversion": _sma_reversion_entry,
    "vwap_reversion": _vwap_reversion_entry,
    "anchored_vwap_reversion": _anchored_vwap_reversion_entry,
    "std_dev_reversion": _std_dev_reversion_entry,
    "mad_reversion": _mad_reversion_entry,
    "percentile_channel": _percentile_channel_entry,
    "rank_reversion": _rank_reversion_entry,
    "beta_reversion": _beta_reversion_entry,
    "correlation_reversion": _correlation_reversion_entry,
    "spread_reversion": _spread_reversion_entry,
    "ratio_reversion": _ratio_reversion_entry,
    "residual_reversion": _residual_reversion_entry,
    "time_decay_reversion": _time_decay_reversion_entry,
    "weighted_reversion": _weighted_reversion_entry,
    "pattern_reversion": _pattern_reversion_entry,
    "seasonal_reversion": _seasonal_reversion_entry,
    "cycle_reversion": _cycle_reversion_entry,
    "wavelet_reversion": _wavelet_reversion_entry,
    "fourier_reversion": _fourier_reversion_entry,
    "entropy_reversion": _entropy_reversion_entry,
    "momentum_reversion_hybrid": _momentum_reversion_hybrid_entry,
    "breakout_reversion_hybrid": _breakout_reversion_hybrid_entry,
    "gap_reversion": _gap_reversion_entry,
    "exhaustion_reversion": _exhaustion_reversion_entry,
    "capitulation_reversion": _capitulation_reversion_entry,
    "panic_reversion": _panic_reversion_entry,
    "snap_back": _snap_back_entry,
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
    print(f"MEAN REVERSION 50 STRATEGY SWEEP")
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
    print(f"Testing {len(MEAN_REVERSION_STRATEGIES)} mean reversion strategies...\n")

    results = []
    total_tests = len(all_candles) * len(MEAN_REVERSION_STRATEGIES)
    test_count = 0

    for strat_def in MEAN_REVERSION_STRATEGIES:
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

    out_path = Path(__file__).parent.parent / "reports" / "mean_reversion_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 MEAN REVERSION STRATEGIES:")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:>2}. {r['strategy']:<30} ${r['total_net_pnl']:>8.2f}  "
              f"Hit: {r['hit_rate']:>5.1f}%  "
              f"Profitable: {r['profitable_coins']}/{r['coins_tested']}")

    if output["promoted_for_30d"]:
        print(f"\nPROMOTED FOR 30D VALIDATION:")
        for s in output["promoted_for_30d"]:
            print(f"  ✅ {s}")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
