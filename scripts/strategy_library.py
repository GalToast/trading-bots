#!/usr/bin/env python3
"""
Strategy Library — Single source of truth for all trading strategies.

Every strategy uses IDENTICAL semantics:
- Entry at candle OPEN (with configurable slippage)
- Exit at TP/SL/timeout within the same bar
- Fees on both sides
- Session gate (dead hours: 0, 6, 12, 19 UTC)
- Deterministic fill probability (seeded RNG)
- Starting cash: configurable (default $100)

Usage:
    from strategy_library import momentum, bb_reversion, rsi_mr
    result = momentum(candles, lookback=10, tp_pct=10, sl_pct=10)
    # Returns: {net_pnl, trades, win_rate, max_drawdown, ...}
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional

SESSION_DEAD_HOURS = {0, 6, 12, 19}


def compute_rsi(closes: list[float], period: int = 3) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


def compute_ema(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def compute_bb(closes: list[float], period: int = 20, std_mult: float = 2.0):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    sma = sum(window) / period
    variance = sum((x - sma) ** 2 for x in window) / period
    std = variance ** 0.5
    return sma, sma + std_mult * std, sma - std_mult * std


def compute_atr(candles: list[dict], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    true_ranges: list[float] = []
    for idx in range(-period, 0):
        candle = candles[idx]
        prev_close = float(candles[idx - 1]["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(true_ranges) / period if true_ranges else None


def backtest(candles: list[dict], entry_fn, params: dict,
             fee_rate: float = 0.004, starting_cash: float = 100.0,
             entry_slip: float = 0.0008, exit_slip: float = 0.0,
             fill_prob: float = 1.0, seed: int = 42) -> dict:
    """
    Generic backtest engine used by all strategies.

    entry_fn: function that takes (candles_so_far, closes_so_far, current_candle, params)
              and returns True if a signal fires.
    """
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    peak = starting_cash
    max_dd = 0.0
    closes_history = []
    candles_history = []  # Full candle dicts for strategies needing high/low/volume
    signals_count = 0
    signals_filtered = 0
    signal_filtered_reason = {"session": 0, "fill": 0, "capital": 0}

    tp_pct = params.get("tp_pct", 0)
    sl_pct = params.get("sl_pct", 0)
    max_hold = params.get("max_hold", 48)

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        closes_history.append(close)
        candles_history.append(dict(c))
        if len(closes_history) > 500:
            closes_history = closes_history[-500:]
            candles_history = candles_history[-500:]

        ts = int(c.get("start", c.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in SESSION_DEAD_HOURS

        # EXIT
        if pos:
            pos["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "sl"
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                actual_exit = exit_price * (1 - exit_slip)
                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                closes_count += 1
                total_fees += entry_fee + exit_fee
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos = None

        # ENTRY
        if pos is None:
            signal = entry_fn(candles_history, closes_history, c, params)
            if signal:
                signals_count += 1

                if not session_open:
                    signals_filtered += 1
                    signal_filtered_reason["session"] += 1
                    continue

                if rng.random() > fill_prob:
                    signals_filtered += 1
                    signal_filtered_reason["fill"] += 1
                    continue

                if cash < 10.0:
                    signals_filtered += 1
                    signal_filtered_reason["capital"] += 1
                    continue

                actual_entry = candle_open * (1 + entry_slip)
                deploy = cash
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / actual_entry

                # Compute TP — if tp_pct is 0, BB strategies use dynamic TP
                if tp_pct == 0 and "bb_tp_sma" in params:
                    # BB reversion: TP = middle band (passed in params)
                    tp = params["bb_tp_sma"]
                else:
                    tp = actual_entry * (1 + tp_pct / 100.0)

                sl = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0

                cash -= deploy
                pos = {
                    "ep": actual_entry, "q": deploy, "hold": 0,
                    "tp": tp, "sl": sl, "units": units,
                    "entry_fee": entry_fee, "max_hold": max_hold,
                }

    if pos:
        last_close = float(candles[-1]["close"])
        actual_exit = last_close * (1 - exit_slip)
        units = pos["units"]
        gross = (actual_exit - pos["ep"]) * units
        entry_fee = pos["entry_fee"]
        exit_fee = actual_exit * units * fee_rate
        net = gross - entry_fee - exit_fee
        cash += pos["q"] + net
        closes_count += 1
        total_fees += entry_fee + exit_fee
        if net > 0:
            wins += 1
        else:
            losses += 1
        peak = max(peak, cash)
        dd = (peak - cash) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    net = cash - starting_cash
    wr = wins / max(closes_count, 1) * 100

    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 2),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "max_drawdown": round(max_dd * 100, 1),
        "signals": signals_count,
        "signals_filtered": signals_filtered,
        "fill_rate": round(closes_count / max(signals_count, 1) * 100, 1),
        "total_fees": round(total_fees, 2),
    }


# ---- Strategy Entry Functions ----

def _rsi_mr_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    period = params.get("rsi_period", 3)
    thresh = params.get("os_thresh", 30)
    if len(closes) < period + 2:
        return False
    return compute_rsi(closes[:-1], period) <= thresh


def _momentum_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    lookback = params.get("lookback", 20)
    if len(candles_hist) < lookback + 2:  # Need lookback bars + 1 before current candle
        return False
    current_high = float(candle["high"])
    # Exclude the current candle (which is already in candles_hist)
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    return current_high > highest


def _bb_reversion_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    bb_period = params.get("bb_period", 20)
    rsi_period = params.get("rsi_period", 3)
    rsi_thresh = params.get("rsi_thresh", 30)
    proximity_pct = params.get("proximity_pct", 3.0)

    if len(closes) < bb_period + 2:
        return False

    rsi = compute_rsi(closes[:-1], rsi_period)
    sma, upper, lower = compute_bb(closes[:-1], bb_period)
    if lower is None:
        return False

    current_price = float(candle["close"])
    proximity = (current_price - lower) / lower * 100 if lower > 0 else 999

    # Also compute TP SMA for the backtest to use
    if proximity <= proximity_pct and rsi <= rsi_thresh:
        params["bb_tp_sma"] = sma  # Pass to backtest for TP calculation

    return rsi <= rsi_thresh and proximity <= proximity_pct


def _vol_squeeze_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    bb_period = params.get("bb_period", 20)
    squeeze_thresh = params.get("squeeze_thresh", 2.0)

    if len(closes) < bb_period + 1:
        return False

    sma, upper, lower = compute_bb(closes[:-1], bb_period)
    if upper is None or lower is None:
        return False

    current_price = float(candle["close"])
    bb_width = (upper - lower) / current_price * 100

    if bb_width < squeeze_thresh and sma and current_price > sma:
        return True
    return False


def _ema_pullback_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    ema_period = params.get("ema_period", 200)
    rsi_period = params.get("rsi_period", 3)
    rsi_thresh = params.get("rsi_thresh", 40)

    if len(closes) < ema_period + 10:
        return False

    ema = compute_ema(closes, ema_period)
    if ema is None:
        return False

    current_price = float(candle["close"])
    if current_price <= ema:
        return False

    rsi = compute_rsi(closes[:-1], rsi_period)
    return rsi <= rsi_thresh


def _range_breakout_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    lookback = params.get("range_lookback", 20)
    if len(candles_hist) < lookback + 2:
        return False
    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    return current_high > highest


def _vwap_reversion_entry(candles_history: list[dict], closes_history: list[float], candle: dict, params: dict) -> bool:
    """Buy when price drops >N% below rolling average (VWAP proxy using closes)."""
    vwap_window = params.get("vwap_window", 48)
    dev_pct = params.get("vwap_dev_pct", 2.0)
    if len(closes_history) < vwap_window:
        return False
    window = closes_history[-vwap_window:]
    vwap = sum(window) / len(window)
    current_price = float(candle["close"])
    if vwap <= 0:
        return False
    dist_below = (vwap - current_price) / vwap * 100
    return dist_below >= dev_pct


def _volume_spike_reversion_entry(candles_history: list[dict], closes_history: list[float], candle: dict, params: dict) -> bool:
    """Buy when RSI is oversold AND volume spikes (capitulation)."""
    period = params.get("rsi_period", 3)
    thresh = params.get("os_thresh", 30)
    vol_mult = params.get("vol_mult", 2.0)
    vol_lookback = params.get("vol_lookback", 20)

    if len(closes_history) < max(period + 2, vol_lookback + 1):
        return False
    rsi_val = compute_rsi(closes_history[:-1], period)
    if rsi_val >= thresh:
        return False

    # Real volume spike check using candles_history
    if len(candles_history) < vol_lookback:
        return False
    recent_vols = [float(c.get("volume", 0)) for c in candles_history[-vol_lookback:-1]]
    avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 0
    current_vol = float(candle.get("volume", 0))
    if avg_vol <= 0:
        return False
    return current_vol > avg_vol * vol_mult


def _multi_tf_rsi_entry(candles_history: list[dict], closes_history: list[float], candle: dict, params: dict) -> bool:
    """Multi-timeframe RSI: RSI<30 on current TF AND on aggregated higher TF."""
    period = params.get("rsi_period", 3)
    thresh = params.get("os_thresh", 30)
    if len(closes_history) < period + 2:
        return False
    rsi_val = compute_rsi(closes_history[:-1], period)
    if rsi_val >= thresh:
        return False

    # Aggregate to higher TF (every 3 bars = 1 higher TF bar)
    higher_tf_closes = [closes_history[i] for i in range(len(closes_history) - 1) if i % 3 == 2]
    if len(higher_tf_closes) < period + 1:
        return False
    higher_tf_rsi = compute_rsi(higher_tf_closes, period)
    return higher_tf_rsi <= thresh


def _overnight_gap_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    """Buy at session close (assumed 23:55 UTC = bar index % 288 == 287)."""
    # This needs explicit bar index tracking; not viable via entry_fn alone
    return False


def _opening_range_breakout_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    opening_bars = params.get("opening_bars", 12)
    breakout_buffer_pct = params.get("breakout_buffer_pct", 0.0)
    require_green_bar = params.get("require_green_bar", True)

    if len(candles_hist) < opening_bars + 2:
        return False

    current_ts = int(candle.get("start", candle.get("time", 0)))
    current_day = datetime.fromtimestamp(current_ts, tz=timezone.utc).date()
    prior_same_day = [
        c for c in candles_hist[:-1]
        if datetime.fromtimestamp(int(c.get("start", c.get("time", 0))), tz=timezone.utc).date() == current_day
    ]
    if len(prior_same_day) < opening_bars:
        return False

    opening_window = prior_same_day[:opening_bars]
    opening_high = max(float(c["high"]) for c in opening_window)
    trigger = opening_high * (1 + breakout_buffer_pct / 100.0)

    if float(candle["high"]) <= trigger:
        return False
    if require_green_bar and float(candle["close"]) <= float(candle["open"]):
        return False
    return True


def _atr_expansion_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    """Enters when ATR is expanding and price momentum is up."""
    import math
    if len(candles_hist) < 30:
        return False
    period = params.get("atr_period", 14)
    mult = params.get("atr_mult", 1.5)

    # Compute ATR
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i - 1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)

    if len(trs) < period + 1:
        return False

    current_atr = sum(trs[-period:]) / period
    prev_atr = sum(trs[-period - 1:-1]) / period

    if current_atr > prev_atr * mult:
        if float(candles_hist[-2]["close"]) > float(candles_hist[-3]["close"]):
            return True
    return False


def _regime_gated_momentum_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    lookback = params.get("lookback", 20)
    ema_period = params.get("ema_period", 50)
    atr_period = params.get("atr_period", 14)
    trend_lookback = params.get("trend_lookback", 12)
    min_atr_pct = params.get("min_atr_pct", 1.0)
    min_trend_pct = params.get("min_trend_pct", 1.0)
    min_ema_slope_pct = params.get("min_ema_slope_pct", 0.02)

    required = max(lookback + 2, ema_period + 2, atr_period + 2, trend_lookback + 2)
    if len(candles_hist) < required:
        return False

    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    if current_high <= highest:
        return False

    ema_now = compute_ema(closes[:-1], ema_period)
    ema_prev = compute_ema(closes[:-2], ema_period)
    if ema_now is None or ema_prev is None or ema_prev <= 0:
        return False
    if float(candle["close"]) <= ema_now:
        return False

    ema_slope_pct = (ema_now - ema_prev) / ema_prev * 100
    if ema_slope_pct < min_ema_slope_pct:
        return False

    base_close = closes[-(trend_lookback + 1)]
    if base_close <= 0:
        return False
    trend_pct = (float(candle["close"]) - base_close) / base_close * 100
    if trend_pct < min_trend_pct:
        return False

    atr = compute_atr(candles_hist[:-1], atr_period)
    ref_close = float(candles_hist[-2]["close"])
    if atr is None or ref_close <= 0:
        return False
    atr_pct = atr / ref_close * 100
    if atr_pct < min_atr_pct:
        return False
    return True


def _keltner_breakout_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    """Enters on Keltner channel upper band breakout."""
    import math
    if len(candles_hist) < 30:
        return False
    period = params.get("k_period", 20)
    mult = params.get("k_mult", 2.0)

    past_closes = closes[:-1]
    if len(past_closes) < period:
        return False

    # Simple SMA for Keltner midline
    ema = sum(past_closes[-period:]) / period

    # Compute ATR
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i - 1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)

    if len(trs) < period:
        return False

    atr = sum(trs[-period:]) / period
    upper_band = ema + (atr * mult)

    # Enter if previous close broke above Keltner upper band
    if past_closes[-1] > upper_band and past_closes[-2] <= upper_band:
        return True
    return False


def _hist_vol_squeeze_entry(candles_hist: list[dict], closes: list[float], candle: dict, params: dict) -> bool:
    """Enters when historical volatility compresses to low levels (squeeze)."""
    import math
    if len(closes) < 30:
        return False
    period = params.get("hv_period", 20)

    past_closes = closes[:-1]
    if len(past_closes) < period + 1:
        return False

    # Compute log returns
    returns = []
    for i in range(1, len(past_closes)):
        if past_closes[i - 1] > 0:
            returns.append(math.log(past_closes[i] / past_closes[i - 1]))
        else:
            returns.append(0)

    recent_rets = returns[-period:]
    mean_ret = sum(recent_rets) / period
    variance = sum((r - mean_ret) ** 2 for r in recent_rets) / period
    hv = math.sqrt(variance)

    # Compare to longer lookback to detect squeeze
    prev_rets = returns[-period - 5:-5]
    if len(prev_rets) < period:
        return False

    prev_mean = sum(prev_rets) / len(prev_rets)
    prev_variance = sum((r - prev_mean) ** 2 for r in prev_rets) / len(prev_rets)
    prev_hv = math.sqrt(prev_variance)

    # Enter if current HV is significantly lower than recent HV (squeeze)
    if hv < prev_hv * 0.5:
        return True
    return False


# ---- Public API: Each strategy returns a configured backtest result ----

def rsi_mr(candles: list[dict], rsi_period: int = 3, os_thresh: int = 30,
           tp_pct: float = 25.0, sl_pct: float = 0.0, max_hold: int = 48,
           fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"rsi_period": rsi_period, "os_thresh": os_thresh, "tp_pct": tp_pct,
              "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _rsi_mr_entry, params, fee_rate, starting_cash, **kw)


def bb_reversion(candles: list[dict], bb_period: int = 20, rsi_period: int = 3,
                 rsi_thresh: int = 30, proximity_pct: float = 3.0,
                 sl_pct: float = 5.0, max_hold: int = 24,
                 fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"bb_period": bb_period, "rsi_period": rsi_period, "rsi_thresh": rsi_thresh,
              "proximity_pct": proximity_pct, "sl_pct": sl_pct, "max_hold": max_hold,
              "tp_pct": 0}  # 0 = dynamic TP from BB middle
    return backtest(candles, _bb_reversion_entry, params, fee_rate, starting_cash, **kw)


def vol_squeeze(candles: list[dict], bb_period: int = 20,
                squeeze_thresh: float = 2.0, tp_pct: float = 5.0,
                sl_pct: float = 3.0, max_hold: int = 48,
                fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"bb_period": bb_period, "squeeze_thresh": squeeze_thresh,
              "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _vol_squeeze_entry, params, fee_rate, starting_cash, **kw)


def ema_pullback(candles: list[dict], ema_period: int = 200, rsi_period: int = 3,
                 rsi_thresh: int = 40, tp_pct: float = 5.0, sl_pct: float = 5.0,
                 max_hold: int = 48, fee_rate: float = 0.004,
                 starting_cash: float = 100.0, **kw) -> dict:
    params = {"ema_period": ema_period, "rsi_period": rsi_period, "rsi_thresh": rsi_thresh,
              "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _ema_pullback_entry, params, fee_rate, starting_cash, **kw)


def range_breakout(candles: list[dict], range_lookback: int = 20,
                   tp_pct: float = 5.0, sl_pct: float = 3.0, max_hold: int = 48,
                   fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"range_lookback": range_lookback, "tp_pct": tp_pct,
              "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _range_breakout_entry, params, fee_rate, starting_cash, **kw)


def vwap_reversion(candles: list[dict], vwap_window: int = 48, vwap_dev_pct: float = 2.0,
                   tp_pct: float = 5.0, sl_pct: float = 3.0, max_hold: int = 24,
                   fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"vwap_window": vwap_window, "vwap_dev_pct": vwap_dev_pct, "tp_pct": tp_pct,
              "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _vwap_reversion_entry, params, fee_rate, starting_cash, **kw)


def volume_spike_reversion(candles: list[dict], rsi_period: int = 3, os_thresh: int = 30,
                           vol_mult: float = 2.0, vol_lookback: int = 20,
                           tp_pct: float = 15.0, sl_pct: float = 5.0, max_hold: int = 36,
                           fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"rsi_period": rsi_period, "os_thresh": os_thresh, "vol_mult": vol_mult,
              "vol_lookback": vol_lookback, "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _volume_spike_reversion_entry, params, fee_rate, starting_cash, **kw)


def multi_tf_rsi(candles: list[dict], rsi_period: int = 3, os_thresh: int = 30,
                 tp_pct: float = 20.0, sl_pct: float = 5.0, max_hold: int = 36,
                 fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"rsi_period": rsi_period, "os_thresh": os_thresh, "tp_pct": tp_pct,
              "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _multi_tf_rsi_entry, params, fee_rate, starting_cash, **kw)


def opening_range_breakout(candles: list[dict], opening_bars: int = 12,
                           breakout_buffer_pct: float = 0.0, require_green_bar: bool = True,
                           tp_pct: float = 8.0, sl_pct: float = 4.0, max_hold: int = 24,
                           fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {
        "opening_bars": opening_bars,
        "breakout_buffer_pct": breakout_buffer_pct,
        "require_green_bar": require_green_bar,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "max_hold": max_hold,
    }
    return backtest(candles, _opening_range_breakout_entry, params, fee_rate, starting_cash, **kw)


def regime_gated_momentum(candles: list[dict], lookback: int = 20,
                          ema_period: int = 50, atr_period: int = 14, trend_lookback: int = 12,
                          min_atr_pct: float = 1.0, min_trend_pct: float = 1.0,
                          min_ema_slope_pct: float = 0.02, tp_pct: float = 8.0,
                          sl_pct: float = 4.0, max_hold: int = 24,
                          fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {
        "lookback": lookback,
        "ema_period": ema_period,
        "atr_period": atr_period,
        "trend_lookback": trend_lookback,
        "min_atr_pct": min_atr_pct,
        "min_trend_pct": min_trend_pct,
        "min_ema_slope_pct": min_ema_slope_pct,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "max_hold": max_hold,
    }
    return backtest(candles, _regime_gated_momentum_entry, params, fee_rate, starting_cash, **kw)


def atr_expansion(candles: list[dict], atr_period: int = 14, atr_mult: float = 1.5,
                  tp_pct: float = 8.0, sl_pct: float = 4.0, max_hold: int = 24,
                  fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"atr_period": atr_period, "atr_mult": atr_mult, "tp_pct": tp_pct,
              "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _atr_expansion_entry, params, fee_rate, starting_cash, **kw)


def keltner_breakout(candles: list[dict], k_period: int = 20, k_mult: float = 2.0,
                     tp_pct: float = 6.0, sl_pct: float = 3.0, max_hold: int = 24,
                     fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"k_period": k_period, "k_mult": k_mult, "tp_pct": tp_pct,
              "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _keltner_breakout_entry, params, fee_rate, starting_cash, **kw)


def hist_vol_squeeze(candles: list[dict], hv_period: int = 20,
                     tp_pct: float = 10.0, sl_pct: float = 5.0, max_hold: int = 36,
                     fee_rate: float = 0.004, starting_cash: float = 100.0, **kw) -> dict:
    params = {"hv_period": hv_period, "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hold": max_hold}
    return backtest(candles, _hist_vol_squeeze_entry, params, fee_rate, starting_cash, **kw)


# Momentum needs full candle access for lookback high comparison
def momentum(candles: list[dict], lookback: int = 20, tp_pct: float = 10.0,
             sl_pct: float = 5.0, max_hold: int = 48,
             fee_rate: float = 0.004, starting_cash: float = 100.0,
             entry_slip: float = 0.0008, exit_slip: float = 0.0,
             fill_prob: float = 1.0, seed: int = 42) -> dict:
    """Momentum breakout — entry when price breaks above N-bar high."""
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    total_fees = 0.0
    peak = starting_cash
    max_dd = 0.0
    signals_count = 0

    for i in range(len(candles)):
        c = candles[i]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        ts = int(c.get("start", c.get("time", 0)))
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in SESSION_DEAD_HOURS

        # EXIT
        if pos:
            pos["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= pos["tp"]:
                exit_price = pos["tp"]
                exit_reason = "tp"
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
                exit_reason = "sl"
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                actual_exit = exit_price * (1 - exit_slip)
                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee

                cash += pos["q"] + net
                closes_count += 1
                total_fees += entry_fee + exit_fee
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos = None

        # ENTRY: breakout
        if pos is None and session_open and cash >= 10.0 and i >= lookback:
            highest = max(float(candles[j]["high"]) for j in range(i - lookback, i))
            if high > highest:
                signals_count += 1
                if rng.random() > fill_prob:
                    continue

                actual_entry = candle_open * (1 + entry_slip)
                deploy = cash
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / actual_entry
                tp = actual_entry * (1 + tp_pct / 100.0)
                sl = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0
                cash -= deploy
                pos = {
                    "ep": actual_entry, "q": deploy, "hold": 0,
                    "tp": tp, "sl": sl, "units": units,
                    "entry_fee": entry_fee, "max_hold": max_hold,
                }

    if pos:
        last_close = float(candles[-1]["close"])
        actual_exit = last_close * (1 - exit_slip)
        units = pos["units"]
        gross = (actual_exit - pos["ep"]) * units
        entry_fee = pos["entry_fee"]
        exit_fee = actual_exit * units * fee_rate
        net = gross - entry_fee - exit_fee
        cash += pos["q"] + net
        closes_count += 1
        total_fees += entry_fee + exit_fee
        if net > 0:
            wins += 1
        else:
            losses += 1
        peak = max(peak, cash)
        dd = (peak - cash) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    net = cash - starting_cash
    wr = wins / max(closes_count, 1) * 100

    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 2),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "max_drawdown": round(max_dd * 100, 1),
        "signals": signals_count,
        "fill_rate": round(closes_count / max(signals_count, 1) * 100, 1),
        "total_fees": round(total_fees, 2),
    }


# ---- Strategy Registry for Sweep ----
# Maps strategy name to (entry_func, default_params)

STRATEGY_REGISTRY = {
    "rsi_mr": {"entry": "_rsi_mr_entry", "params": {"rsi_period": 3, "os_thresh": 30, "tp_pct": 25, "sl_pct": 0, "max_hold": 48}},
    "momentum_breakout": {"entry": "_momentum_entry", "params": {"lookback": 10, "tp_pct": 10, "sl_pct": 3, "max_hold": 24}},
    "ema_pullback": {"entry": "_ema_pullback_entry", "params": {"tp_pct": 10, "sl_pct": 5, "max_hold": 30, "pullback_thresh": 0.01}},
    "bb_reversion": {"entry": "_bb_reversion_entry", "params": {"sl_pct": 5, "max_hold": 48}},
    "volatility_squeeze": {"entry": "_vol_squeeze_entry", "params": {"tp_pct": 12, "sl_pct": 6, "max_hold": 36}},
    "range_breakout": {"entry": "_range_breakout_entry", "params": {"lookback": 12, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    "opening_range_breakout": {"entry": "_opening_range_breakout_entry", "params": {"opening_bars": 12, "breakout_buffer_pct": 0.0, "tp_pct": 8, "sl_pct": 4, "max_hold": 24}},
    "vwap_reversion": {"entry": "_vwap_reversion_entry", "params": {"vwap_dev_pct": 2.0, "tp_pct": 5, "sl_pct": 3, "max_hold": 24}},
    "volume_spike_reversion": {"entry": "_volume_spike_reversion_entry", "params": {"rsi_period": 3, "os_thresh": 30, "vol_mult": 2.0, "vol_lookback": 20, "tp_pct": 15, "sl_pct": 5, "max_hold": 36}},
    "multi_tf_rsi": {"entry": "_multi_tf_rsi_entry", "params": {"rsi_period": 3, "os_thresh": 30, "tp_pct": 20, "sl_pct": 5, "max_hold": 36}},
    "overnight_gap": {"entry": "_overnight_gap_entry", "params": {"tp_pct": 5, "sl_pct": 3, "max_hold": 12}},
    "atr_expansion": {"entry": "_atr_expansion_entry", "params": {"atr_period": 14, "atr_mult": 1.5, "tp_pct": 8, "sl_pct": 4, "max_hold": 24}},
    "regime_gated_momentum": {"entry": "_regime_gated_momentum_entry", "params": {"lookback": 20, "ema_period": 50, "atr_period": 14, "trend_lookback": 12, "min_atr_pct": 1.0, "min_trend_pct": 1.0, "tp_pct": 8, "sl_pct": 4, "max_hold": 24}},
    "keltner_breakout": {"entry": "_keltner_breakout_entry", "params": {"k_period": 20, "k_mult": 2.0, "tp_pct": 6, "sl_pct": 3, "max_hold": 24}},
    "hist_vol_squeeze": {"entry": "_hist_vol_squeeze_entry", "params": {"hv_period": 20, "tp_pct": 10, "sl_pct": 5, "max_hold": 36}},
}


if __name__ == "__main__":
    print("Strategy Library — Available strategies:")
    for name, info in STRATEGY_REGISTRY.items():
        print(f"  {name}: {info['params']}")
