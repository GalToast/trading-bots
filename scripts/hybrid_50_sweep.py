#!/usr/bin/env python3
"""
Hybrid 50 Strategy Sweep — Batch #5 of the 500 Strategies Initiative.

Tests 50 unique HYBRID/ENSEMBLE strategies across 35 coins on 7d data
for fast edge discovery. Each strategy requires MULTIPLE signal types
to align simultaneously (e.g., RSI AND volume, MA AND ATR, etc.).

Top candidates get promoted for 30d validation.

Strategy categories:
- Signal x Volume (1-10): Primary signal + volume confirmation
- Signal x Volatility (11-20): Primary signal + volatility regime
- Signal x Signal (21-27): Two different signal types combined
- Multi-signal (28-32): 3+ indicators, multi-timeframe, multi-regime
- Multi-cycle (33-34): Cycle alignment strategies
- Ensemble (34-39): Simple/weighted/adaptive/dynamic/ML/deep ensembles
- Hybrid ML-style (40-50): ML-proxy strategies (tree, forest, boosting, etc.)

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
from strategy_library import backtest, compute_rsi, compute_ema, compute_bb, compute_atr

# ==========================================
# HYBRID HELPER FUNCTIONS
# ==========================================

def compute_rsi_series(candles, period=14):
    """RSI value for the full candle history."""
    closes = [float(c["close"]) for c in candles]
    return compute_rsi(closes, period)


def compute_sma(candles, period=20):
    """Simple Moving Average."""
    if len(candles) < period:
        return None
    closes = [float(c["close"]) for c in candles[-period:]]
    return sum(closes) / period


def compute_volume_avg(candles, period=20):
    """Average volume over period."""
    if len(candles) < period:
        return None
    vols = [float(c["volume"]) for c in candles[-period:]]
    return sum(vols) / period


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


def compute_atr_pct(candles, period=14):
    """ATR as percentage of price."""
    atr = compute_atr(candles, period)
    if atr is None or len(candles) < 1:
        return None
    price = float(candles[-1]["close"])
    if price == 0:
        return None
    return atr / price * 100


def compute_volatility_regime(candles, period=20):
    """Classify volatility regime: 'low', 'normal', 'high'."""
    atr_pct = compute_atr_pct(candles, period)
    if atr_pct is None:
        return "normal"
    if atr_pct < 1.0:
        return "low"
    elif atr_pct < 3.0:
        return "normal"
    return "high"


def compute_momentum(candles, period=10):
    """Price momentum over period (percent change)."""
    if len(candles) < period + 1:
        return 0.0
    closes = [float(c["close"]) for c in candles]
    return (closes[-1] / closes[-period - 1] - 1) * 100


def compute_mean_reversion_z(candles, period=20):
    """Z-score of current price vs SMA."""
    if len(candles) < period:
        return 0.0
    closes = [float(c["close"]) for c in candles]
    sma = sum(closes[-period:]) / period
    std = math.sqrt(sum((x - sma) ** 2 for x in closes[-period:]) / period)
    if std == 0:
        return 0.0
    return (closes[-1] - sma) / std


def compute_trend_strength(candles, period=20):
    """Trend strength via EMA slope."""
    if len(candles) < period + 5:
        return 0.0
    closes = [float(c["close"]) for c in candles]
    ema_now = compute_ema(closes, period)
    ema_prev = compute_ema(closes[:-5], period)
    if ema_now is None or ema_prev is None:
        return 0.0
    return (ema_now - ema_prev) / ema_prev * 100


def compute_candle_pattern(candles):
    """Detect bullish candle patterns: returns 1 if bullish, -1 if bearish, 0 otherwise."""
    if len(candles) < 2:
        return 0
    c = candles[-1]
    o, cl, h, l = float(c["open"]), float(c["close"]), float(c["high"]), float(c["low"])
    body = cl - o
    rng = h - l
    if rng == 0:
        return 0
    # Hammer / bullish engulfing
    prev = candles[-2]
    prev_body = float(prev["close"]) - float(prev["open"])
    if body > 0 and abs(body) / rng > 0.6:
        # Strong bullish candle
        if body > abs(prev_body):
            return 1
    return 0


def compute_time_signal(candles, candle):
    """Time-based signal: returns 1 during high-activity hours."""
    ts = int(candle.get("start", candle.get("time", 0)))
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    # US/EU overlap hours: 13-17 UTC
    if 13 <= hour <= 17:
        return 1
    # Asian hours: 0-4 UTC
    if 0 <= hour <= 4:
        return -1
    return 0


def compute_statistical_signal(candles, period=20):
    """Statistical mean-reversion signal (price vs 2 std bands)."""
    if len(candles) < period:
        return False
    closes = [float(c["close"]) for c in candles[-period:]]
    sma = sum(closes) / period
    std = math.sqrt(sum((x - sma) ** 2 for x in closes) / period)
    current = float(candles[-1]["close"])
    # Price below lower band = statistical oversold
    return current < (sma - 2 * std)


def compute_cross_asset_proxy(candles, period=20):
    """Cross-asset proxy: use BTC-like momentum from the coin itself.
    Simplified: if the coin has strong momentum, treat as risk-on."""
    mom = compute_momentum(candles, period)
    return mom


def compute_breakout_level(candles, period=20):
    """Detect if price is breaking above recent high."""
    if len(candles) < period + 1:
        return False
    highs = [float(c["high"]) for c in candles[-period:-1]]
    if not highs:
        return False
    recent_high = max(highs)
    current = float(candles[-1]["close"])
    return current > recent_high


def compute_volatility_compression(candles, period=10):
    """Detect volatility compression (ATR declining)."""
    if len(candles) < period * 2 + 1:
        return False
    atr_recent = compute_atr(candles[-period:], period)
    atr_prev = compute_atr(candles[-period * 2:-period], period)
    if atr_recent is None or atr_prev is None:
        return False
    return atr_recent < atr_prev * 0.8


def compute_volatility_expansion(candles, period=10):
    """Detect volatility expansion (ATR increasing)."""
    if len(candles) < period * 2 + 1:
        return False
    atr_recent = compute_atr(candles[-period:], period)
    atr_prev = compute_atr(candles[-period * 2:-period], period)
    if atr_recent is None or atr_prev is None:
        return False
    return atr_recent > atr_prev * 1.3


def compute_ma_crossover(candles, fast=5, slow=20):
    """MA crossover: fast > slow = bullish."""
    if len(candles) < slow:
        return False
    closes = [float(c["close"]) for c in candles]
    fast_ema = compute_ema(closes, fast)
    slow_ema = compute_ema(closes, slow)
    if fast_ema is None or slow_ema is None:
        return False
    return fast_ema > slow_ema


def compute_volume_decline(candles, period=10):
    """Volume drying up (declining over period)."""
    if len(candles) < period * 2:
        return False
    vols = [float(c["volume"]) for c in candles]
    recent_avg = sum(vols[-period:]) / period
    prev_avg = sum(vols[-period * 2:-period]) / period
    if prev_avg == 0:
        return False
    return recent_avg < prev_avg * 0.7


def compute_volume_surge(candles, period=10):
    """Volume surge above average."""
    if len(candles) < period * 2:
        return False
    vols = [float(c["volume"]) for c in candles]
    current_vol = vols[-1]
    avg_vol = sum(vols[-period * 2:-period]) / (period * 2)
    if avg_vol == 0:
        return False
    return current_vol > avg_vol * 1.5


# ==========================================
# HYBRID STRATEGY ENTRY FUNCTIONS
# ==========================================

# --- Signal x Volume (1-10) ---

def _rsi_volume_entry(candles_hist, closes, candle, params):
    """RSI oversold AND volume spike confirmation."""
    if len(candles_hist) < 30:
        return False
    rsi_period = params.get("rsi_period", 14)
    os_thresh = params.get("os_thresh", 30)

    rsi = compute_rsi(closes, rsi_period)
    if rsi is None or rsi > os_thresh:
        return False
    # Volume confirmation
    vol = float(candle["volume"])
    avg_vol = compute_volume_avg(candles_hist, 20)
    if avg_vol is None:
        return False
    if vol > avg_vol * 1.3 and closes[-1] > closes[-2]:
        return True
    return False


def _ma_atr_entry(candles_hist, closes, candle, params):
    """MA crossover AND ATR expansion confirmation."""
    if len(candles_hist) < 30:
        return False
    if not compute_ma_crossover(candles_hist):
        return False
    atr_pct = compute_atr_pct(candles_hist, 14)
    if atr_pct is None or atr_pct < 1.5:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_volume_entry(candles_hist, closes, candle, params):
    """Price breakout AND volume above average."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    if not compute_breakout_level(candles_hist, period):
        return False
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is None or vol_ratio < 1.2:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _momentum_volatility_entry(candles_hist, closes, candle, params):
    """Momentum signal AND volatility regime filter."""
    if len(candles_hist) < 30:
        return False
    mom = compute_momentum(candles_hist, 10)
    if mom < 2.0:
        return False
    regime = compute_volatility_regime(candles_hist, 20)
    if regime not in ("normal", "high"):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _mean_reversion_volume_entry(candles_hist, closes, candle, params):
    """Mean reversion AND volume decline (drying up)."""
    if len(candles_hist) < 30:
        return False
    z = compute_mean_reversion_z(candles_hist, 20)
    if z > -1.5:
        return False
    if not compute_volume_decline(candles_hist, 10):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _trend_volume_entry(candles_hist, closes, candle, params):
    """Trend confirmation AND volume trend alignment."""
    if len(candles_hist) < 30:
        return False
    trend = compute_trend_strength(candles_hist, 20)
    if trend < 0.3:
        return False
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is None or vol_ratio < 1.0:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _pattern_volume_entry(candles_hist, closes, candle, params):
    """Candle pattern AND volume confirmation."""
    if len(candles_hist) < 20:
        return False
    pattern = compute_candle_pattern(candles_hist)
    if pattern != 1:
        return False
    vol = float(candle["volume"])
    avg_vol = compute_volume_avg(candles_hist, 20)
    if avg_vol is None or vol < avg_vol * 1.2:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _statistical_volume_entry(candles_hist, closes, candle, params):
    """Statistical signal AND volume alignment."""
    if len(candles_hist) < 30:
        return False
    if not compute_statistical_signal(candles_hist, 20):
        return False
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is None or vol_ratio < 0.8:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _time_volume_entry(candles_hist, closes, candle, params):
    """Time-based signal AND volume pattern match."""
    if len(candles_hist) < 20:
        return False
    ts = compute_time_signal(candles_hist, candle)
    if ts != 1:
        return False
    vol = float(candle["volume"])
    avg_vol = compute_volume_avg(candles_hist, 20)
    if avg_vol is None or vol < avg_vol * 1.1:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _cross_asset_volume_entry(candles_hist, closes, candle, params):
    """Cross-asset proxy AND volume confirmation."""
    if len(candles_hist) < 30:
        return False
    proxy = compute_cross_asset_proxy(candles_hist, 10)
    if proxy < 1.0:
        return False
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is None or vol_ratio < 1.1:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


# --- Signal x Volatility (11-20) ---

def _rsi_volatility_entry(candles_hist, closes, candle, params):
    """RSI signal AND volatility regime filter."""
    if len(candles_hist) < 30:
        return False
    rsi = compute_rsi(closes, 14)
    if rsi is None or rsi > 35:
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    if regime != "high":
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _ma_volatility_entry(candles_hist, closes, candle, params):
    """MA crossover AND volatility compression."""
    if len(candles_hist) < 30:
        return False
    if not compute_ma_crossover(candles_hist):
        return False
    if not compute_volatility_compression(candles_hist, 10):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_volatility_entry(candles_hist, closes, candle, params):
    """Breakout AND volatility expansion."""
    if len(candles_hist) < 30:
        return False
    if not compute_breakout_level(candles_hist, 20):
        return False
    if not compute_volatility_expansion(candles_hist, 10):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _momentum_volume_entry(candles_hist, closes, candle, params):
    """Momentum AND volume surge."""
    if len(candles_hist) < 30:
        return False
    mom = compute_momentum(candles_hist, 5)
    if mom < 1.0:
        return False
    if not compute_volume_surge(candles_hist, 10):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _mean_reversion_volatility_entry(candles_hist, closes, candle, params):
    """Mean reversion AND high volatility."""
    if len(candles_hist) < 30:
        return False
    z = compute_mean_reversion_z(candles_hist, 20)
    if z > -1.0:
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    if regime != "high":
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _trend_volatility_entry(candles_hist, closes, candle, params):
    """Trend AND volatility trend alignment."""
    if len(candles_hist) < 30:
        return False
    trend = compute_trend_strength(candles_hist, 20)
    if trend < 0.5:
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    if regime == "low":
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _pattern_volatility_entry(candles_hist, closes, candle, params):
    """Candle pattern AND volatility regime."""
    if len(candles_hist) < 20:
        return False
    pattern = compute_candle_pattern(candles_hist)
    if pattern != 1:
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    if regime not in ("normal", "high"):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _statistical_volatility_entry(candles_hist, closes, candle, params):
    """Statistical signal AND volatility filter."""
    if len(candles_hist) < 30:
        return False
    if not compute_statistical_signal(candles_hist, 20):
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    if regime != "high":
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _time_volatility_entry(candles_hist, closes, candle, params):
    """Time-based AND volatility pattern."""
    if len(candles_hist) < 20:
        return False
    ts = compute_time_signal(candles_hist, candle)
    if ts != 1:
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    if regime == "low":
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _cross_asset_volatility_entry(candles_hist, closes, candle, params):
    """Cross-asset proxy AND volatility alignment."""
    if len(candles_hist) < 30:
        return False
    proxy = compute_cross_asset_proxy(candles_hist, 10)
    if proxy < 2.0:
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    if regime == "low":
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


# --- Signal x Signal (21-27) ---

def _rsi_momentum_entry(candles_hist, closes, candle, params):
    """RSI oversold AND momentum confirmation."""
    if len(candles_hist) < 30:
        return False
    rsi = compute_rsi(closes, 14)
    if rsi is None or rsi > 35:
        return False
    mom = compute_momentum(candles_hist, 5)
    if mom < 0.5:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _ma_breakout_entry(candles_hist, closes, candle, params):
    """MA support AND breakout confirmation."""
    if len(candles_hist) < 30:
        return False
    if not compute_ma_crossover(candles_hist):
        return False
    if not compute_breakout_level(candles_hist, 20):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_momentum_entry(candles_hist, closes, candle, params):
    """Breakout AND momentum alignment."""
    if len(candles_hist) < 30:
        return False
    if not compute_breakout_level(candles_hist, 20):
        return False
    mom = compute_momentum(candles_hist, 5)
    if mom < 0.5:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _momentum_mean_reversion_entry(candles_hist, closes, candle, params):
    """Momentum AND mean reversion (counter-trend entry)."""
    if len(candles_hist) < 30:
        return False
    mom = compute_momentum(candles_hist, 20)
    if mom < 2.0:
        return False
    z = compute_mean_reversion_z(candles_hist, 20)
    if z > -0.5:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _trend_pattern_entry(candles_hist, closes, candle, params):
    """Trend AND candle pattern alignment."""
    if len(candles_hist) < 30:
        return False
    trend = compute_trend_strength(candles_hist, 20)
    if trend < 0.3:
        return False
    pattern = compute_candle_pattern(candles_hist)
    if pattern != 1:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _statistical_time_entry(candles_hist, closes, candle, params):
    """Statistical signal AND time filter."""
    if len(candles_hist) < 30:
        return False
    if not compute_statistical_signal(candles_hist, 20):
        return False
    ts = compute_time_signal(candles_hist, candle)
    if ts != 1:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _cross_asset_momentum_entry(candles_hist, closes, candle, params):
    """Cross-asset proxy AND momentum."""
    if len(candles_hist) < 30:
        return False
    proxy = compute_cross_asset_proxy(candles_hist, 10)
    if proxy < 1.0:
        return False
    mom = compute_momentum(candles_hist, 5)
    if mom < 0.5:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


# --- Multi-signal (28-32) ---

def _multi_indicator_entry(candles_hist, closes, candle, params):
    """3+ indicators aligned (RSI + volume + trend)."""
    if len(candles_hist) < 30:
        return False
    rsi = compute_rsi(closes, 14)
    if rsi is None or rsi > 40:
        return False
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is None or vol_ratio < 1.1:
        return False
    trend = compute_trend_strength(candles_hist, 20)
    if trend < 0.2:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _multi_timeframe_entry(candles_hist, closes, candle, params):
    """Signal confirmed on multiple timeframes (fast + slow MA)."""
    if len(candles_hist) < 50:
        return False
    fast_ma = compute_ma_crossover(candles_hist, 5, 10)
    slow_ma = compute_ma_crossover(candles_hist, 10, 20)
    if not (fast_ma and slow_ma):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _multi_asset_entry(candles_hist, closes, candle, params):
    """Multi-asset alignment (simplified proxy)."""
    if len(candles_hist) < 30:
        return False
    proxy = compute_cross_asset_proxy(candles_hist, 10)
    if proxy < 1.5:
        return False
    rsi = compute_rsi(closes, 14)
    if rsi is None or rsi > 45:
        return False
    mom = compute_momentum(candles_hist, 5)
    if mom < 0.5:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _multi_factor_entry(candles_hist, closes, candle, params):
    """Multiple factors aligned (momentum + value + quality proxies)."""
    if len(candles_hist) < 30:
        return False
    mom = compute_momentum(candles_hist, 10)
    if mom < 1.0:
        return False
    z = compute_mean_reversion_z(candles_hist, 20)
    if z > -0.5:
        return False
    trend = compute_trend_strength(candles_hist, 20)
    if trend < 0.2:
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _multi_regime_entry(candles_hist, closes, candle, params):
    """Strategy adapts to detected regime."""
    if len(candles_hist) < 30:
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    if regime == "high":
        rsi = compute_rsi(closes, 14)
        if rsi is None or rsi > 25:
            return False
    elif regime == "normal":
        if not compute_ma_crossover(candles_hist):
            return False
    else:
        mom = compute_momentum(candles_hist, 10)
        if mom < 1.0:
            return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _multi_cycle_entry(candles_hist, closes, candle, params):
    """Multiple cycle alignments."""
    if len(candles_hist) < 50:
        return False
    short_mom = compute_momentum(candles_hist, 5)
    med_mom = compute_momentum(candles_hist, 10)
    long_mom = compute_momentum(candles_hist, 20)
    if not (short_mom > 0.5 and med_mom > 0.3 and long_mom > 0.1):
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


# --- Ensemble (34-39) ---

def _ensemble_simple_entry(candles_hist, closes, candle, params):
    """Simple average of 3 signals."""
    if len(candles_hist) < 30:
        return False
    signals = 0
    rsi = compute_rsi(closes, 14)
    if rsi is not None and rsi < 40:
        signals += 1
    if compute_ma_crossover(candles_hist):
        signals += 1
    mom = compute_momentum(candles_hist, 10)
    if mom > 0.5:
        signals += 1
    # At least 2 of 3 must agree
    if signals >= 2 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _ensemble_weighted_entry(candles_hist, closes, candle, params):
    """Weighted average of 3 signals (weight by recency)."""
    if len(candles_hist) < 30:
        return False
    score = 0.0
    rsi = compute_rsi(closes, 14)
    if rsi is not None and rsi < 40:
        score += 0.4
    if compute_ma_crossover(candles_hist):
        score += 0.35
    mom = compute_momentum(candles_hist, 10)
    if mom > 0.5:
        score += 0.25
    if score >= 0.6 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _ensemble_adaptive_entry(candles_hist, closes, candle, params):
    """Adaptive weighting based on regime."""
    if len(candles_hist) < 30:
        return False
    regime = compute_volatility_regime(candles_hist, 14)
    score = 0.0
    if regime == "high":
        rsi = compute_rsi(closes, 14)
        if rsi is not None and rsi < 35:
            score += 0.5
    elif regime == "normal":
        if compute_ma_crossover(candles_hist):
            score += 0.5
    else:
        mom = compute_momentum(candles_hist, 10)
        if mom > 1.0:
            score += 0.5
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is not None and vol_ratio > 1.1:
        score += 0.3
    if score >= 0.5 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _ensemble_dynamic_entry(candles_hist, closes, candle, params):
    """Dynamic weight adjustment based on recent signal performance."""
    if len(candles_hist) < 40:
        return False
    score = 0.0
    rsi = compute_rsi(closes, 14)
    if rsi is not None and rsi < 40:
        score += 0.3
    if compute_ma_crossover(candles_hist):
        score += 0.3
    if compute_breakout_level(candles_hist, 20):
        score += 0.2
    vol_surge = compute_volume_surge(candles_hist, 10)
    if vol_surge:
        score += 0.2
    if score >= 0.5 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _ensemble_ml_entry(candles_hist, closes, candle, params):
    """ML-style ensemble (decision tree proxy)."""
    if len(candles_hist) < 40:
        return False
    # Decision tree proxy: nested conditions
    rsi = compute_rsi(closes, 14)
    if rsi is None:
        return False
    if rsi < 30:
        # Deep oversold branch
        vol_ratio = compute_volume_ratio(candles_hist)
        if vol_ratio is not None and vol_ratio > 1.2:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    elif rsi < 45:
        # Moderate oversold branch
        if compute_ma_crossover(candles_hist):
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _ensemble_deep_entry(candles_hist, closes, candle, params):
    """Deep ensemble (multiple layers of filtering)."""
    if len(candles_hist) < 50:
        return False
    # Layer 1: RSI filter
    rsi = compute_rsi(closes, 14)
    if rsi is None or rsi > 45:
        return False
    # Layer 2: Trend filter
    trend = compute_trend_strength(candles_hist, 20)
    if trend < 0.1:
        return False
    # Layer 3: Volume filter
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is None or vol_ratio < 1.0:
        return False
    # Layer 4: Volatility filter
    regime = compute_volatility_regime(candles_hist, 14)
    if regime == "low":
        return False
    if len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


# --- Hybrid ML-style (40-50) ---

def _hybrid_ml_entry(candles_hist, closes, candle, params):
    """Hybrid ML signal (simplified logistic regression proxy)."""
    if len(candles_hist) < 40:
        return False
    # Features as linear combination
    rsi = compute_rsi(closes, 14) or 50
    mom = compute_momentum(candles_hist, 10)
    trend = compute_trend_strength(candles_hist, 20)
    z = compute_mean_reversion_z(candles_hist, 20)
    vol_ratio = compute_volume_ratio(candles_hist) or 1.0

    # Weighted features (simplified weights)
    score = (
        (50 - rsi) * 0.02 +
        mom * 0.3 +
        trend * 0.2 +
        (-z) * 0.15 +
        (vol_ratio - 1.0) * 0.15
    )
    if score > 0.5 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_deep_entry(candles_hist, closes, candle, params):
    """Deep hybrid signal (multi-layer feature extraction)."""
    if len(candles_hist) < 50:
        return False
    # Layer 1: Technical features
    rsi = compute_rsi(closes, 14) or 50
    mom = compute_momentum(candles_hist, 10)
    # Layer 2: Statistical features
    z = compute_mean_reversion_z(candles_hist, 20)
    # Layer 3: Volume features
    vol_ratio = compute_volume_ratio(candles_hist) or 1.0
    # Layer 4: Regime features
    regime = compute_volatility_regime(candles_hist, 14)
    regime_score = 1.0 if regime == "high" else 0.5 if regime == "normal" else 0.0

    # Deep combination
    technical = (50 - rsi) * 0.01 + mom * 0.2
    statistical = (-z) * 0.1
    volume = (vol_ratio - 1.0) * 0.2
    total = technical + statistical + volume + regime_score * 0.3

    if total > 0.4 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_reinforcement_entry(candles_hist, closes, candle, params):
    """Reinforcement learning proxy (reward-based signal)."""
    if len(candles_hist) < 40:
        return False
    # Reward function: reward signals that would have been profitable
    reward = 0.0
    rsi = compute_rsi(closes, 14) or 50
    if rsi < 40:
        reward += 1.0
    if compute_ma_crossover(candles_hist):
        reward += 0.5
    mom = compute_momentum(candles_hist, 10)
    if mom > 0.5:
        reward += 0.5
    if compute_breakout_level(candles_hist, 20):
        reward += 0.5
    vol_surge = compute_volume_surge(candles_hist, 10)
    if vol_surge:
        reward += 0.5

    # Action: enter if reward exceeds threshold
    if reward >= 2.0 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_genetic_entry(candles_hist, closes, candle, params):
    """Genetic algorithm proxy (evolutionary selection of signals)."""
    if len(candles_hist) < 40:
        return False
    # Population of signal combinations
    fitness = 0
    # Individual 1: RSI only
    rsi = compute_rsi(closes, 14) or 50
    if rsi < 40:
        fitness += 1
    # Individual 2: MA crossover only
    if compute_ma_crossover(candles_hist):
        fitness += 1
    # Individual 3: Momentum only
    mom = compute_momentum(candles_hist, 10)
    if mom > 0.5:
        fitness += 1
    # Individual 4: Volume only
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is not None and vol_ratio > 1.2:
        fitness += 1

    # Selection: survive if fitness >= 2
    if fitness >= 2 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_bayesian_entry(candles_hist, closes, candle, params):
    """Bayesian updating signal (prior -> posterior)."""
    if len(candles_hist) < 40:
        return False
    # Prior probability (base rate)
    prior = 0.5
    # Update with evidence
    rsi = compute_rsi(closes, 14) or 50
    if rsi < 40:
        prior *= 1.5  # Likelihood ratio
    if compute_ma_crossover(candles_hist):
        prior *= 1.3
    mom = compute_momentum(candles_hist, 10)
    if mom > 0.5:
        prior *= 1.2
    vol_ratio = compute_volume_ratio(candles_hist)
    if vol_ratio is not None and vol_ratio > 1.1:
        prior *= 1.2

    # Posterior threshold
    if prior > 1.5 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_fuzzy_entry(candles_hist, closes, candle, params):
    """Fuzzy logic signal (membership functions)."""
    if len(candles_hist) < 40:
        return False
    # Fuzzy membership: RSI oversold
    rsi = compute_rsi(closes, 14) or 50
    if rsi < 30:
        rsi_membership = 1.0
    elif rsi < 40:
        rsi_membership = (40 - rsi) / 10
    else:
        rsi_membership = 0.0

    # Fuzzy membership: Volume surge
    vol_ratio = compute_volume_ratio(candles_hist) or 1.0
    if vol_ratio > 1.5:
        vol_membership = 1.0
    elif vol_ratio > 1.0:
        vol_membership = (vol_ratio - 1.0) / 0.5
    else:
        vol_membership = 0.0

    # Fuzzy membership: Momentum
    mom = compute_momentum(candles_hist, 10)
    if mom > 2.0:
        mom_membership = 1.0
    elif mom > 0:
        mom_membership = mom / 2.0
    else:
        mom_membership = 0.0

    # Defuzzify: weighted average
    combined = (rsi_membership * 0.4 + vol_membership * 0.3 + mom_membership * 0.3)
    if combined > 0.5 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_neuro_entry(candles_hist, closes, candle, params):
    """Neural network proxy (simplified feed-forward)."""
    if len(candles_hist) < 40:
        return False
    # Input layer
    rsi = (compute_rsi(closes, 14) or 50) / 100.0
    mom = compute_momentum(candles_hist, 10) / 10.0
    trend = compute_trend_strength(candles_hist, 20) / 10.0
    z = compute_mean_reversion_z(candles_hist, 20) / 3.0
    vol = (compute_volume_ratio(candles_hist) or 1.0) / 2.0

    # Hidden layer (simplified ReLU)
    h1 = max(0, rsi * -1.5 + mom * 0.8 + 0.3)
    h2 = max(0, trend * 1.2 + z * -0.8 + vol * 0.5)
    h3 = max(0, mom * 0.5 + vol * 0.7 - 0.2)

    # Output layer
    output = h1 * 0.4 + h2 * 0.35 + h3 * 0.25
    if output > 0.15 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_svm_entry(candles_hist, closes, candle, params):
    """SVM classification proxy (hyperplane separation)."""
    if len(candles_hist) < 40:
        return False
    # Feature vector
    rsi = (compute_rsi(closes, 14) or 50) / 100.0
    mom = compute_momentum(candles_hist, 10) / 10.0
    vol = (compute_volume_ratio(candles_hist) or 1.0) / 2.0

    # SVM decision boundary (simplified linear kernel)
    # w . x + b > 0 => class +1 (buy)
    decision = -2.0 * rsi + 3.0 * mom + 1.5 * vol - 0.3
    if decision > 0 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_tree_entry(candles_hist, closes, candle, params):
    """Decision tree signal (nested if-else)."""
    if len(candles_hist) < 30:
        return False
    rsi = compute_rsi(closes, 14) or 50
    if rsi < 35:
        vol_ratio = compute_volume_ratio(candles_hist) or 1.0
        if vol_ratio > 1.2:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    elif rsi < 50:
        if compute_ma_crossover(candles_hist):
            mom = compute_momentum(candles_hist, 10)
            if mom > 0.5:
                if len(closes) > 1 and closes[-1] > closes[-2]:
                    return True
    return False


def _hybrid_forest_entry(candles_hist, closes, candle, params):
    """Random forest proxy (average of multiple tree votes)."""
    if len(candles_hist) < 40:
        return False
    votes = 0
    n_trees = 5

    # Tree 1: RSI-based
    rsi = compute_rsi(closes, 14) or 50
    if rsi < 40:
        votes += 1

    # Tree 2: MA-based
    if compute_ma_crossover(candles_hist):
        votes += 1

    # Tree 3: Momentum-based
    mom = compute_momentum(candles_hist, 10)
    if mom > 0.5:
        votes += 1

    # Tree 4: Volume-based
    vol_ratio = compute_volume_ratio(candles_hist) or 1.0
    if vol_ratio > 1.2:
        votes += 1

    # Tree 5: Breakout-based
    if compute_breakout_level(candles_hist, 20):
        votes += 1

    # Majority vote
    if votes >= 3 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _hybrid_boost_entry(candles_hist, closes, candle, params):
    """Gradient boosting proxy (sequential residual correction)."""
    if len(candles_hist) < 40:
        return False
    # Stage 1: Base predictor (RSI)
    rsi = compute_rsi(closes, 14) or 50
    residual = (50 - rsi) / 50.0

    # Stage 2: Correct with momentum
    mom = compute_momentum(candles_hist, 10) / 10.0
    residual += mom * 0.3

    # Stage 3: Correct with volume
    vol_ratio = compute_volume_ratio(candles_hist) or 1.0
    residual += (vol_ratio - 1.0) * 0.2

    # Stage 4: Correct with trend
    trend = compute_trend_strength(candles_hist, 20) / 10.0
    residual += trend * 0.2

    # Stage 5: Final correction with volatility
    regime = compute_volatility_regime(candles_hist, 14)
    if regime == "high":
        residual += 0.1
    elif regime == "low":
        residual -= 0.1

    if residual > 0.3 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

HYBRID_STRATEGIES = [
    # Signal x Volume (1-10)
    {"name": "rsi_volume", "params": {"rsi_period": 14, "os_thresh": 30, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ma_atr", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "breakout_volume", "params": {"period": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "momentum_volatility", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "mean_reversion_volume", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "trend_volume", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "pattern_volume", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "statistical_volume", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "time_volume", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "cross_asset_volume", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Signal x Volatility (11-20)
    {"name": "rsi_volatility", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "ma_volatility", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_volatility", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "momentum_volume", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "mean_reversion_volatility", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "trend_volatility", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "pattern_volatility", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "statistical_volatility", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "time_volatility", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "cross_asset_volatility", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Signal x Signal (21-27)
    {"name": "rsi_momentum", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ma_breakout", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "breakout_momentum", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "momentum_mean_reversion", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "trend_pattern", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "statistical_time", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_asset_momentum", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Multi-signal (28-32)
    {"name": "multi_indicator", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "multi_timeframe", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "multi_asset", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "multi_factor", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "multi_regime", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "multi_cycle", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Ensemble (34-39)
    {"name": "ensemble_simple", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ensemble_weighted", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ensemble_adaptive", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ensemble_dynamic", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ensemble_ml", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "ensemble_deep", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},

    # Hybrid ML-style (40-50)
    {"name": "hybrid_ml", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_deep", "params": {"tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "hybrid_reinforcement", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_genetic", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_bayesian", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_fuzzy", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_neuro", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_svm", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_tree", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_forest", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "hybrid_boost", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "rsi_volume": _rsi_volume_entry,
    "ma_atr": _ma_atr_entry,
    "breakout_volume": _breakout_volume_entry,
    "momentum_volatility": _momentum_volatility_entry,
    "mean_reversion_volume": _mean_reversion_volume_entry,
    "trend_volume": _trend_volume_entry,
    "pattern_volume": _pattern_volume_entry,
    "statistical_volume": _statistical_volume_entry,
    "time_volume": _time_volume_entry,
    "cross_asset_volume": _cross_asset_volume_entry,
    "rsi_volatility": _rsi_volatility_entry,
    "ma_volatility": _ma_volatility_entry,
    "breakout_volatility": _breakout_volatility_entry,
    "momentum_volume": _momentum_volume_entry,
    "mean_reversion_volatility": _mean_reversion_volatility_entry,
    "trend_volatility": _trend_volatility_entry,
    "pattern_volatility": _pattern_volatility_entry,
    "statistical_volatility": _statistical_volatility_entry,
    "time_volatility": _time_volatility_entry,
    "cross_asset_volatility": _cross_asset_volatility_entry,
    "rsi_momentum": _rsi_momentum_entry,
    "ma_breakout": _ma_breakout_entry,
    "breakout_momentum": _breakout_momentum_entry,
    "momentum_mean_reversion": _momentum_mean_reversion_entry,
    "trend_pattern": _trend_pattern_entry,
    "statistical_time": _statistical_time_entry,
    "cross_asset_momentum": _cross_asset_momentum_entry,
    "multi_indicator": _multi_indicator_entry,
    "multi_timeframe": _multi_timeframe_entry,
    "multi_asset": _multi_asset_entry,
    "multi_factor": _multi_factor_entry,
    "multi_regime": _multi_regime_entry,
    "multi_cycle": _multi_cycle_entry,
    "ensemble_simple": _ensemble_simple_entry,
    "ensemble_weighted": _ensemble_weighted_entry,
    "ensemble_adaptive": _ensemble_adaptive_entry,
    "ensemble_dynamic": _ensemble_dynamic_entry,
    "ensemble_ml": _ensemble_ml_entry,
    "ensemble_deep": _ensemble_deep_entry,
    "hybrid_ml": _hybrid_ml_entry,
    "hybrid_deep": _hybrid_deep_entry,
    "hybrid_reinforcement": _hybrid_reinforcement_entry,
    "hybrid_genetic": _hybrid_genetic_entry,
    "hybrid_bayesian": _hybrid_bayesian_entry,
    "hybrid_fuzzy": _hybrid_fuzzy_entry,
    "hybrid_neuro": _hybrid_neuro_entry,
    "hybrid_svm": _hybrid_svm_entry,
    "hybrid_tree": _hybrid_tree_entry,
    "hybrid_forest": _hybrid_forest_entry,
    "hybrid_boost": _hybrid_boost_entry,
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
    print(f"HYBRID 50 STRATEGY SWEEP — Batch #5")
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
    print(f"Testing {len(HYBRID_STRATEGIES)} hybrid strategies...\n")

    results = []
    total_tests = len(all_candles) * len(HYBRID_STRATEGIES)
    test_count = 0

    for strat_def in HYBRID_STRATEGIES:
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

    out_path = Path(__file__).parent.parent / "reports" / "hybrid_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 HYBRID STRATEGIES:")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:>2}. {r['strategy']:<25} ${r['total_net_pnl']:>8.2f}  "
              f"Hit: {r['hit_rate']:>5.1f}%  "
              f"Profitable: {r['profitable_coins']}/{r['coins_tested']}")

    if output["promoted_for_30d"]:
        print(f"\nPROMOTED FOR 30D VALIDATION:")
        for s in output["promoted_for_30d"]:
            print(f"  + {s}")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
