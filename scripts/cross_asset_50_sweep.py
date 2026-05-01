#!/usr/bin/env python3
"""
Cross-Asset 50 Strategy Sweep — Tests 50 unique cross-asset strategies across 35 coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants use the coin's own price/volume/time data as proxies for cross-asset concepts:
- Beta and correlation proxies via volatility/volume ratios
- Sector and style rotation via regime detection
- Market hours effects via timestamp analysis
- Factor timing via multi-indicator composites
- Cross-asset momentum/volatility/liquidity alignment
- Arbitrage and statistical relationship proxies
- Macro/micro/sentiment factor timing

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
# CROSS-ASSET HELPER FUNCTIONS
# ==========================================

def compute_volatility_ratio(candles, short_period=5, long_period=20):
    """Short-term realized vol / long-term realized vol (market vol regime proxy)."""
    if len(candles) < long_period + 1:
        return None
    returns_short = []
    for i in range(-short_period, 0):
        if abs(candles[i - 1]["close"]) > 0:
            returns_short.append(float(candles[i]["close"]) / float(candles[i - 1]["close"]) - 1)
    returns_long = []
    for i in range(-long_period, -short_period):
        if abs(candles[i - 1]["close"]) > 0:
            returns_long.append(float(candles[i]["close"]) / float(candles[i - 1]["close"]) - 1)
    if len(returns_short) < 2 or len(returns_long) < 2:
        return None
    vol_short = math.sqrt(sum(r ** 2 for r in returns_short) / len(returns_short))
    vol_long = math.sqrt(sum(r ** 2 for r in returns_long) / len(returns_long))
    if vol_long == 0:
        return None
    return vol_short / vol_long


def compute_beta_proxy(candles, period=20):
    """Coin's volatility relative to typical crypto vol (beta to market proxy)."""
    if len(candles) < period + 1:
        return None
    returns = []
    for i in range(-period, 0):
        if abs(candles[i - 1]["close"]) > 0:
            returns.append(float(candles[i]["close"]) / float(candles[i - 1]["close"]) - 1)
    if len(returns) < 5:
        return None
    # Typical crypto daily vol ~2-3%
    typical_vol = 0.025
    realized_vol = math.sqrt(sum(r ** 2 for r in returns) / len(returns))
    return realized_vol / typical_vol


def compute_correlation_proxy(candles, period=20):
    """Price-volume correlation as proxy for ETH/market correlation."""
    if len(candles) < period:
        return None
    closes = [float(c["close"]) for c in candles[-period:]]
    volumes = [float(c["volume"]) for c in candles[-period:]]
    n = len(closes)
    if n < 3:
        return None
    mean_c = sum(closes) / n
    mean_v = sum(volumes) / n
    cov = sum((c - mean_c) * (v - mean_v) for c, v in zip(closes, volumes)) / n
    std_c = math.sqrt(sum((c - mean_c) ** 2 for c in closes) / n)
    std_v = math.sqrt(sum((v - mean_v) ** 2 for v in volumes) / n)
    if std_c == 0 or std_v == 0:
        return None
    return cov / (std_c * std_v)


def compute_momentum(candles, period=10):
    """Simple momentum: price change over period."""
    if len(candles) < period + 1:
        return None
    return float(candles[-1]["close"]) / float(candles[-period - 1]["close"]) - 1


def compute_mean_reversion_signal(candles, period=20):
    """Distance from mean as mean-reversion signal."""
    if len(candles) < period:
        return None
    closes = [float(c["close"]) for c in candles[-period:]]
    mean_price = sum(closes) / len(closes)
    if mean_price == 0:
        return None
    return (closes[-1] - mean_price) / mean_price


def compute_trend_strength(candles, period=20):
    """Trend strength via linear regression slope."""
    if len(candles) < period:
        return None
    closes = [float(c["close"]) for c in candles[-period:]]
    n = len(closes)
    x_mean = (n - 1) / 2
    y_mean = sum(closes) / n
    num = sum((i - x_mean) * (c - y_mean) for i, c in enumerate(closes))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0
    slope = num / den
    return slope / y_mean if y_mean != 0 else 0


def compute_volume_regime(candles, period=20):
    """Volume regime: high/low relative to trailing average."""
    if len(candles) < period * 2:
        return None
    vols = [float(c["volume"]) for c in candles[-period * 2:]]
    avg_vol = sum(vols[:period]) / period
    recent_vol = sum(vols[period:]) / period
    if avg_vol == 0:
        return None
    return recent_vol / avg_vol


def compute_hour_of_day(candle):
    """Extract UTC hour from candle timestamp."""
    ts = int(candle.get("start", candle.get("time", 0)))
    return datetime.fromtimestamp(ts, tz=timezone.utc).hour


def compute_day_of_week(candle):
    """Extract day of week (0=Mon, 6=Sun) from candle timestamp."""
    ts = int(candle.get("start", candle.get("time", 0)))
    return datetime.fromtimestamp(ts, tz=timezone.utc).weekday()


def compute_rsi(candles, period=14):
    """RSI computation."""
    if len(candles) < period + 1:
        return None
    closes = [float(c["close"]) for c in candles[-(period + 1):]]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    if not gains:
        return None
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


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


def compute_bollinger_position(candles, period=20, num_std=2):
    """Position within Bollinger Bands (0=lower, 1=upper)."""
    if len(candles) < period:
        return None
    closes = [float(c["close"]) for c in candles[-period:]]
    mean = sum(closes) / len(closes)
    std = math.sqrt(sum((c - mean) ** 2 for c in closes) / len(closes))
    if std == 0:
        return 0.5
    current = float(candles[-1]["close"])
    return (current - (mean - num_std * std)) / (2 * num_std * std)


def compute_macd(candles, fast=12, slow=26, signal=9):
    """MACD histogram."""
    if len(candles) < slow + signal:
        return None
    closes = [float(c["close"]) for c in candles]

    def ema(data, period):
        mult = 2 / (period + 1)
        e = sum(data[:period]) / period
        for x in data[period:]:
            e = (x - e) * mult + e
        return e

    fast_ema = ema(closes[-slow:], fast)
    slow_ema = ema(closes[-slow:], slow)
    macd_line = fast_ema - slow_ema
    return macd_line


# ==========================================
# CROSS-ASSET STRATEGY ENTRY FUNCTIONS
# ==========================================

def _btc_beta_entry(candles_hist, closes, candle, params):
    """High-beta-to-BTC proxy: enter when high-beta coin confirms upward move in low-vol regime."""
    if len(candles_hist) < 30:
        return False
    beta = compute_beta_proxy(candles_hist, params.get("beta_period", 20))
    if beta is None or beta < params.get("beta_thresh", 1.2):
        return False
    vol_ratio = compute_volatility_ratio(candles_hist)
    if vol_ratio is not None and vol_ratio < 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _eth_correlation_entry(candles_hist, closes, candle, params):
    """ETH correlation proxy via price-volume correlation breakout."""
    if len(candles_hist) < 30:
        return False
    corr = compute_correlation_proxy(candles_hist, params.get("corr_period", 20))
    if corr is None or corr < params.get("corr_thresh", 0.3):
        return False
    mom = compute_momentum(candles_hist, params.get("mom_period", 5))
    if mom is not None and mom > 0.01:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _sector_rotation_entry(candles_hist, closes, candle, params):
    """Rotate between high-vol and low-vol regimes: enter on regime shift confirmation."""
    if len(candles_hist) < 40:
        return False
    vol_ratio = compute_volatility_ratio(candles_hist)
    if vol_ratio is None:
        return False
    prev_vol_ratio = compute_volatility_ratio(candles_hist[:-5])
    if prev_vol_ratio is None:
        return False
    # Transition from high to low vol regime
    if vol_ratio < 1.0 and prev_vol_ratio > 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _market_cap_rotation_entry(candles_hist, closes, candle, params):
    """Large cap vs small cap style: large cap = smoother price action, enter on smooth uptrend."""
    if len(candles_hist) < 30:
        return False
    atr = compute_atr(candles_hist, params.get("atr_period", 14))
    if atr is None:
        return False
    price = float(candle["close"])
    atr_pct = atr / price if price > 0 else 0
    # Low ATR % = large-cap-like behavior
    if atr_pct < params.get("atr_thresh", 0.02):
        mom = compute_momentum(candles_hist, params.get("mom_period", 10))
        if mom is not None and mom > 0.02:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _style_rotation_entry(candles_hist, closes, candle, params):
    """Momentum vs mean reversion style detection: enter when momentum regime detected."""
    if len(candles_hist) < 30:
        return False
    trend = compute_trend_strength(candles_hist, params.get("trend_period", 20))
    if trend is None:
        return False
    # Strong trend = momentum regime
    if abs(trend) > params.get("trend_thresh", 0.001):
        if trend > 0:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _factor_rotation_entry(candles_hist, closes, candle, params):
    """Switch between trend and reversal factors based on signal strength."""
    if len(candles_hist) < 30:
        return False
    trend = compute_trend_strength(candles_hist)
    mr = compute_mean_reversion_signal(candles_hist)
    if trend is None or mr is None:
        return False
    # Trend factor dominates
    if abs(trend) > abs(mr) * params.get("factor_mult", 2):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_asset_momentum_entry(candles_hist, closes, candle, params):
    """Momentum adjusted for market beta: enter when beta-adjusted momentum is positive."""
    if len(candles_hist) < 30:
        return False
    mom = compute_momentum(candles_hist, params.get("mom_period", 10))
    beta = compute_beta_proxy(candles_hist, params.get("beta_period", 20))
    if mom is None or beta is None or beta == 0:
        return False
    adj_momentum = mom / beta
    if adj_momentum > params.get("adj_mom_thresh", 0.01):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_asset_reversion_entry(candles_hist, closes, candle, params):
    """Reversion adjusted for market regime: enter on deep reversion in low-vol regime."""
    if len(candles_hist) < 30:
        return False
    mr = compute_mean_reversion_signal(candles_hist, params.get("mr_period", 20))
    vol_ratio = compute_volatility_ratio(candles_hist)
    if mr is None or vol_ratio is None:
        return False
    if mr < params.get("mr_thresh", -0.02) and vol_ratio < 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_asset_breakout_entry(candles_hist, closes, candle, params):
    """Breakout confirmed by market regime: price breaks above range during vol contraction."""
    if len(candles_hist) < 40:
        return False
    vol_ratio = compute_volatility_ratio(candles_hist)
    if vol_ratio is None or vol_ratio > 0.8:
        return False
    period = params.get("lookback", 20)
    highs = [float(c["high"]) for c in candles_hist[-period - 1:-1]]
    if not highs:
        return False
    if float(candle["close"]) > max(highs):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_asset_volatility_entry(candles_hist, closes, candle, params):
    """Vol adjusted for market vol regime: enter when vol expands from contraction."""
    if len(candles_hist) < 40:
        return False
    vol_ratio = compute_volatility_ratio(candles_hist, short_period=5, long_period=20)
    vol_ratio_prev = compute_volatility_ratio(candles_hist[:-5], short_period=5, long_period=20)
    if vol_ratio is None or vol_ratio_prev is None:
        return False
    if vol_ratio_prev < 0.7 and vol_ratio > 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_asset_volume_entry(candles_hist, closes, candle, params):
    """Volume relative to market volume: enter when volume expands during price rise."""
    if len(candles_hist) < 30:
        return False
    vol_regime = compute_volume_regime(candles_hist)
    if vol_regime is None or vol_regime < params.get("vol_regime_thresh", 1.3):
        return False
    mom = compute_momentum(candles_hist, params.get("mom_period", 5))
    if mom is not None and mom > 0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_asset_pattern_entry(candles_hist, closes, candle, params):
    """Pattern strength by market regime: enter on bullish pattern in favorable regime."""
    if len(candles_hist) < 30:
        return False
    rsi = compute_rsi(candles_hist, params.get("rsi_period", 14))
    if rsi is None or rsi < 40 or rsi > 70:
        return False
    vol_ratio = compute_volatility_ratio(candles_hist)
    if vol_ratio is not None and vol_ratio < 1.2:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _crypto_market_entry(candles_hist, closes, candle, params):
    """Crypto market regime proxy: enter during crypto-active hours with momentum."""
    if len(candles_hist) < 20:
        return False
    hour = compute_hour_of_day(candle)
    # Crypto active hours: broad window
    if hour < 6 or hour > 22:
        return False
    mom = compute_momentum(candles_hist, params.get("mom_period", 5))
    if mom is not None and mom > 0.005:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _stock_market_entry(candles_hist, closes, candle, params):
    """Stock market hours effect: enter during US market hours with confirmation."""
    if len(candles_hist) < 20:
        return False
    hour = compute_hour_of_day(candle)
    # US market hours: 14:30-21:00 UTC
    if 14 <= hour <= 21:
        mom = compute_momentum(candles_hist, params.get("mom_period", 5))
        if mom is not None and mom > 0.003:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _bond_market_entry(candles_hist, closes, candle, params):
    """Bond market hours effect: enter during early UTC hours (bond market active)."""
    if len(candles_hist) < 20:
        return False
    hour = compute_hour_of_day(candle)
    # Bond market: early UTC hours ~7-16
    if 7 <= hour <= 16:
        vol_regime = compute_volume_regime(candles_hist)
        if vol_regime is not None and vol_regime > 1.0:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _commodity_market_entry(candles_hist, closes, candle, params):
    """Commodity hours effect: enter during commodity trading hours."""
    if len(candles_hist) < 20:
        return False
    hour = compute_hour_of_day(candle)
    # Commodity markets: overlap ~12-20 UTC
    if 12 <= hour <= 20:
        mom = compute_momentum(candles_hist, params.get("mom_period", 5))
        if mom is not None and mom > 0.002:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _forex_market_entry(candles_hist, closes, candle, params):
    """Forex market overlap effect: enter during London/NY forex overlap."""
    if len(candles_hist) < 20:
        return False
    hour = compute_hour_of_day(candle)
    # London/NY overlap: 12:00-16:00 UTC
    if 12 <= hour <= 16:
        vol_regime = compute_volume_regime(candles_hist)
        if vol_regime is not None and vol_regime > 1.1:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _real_estate_entry(candles_hist, closes, candle, params):
    """REIT hours effect: enter during US midday (REIT proxy hours)."""
    if len(candles_hist) < 20:
        return False
    hour = compute_hour_of_day(candle)
    # REIT proxy: US midday 16-19 UTC
    if 16 <= hour <= 19:
        trend = compute_trend_strength(candles_hist, params.get("trend_period", 10))
        if trend is not None and trend > 0:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _credit_market_entry(candles_hist, closes, candle, params):
    """Credit market stress proxy: enter when vol is low and trend is stable."""
    if len(candles_hist) < 30:
        return False
    vol_ratio = compute_volatility_ratio(candles_hist)
    if vol_ratio is None or vol_ratio > 0.9:
        return False
    trend = compute_trend_strength(candles_hist)
    if trend is not None and trend > params.get("trend_thresh", 0.0005):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _money_market_entry(candles_hist, closes, candle, params):
    """Money market hours effect: enter during very low-vol stable periods."""
    if len(candles_hist) < 30:
        return False
    hour = compute_hour_of_day(candle)
    # Money market: overnight UTC ~22-02
    if 22 <= hour or hour <= 2:
        vol_ratio = compute_volatility_ratio(candles_hist)
        if vol_ratio is not None and vol_ratio < 0.8:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _derivatives_market_entry(candles_hist, closes, candle, params):
    """Options expiry effect proxy: enter near candle-count boundaries (expiry proxy)."""
    if len(candles_hist) < 40:
        return False
    # Proxy: use volume surge as expiry-related activity
    vol_regime = compute_volume_regime(candles_hist)
    if vol_regime is not None and vol_regime > params.get("vol_thresh", 1.4):
        mom = compute_momentum(candles_hist, params.get("mom_period", 5))
        if mom is not None and mom > 0.005:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _options_market_entry(candles_hist, closes, candle, params):
    """Options expiry week effect: enter on Friday-like candle patterns."""
    if len(candles_hist) < 30:
        return False
    dow = compute_day_of_week(candle)
    # Friday proxy: day 4
    if dow == 4:
        vol_regime = compute_volume_regime(candles_hist)
        if vol_regime is not None and vol_regime > 1.2:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _futures_market_entry(candles_hist, closes, candle, params):
    """Futures roll effect: enter on volume spike at roll proxy boundary."""
    if len(candles_hist) < 40:
        return False
    # Proxy: volume regime shift indicates roll activity
    vol_now = compute_volume_regime(candles_hist)
    vol_prev = compute_volume_regime(candles_hist[:-10])
    if vol_now is None or vol_prev is None:
        return False
    if vol_now > vol_prev * 1.3:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _spot_market_entry(candles_hist, closes, candle, params):
    """Spot vs futures basis proxy: enter on price divergence from volume-weighted average."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    cum_vp = sum(float(c["volume"]) * float(c["close"]) for c in candles_hist[-period:])
    cum_v = sum(float(c["volume"]) for c in candles_hist[-period:])
    if cum_v == 0:
        return False
    vwap = cum_vp / cum_v
    current = float(candle["close"])
    basis = (current - vwap) / vwap
    if abs(basis) < params.get("basis_thresh", 0.005):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _arbitrage_entry(candles_hist, closes, candle, params):
    """Cross-exchange arb proxy: enter on tight spread convergence."""
    if len(candles_hist) < 30:
        return False
    # Proxy: narrow high-low range relative to ATR = convergence
    atr = compute_atr(candles_hist, params.get("atr_period", 14))
    if atr is None:
        return False
    spread = float(candle["high"]) - float(candle["low"])
    if spread < atr * params.get("spread_mult", 0.5):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _statistical_arbitrage_entry(candles_hist, closes, candle, params):
    """Stat arb with market hedge proxy: enter when z-score reverts."""
    if len(candles_hist) < 40:
        return False
    closes_list = [float(c["close"]) for c in candles_hist[-params.get("z_period", 20):]]
    mean = sum(closes_list) / len(closes_list)
    std = math.sqrt(sum((c - mean) ** 2 for c in closes_list) / len(closes_list))
    if std == 0:
        return False
    z = (closes_list[-1] - mean) / std
    if z < params.get("z_thresh", -1.5):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _pairs_trading_cross_entry(candles_hist, closes, candle, params):
    """Pairs trading with market pair proxy: enter when coin diverges from market trend."""
    if len(candles_hist) < 40:
        return False
    mom = compute_momentum(candles_hist, params.get("mom_period", 10))
    if mom is None:
        return False
    # Market proxy: average momentum over longer period
    market_mom = compute_momentum(candles_hist, params.get("market_mom_period", 20))
    if market_mom is None:
        return False
    # Pair divergence: short-term negative, long-term positive
    if mom < 0 and market_mom > 0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _triangular_arbitrage_entry(candles_hist, closes, candle, params):
    """Triangular relationship proxy: enter on three-bar pattern alignment."""
    if len(candles_hist) < 30:
        return False
    # Proxy: three consecutive green candles with increasing volume
    if len(closes) < 3:
        return False
    if closes[-1] > closes[-2] > closes[-3]:
        vols = [float(c["volume"]) for c in candles_hist[-3:]]
        if vols[2] > vols[1] > vols[0]:
            return True
    return False


def _convergence_trade_entry(candles_hist, closes, candle, params):
    """Convergence to fair value proxy: enter when price near mean with low vol."""
    if len(candles_hist) < 30:
        return False
    mr = compute_mean_reversion_signal(candles_hist, params.get("mr_period", 20))
    vol_ratio = compute_volatility_ratio(candles_hist)
    if mr is None or vol_ratio is None:
        return False
    if abs(mr) < params.get("mr_thresh", 0.01) and vol_ratio < 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _basis_trade_entry(candles_hist, closes, candle, params):
    """Basis trading signal proxy: enter on basis narrowing."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    cum_vp = sum(float(c["volume"]) * float(c["close"]) for c in candles_hist[-period:])
    cum_v = sum(float(c["volume"]) for c in candles_hist[-period:])
    if cum_v == 0:
        return False
    vwap = cum_vp / cum_v
    prev_vwap = None
    if len(candles_hist) > period + 5:
        prev_candles = candles_hist[-period - 5:-5]
        prev_cum_vp = sum(float(c["volume"]) * float(c["close"]) for c in prev_candles)
        prev_cum_v = sum(float(c["volume"]) for c in prev_candles)
        if prev_cum_v > 0:
            prev_vwap = prev_cum_vp / prev_cum_v

    if prev_vwap is not None:
        basis_now = abs(float(candle["close"]) - vwap) / vwap
        basis_prev = abs(float(candle["close"]) - prev_vwap) / prev_vwap
        if basis_now < basis_prev:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _calendar_spread_entry(candles_hist, closes, candle, params):
    """Calendar spread relationship: enter on short vs long momentum divergence."""
    if len(candles_hist) < 40:
        return False
    short_mom = compute_momentum(candles_hist, params.get("short_mom", 5))
    long_mom = compute_momentum(candles_hist, params.get("long_mom", 20))
    if short_mom is None or long_mom is None:
        return False
    # Calendar spread: short outperforming long
    if short_mom > long_mom and short_mom > 0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _inter_commodity_entry(candles_hist, closes, candle, params):
    """Inter-commodity relationship proxy: enter on volume-price alignment."""
    if len(candles_hist) < 30:
        return False
    corr = compute_correlation_proxy(candles_hist, params.get("corr_period", 20))
    if corr is None:
        return False
    # Positive correlation = inter-commodity alignment
    if corr > params.get("corr_thresh", 0.2):
        mom = compute_momentum(candles_hist, params.get("mom_period", 5))
        if mom is not None and mom > 0:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _inter_market_entry(candles_hist, closes, candle, params):
    """Inter-market analysis proxy: enter when multiple timeframes align bullish."""
    if len(candles_hist) < 40:
        return False
    mom_short = compute_momentum(candles_hist, params.get("short_mom", 5))
    mom_long = compute_momentum(candles_hist, params.get("long_mom", 20))
    if mom_short is None or mom_long is None:
        return False
    if mom_short > 0 and mom_long > 0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_currency_entry(candles_hist, closes, candle, params):
    """Cross-currency effect: enter during forex-active hours with trend."""
    if len(candles_hist) < 20:
        return False
    hour = compute_hour_of_day(candle)
    # Forex active: 7-17 UTC
    if 7 <= hour <= 17:
        trend = compute_trend_strength(candles_hist, params.get("trend_period", 10))
        if trend is not None and trend > params.get("trend_thresh", 0.0005):
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_volatility_entry(candles_hist, closes, candle, params):
    """Cross-volatility regime: enter when vol regime shifts from low to expanding."""
    if len(candles_hist) < 40:
        return False
    vol_now = compute_volatility_ratio(candles_hist, short_period=5, long_period=20)
    vol_prev = compute_volatility_ratio(candles_hist[:-5], short_period=5, long_period=20)
    if vol_now is None or vol_prev is None:
        return False
    if vol_prev < 0.8 and vol_now > 0.9:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_liquidity_entry(candles_hist, closes, candle, params):
    """Liquidity regime detection: enter when liquidity is favorable (high volume, tight spread)."""
    if len(candles_hist) < 30:
        return False
    vol_regime = compute_volume_regime(candles_hist)
    if vol_regime is None or vol_regime < params.get("vol_thresh", 1.2):
        return False
    spread = float(candle["high"]) - float(candle["low"])
    atr = compute_atr(candles_hist, params.get("atr_period", 14))
    if atr is None or spread > atr * params.get("spread_mult", 1.5):
        return False
    return len(closes) > 1 and closes[-1] > closes[-2]


def _cross_momentum_entry(candles_hist, closes, candle, params):
    """Cross-asset momentum alignment: enter when multiple momentum signals align."""
    if len(candles_hist) < 30:
        return False
    mom_5 = compute_momentum(candles_hist, 5)
    mom_10 = compute_momentum(candles_hist, 10)
    mom_20 = compute_momentum(candles_hist, 20)
    if mom_5 is None or mom_10 is None or mom_20 is None:
        return False
    aligned = sum(1 for m in [mom_5, mom_10, mom_20] if m > 0)
    if aligned >= params.get("align_thresh", 2):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_sentiment_entry(candles_hist, closes, candle, params):
    """Sentiment regime proxy: enter when RSI + volume confirm bullish sentiment."""
    if len(candles_hist) < 30:
        return False
    rsi = compute_rsi(candles_hist, params.get("rsi_period", 14))
    if rsi is None or rsi < 50 or rsi > 65:
        return False
    vol_regime = compute_volume_regime(candles_hist)
    if vol_regime is not None and vol_regime > 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_flow_entry(candles_hist, closes, candle, params):
    """Flow regime detection: enter when cumulative volume flow is positive."""
    if len(candles_hist) < 30:
        return False
    period = params.get("period", 20)
    flow = 0
    for i in range(max(0, len(candles_hist) - period), len(candles_hist)):
        c = candles_hist[i]
        cl = float(c["close"])
        op = float(c["open"])
        v = float(c["volume"])
        if cl > op:
            flow += v
        elif cl < op:
            flow -= v
    if flow > 0 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _cross_regime_entry(candles_hist, closes, candle, params):
    """Multi-asset regime detection: enter in bull regime (trend up, vol low)."""
    if len(candles_hist) < 40:
        return False
    trend = compute_trend_strength(candles_hist, params.get("trend_period", 20))
    vol_ratio = compute_volatility_ratio(candles_hist)
    if trend is None or vol_ratio is None:
        return False
    if trend > params.get("trend_thresh", 0.001) and vol_ratio < 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_cycle_entry(candles_hist, closes, candle, params):
    """Cross-asset cycle alignment: enter on cycle upswing detection."""
    if len(candles_hist) < 50:
        return False
    mom = compute_momentum(candles_hist, params.get("mom_period", 20))
    if mom is None or mom < 0:
        return False
    vol_regime = compute_volume_regime(candles_hist)
    if vol_regime is not None and vol_regime > 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _cross_seasonal_entry(candles_hist, closes, candle, params):
    """Cross-asset seasonal alignment: enter on day-of-week seasonal effect."""
    if len(candles_hist) < 20:
        return False
    dow = compute_day_of_week(candle)
    # Monday-Friday seasonal bias
    if dow < 5:
        mom = compute_momentum(candles_hist, params.get("mom_period", 5))
        if mom is not None and mom > 0.003:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _macro_factor_entry(candles_hist, closes, candle, params):
    """Macro factor proxy: enter when trend + vol regime align bullishly."""
    if len(candles_hist) < 40:
        return False
    trend = compute_trend_strength(candles_hist, params.get("trend_period", 20))
    vol_ratio = compute_volatility_ratio(candles_hist)
    mom = compute_momentum(candles_hist, params.get("mom_period", 10))
    if trend is None or vol_ratio is None or mom is None:
        return False
    if trend > 0 and vol_ratio < 1.1 and mom > 0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _micro_factor_entry(candles_hist, closes, candle, params):
    """Micro factor proxy: enter on tight-spread volume surge."""
    if len(candles_hist) < 30:
        return False
    vol_regime = compute_volume_regime(candles_hist)
    if vol_regime is None or vol_regime < params.get("vol_thresh", 1.3):
        return False
    spread = float(candle["high"]) - float(candle["low"])
    price = float(candle["close"])
    if price > 0 and spread / price < params.get("spread_pct_thresh", 0.01):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _style_factor_entry(candles_hist, closes, candle, params):
    """Style factor timing: enter when momentum style dominates."""
    if len(candles_hist) < 30:
        return False
    trend = compute_trend_strength(candles_hist, params.get("trend_period", 20))
    mr = compute_mean_reversion_signal(candles_hist, params.get("mr_period", 20))
    if trend is None or mr is None:
        return False
    # Momentum style: trend >> mean reversion
    if trend > abs(mr) and trend > params.get("trend_thresh", 0.001):
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _risk_factor_entry(candles_hist, closes, candle, params):
    """Risk factor timing: enter when risk-on signal (low vol, positive momentum)."""
    if len(candles_hist) < 30:
        return False
    vol_ratio = compute_volatility_ratio(candles_hist)
    if vol_ratio is None or vol_ratio > 1.0:
        return False
    mom = compute_momentum(candles_hist, params.get("mom_period", 10))
    if mom is not None and mom > 0.01:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _return_factor_entry(candles_hist, closes, candle, params):
    """Return factor timing: enter when return acceleration detected."""
    if len(candles_hist) < 30:
        return False
    short_mom = compute_momentum(candles_hist, params.get("short_mom", 5))
    long_mom = compute_momentum(candles_hist, params.get("long_mom", 20))
    if short_mom is None or long_mom is None:
        return False
    # Acceleration: short > long
    if short_mom > long_mom * params.get("accel_mult", 2) and short_mom > 0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _volatility_factor_entry(candles_hist, closes, candle, params):
    """Volatility factor timing: enter on vol expansion after contraction."""
    if len(candles_hist) < 40:
        return False
    vol_short = compute_volatility_ratio(candles_hist, short_period=5, long_period=20)
    if vol_short is None:
        return False
    if vol_short > params.get("vol_thresh", 0.9) and vol_short < 1.5:
        mom = compute_momentum(candles_hist, params.get("mom_period", 5))
        if mom is not None and mom > 0:
            return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _liquidity_factor_entry(candles_hist, closes, candle, params):
    """Liquidity factor timing: enter when liquidity improves."""
    if len(candles_hist) < 30:
        return False
    vol_now = compute_volume_regime(candles_hist)
    vol_prev = compute_volume_regime(candles_hist[:-10])
    if vol_now is None or vol_prev is None:
        return False
    if vol_now > vol_prev and vol_now > 1.0:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


def _sentiment_factor_entry(candles_hist, closes, candle, params):
    """Sentiment factor timing: enter when RSI and momentum both confirm."""
    if len(candles_hist) < 30:
        return False
    rsi = compute_rsi(candles_hist, params.get("rsi_period", 14))
    mom = compute_momentum(candles_hist, params.get("mom_period", 10))
    if rsi is None or mom is None:
        return False
    if 50 < rsi < 70 and mom > 0.01:
        return len(closes) > 1 and closes[-1] > closes[-2]
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

CROSS_ASSET_STRATEGIES = [
    {"name": "btc_beta", "params": {"beta_period": 20, "beta_thresh": 1.2, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "eth_correlation", "params": {"corr_period": 20, "corr_thresh": 0.3, "mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "sector_rotation", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "market_cap_rotation", "params": {"atr_period": 14, "atr_thresh": 0.02, "mom_period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "style_rotation", "params": {"trend_period": 20, "trend_thresh": 0.001, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "factor_rotation", "params": {"factor_mult": 2, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_asset_momentum", "params": {"mom_period": 10, "beta_period": 20, "adj_mom_thresh": 0.01, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_asset_reversion", "params": {"mr_period": 20, "mr_thresh": -0.02, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "cross_asset_breakout", "params": {"lookback": 20, "tp_pct": 10, "sl_pct": 4, "max_hold": 24}},
    {"name": "cross_asset_volatility", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_asset_volume", "params": {"vol_regime_thresh": 1.3, "mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_asset_pattern", "params": {"rsi_period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "crypto_market", "params": {"mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "stock_market", "params": {"mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "bond_market", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "commodity_market", "params": {"mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "forex_market", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "real_estate", "params": {"trend_period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "credit_market", "params": {"trend_thresh": 0.0005, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "money_market", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "derivatives_market", "params": {"vol_thresh": 1.4, "mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "options_market", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "futures_market", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "spot_market", "params": {"period": 20, "basis_thresh": 0.005, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "arbitrage", "params": {"atr_period": 14, "spread_mult": 0.5, "tp_pct": 6, "sl_pct": 2, "max_hold": 12}},
    {"name": "statistical_arbitrage", "params": {"z_period": 20, "z_thresh": -1.5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "pairs_trading_cross", "params": {"mom_period": 10, "market_mom_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "triangular_arbitrage", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "convergence_trade", "params": {"mr_period": 20, "mr_thresh": 0.01, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "basis_trade", "params": {"period": 20, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "calendar_spread", "params": {"short_mom": 5, "long_mom": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "inter_commodity", "params": {"corr_period": 20, "corr_thresh": 0.2, "mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "inter_market", "params": {"short_mom": 5, "long_mom": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_currency", "params": {"trend_period": 10, "trend_thresh": 0.0005, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_volatility", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_liquidity", "params": {"vol_thresh": 1.2, "atr_period": 14, "spread_mult": 1.5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_momentum", "params": {"align_thresh": 2, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_sentiment", "params": {"rsi_period": 14, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_flow", "params": {"period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_regime", "params": {"trend_period": 20, "trend_thresh": 0.001, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_cycle", "params": {"mom_period": 20, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "cross_seasonal", "params": {"mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "macro_factor", "params": {"trend_period": 20, "mom_period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "micro_factor", "params": {"vol_thresh": 1.3, "spread_pct_thresh": 0.01, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "style_factor", "params": {"trend_period": 20, "mr_period": 20, "trend_thresh": 0.001, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "risk_factor", "params": {"mom_period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "return_factor", "params": {"short_mom": 5, "long_mom": 20, "accel_mult": 2, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "volatility_factor", "params": {"vol_thresh": 0.9, "mom_period": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "liquidity_factor", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "sentiment_factor", "params": {"rsi_period": 14, "mom_period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "btc_beta": _btc_beta_entry,
    "eth_correlation": _eth_correlation_entry,
    "sector_rotation": _sector_rotation_entry,
    "market_cap_rotation": _market_cap_rotation_entry,
    "style_rotation": _style_rotation_entry,
    "factor_rotation": _factor_rotation_entry,
    "cross_asset_momentum": _cross_asset_momentum_entry,
    "cross_asset_reversion": _cross_asset_reversion_entry,
    "cross_asset_breakout": _cross_asset_breakout_entry,
    "cross_asset_volatility": _cross_asset_volatility_entry,
    "cross_asset_volume": _cross_asset_volume_entry,
    "cross_asset_pattern": _cross_asset_pattern_entry,
    "crypto_market": _crypto_market_entry,
    "stock_market": _stock_market_entry,
    "bond_market": _bond_market_entry,
    "commodity_market": _commodity_market_entry,
    "forex_market": _forex_market_entry,
    "real_estate": _real_estate_entry,
    "credit_market": _credit_market_entry,
    "money_market": _money_market_entry,
    "derivatives_market": _derivatives_market_entry,
    "options_market": _options_market_entry,
    "futures_market": _futures_market_entry,
    "spot_market": _spot_market_entry,
    "arbitrage": _arbitrage_entry,
    "statistical_arbitrage": _statistical_arbitrage_entry,
    "pairs_trading_cross": _pairs_trading_cross_entry,
    "triangular_arbitrage": _triangular_arbitrage_entry,
    "convergence_trade": _convergence_trade_entry,
    "basis_trade": _basis_trade_entry,
    "calendar_spread": _calendar_spread_entry,
    "inter_commodity": _inter_commodity_entry,
    "inter_market": _inter_market_entry,
    "cross_currency": _cross_currency_entry,
    "cross_volatility": _cross_volatility_entry,
    "cross_liquidity": _cross_liquidity_entry,
    "cross_momentum": _cross_momentum_entry,
    "cross_sentiment": _cross_sentiment_entry,
    "cross_flow": _cross_flow_entry,
    "cross_regime": _cross_regime_entry,
    "cross_cycle": _cross_cycle_entry,
    "cross_seasonal": _cross_seasonal_entry,
    "macro_factor": _macro_factor_entry,
    "micro_factor": _micro_factor_entry,
    "style_factor": _style_factor_entry,
    "risk_factor": _risk_factor_entry,
    "return_factor": _return_factor_entry,
    "volatility_factor": _volatility_factor_entry,
    "liquidity_factor": _liquidity_factor_entry,
    "sentiment_factor": _sentiment_factor_entry,
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
    print(f"CROSS-ASSET 50 STRATEGY SWEEP")
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

    fast_coins = coins[:35] + [c for c in ["GHST-USD", "NOM-USD", "TRU-USD", "MOG-USD", "RAVE-USD"] if c not in coins[:35]]
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
    print(f"Testing {len(CROSS_ASSET_STRATEGIES)} cross-asset strategies...\n")

    results = []
    total_tests = len(all_candles) * len(CROSS_ASSET_STRATEGIES)
    test_count = 0

    for strat_def in CROSS_ASSET_STRATEGIES:
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

    out_path = Path(__file__).parent.parent / "reports" / "cross_asset_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 CROSS-ASSET STRATEGIES:")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:>2}. {r['strategy']:<25} ${r['total_net_pnl']:>8.2f}  "
              f"Hit: {r['hit_rate']:>5.1f}%  "
              f"Profitable: {r['profitable_coins']}/{r['coins_tested']}")

    if output["promoted_for_30d"]:
        print(f"\nPROMOTED FOR 30D VALIDATION:")
        for s in output["promoted_for_30d"]:
            print(f"  [PROMOTED] {s}")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
