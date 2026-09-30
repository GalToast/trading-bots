"""Pure technical-indicator math (RSI/ATR/EMA). No MT5 calls; unit-tested."""
from __future__ import annotations


def calc_rsi(closes, period=14):
    """Calculate RSI"""
    if len(closes) < period + 1:
        return 50.0  # neutral default

    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i-1]
        if delta > 0:
            gains.append(delta)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(delta))

    # Initial averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed averages (Wilder's method)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_atr(bars, period=14):
    """Calculate Average True Range"""
    if len(bars) < period + 1:
        return 0.0

    trs = []
    for i in range(1, len(bars)):
        tr = max(
            bars[i]['h'] - bars[i]['l'],
            abs(bars[i]['h'] - bars[i-1]['c']),
            abs(bars[i]['l'] - bars[i-1]['c'])
        )
        trs.append(tr)

    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0.0

    # Wilder's smoothing
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr

def calc_ema(values, period):
    """Calculate Exponential Moving Average"""
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    multiplier = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for val in values[period:]:
        ema = (val - ema) * multiplier + ema
    return ema
