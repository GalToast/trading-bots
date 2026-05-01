#!/usr/bin/env python3
"""
Candle Pattern 50 Strategy Sweep — Batch #4 of the 500 Strategies Initiative.

Tests 50 unique candle-pattern strategies across 35 Coinbase coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants cover classic Japanese candlestick patterns:
- Single-bar reversals (hammer, shooting star, doji, marubozu, spinning top)
- Two-bar patterns (engulfing, harami, piercing, dark cloud, tweezers, belt hold)
- Three-bar patterns (morning/evening star, three soldiers/crows, inside/outside breakouts)
- Multi-bar patterns (three methods, rising/falling three, mat hold, breakaway)
- Gap patterns (upside/downside gap, separating lines, abandoned baby, kick)
- Tower and river patterns (consolidation at extremes)

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
# CANDLE PATTERN HELPER FUNCTIONS
# ==========================================

def _body_size(candle):
    """Absolute body size."""
    return abs(float(candle["close"]) - float(candle["open"]))


def _body_pct(candle):
    """Body as pct of total range."""
    h = float(candle["high"])
    l = float(candle["low"])
    body = _body_size(candle)
    rng = h - l
    if rng == 0:
        return 0.0
    return body / rng


def _upper_wick(candle):
    return float(candle["high"]) - max(float(candle["open"]), float(candle["close"]))


def _lower_wick(candle):
    return min(float(candle["open"]), float(candle["close"])) - float(candle["low"])


def _is_green(candle):
    return float(candle["close"]) > float(candle["open"])


def _is_red(candle):
    return float(candle["close"]) < float(candle["open"])


def _avg_body(candles_hist, n=10):
    """Average body size over last n candles."""
    recent = candles_hist[-n:]
    bodies = [_body_size(c) for c in recent]
    return sum(bodies) / len(bodies) if bodies else 0


def _avg_range(candles_hist, n=10):
    """Average high-low range over last n candles."""
    recent = candles_hist[-n:]
    ranges = [float(c["high"]) - float(c["low"]) for c in recent]
    return sum(ranges) / len(ranges) if ranges else 0


def _trend_direction(closes, lookback=10):
    """1 if uptrend, -1 if downtrend, 0 if flat."""
    if len(closes) < lookback + 1:
        return 0
    recent = closes[-lookback:]
    if recent[-1] > recent[0] * 1.01:
        return 1
    elif recent[-1] < recent[0] * 0.99:
        return -1
    return 0


def _price_confirmation(closes):
    """Close is higher than previous close."""
    return len(closes) > 1 and closes[-1] > closes[-2]


def _candle_range(candle):
    return float(candle["high"]) - float(candle["low"])


def _midpoint(candle):
    return (float(candle["open"]) + float(candle["close"])) / 2


# ==========================================
# CANDLE PATTERN STRATEGY ENTRY FUNCTIONS
# ==========================================

def _bullish_engulfing_entry(candles_hist, closes, candle, params):
    """Previous red, current green completely engulfs previous body."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    if _is_red(prev) and _is_green(curr):
        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        curr_open = float(curr["open"])
        curr_close = float(curr["close"])
        if curr_open <= prev_close and curr_close >= prev_open:
            return _price_confirmation(closes)
    return False


def _bearish_engulfing_reversal_entry(candles_hist, closes, candle, params):
    """Engulfing at bottom + reversal confirmation (after downtrend)."""
    if len(candles_hist) < 30:
        return False
    if _trend_direction(closes, 10) != -1:
        return False
    # Look for bullish engulfing at the bottom
    prev = candles_hist[-2]
    curr = candle
    if _is_red(prev) and _is_green(curr):
        if float(curr["open"]) <= float(prev["close"]) and float(curr["close"]) >= float(prev["open"]):
            return _price_confirmation(closes)
    return False


def _hammer_entry(candles_hist, closes, candle, params):
    """Small body at top, long lower wick (at least 2x body)."""
    if len(candles_hist) < 22:
        return False
    thresh = params.get("wick_ratio", 2.0)
    body = _body_size(candle)
    lower = _lower_wick(candle)
    upper = _upper_wick(candle)
    avg_body = _avg_body(candles_hist, 10)
    if body <= avg_body * 0.5 and lower >= body * thresh and upper < body * 2:
        # After downtrend or flat
        if _trend_direction(closes, 10) <= 0:
            return _price_confirmation(closes)
    return False


def _inverted_hammer_entry(candles_hist, closes, candle, params):
    """Small body at bottom, long upper wick."""
    if len(candles_hist) < 22:
        return False
    thresh = params.get("wick_ratio", 2.0)
    body = _body_size(candle)
    upper = _upper_wick(candle)
    lower = _lower_wick(candle)
    avg_body = _avg_body(candles_hist, 10)
    if body <= avg_body * 0.5 and upper >= body * thresh and lower < body * 2:
        if _trend_direction(closes, 10) <= 0:
            return _price_confirmation(closes)
    return False


def _hanging_man_entry(candles_hist, closes, candle, params):
    """Hammer shape at top of uptrend."""
    if len(candles_hist) < 22:
        return False
    thresh = params.get("wick_ratio", 2.0)
    body = _body_size(candle)
    lower = _lower_wick(candle)
    upper = _upper_wick(candle)
    avg_body = _avg_body(candles_hist, 10)
    if body <= avg_body * 0.5 and lower >= body * thresh and upper < body * 2:
        if _trend_direction(closes, 10) == 1:
            return _price_confirmation(closes)
    return False


def _shooting_star_entry(candles_hist, closes, candle, params):
    """Inverted hammer shape at top of uptrend."""
    if len(candles_hist) < 22:
        return False
    thresh = params.get("wick_ratio", 2.0)
    body = _body_size(candle)
    upper = _upper_wick(candle)
    lower = _lower_wick(candle)
    avg_body = _avg_body(candles_hist, 10)
    if body <= avg_body * 0.5 and upper >= body * thresh and lower < body * 2:
        if _trend_direction(closes, 10) == 1:
            return _price_confirmation(closes)
    return False


def _doji_entry(candles_hist, closes, candle, params):
    """Open approx close — very small body (< 5% of range)."""
    if len(candles_hist) < 22:
        return False
    bp = _body_pct(candle)
    doji_thresh = params.get("doji_thresh", 0.05)
    if bp < doji_thresh and _candle_range(candle) > _avg_range(candles_hist, 10) * 0.5:
        return _price_confirmation(closes)
    return False


def _dragonfly_doji_entry(candles_hist, closes, candle, params):
    """Doji with long lower wick, tiny/no upper wick."""
    if len(candles_hist) < 22:
        return False
    bp = _body_pct(candle)
    lower = _lower_wick(candle)
    upper = _upper_wick(candle)
    rng = _candle_range(candle)
    doji_thresh = params.get("doji_thresh", 0.05)
    if bp < doji_thresh and lower > rng * 0.6 and upper < rng * 0.1:
        if _trend_direction(closes, 10) <= 0:
            return _price_confirmation(closes)
    return False


def _gravestone_doji_entry(candles_hist, closes, candle, params):
    """Doji with long upper wick, tiny/no lower wick."""
    if len(candles_hist) < 22:
        return False
    bp = _body_pct(candle)
    upper = _upper_wick(candle)
    lower = _lower_wick(candle)
    rng = _candle_range(candle)
    doji_thresh = params.get("doji_thresh", 0.05)
    if bp < doji_thresh and upper > rng * 0.6 and lower < rng * 0.1:
        return _price_confirmation(closes)
    return False


def _long_legged_doji_entry(candles_hist, closes, candle, params):
    """Doji with long upper AND lower wicks."""
    if len(candles_hist) < 22:
        return False
    bp = _body_pct(candle)
    lower = _lower_wick(candle)
    upper = _upper_wick(candle)
    rng = _candle_range(candle)
    doji_thresh = params.get("doji_thresh", 0.05)
    if bp < doji_thresh and lower > rng * 0.3 and upper > rng * 0.3:
        return _price_confirmation(closes)
    return False


def _morning_star_entry(candles_hist, closes, candle, params):
    """3-bar reversal: big red, small middle, big green."""
    if len(candles_hist) < 23:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    if _is_red(c1) and _is_green(c3):
        body1 = _body_size(c1)
        body2 = _body_size(c2)
        body3 = _body_size(c3)
        avg_body = _avg_body(candles_hist, 10)
        if body1 > avg_body * 0.8 and body2 < avg_body * 0.4 and body3 > avg_body * 0.8:
            if body3 > body1 * 0.5:
                return _price_confirmation(closes)
    return False


def _evening_star_entry(candles_hist, closes, candle, params):
    """3-bar reversal: big green, small middle, big red — reversed for entry."""
    if len(candles_hist) < 23:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    # Evening star is bearish, so enter long only if we see reversal after the pattern
    if _is_green(c1) and _is_red(c3):
        body1 = _body_size(c1)
        body2 = _body_size(c2)
        body3 = _body_size(c3)
        avg_body = _avg_body(candles_hist, 10)
        if body1 > avg_body * 0.8 and body2 < avg_body * 0.4 and body3 > avg_body * 0.8:
            # Enter on bounce after evening star completes
            return _price_confirmation(closes)
    return False


def _three_white_soldiers_entry(candles_hist, closes, candle, params):
    """3 consecutive green candles with higher closes."""
    if len(candles_hist) < 23:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    if _is_green(c1) and _is_green(c2) and _is_green(c3):
        if float(c2["close"]) > float(c1["close"]) and float(c3["close"]) > float(c2["close"]):
            return _price_confirmation(closes)
    return False


def _three_black_crows_entry(candles_hist, closes, candle, params):
    """3 consecutive red candles with lower closes — enter on bounce."""
    if len(candles_hist) < 23:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    if _is_red(c1) and _is_red(c2) and _is_red(c3):
        if float(c2["close"]) < float(c1["close"]) and float(c3["close"]) < float(c2["close"]):
            # Oversold bounce
            return _price_confirmation(closes)
    return False


def _inside_bar_entry(candles_hist, closes, candle, params):
    """Current bar inside previous bar's range."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    if float(curr["high"]) <= float(prev["high"]) and float(curr["low"]) >= float(prev["low"]):
        return _price_confirmation(closes)
    return False


def _outside_bar_entry(candles_hist, closes, candle, params):
    """Current bar engulfs previous bar's range."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    if float(curr["high"]) >= float(prev["high"]) and float(curr["low"]) <= float(prev["low"]):
        if _is_green(curr):
            return _price_confirmation(closes)
    return False


def _harami_entry(candles_hist, closes, candle, params):
    """Small bar inside previous large bar."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    prev_body = _body_size(prev)
    curr_body = _body_size(curr)
    avg_body = _avg_body(candles_hist, 10)
    if prev_body > avg_body * 0.7 and curr_body < avg_body * 0.3:
        if float(curr["high"]) <= float(prev["high"]) and float(curr["low"]) >= float(prev["low"]):
            return _price_confirmation(closes)
    return False


def _harami_cross_entry(candles_hist, closes, candle, params):
    """Harami where small bar is a doji."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    prev_body = _body_size(prev)
    bp = _body_pct(curr)
    avg_body = _avg_body(candles_hist, 10)
    doji_thresh = params.get("doji_thresh", 0.05)
    if prev_body > avg_body * 0.7 and bp < doji_thresh:
        if float(curr["high"]) <= float(prev["high"]) and float(curr["low"]) >= float(prev["low"]):
            return _price_confirmation(closes)
    return False


def _piercing_line_entry(candles_hist, closes, candle, params):
    """Red candle, then green closes above red midpoint."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    if _is_red(prev) and _is_green(curr):
        prev_mid = (float(prev["high"]) + float(prev["low"])) / 2
        if float(curr["close"]) > prev_mid:
            return _price_confirmation(closes)
    return False


def _dark_cloud_cover_entry(candles_hist, closes, candle, params):
    """Green candle, then red closes below green midpoint — enter on bounce."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    if _is_green(prev) and _is_red(curr):
        prev_mid = (float(prev["high"]) + float(prev["low"])) / 2
        if float(curr["close"]) < prev_mid:
            # Oversold bounce
            return _price_confirmation(closes)
    return False


def _tweezers_bottom_entry(candles_hist, closes, candle, params):
    """Two candles with matching lows (within 0.5%)."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    prev_low = float(prev["low"])
    curr_low = float(curr["low"])
    if prev_low > 0 and abs(curr_low - prev_low) / prev_low < 0.005:
        if _is_green(curr):
            return _price_confirmation(closes)
    return False


def _tweezers_top_entry(candles_hist, closes, candle, params):
    """Two candles with matching highs — enter on pullback bounce."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    prev_high = float(prev["high"])
    curr_high = float(curr["high"])
    if prev_high > 0 and abs(curr_high - prev_high) / prev_high < 0.005:
        return _price_confirmation(closes)
    return False


def _three_inside_up_entry(candles_hist, closes, candle, params):
    """Inside bar followed by breakout up."""
    if len(candles_hist) < 24:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    # c2 inside c1
    if float(c2["high"]) <= float(c1["high"]) and float(c2["low"]) >= float(c1["low"]):
        # c3 breaks above c1 high
        if float(c3["close"]) > float(c1["high"]):
            return _price_confirmation(closes)
    return False


def _three_inside_down_entry(candles_hist, closes, candle, params):
    """Inside bar followed by breakout down — enter on bounce."""
    if len(candles_hist) < 24:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    if float(c2["high"]) <= float(c1["high"]) and float(c2["low"]) >= float(c1["low"]):
        if float(c3["close"]) < float(c1["low"]):
            return _price_confirmation(closes)
    return False


def _three_outside_up_entry(candles_hist, closes, candle, params):
    """Engulfing followed by higher close."""
    if len(candles_hist) < 24:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    # c2 bullish engulfs c1
    if _is_red(c1) and _is_green(c2):
        if float(c2["open"]) <= float(c1["close"]) and float(c2["close"]) >= float(c1["open"]):
            if float(c3["close"]) > float(c2["close"]):
                return _price_confirmation(closes)
    return False


def _three_outside_down_entry(candles_hist, closes, candle, params):
    """Engulfing followed by lower close — enter on bounce."""
    if len(candles_hist) < 24:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    if _is_green(c1) and _is_red(c2):
        if float(c2["open"]) >= float(c1["close"]) and float(c2["close"]) <= float(c1["open"]):
            if float(c3["close"]) < float(c2["close"]):
                return _price_confirmation(closes)
    return False


def _upside_gap_entry(candles_hist, closes, candle, params):
    """Gap up followed by continuation."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    gap_pct = params.get("gap_pct", 0.5)
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    curr_open = float(curr["open"])
    # Gap up
    if curr_open > prev_high * (1 + gap_pct / 100):
        if _is_green(curr) and float(curr["close"]) > curr_open:
            return _price_confirmation(closes)
    return False


def _downside_gap_entry(candles_hist, closes, candle, params):
    """Gap down followed by bounce."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    gap_pct = params.get("gap_pct", 0.5)
    if curr_open := float(curr["open"]) < float(prev["low"]) * (1 - gap_pct / 100):
        if _is_green(curr) and float(curr["close"]) > curr_open:
            return _price_confirmation(closes)
    return False


def _spinning_top_entry(candles_hist, closes, candle, params):
    """Small body with wicks on both sides."""
    if len(candles_hist) < 22:
        return False
    body = _body_size(candle)
    upper = _upper_wick(candle)
    lower = _lower_wick(candle)
    avg_body = _avg_body(candles_hist, 10)
    if body < avg_body * 0.5 and upper > body and lower > body:
        return _price_confirmation(closes)
    return False


def _marubozu_entry(candles_hist, closes, candle, params):
    """No wicks — full body candle (within 2% tolerance)."""
    if len(candles_hist) < 22:
        return False
    upper = _upper_wick(candle)
    lower = _lower_wick(candle)
    rng = _candle_range(candle)
    tol = params.get("wick_tol", 0.02)
    if rng > 0 and upper / rng < tol and lower / rng < tol:
        if _is_green(candle):
            return _price_confirmation(closes)
    return False


def _belt_hold_bullish_entry(candles_hist, closes, candle, params):
    """Opens at low, closes at high (or very near)."""
    if len(candles_hist) < 22:
        return False
    tol = params.get("tol", 0.01)
    rng = _candle_range(candle)
    if rng == 0:
        return False
    if abs(float(candle["open"]) - float(candle["low"])) / rng < tol:
        if abs(float(candle["close"]) - float(candle["high"])) / rng < tol:
            if _is_green(candle):
                return _price_confirmation(closes)
    return False


def _belt_hold_bearish_entry(candles_hist, closes, candle, params):
    """Opens at high, closes at low — enter on bounce."""
    if len(candles_hist) < 22:
        return False
    tol = params.get("tol", 0.01)
    rng = _candle_range(candle)
    if rng == 0:
        return False
    if abs(float(candle["open"]) - float(candle["high"])) / rng < tol:
        if abs(float(candle["close"]) - float(candle["low"])) / rng < tol:
            return _price_confirmation(closes)
    return False


def _separating_lines_entry(candles_hist, closes, candle, params):
    """Continuation gap pattern: red then green at same open."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    if _is_red(prev) and _is_green(curr):
        # Opens at approximately same level
        if abs(float(curr["open"]) - float(prev["open"])) / float(prev["open"]) < 0.005:
            return _price_confirmation(closes)
    return False


def _counterattack_entry(candles_hist, closes, candle, params):
    """Opposing candle at same close price."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    tol = params.get("tol", 0.005)
    if abs(float(curr["close"]) - float(prev["close"])) / float(prev["close"]) < tol:
        if _is_red(prev) and _is_green(curr):
            return _price_confirmation(closes)
    return False


def _thrusting_entry(candles_hist, closes, candle, params):
    """Pattern with close in lower half of previous."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    if _is_red(prev) and _is_green(curr):
        prev_mid = (float(prev["open"]) + float(prev["close"])) / 2
        # Current close is in lower half of previous body
        if float(curr["close"]) < prev_mid and float(curr["close"]) > float(prev["close"]):
            return _price_confirmation(closes)
    return False


def _in_on_neck_entry(candles_hist, closes, candle, params):
    """Pattern with close at/near previous close."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    tol = params.get("tol", 0.01)
    if _is_red(prev) and _is_green(curr):
        if abs(float(curr["close"]) - float(prev["close"])) / float(prev["close"]) < tol:
            return _price_confirmation(closes)
    return False


def _unique_three_entry(candles_hist, closes, candle, params):
    """Three-bar reversal with specific structure: red, lower red, strong green."""
    if len(candles_hist) < 23:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    if _is_red(c1) and _is_red(c2) and _is_green(c3):
        if float(c2["close"]) < float(c1["close"]):
            if float(c3["close"]) > _midpoint(c1):
                return _price_confirmation(closes)
    return False


def _mat_hold_entry(candles_hist, closes, candle, params):
    """Bullish continuation pause: green, small pullback candles, green continuation."""
    if len(candles_hist) < 25:
        return False
    c1 = candles_hist[-4]
    c2 = candles_hist[-3]
    c3 = candles_hist[-2]
    c4 = candle
    avg_body = _avg_body(candles_hist, 10)
    if _is_green(c1) and _body_size(c1) > avg_body * 0.6:
        # Small pullbacks
        if _body_size(c2) < avg_body * 0.4 and _body_size(c3) < avg_body * 0.4:
            if _is_green(c4) and float(c4["close"]) > float(c1["close"]):
                return _price_confirmation(closes)
    return False


def _advance_block_entry(candles_hist, closes, candle, params):
    """Three green candles with shrinking bodies."""
    if len(candles_hist) < 23:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    if _is_green(c1) and _is_green(c2) and _is_green(c3):
        b1 = _body_size(c1)
        b2 = _body_size(c2)
        b3 = _body_size(c3)
        if b1 > b2 > b3:
            # Waning momentum but still trending up
            return _price_confirmation(closes)
    return False


def _stalled_pattern_entry(candles_hist, closes, candle, params):
    """Three green then stall (doji/small) — enter on resumption."""
    if len(candles_hist) < 24:
        return False
    c1 = candles_hist[-4]
    c2 = candles_hist[-3]
    c3 = candles_hist[-2]
    c4 = candle
    avg_body = _avg_body(candles_hist, 10)
    if _is_green(c1) and _is_green(c2) and _is_green(c3):
        # Stall bar
        if _body_size(c4) < avg_body * 0.3 or _body_pct(c4) < 0.1:
            return _price_confirmation(closes)
    return False


def _rising_three_methods_entry(candles_hist, closes, candle, params):
    """5-bar continuation: big green, 3 small reds, big green breakout."""
    if len(candles_hist) < 26:
        return False
    c1 = candles_hist[-5]
    c2 = candles_hist[-4]
    c3 = candles_hist[-3]
    c4 = candles_hist[-2]
    c5 = candle
    avg_body = _avg_body(candles_hist, 10)
    if _is_green(c1) and _body_size(c1) > avg_body * 0.7:
        # Three small reds staying within c1 range
        if all(_is_red(c) and _body_size(c) < avg_body * 0.5 for c in [c2, c3, c4]):
            if float(c4["low"]) >= float(c1["low"]):
                if _is_green(c5) and float(c5["close"]) > float(c1["close"]):
                    return _price_confirmation(closes)
    return False


def _falling_three_methods_entry(candles_hist, closes, candle, params):
    """5-bar bearish continuation — enter on bounce after completion."""
    if len(candles_hist) < 26:
        return False
    c1 = candles_hist[-5]
    c2 = candles_hist[-4]
    c3 = candles_hist[-3]
    c4 = candles_hist[-2]
    c5 = candle
    avg_body = _avg_body(candles_hist, 10)
    if _is_red(c1) and _body_size(c1) > avg_body * 0.7:
        if all(_is_green(c) and _body_size(c) < avg_body * 0.5 for c in [c2, c3, c4]):
            if float(c4["high"]) <= float(c1["high"]):
                if _is_red(c5) and float(c5["close"]) < float(c1["close"]):
                    return _price_confirmation(closes)
    return False


def _breakaway_bullish_entry(candles_hist, closes, candle, params):
    """Gap down then recovery pattern."""
    if len(candles_hist) < 26:
        return False
    c1 = candles_hist[-5]
    c2 = candles_hist[-4]
    c3 = candles_hist[-3]
    c4 = candles_hist[-2]
    c5 = candle
    # Gap down on c2
    if float(c2["close"]) < float(c1["close"]) * 0.99:
        # Series of lower closes then recovery
        if _is_red(c2) and _is_red(c3) and float(c4["close"]) < float(c3["close"]):
            if _is_green(c5) and float(c5["close"]) > float(c4["close"]):
                return _price_confirmation(closes)
    return False


def _breakaway_bearish_entry(candles_hist, closes, candle, params):
    """Gap up then decline pattern — enter on bounce."""
    if len(candles_hist) < 26:
        return False
    c1 = candles_hist[-5]
    c2 = candles_hist[-4]
    c3 = candles_hist[-3]
    c4 = candles_hist[-2]
    c5 = candle
    if float(c2["close"]) > float(c1["close"]) * 1.01:
        if _is_green(c2) and _is_red(c3) and float(c4["close"]) < float(c3["close"]):
            if _is_red(c5) and float(c5["close"]) < float(c4["close"]):
                return _price_confirmation(closes)
    return False


def _abandoned_baby_entry(candles_hist, closes, candle, params):
    """Doji with gaps on both sides."""
    if len(candles_hist) < 24:
        return False
    c1 = candles_hist[-3]
    c2 = candles_hist[-2]
    c3 = candle
    gap_pct = params.get("gap_pct", 0.3)
    # c2 is doji
    if _body_pct(c2) < 0.05:
        # Gap between c1 and c2
        if _is_red(c1) and float(c2["low"]) > float(c1["high"]) * (1 + gap_pct / 100):
            # Gap between c2 and c3
            if _is_green(c3) and float(c3["open"]) > float(c2["high"]) * (1 + gap_pct / 100):
                return _price_confirmation(closes)
    return False


def _kick_pattern_entry(candles_hist, closes, candle, params):
    """Strong reversal with marubozu."""
    if len(candles_hist) < 22:
        return False
    prev = candles_hist[-2]
    curr = candle
    rng = _candle_range(curr)
    tol = params.get("wick_tol", 0.03)
    if rng > 0 and _upper_wick(curr) / rng < tol and _lower_wick(curr) / rng < tol:
        if _is_red(prev) and _is_green(curr):
            # Strong reversal
            return _price_confirmation(closes)
    return False


def _tower_bottom_entry(candles_hist, closes, candle, params):
    """Series of small candles at bottom (4+ consecutive)."""
    if len(candles_hist) < 26:
        return False
    num_tower = params.get("num_tower", 4)
    avg_body = _avg_body(candles_hist, 10)
    # Check last num_tower candles all have small bodies
    small_count = 0
    for i in range(num_tower, 0, -1):
        idx = -i if i > 0 else -1
        if idx >= -len(candles_hist):
            c = candles_hist[idx]
            if _body_size(c) < avg_body * 0.4:
                small_count += 1
    if small_count >= num_tower:
        if _is_green(candle) and float(candle["close"]) > float(candles_hist[-2]["close"]):
            return _price_confirmation(closes)
    return False


def _tower_top_entry(candles_hist, closes, candle, params):
    """Series of small candles at top — enter on breakout."""
    if len(candles_hist) < 26:
        return False
    num_tower = params.get("num_tower", 4)
    avg_body = _avg_body(candles_hist, 10)
    small_count = 0
    for i in range(num_tower, 0, -1):
        idx = -i if i > 0 else -1
        if idx >= -len(candles_hist):
            c = candles_hist[idx]
            if _body_size(c) < avg_body * 0.4:
                small_count += 1
    if small_count >= num_tower:
        if _is_green(candle):
            return _price_confirmation(closes)
    return False


def _river_bottom_entry(candles_hist, closes, candle, params):
    """Multiple dojis at support level."""
    if len(candles_hist) < 26:
        return False
    num_dojis = params.get("num_dojis", 3)
    doji_thresh = params.get("doji_thresh", 0.08)
    doji_count = 0
    for i in range(num_dojis + 1, 1, -1):
        idx = -i
        if idx >= -len(candles_hist):
            if _body_pct(candles_hist[idx]) < doji_thresh:
                doji_count += 1
    if doji_count >= num_dojis:
        if _is_green(candle):
            return _price_confirmation(closes)
    return False


def _river_top_entry(candles_hist, closes, candle, params):
    """Multiple dojis at resistance level."""
    if len(candles_hist) < 26:
        return False
    num_dojis = params.get("num_dojis", 3)
    doji_thresh = params.get("doji_thresh", 0.08)
    doji_count = 0
    for i in range(num_dojis + 1, 1, -1):
        idx = -i
        if idx >= -len(candles_hist):
            if _body_pct(candles_hist[idx]) < doji_thresh:
                doji_count += 1
    if doji_count >= num_dojis:
        if _is_green(candle) and float(candle["close"]) > max(
            float(candles_hist[-j]["close"]) for j in range(2, num_dojis + 2)
        ):
            return _price_confirmation(closes)
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

CANDLE_STRATEGIES = [
    # Single-bar reversals
    {"name": "hammer", "params": {"wick_ratio": 2.0, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "inverted_hammer", "params": {"wick_ratio": 2.0, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "hanging_man", "params": {"wick_ratio": 2.0, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "shooting_star", "params": {"wick_ratio": 2.0, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "doji", "params": {"doji_thresh": 0.05, "tp_pct": 4, "sl_pct": 3, "max_hold": 24}},
    {"name": "dragonfly_doji", "params": {"doji_thresh": 0.05, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "gravestone_doji", "params": {"doji_thresh": 0.05, "tp_pct": 4, "sl_pct": 3, "max_hold": 24}},
    {"name": "long_legged_doji", "params": {"doji_thresh": 0.05, "tp_pct": 4, "sl_pct": 3, "max_hold": 24}},
    {"name": "spinning_top", "params": {"tp_pct": 4, "sl_pct": 3, "max_hold": 24}},
    {"name": "marubozu", "params": {"wick_tol": 0.02, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Two-bar patterns
    {"name": "bullish_engulfing", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "bearish_engulfing_reversal", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "harami", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "harami_cross", "params": {"doji_thresh": 0.05, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "piercing_line", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "dark_cloud_cover", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "tweezers_bottom", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "tweezers_top", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "inside_bar", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "outside_bar", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "belt_hold_bullish", "params": {"tol": 0.01, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "belt_hold_bearish", "params": {"tol": 0.01, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "separating_lines", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "counterattack", "params": {"tol": 0.005, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "thrusting", "params": {"tp_pct": 4, "sl_pct": 3, "max_hold": 24}},
    {"name": "in_on_neck", "params": {"tol": 0.01, "tp_pct": 4, "sl_pct": 3, "max_hold": 24}},

    # Three-bar patterns
    {"name": "morning_star", "params": {"tp_pct": 7, "sl_pct": 3, "max_hold": 24}},
    {"name": "evening_star", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "three_white_soldiers", "params": {"tp_pct": 8, "sl_pct": 4, "max_hold": 24}},
    {"name": "three_black_crows", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "three_inside_up", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "three_inside_down", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "three_outside_up", "params": {"tp_pct": 7, "sl_pct": 3, "max_hold": 24}},
    {"name": "three_outside_down", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "unique_three", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Gap patterns
    {"name": "upside_gap", "params": {"gap_pct": 0.5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "downside_gap", "params": {"gap_pct": 0.5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "abandoned_baby", "params": {"gap_pct": 0.3, "tp_pct": 7, "sl_pct": 3, "max_hold": 24}},
    {"name": "kick_pattern", "params": {"wick_tol": 0.03, "tp_pct": 8, "sl_pct": 4, "max_hold": 24}},

    # Multi-bar patterns
    {"name": "mat_hold", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "advance_block", "params": {"tp_pct": 4, "sl_pct": 3, "max_hold": 24}},
    {"name": "stalled_pattern", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "rising_three_methods", "params": {"tp_pct": 7, "sl_pct": 3, "max_hold": 24}},
    {"name": "falling_three_methods", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakaway_bullish", "params": {"tp_pct": 7, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakaway_bearish", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 24}},

    # Tower and river patterns
    {"name": "tower_bottom", "params": {"num_tower": 4, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "tower_top", "params": {"num_tower": 4, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "river_bottom", "params": {"num_dojis": 3, "doji_thresh": 0.08, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    {"name": "river_top", "params": {"num_dojis": 3, "doji_thresh": 0.08, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "hammer": _hammer_entry,
    "inverted_hammer": _inverted_hammer_entry,
    "hanging_man": _hanging_man_entry,
    "shooting_star": _shooting_star_entry,
    "doji": _doji_entry,
    "dragonfly_doji": _dragonfly_doji_entry,
    "gravestone_doji": _gravestone_doji_entry,
    "long_legged_doji": _long_legged_doji_entry,
    "spinning_top": _spinning_top_entry,
    "marubozu": _marubozu_entry,
    "bullish_engulfing": _bullish_engulfing_entry,
    "bearish_engulfing_reversal": _bearish_engulfing_reversal_entry,
    "harami": _harami_entry,
    "harami_cross": _harami_cross_entry,
    "piercing_line": _piercing_line_entry,
    "dark_cloud_cover": _dark_cloud_cover_entry,
    "tweezers_bottom": _tweezers_bottom_entry,
    "tweezers_top": _tweezers_top_entry,
    "inside_bar": _inside_bar_entry,
    "outside_bar": _outside_bar_entry,
    "belt_hold_bullish": _belt_hold_bullish_entry,
    "belt_hold_bearish": _belt_hold_bearish_entry,
    "separating_lines": _separating_lines_entry,
    "counterattack": _counterattack_entry,
    "thrusting": _thrusting_entry,
    "in_on_neck": _in_on_neck_entry,
    "morning_star": _morning_star_entry,
    "evening_star": _evening_star_entry,
    "three_white_soldiers": _three_white_soldiers_entry,
    "three_black_crows": _three_black_crows_entry,
    "three_inside_up": _three_inside_up_entry,
    "three_inside_down": _three_inside_down_entry,
    "three_outside_up": _three_outside_up_entry,
    "three_outside_down": _three_outside_down_entry,
    "unique_three": _unique_three_entry,
    "upside_gap": _upside_gap_entry,
    "downside_gap": _downside_gap_entry,
    "abandoned_baby": _abandoned_baby_entry,
    "kick_pattern": _kick_pattern_entry,
    "mat_hold": _mat_hold_entry,
    "advance_block": _advance_block_entry,
    "stalled_pattern": _stalled_pattern_entry,
    "rising_three_methods": _rising_three_methods_entry,
    "falling_three_methods": _falling_three_methods_entry,
    "breakaway_bullish": _breakaway_bullish_entry,
    "breakaway_bearish": _breakaway_bearish_entry,
    "tower_bottom": _tower_bottom_entry,
    "tower_top": _tower_top_entry,
    "river_bottom": _river_bottom_entry,
    "river_top": _river_top_entry,
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
    print(f"CANDLE PATTERN 50 STRATEGY SWEEP — Batch #4")
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
    print(f"Testing {len(CANDLE_STRATEGIES)} candle pattern strategies...\n")

    results = []
    total_tests = len(all_candles) * len(CANDLE_STRATEGIES)
    test_count = 0

    for strat_def in CANDLE_STRATEGIES:
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

    out_path = Path(__file__).parent.parent / "reports" / "candle_pattern_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 CANDLE PATTERN STRATEGIES:")
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
