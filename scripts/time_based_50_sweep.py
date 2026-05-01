#!/usr/bin/env python3
"""
Time-Based 50 Strategy Sweep — Temporal Edge Discovery.

Tests 50 unique time-based/temporal strategies across 35 coins on 7d data
for fast edge discovery. Top candidates get promoted for 30d validation.

Strategy variants cover:
- Session-based entries (open/close, market hours)
- Hour-of-day patterns (midnight, noon, prime hours)
- Day-of-week effects (weekend, Monday, Friday)
- Intra-hour timing (first/second half, quarters)
- Cycle-based timing (daily, weekly, monthly, lunar)
- Mathematical timing (Fibonacci, golden ratio, harmonic, primes)
- Sequence-based timing (candle age, run length, trend duration)
- Time-decay and momentum timing
- Circadian and microstructure proxies

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
# TIME HELPER FUNCTIONS
# ==========================================

def get_candle_hour(candle):
    """Extract UTC hour from candle timestamp."""
    ts = int(candle.get("start", candle.get("time", 0)))
    return datetime.fromtimestamp(ts, tz=timezone.utc).hour


def get_candle_minute(candle):
    """Extract UTC minute from candle timestamp."""
    ts = int(candle.get("start", candle.get("time", 0)))
    return datetime.fromtimestamp(ts, tz=timezone.utc).minute


def get_candle_day_of_week(candle):
    """Extract day of week (0=Monday, 6=Sunday)."""
    ts = int(candle.get("start", candle.get("time", 0)))
    return datetime.fromtimestamp(ts, tz=timezone.utc).weekday()


def get_candle_day_of_month(candle):
    """Extract day of month."""
    ts = int(candle.get("start", candle.get("time", 0)))
    return datetime.fromtimestamp(ts, tz=timezone.utc).day


def bars_since_signal(closes, threshold=0.01):
    """Count bars since last significant price move."""
    count = 0
    for i in range(len(closes) - 2, -1, -1):
        count += 1
        if abs(closes[i + 1] / closes[i] - 1) > threshold:
            return count
    return count


def consecutive_same_direction(closes):
    """Count consecutive same-direction candles at end of series."""
    if len(closes) < 2:
        return 0, "none"
    direction = "up" if closes[-1] > closes[-2] else "down"
    count = 1
    for i in range(len(closes) - 3, -1, -1):
        if direction == "up" and closes[i + 1] > closes[i]:
            count += 1
        elif direction == "down" and closes[i + 1] < closes[i]:
            count += 1
        else:
            break
    return count, direction


def bars_since_high(candles_hist, lookback=50):
    """Count bars since recent high."""
    if len(candles_hist) < lookback:
        return 0
    window = candles_hist[-lookback:]
    highs = [float(c["high"]) for c in window]
    high_idx = highs.index(max(highs))
    return len(window) - 1 - high_idx


def bars_since_low(candles_hist, lookback=50):
    """Count bars since recent low."""
    if len(candles_hist) < lookback:
        return 0
    window = candles_hist[-lookback:]
    lows = [float(c["low"]) for c in window]
    low_idx = lows.index(min(lows))
    return len(window) - 1 - low_idx


def trend_duration(closes, min_bars=3):
    """Count how many bars the current trend has persisted."""
    if len(closes) < min_bars + 1:
        return 0
    direction = 1 if closes[-1] > closes[-2] else -1
    count = 1
    for i in range(len(closes) - 3, -1, -1):
        d = 1 if closes[i + 1] > closes[i] else -1
        if d == direction:
            count += 1
        else:
            break
    return count


def consolidation_length(candles_hist, threshold_pct=0.005, lookback=30):
    """Count consecutive bars with tight range."""
    if len(candles_hist) < 2:
        return 0
    count = 0
    for i in range(len(candles_hist) - 1, max(0, len(candles_hist) - lookback) - 1, -1):
        c = candles_hist[i]
        h = float(c["high"])
        l = float(c["low"])
        if h > 0 and (h - l) / h < threshold_pct:
            count += 1
        else:
            break
    return count


def price_acceleration(closes, n=3):
    """Compute price acceleration (change of rate of change)."""
    if len(closes) < n + 2:
        return 0
    roc1 = closes[-1] / closes[-2] - 1 if closes[-2] != 0 else 0
    roc2 = closes[-2] / closes[-3] - 1 if closes[-3] != 0 else 0
    roc3 = closes[-3] / closes[-4] - 1 if len(closes) > 3 and closes[-4] != 0 else 0
    return (roc1 - roc2) + (roc2 - roc3)


# ==========================================
# TIME-BASED STRATEGY ENTRY FUNCTIONS
# ==========================================

def _session_open_entry(candles_hist, closes, candle, params):
    """Enter at start of trading sessions (hours 1, 7, 13, 20 UTC)."""
    if len(candles_hist) < 10:
        return False
    session_hours = params.get("session_hours", [1, 7, 13, 20])
    hour = get_candle_hour(candle)
    if hour in session_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _session_close_entry(candles_hist, closes, candle, params):
    """Enter near session end (hours 5, 11, 18, 23 UTC)."""
    if len(candles_hist) < 10:
        return False
    close_hours = params.get("close_hours", [5, 11, 18, 23])
    hour = get_candle_hour(candle)
    if hour in close_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _us_market_open_entry(candles_hist, closes, candle, params):
    """Enter at 14:30 UTC (9:30am ET US market open)."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    minute = get_candle_minute(candle)
    # 5-min candles: check if hour is 14 and minute is 25-34
    if hour == 14 and 25 <= minute <= 35:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _us_market_close_entry(candles_hist, closes, candle, params):
    """Enter at 21:00 UTC (4pm ET US market close)."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    minute = get_candle_minute(candle)
    if hour == 21 and minute <= 5:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _asia_open_entry(candles_hist, closes, candle, params):
    """Enter at 00:00 UTC (Tokyo/Sydney open)."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    if hour == 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _london_open_entry(candles_hist, closes, candle, params):
    """Enter at 08:00 UTC (London open)."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    if hour == 8:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _hour_0_entry(candles_hist, closes, candle, params):
    """Enter at midnight UTC."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    if hour == 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _hour_12_entry(candles_hist, closes, candle, params):
    """Enter at noon UTC."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    if hour == 12:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _weekend_effect_entry(candles_hist, closes, candle, params):
    """Enter on Saturday/Sunday."""
    if len(candles_hist) < 10:
        return False
    dow = get_candle_day_of_week(candle)
    if dow >= 5:  # 5=Saturday, 6=Sunday
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _monday_effect_entry(candles_hist, closes, candle, params):
    """Enter on Monday."""
    if len(candles_hist) < 10:
        return False
    dow = get_candle_day_of_week(candle)
    if dow == 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _friday_effect_entry(candles_hist, closes, candle, params):
    """Enter on Friday."""
    if len(candles_hist) < 10:
        return False
    dow = get_candle_day_of_week(candle)
    if dow == 4:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _turn_of_month_entry(candles_hist, closes, candle, params):
    """Enter near month boundary (days 1-3 or 28-31)."""
    if len(candles_hist) < 10:
        return False
    dom = get_candle_day_of_month(candle)
    if dom <= 3 or dom >= 28:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _first_half_hour_entry(candles_hist, closes, candle, params):
    """Enter in first 30 min of each hour."""
    if len(candles_hist) < 10:
        return False
    minute = get_candle_minute(candle)
    if minute < 30:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _second_half_hour_entry(candles_hist, closes, candle, params):
    """Enter in second 30 min of each hour."""
    if len(candles_hist) < 10:
        return False
    minute = get_candle_minute(candle)
    if minute >= 30:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _first_quarter_hour_entry(candles_hist, closes, candle, params):
    """Enter in first 15 min of each hour."""
    if len(candles_hist) < 10:
        return False
    minute = get_candle_minute(candle)
    if minute < 15:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _last_quarter_hour_entry(candles_hist, closes, candle, params):
    """Enter in last 15 min of each hour."""
    if len(candles_hist) < 10:
        return False
    minute = get_candle_minute(candle)
    if minute >= 45:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _odd_hours_entry(candles_hist, closes, candle, params):
    """Enter on odd-numbered hours."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    if hour % 2 == 1:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _even_hours_entry(candles_hist, closes, candle, params):
    """Enter on even-numbered hours."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    if hour % 2 == 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _morning_momentum_entry(candles_hist, closes, candle, params):
    """Enter 09:00-11:00 UTC with upward bias."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    if 9 <= hour <= 11:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _afternoon_reversal_entry(candles_hist, closes, candle, params):
    """Enter 14:00-16:00 UTC with reversal bias (enter after pullback)."""
    if len(candles_hist) < 20:
        return False
    hour = get_candle_hour(candle)
    if 14 <= hour <= 16:
        # Look for pullback then recovery
        if len(closes) > 5:
            pullback = closes[-3] < closes[-5]
            recovery = closes[-1] > closes[-2]
            if pullback and recovery:
                return True
    return False


def _overnight_drift_entry(candles_hist, closes, candle, params):
    """Enter 00:00-06:00 UTC (overnight session drift)."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    if 0 <= hour < 6:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _intraday_mean_revert_entry(candles_hist, closes, candle, params):
    """Enter during high-volume hours (13:00-21:00 UTC) on mean reversion."""
    if len(candles_hist) < 20:
        return False
    hour = get_candle_hour(candle)
    if 13 <= hour <= 21:
        # Enter after a dip during active hours
        if len(closes) > 5 and closes[-1] < closes[-3] and closes[-1] > closes[-2]:
            return True
    return False


def _hourly_cycle_entry(candles_hist, closes, candle, params):
    """Enter every N hours (cycle-based)."""
    if len(candles_hist) < 10:
        return False
    cycle = params.get("cycle_hours", 4)
    hour = get_candle_hour(candle)
    if hour % cycle == 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _daily_cycle_entry(candles_hist, closes, candle, params):
    """Enter once per day at optimal hour."""
    if len(candles_hist) < 10:
        return False
    optimal_hour = params.get("optimal_hour", 14)
    hour = get_candle_hour(candle)
    minute = get_candle_minute(candle)
    if hour == optimal_hour and minute < 5:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _weekly_cycle_entry(candles_hist, closes, candle, params):
    """Enter once per week (Monday open)."""
    if len(candles_hist) < 10:
        return False
    dow = get_candle_day_of_week(candle)
    hour = get_candle_hour(candle)
    minute = get_candle_minute(candle)
    if dow == 0 and hour == 0 and minute < 10:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _biweekly_cycle_entry(candles_hist, closes, candle, params):
    """Enter every 2 weeks."""
    if len(candles_hist) < 10:
        return False
    dom = get_candle_day_of_month(candle)
    hour = get_candle_hour(candle)
    minute = get_candle_minute(candle)
    # Enter on 1st and 15th
    if dom in (1, 15) and hour == 0 and minute < 10:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _monthly_cycle_entry(candles_hist, closes, candle, params):
    """Enter once per month (1st of month)."""
    if len(candles_hist) < 10:
        return False
    dom = get_candle_day_of_month(candle)
    hour = get_candle_hour(candle)
    minute = get_candle_minute(candle)
    if dom == 1 and hour == 0 and minute < 10:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _lunar_cycle_entry(candles_hist, closes, candle, params):
    """Enter on 28-day cycle (approximate lunar)."""
    if len(candles_hist) < 10:
        return False
    ts = int(candle.get("start", candle.get("time", 0)))
    day_in_cycle = (ts // 86400) % 28
    hour = get_candle_hour(candle)
    if day_in_cycle == 0 and hour == 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _fibonacci_hours_entry(candles_hist, closes, candle, params):
    """Enter at Fibonacci-spaced hour intervals (0, 1, 1, 2, 3, 5, 8, 13, 21)."""
    if len(candles_hist) < 10:
        return False
    fib_hours = {0, 1, 2, 3, 5, 8, 13, 21}
    hour = get_candle_hour(candle)
    if hour in fib_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _golden_ratio_timing_entry(candles_hist, closes, candle, params):
    """Enter at 0.618 fraction of day (~14:50 UTC)."""
    if len(candles_hist) < 10:
        return False
    hour = get_candle_hour(candle)
    minute = get_candle_minute(candle)
    # 0.618 * 24 = 14.832 -> ~14:50
    if hour == 14 and 45 <= minute <= 55:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _harmonic_timing_entry(candles_hist, closes, candle, params):
    """Enter at harmonic fractions of day (1/3=8:00, 1/4=6:00, 1/6=4:00)."""
    if len(candles_hist) < 10:
        return False
    harmonic_hours = {4, 6, 8, 12, 16, 18}  # 1/6, 1/4, 1/3, 1/2, 2/3, 3/4
    hour = get_candle_hour(candle)
    if hour in harmonic_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _prime_hours_entry(candles_hist, closes, candle, params):
    """Enter at prime-numbered hours (2, 3, 5, 7, 11, 13, 17, 19, 23)."""
    if len(candles_hist) < 10:
        return False
    prime_hours = {2, 3, 5, 7, 11, 13, 17, 19, 23}
    hour = get_candle_hour(candle)
    if hour in prime_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _power_of_2_hours_entry(candles_hist, closes, candle, params):
    """Enter at hours 0, 1, 2, 4, 8, 16."""
    if len(candles_hist) < 10:
        return False
    pow2_hours = {0, 1, 2, 4, 8, 16}
    hour = get_candle_hour(candle)
    if hour in pow2_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _triangular_numbers_entry(candles_hist, closes, candle, params):
    """Enter at triangular number hours (0, 1, 3, 6, 10, 15, 21)."""
    if len(candles_hist) < 10:
        return False
    tri_hours = {0, 1, 3, 6, 10, 15, 21}
    hour = get_candle_hour(candle)
    if hour in tri_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _square_hours_entry(candles_hist, closes, candle, params):
    """Enter at square number hours (0, 1, 4, 9, 16)."""
    if len(candles_hist) < 10:
        return False
    sq_hours = {0, 1, 4, 9, 16}
    hour = get_candle_hour(candle)
    if hour in sq_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _candle_age_entry(candles_hist, closes, candle, params):
    """Enter based on how many candles since last significant signal."""
    if len(candles_hist) < 20:
        return False
    min_age = params.get("min_age", 10)
    age = bars_since_signal(closes, threshold=0.01)
    if age >= min_age:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _inter_candle_gap_entry(candles_hist, closes, candle, params):
    """Enter based on time gap between candles (missing candles proxy)."""
    if len(candles_hist) < 20:
        return False
    if len(candles_hist) < 2:
        return False
    last_ts = int(candles_hist[-1].get("start", candles_hist[-1].get("time", 0)))
    prev_ts = int(candles_hist[-2].get("start", candles_hist[-2].get("time", 0)))
    expected_gap = 300  # 5-min candles
    actual_gap = last_ts - prev_ts
    # Enter when gap is larger than expected (data interruption recovery)
    if actual_gap > expected_gap * 1.5:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _candle_sequence_length_entry(candles_hist, closes, candle, params):
    """Enter after N consecutive same-direction candles."""
    if len(candles_hist) < 20:
        return False
    n = params.get("consecutive", 5)
    count, direction = consecutive_same_direction(closes)
    if count >= n and direction == "up":
        return True
    return False


def _run_length_entry(candles_hist, closes, candle, params):
    """Enter after longest run of green/red candles shows exhaustion."""
    if len(candles_hist) < 30:
        return False
    max_run = params.get("max_run", 7)
    count, direction = consecutive_same_direction(closes)
    # Enter when run is long but showing first reversal sign
    if count >= max_run and len(closes) > 1:
        if direction == "down" and closes[-1] > closes[-2]:
            return True
    return False


def _time_since_high_entry(candles_hist, closes, candle, params):
    """Enter based on bars since recent high (pullback timing)."""
    if len(candles_hist) < 20:
        return False
    min_bars = params.get("min_bars_since_high", 5)
    bars = bars_since_high(candles_hist, lookback=50)
    if bars >= min_bars and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _time_since_low_entry(candles_hist, closes, candle, params):
    """Enter based on bars since recent low (recovery timing)."""
    if len(candles_hist) < 20:
        return False
    max_bars = params.get("max_bars_since_low", 10)
    bars = bars_since_low(candles_hist, lookback=50)
    if 0 < bars <= max_bars and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _time_in_trend_entry(candles_hist, closes, candle, params):
    """Enter after trend has persisted N bars (trend following)."""
    if len(candles_hist) < 20:
        return False
    min_trend = params.get("min_trend_bars", 4)
    dur = trend_duration(closes, min_bars=3)
    if dur >= min_trend and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _exhaustion_timing_entry(candles_hist, closes, candle, params):
    """Enter when trend is N bars old (exhaustion/counter-trend play)."""
    if len(candles_hist) < 30:
        return False
    exhaustion_bars = params.get("exhaustion_bars", 8)
    dur = trend_duration(closes, min_bars=3)
    # Enter when uptrend is exhausted (counter-trend short proxy -> long on reversal)
    if dur >= exhaustion_bars and len(closes) > 2 and closes[-1] > closes[-2] and closes[-2] < closes[-3]:
        return True
    return False


def _consolidation_duration_entry(candles_hist, closes, candle, params):
    """Enter after N bars of tight range (consolidation breakout setup)."""
    if len(candles_hist) < 20:
        return False
    min_consolidation = params.get("min_consolidation", 6)
    cons_len = consolidation_length(candles_hist, threshold_pct=0.005, lookback=30)
    if cons_len >= min_consolidation and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _breakout_timing_entry(candles_hist, closes, candle, params):
    """Enter after consolidation of N bars breaks."""
    if len(candles_hist) < 30:
        return False
    min_consolidation = params.get("min_consolidation", 5)
    cons_len = consolidation_length(candles_hist, threshold_pct=0.005, lookback=30)
    # Was consolidating, now breaking out
    if cons_len == 0:
        prev_cons = consolidation_length(candles_hist[:-1], threshold_pct=0.005, lookback=30)
        if prev_cons >= min_consolidation and len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _time_decay_signal_entry(candles_hist, closes, candle, params):
    """Signal strength decays with time since trigger (enter only when fresh)."""
    if len(candles_hist) < 20:
        return False
    decay_window = params.get("decay_window", 5)
    age = bars_since_signal(closes, threshold=0.005)
    # Exponential decay: only enter if signal is recent
    if age <= decay_window:
        strength = math.exp(-age / decay_window)
        if strength > 0.5 and len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _momentum_acceleration_entry(candles_hist, closes, candle, params):
    """Enter when price acceleration is positive."""
    if len(candles_hist) < 20:
        return False
    accel = price_acceleration(closes, n=3)
    if accel > 0:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _time_weighted_volume_entry(candles_hist, closes, candle, params):
    """Volume weighted by recency (recent volume matters more)."""
    if len(candles_hist) < 20:
        return False
    lookback = params.get("lookback", 10)
    if len(candles_hist) < lookback:
        return False
    weighted_vol = 0
    total_weight = 0
    for i in range(lookback):
        w = (i + 1)  # linear weighting, most recent = highest
        weighted_vol += float(candles_hist[-(lookback - i)]["volume"]) * w
        total_weight += w
    avg_weighted = weighted_vol / total_weight if total_weight > 0 else 0
    current_vol = float(candle["volume"])
    if current_vol > avg_weighted * 1.2:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _circadian_rhythm_entry(candles_hist, closes, candle, params):
    """Enter based on 24-hour biological rhythm proxy (peak at certain hours)."""
    if len(candles_hist) < 10:
        return False
    # Circadian peaks: 09:00 (morning alertness), 15:00 (afternoon peak)
    peak_hours = params.get("peak_hours", [9, 15])
    hour = get_candle_hour(candle)
    if hour in peak_hours:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _market_microstructure_entry(candles_hist, closes, candle, params):
    """Enter based on bid-ask spread patterns (volume/volatility proxy)."""
    if len(candles_hist) < 20:
        return False
    # Use high-low range as spread proxy
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])
    if h == 0:
        return False
    spread_pct = (h - l) / h
    # Enter when spread is narrowing (compression before move)
    recent_spreads = []
    for candle_hist in candles_hist[-10:]:
        hh = float(candle_hist["high"])
        ll = float(candle_hist["low"])
        if hh > 0:
            recent_spreads.append((hh - ll) / hh)
    if recent_spreads:
        avg_spread = sum(recent_spreads) / len(recent_spreads)
        if spread_pct < avg_spread * 0.7 and c > float(candle["open"]):
            return True
    return False


# ==========================================
# STRATEGY DEFINITIONS
# ==========================================

TIME_STRATEGIES = [
    # Session-based
    {"name": "session_open", "params": {"session_hours": [1, 7, 13, 20], "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "session_close", "params": {"close_hours": [5, 11, 18, 23], "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "us_market_open", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "us_market_close", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "asia_open", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "london_open", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Specific hours
    {"name": "hour_0", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "hour_12", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Day-of-week
    {"name": "weekend_effect", "params": {"tp_pct": 8, "sl_pct": 4, "max_hold": 48}},
    {"name": "monday_effect", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "friday_effect", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Monthly
    {"name": "turn_of_month", "params": {"tp_pct": 8, "sl_pct": 3, "max_hold": 48}},

    # Intra-hour
    {"name": "first_half_hour", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 12}},
    {"name": "second_half_hour", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 12}},
    {"name": "first_quarter_hour", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 12}},
    {"name": "last_quarter_hour", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 12}},

    # Parity
    {"name": "odd_hours", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "even_hours", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Time windows
    {"name": "morning_momentum", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "afternoon_reversal", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "overnight_drift", "params": {"tp_pct": 8, "sl_pct": 4, "max_hold": 48}},
    {"name": "intraday_mean_revert", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Cycles
    {"name": "hourly_cycle", "params": {"cycle_hours": 4, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "daily_cycle", "params": {"optimal_hour": 14, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "weekly_cycle", "params": {"tp_pct": 10, "sl_pct": 5, "max_hold": 96}},
    {"name": "biweekly_cycle", "params": {"tp_pct": 10, "sl_pct": 5, "max_hold": 96}},
    {"name": "monthly_cycle", "params": {"tp_pct": 12, "sl_pct": 5, "max_hold": 168}},
    {"name": "lunar_cycle", "params": {"tp_pct": 10, "sl_pct": 5, "max_hold": 96}},

    # Mathematical timing
    {"name": "fibonacci_hours", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "golden_ratio_timing", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 12}},
    {"name": "harmonic_timing", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "prime_hours", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "power_of_2_hours", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "triangular_numbers", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "square_hours", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Sequence-based
    {"name": "candle_age_entry", "params": {"min_age": 10, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "inter_candle_gap", "params": {"tp_pct": 8, "sl_pct": 4, "max_hold": 24}},
    {"name": "candle_sequence_length", "params": {"consecutive": 5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "run_length", "params": {"max_run": 7, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Time-since-event
    {"name": "time_since_high", "params": {"min_bars_since_high": 5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "time_since_low", "params": {"max_bars_since_low": 10, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "time_in_trend", "params": {"min_trend_bars": 4, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "exhaustion_timing", "params": {"exhaustion_bars": 8, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Consolidation/breakout
    {"name": "consolidation_duration", "params": {"min_consolidation": 6, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},
    {"name": "breakout_timing", "params": {"min_consolidation": 5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24}},

    # Time-decay and momentum
    {"name": "time_decay_signal", "params": {"decay_window": 5, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "momentum_acceleration", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "time_weighted_volume", "params": {"lookback": 10, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},

    # Rhythm and microstructure
    {"name": "circadian_rhythm", "params": {"peak_hours": [9, 15], "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    {"name": "market_microstructure", "params": {"tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
]

ENTRY_FUNCS = {
    "session_open": _session_open_entry,
    "session_close": _session_close_entry,
    "us_market_open": _us_market_open_entry,
    "us_market_close": _us_market_close_entry,
    "asia_open": _asia_open_entry,
    "london_open": _london_open_entry,
    "hour_0": _hour_0_entry,
    "hour_12": _hour_12_entry,
    "weekend_effect": _weekend_effect_entry,
    "monday_effect": _monday_effect_entry,
    "friday_effect": _friday_effect_entry,
    "turn_of_month": _turn_of_month_entry,
    "first_half_hour": _first_half_hour_entry,
    "second_half_hour": _second_half_hour_entry,
    "first_quarter_hour": _first_quarter_hour_entry,
    "last_quarter_hour": _last_quarter_hour_entry,
    "odd_hours": _odd_hours_entry,
    "even_hours": _even_hours_entry,
    "morning_momentum": _morning_momentum_entry,
    "afternoon_reversal": _afternoon_reversal_entry,
    "overnight_drift": _overnight_drift_entry,
    "intraday_mean_revert": _intraday_mean_revert_entry,
    "hourly_cycle": _hourly_cycle_entry,
    "daily_cycle": _daily_cycle_entry,
    "weekly_cycle": _weekly_cycle_entry,
    "biweekly_cycle": _biweekly_cycle_entry,
    "monthly_cycle": _monthly_cycle_entry,
    "lunar_cycle": _lunar_cycle_entry,
    "fibonacci_hours": _fibonacci_hours_entry,
    "golden_ratio_timing": _golden_ratio_timing_entry,
    "harmonic_timing": _harmonic_timing_entry,
    "prime_hours": _prime_hours_entry,
    "power_of_2_hours": _power_of_2_hours_entry,
    "triangular_numbers": _triangular_numbers_entry,
    "square_hours": _square_hours_entry,
    "candle_age_entry": _candle_age_entry,
    "inter_candle_gap": _inter_candle_gap_entry,
    "candle_sequence_length": _candle_sequence_length_entry,
    "run_length": _run_length_entry,
    "time_since_high": _time_since_high_entry,
    "time_since_low": _time_since_low_entry,
    "time_in_trend": _time_in_trend_entry,
    "exhaustion_timing": _exhaustion_timing_entry,
    "consolidation_duration": _consolidation_duration_entry,
    "breakout_timing": _breakout_timing_entry,
    "time_decay_signal": _time_decay_signal_entry,
    "momentum_acceleration": _momentum_acceleration_entry,
    "time_weighted_volume": _time_weighted_volume_entry,
    "circadian_rhythm": _circadian_rhythm_entry,
    "market_microstructure": _market_microstructure_entry,
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
    print(f"TIME-BASED 50 STRATEGY SWEEP — Temporal Edge Discovery")
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
    print(f"Testing {len(TIME_STRATEGIES)} time-based strategies...\n")

    results = []
    total_tests = len(all_candles) * len(TIME_STRATEGIES)
    test_count = 0

    for strat_def in TIME_STRATEGIES:
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

    out_path = Path(__file__).parent.parent / "reports" / "time_based_50_sweep_7d.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"SWEEP COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results saved to: {out_path}")
    print(f"\nTOP 10 TIME-BASED STRATEGIES:")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:>2}. {r['strategy']:<28} ${r['total_net_pnl']:>8.2f}  "
              f"Hit: {r['hit_rate']:>5.1f}%  "
              f"Profitable: {r['profitable_coins']}/{r['coins_tested']}")

    if output["promoted_for_30d"]:
        print(f"\nPROMOTED FOR 30D VALIDATION:")
        for s in output["promoted_for_30d"]:
            print(f"  -> {s}")

    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
