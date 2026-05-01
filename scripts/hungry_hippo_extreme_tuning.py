#!/usr/bin/env python3
"""Hungry Hippo — Regime-Segmented Extreme Tuning Sweep.

Tests the Shapeshifter Manifesto hypothesis:
  "Extremes are trampolines — trade every regime with the right personality."

Pulls 7 days of M15 bars, classifies each bar into a regime (EXTREME / TREND /
CHOP / MIXED), then runs a tuning sweep per regime with the matching lattice
personality.  Compares three strategies: static, regime-matched, regime-gated.

Usage:
    python scripts/hungry_hippo_extreme_tuning.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc
DAYS = 7
VOLUME = 0.01
WARMUP = 50  # bars before regime classification starts

SYMBOLS = ["GBPUSD", "EURUSD", "NZDUSD", "NAS100", "US30", "XAUUSD", "BTCUSD", "ETHUSD", "USDJPY"]

# ---------------------------------------------------------------------------
# Symbol metadata — pip size, point value, digit count
# ---------------------------------------------------------------------------
SYMBOL_META = {
    "GBPUSD":  {"pip": 0.0001,  "pip_value": 0.10,  "digits": 5},
    "EURUSD":  {"pip": 0.0001,  "pip_value": 0.10,  "digits": 5},
    "NZDUSD":  {"pip": 0.0001,  "pip_value": 0.10,  "digits": 5},
    "USDJPY":  {"pip": 0.01,    "pip_value": 0.67,  "digits": 3},
    "NAS100":  {"pip": 1.0,     "pip_value": 0.01,  "digits": 2},
    "US30":    {"pip": 1.0,     "pip_value": 0.01,  "digits": 2},
    "XAUUSD":  {"pip": 0.01,    "pip_value": 0.01,  "digits": 2},
    "BTCUSD":  {"pip": 1.0,     "pip_value": 0.01,  "digits": 1},
    "ETHUSD":  {"pip": 0.01,    "pip_value": 0.01,  "digits": 2},
}

# ---------------------------------------------------------------------------
# Regime → personality tuning ranges
# ---------------------------------------------------------------------------
REGIME_PERSONALITIES = {
    "EXTREME": {
        "personality": "CHOP_AGGRESSIVE",
        "step_mult_min": 1.5,
        "step_mult_max": 2.5,
        "asymmetry": "1:1",          # symmetric
        "alpha_min": 0.2,
        "alpha_max": 0.4,
        "step_grid": [1.5, 2.0, 2.5],
        "alpha_grid": [0.2, 0.3, 0.4],
    },
    "TREND": {
        "personality": "BREAKOUT",
        "step_mult_min": 1.0,
        "step_mult_max": 2.0,
        "asymmetry": "1.5-3.0:1 tight-on-trend",
        "alpha_min": 0.5,
        "alpha_max": 0.8,
        "step_grid": [1.0, 1.5, 2.0],
        "alpha_grid": [0.5, 0.65, 0.8],
    },
    "CHOP": {
        "personality": "CHOP_MODERATE",
        "step_mult_min": 0.5,
        "step_mult_max": 1.0,
        "asymmetry": "1:1 or 2:1",
        "alpha_min": 0.3,
        "alpha_max": 0.5,
        "step_grid": [0.5, 0.75, 1.0],
        "alpha_grid": [0.3, 0.4, 0.5],
    },
    "MIXED": {
        "personality": "DEFENSIVE",
        "step_mult_min": 2.0,
        "step_mult_max": 3.0,
        "asymmetry": "1:1",
        "alpha_min": 0.2,
        "alpha_max": 0.3,
        "step_grid": [2.0, 2.5, 3.0],
        "alpha_grid": [0.2, 0.25, 0.3],
    },
}

# Static baseline config (one config for all bars)
STATIC_CONFIG = {
    "step_mult": 1.0,
    "asym_ratio": 2.0,   # BUY-tight asymmetry
    "alpha": 0.5,
    "max_open_per_side": 12,
}


# ===================================================================
# Technical indicators
# ===================================================================

def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """ATR(14) as SMA of true ranges.  Returns array of same length as input (NaN for warmup)."""
    n = len(closes)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.full(n, np.nan, dtype=np.float64)
    if n >= period:
        for i in range(period - 1, n):
            window = tr[i - period + 1:i + 1]
            atr[i] = np.mean(window)
    return atr


def compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Simplified ADX via directional movement.  Returns array (NaN for warmup)."""
    n = len(closes)
    adx = np.full(n, np.nan, dtype=np.float64)
    if n < period + 2:
        return adx

    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down

    smooth_plus = np.zeros(n)
    smooth_minus = np.zeros(n)
    smooth_plus[period - 1] = np.sum(plus_dm[1:period])
    smooth_minus[period - 1] = np.sum(minus_dm[1:period])
    for i in range(period, n):
        smooth_plus[i] = smooth_plus[i - 1] - smooth_plus[i - 1] / period + plus_dm[i]
        smooth_minus[i] = smooth_minus[i - 1] - smooth_minus[i - 1] / period + minus_dm[i]

    tr = np.zeros(n)
    for i in range(n):
        if i == 0:
            tr[i] = highs[i] - lows[i]
        else:
            tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    smooth_tr = np.zeros(n)
    smooth_tr[period - 1] = np.sum(tr[1:period])
    for i in range(period, n):
        smooth_tr[i] = smooth_tr[i - 1] - smooth_tr[i - 1] / period + tr[i]

    for i in range(period, n):
        if smooth_tr[i] == 0:
            adx[i] = 0.0
            continue
        di_plus = 100.0 * smooth_plus[i] / smooth_tr[i]
        di_minus = 100.0 * smooth_minus[i] / smooth_tr[i]
        di_sum = di_plus + di_minus
        if di_sum == 0:
            adx[i] = 0.0
        else:
            adx[i] = 100.0 * abs(di_plus - di_minus) / di_sum
    return adx


def hurst_exponent(prices: np.ndarray, max_lag: int = 20) -> float:
    """Rescaled-range (R/S) Hurst exponent estimation.
    H > 0.55 -> trending, H < 0.45 -> mean-reverting.
    """
    n = len(prices)
    if n < max_lag * 2:
        return 0.5
    lags = range(2, max_lag)
    rs_vals = []
    for lag in lags:
        if lag > n:
            break
        returns = np.diff(prices[:lag])
        mean_r = np.mean(returns)
        deviations = np.cumsum(returns - mean_r)
        r = np.max(deviations) - np.min(deviations)
        s = np.std(returns, ddof=1)
        if s > 0:
            rs_vals.append(r / s)
        else:
            rs_vals.append(1e-8)
    if len(rs_vals) < 3:
        return 0.5
    log_lags = np.log(list(lags[:len(rs_vals)]))
    log_rs = np.log(rs_vals)
    # linear regression slope = Hurst exponent
    slope, _ = np.polyfit(log_lags, log_rs, 1)
    return float(slope)


def rolling_hurst(closes: np.ndarray, window: int = 50, max_lag: int = 20) -> np.ndarray:
    """Rolling Hurst exponent over a sliding window."""
    n = len(closes)
    h = np.full(n, np.nan, dtype=np.float64)
    for i in range(window, n):
        window_data = closes[i - window:i]
        h[i] = hurst_exponent(window_data, max_lag=max_lag)
    return h


def price_position(closes: np.ndarray, window: int = 200) -> np.ndarray:
    """Price position within the rolling window range, scaled 0-100."""
    n = len(closes)
    pp = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        w = closes[i - window + 1:i + 1]
        w_min = np.min(w)
        w_max = np.max(w)
        rng = w_max - w_min
        if rng > 0:
            pp[i] = (closes[i] - w_min) / rng * 100.0
        else:
            pp[i] = 50.0
    return pp


def classify_regime(adx: np.ndarray, hurst: np.ndarray, pp: np.ndarray) -> np.ndarray:
    """Classify each bar into EXTREME, TREND, CHOP, or MIXED.
    Returns string array.
    """
    n = len(adx)
    regimes = np.full(n, "MIXED", dtype="<U8")
    for i in range(n):
        if np.isnan(adx[i]) or np.isnan(hurst[i]) or np.isnan(pp[i]):
            continue
        a = adx[i]
        h = hurst[i]
        p = pp[i]
        if p > 85 or p < 15:
            regimes[i] = "EXTREME"
        elif a > 30 and h > 0.55:
            regimes[i] = "TREND"
        elif a < 25 and 20 < p < 80:
            regimes[i] = "CHOP"
        else:
            regimes[i] = "MIXED"
    return regimes


# ===================================================================
# Bar-level lattice simulation
# ===================================================================

def pnl_usd(direction: str, entry: float, exit_price: float, meta: dict) -> float:
    """Compute PnL in USD for a single position."""
    pip = meta["pip"]
    pip_value = meta["pip_value"]
    diff = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
    pips = diff / pip
    return pips * pip_value * VOLUME * 100.0  # 0.01 lot scaling


def derive_asymmetric_steps(step: float, asym_ratio: float) -> tuple[float, float]:
    """Derive buy/sell steps.  asym_ratio > 1 -> BUY-tight, < 1 -> SELL-tight."""
    if asym_ratio <= 0:
        return step, step
    if asym_ratio >= 1.0:
        sr = math.sqrt(asym_ratio)
        return step / sr, step * sr
    else:
        sr = math.sqrt(1.0 / asym_ratio)
        return step * sr, step / sr


def simulate_lattice(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    atr_series: np.ndarray,
    regimes: np.ndarray,
    config: dict,
    meta: dict,
    regime_filter: str | None = None,
) -> dict:
    """Bar-level lattice simulation.

    Args:
        closes, highs, lows: price arrays
        atr_series: ATR for each bar (used to compute absolute step size)
        regimes: regime label for each bar
        config: dict with step_mult, asym_ratio, alpha, max_open_per_side
        meta: symbol metadata (pip, pip_value, etc.)
        regime_filter: if set, only simulate bars matching this regime (None = all)
    """
    n = len(closes)
    max_open = config.get("max_open_per_side", 12)
    alpha = config["alpha"]
    step_mult = config["step_mult"]
    asym_ratio = config.get("asym_ratio", 1.0)

    anchor = closes[WARMUP]
    atr_ref = atr_series[WARMUP] if not np.isnan(atr_series[WARMUP]) else abs(closes[WARMUP] - closes[WARMUP - 1])
    if atr_ref <= 0:
        atr_ref = closes[WARMUP] * 0.001
    base_step = atr_ref * step_mult
    step_sell, step_buy = derive_asymmetric_steps(base_step, asym_ratio)

    next_sell_level = anchor + step_sell
    next_buy_level = anchor - step_buy

    sell_tickets = []  # {entry, idx}
    buy_tickets = []

    realized_net = 0.0
    realized_closes = 0
    anchor_resets = 0
    running_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0

    for i in range(WARMUP + 1, n):
        bar_high = highs[i]
        bar_low = lows[i]
        bar_close = closes[i]

        # Regime filter: skip bars that don't match
        if regime_filter is not None and regimes[i] != regime_filter:
            continue

        # Update step size based on current ATR
        cur_atr = atr_series[i]
        if not np.isnan(cur_atr) and cur_atr > 0:
            base_step = cur_atr * step_mult
            step_sell, step_buy = derive_asymmetric_steps(base_step, asym_ratio)

        # Open SELL positions (price goes UP through sell levels)
        attempts = 0
        while bar_high >= next_sell_level and len(sell_tickets) < max_open and attempts < 200:
            sell_tickets.append({"entry": next_sell_level, "idx": i})
            next_sell_level += step_sell
            attempts += 1

        # Open BUY positions (price goes DOWN through buy levels)
        attempts = 0
        while bar_low <= next_buy_level and len(buy_tickets) < max_open and attempts < 200:
            buy_tickets.append({"entry": next_buy_level, "idx": i})
            next_buy_level -= step_buy
            attempts += 1

        # Close SELL: price comes back DOWN
        sell_tickets.sort(key=lambda t: t["entry"], reverse=True)
        while len(sell_tickets) > 1:
            gap_idx = min(1, len(sell_tickets) - 1)
            close_threshold = sell_tickets[gap_idx]["entry"]
            if bar_low > close_threshold:
                break
            outer = sell_tickets[0]
            pnl = pnl_usd("SELL", outer["entry"], close_threshold, meta)
            realized_net += pnl
            realized_closes += 1
            sell_tickets.pop(0)

        # Close BUY: price comes back UP
        buy_tickets.sort(key=lambda t: t["entry"])
        while len(buy_tickets) > 1:
            gap_idx = min(1, len(buy_tickets) - 1)
            close_threshold = buy_tickets[gap_idx]["entry"]
            if bar_high < close_threshold:
                break
            outer = buy_tickets[0]
            pnl = pnl_usd("BUY", outer["entry"], close_threshold, meta)
            realized_net += pnl
            realized_closes += 1
            buy_tickets.pop(0)

        # Alpha-weighted close: close alpha fraction of ALL profitable positions
        profitable = []
        for t in sell_tickets:
            pnl = pnl_usd("SELL", t["entry"], bar_close, meta)
            if pnl > 0:
                profitable.append(("SELL", t))
        for t in buy_tickets:
            pnl = pnl_usd("BUY", t["entry"], bar_close, meta)
            if pnl > 0:
                profitable.append(("BUY", t))

        if profitable:
            n_to_close = max(1, int(len(profitable) * alpha))
            # Sort by PnL descending — close most profitable first
            profitable.sort(key=lambda x: pnl_usd(x[0], x[1]["entry"], bar_close, meta), reverse=True)
            for direction, ticket in profitable[:n_to_close]:
                pnl = pnl_usd(direction, ticket["entry"], bar_close, meta)
                realized_net += pnl
                realized_closes += 1
                lst = sell_tickets if direction == "SELL" else buy_tickets
                if ticket in lst:
                    lst.remove(ticket)

        # Reset anchor if flat
        if not sell_tickets and not buy_tickets:
            mid = (bar_high + bar_low) / 2.0
            if abs(mid - anchor) >= max(step_sell, step_buy):
                anchor = mid
                next_sell_level = anchor + step_sell
                next_buy_level = anchor - step_buy
                anchor_resets += 1

        # Track drawdown
        running_pnl = realized_net
        peak_pnl = max(peak_pnl, running_pnl)
        dd = peak_pnl - running_pnl
        max_dd = max(max_dd, dd)

    return {
        "closes": realized_closes,
        "net_usd": round(realized_net, 2),
        "per_close": round(realized_net / max(1, realized_closes), 4),
        "max_dd": round(max_dd, 2),
        "resets": anchor_resets,
    }


# ===================================================================
# MT5 data loading (with fallback to cached data)
# ===================================================================

def load_m15_mt5(symbol: str, days: int) -> dict | None:
    """Load M15 bars from MetaTrader5."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None
        end_utc = datetime.now(UTC)
        start_utc = end_utc - timedelta(days=days)
        tf = mt5.TIMEFRAME_M15
        rates = mt5.copy_rates_range(symbol, tf, start_utc, end_utc)
        mt5.shutdown()
        if rates is None or len(rates) == 0:
            return None
        return {
            "time": np.array([r[0] for r in rates]),
            "open": np.array([float(r[1]) for r in rates]),
            "high": np.array([float(r[2]) for r in rates]),
            "low": np.array([float(r[3]) for r in rates]),
            "close": np.array([float(r[4]) for r in rates]),
        }
    except ImportError:
        return None
    except Exception:
        return None


def load_cached_data(symbol: str) -> dict | None:
    """Try to load cached bars from the data directory."""
    data_dir = REPO / "data"
    for suffix in [".npy", ".csv", ".json"]:
        candidates = list(data_dir.glob(f"*{symbol}*{suffix}"))
        if candidates:
            p = candidates[0]
            try:
                if p.suffix == ".npy":
                    arr = np.load(p, allow_pickle=True)
                    if isinstance(arr, dict):
                        return arr
                elif p.suffix == ".json":
                    with open(p) as f:
                        d = json.load(f)
                    if isinstance(d, dict) and "close" in d:
                        return {
                            "close": np.array(d["close"]),
                            "high": np.array(d.get("high", d["close"])),
                            "low": np.array(d.get("low", d["close"])),
                            "open": np.array(d.get("open", d["close"])),
                            "time": np.array(d.get("time", range(len(d["close"])))),
                        }
            except Exception:
                pass
    return None


def generate_synthetic(symbol: str, n_bars: int = 700) -> dict:
    """Generate synthetic M15 data for fallback testing."""
    np.random.seed(hash(symbol) % 2**32)
    meta = SYMBOL_META[symbol]
    pip = meta["pip"]

    # Start from a reasonable mid-price
    base_prices = {
        "GBPUSD": 1.2700, "EURUSD": 1.0850, "NZDUSD": 0.5900,
        "USDJPY": 154.50, "NAS100": 19500, "US30": 42000,
        "XAUUSD": 3100, "BTCUSD": 84000, "ETHUSD": 1700,
    }
    price = base_prices.get(symbol, 1.0)

    # Simulate with regime shifts
    closes = []
    highs = []
    lows = []
    opens = []
    times = []

    t0 = int(datetime.now(UTC).timestamp()) - n_bars * 900
    regime = "chop"
    regime_dur = 0

    for i in range(n_bars):
        if regime_dur <= 0:
            regime = np.random.choice(["chop", "trend", "extreme"], p=[0.4, 0.35, 0.25])
            regime_dur = np.random.randint(20, 80)
        regime_dur -= 1

        if regime == "trend":
            drift = np.random.choice([-1, 1]) * pip * 3
            vol = pip * 2
        elif regime == "extreme":
            drift = np.random.choice([-1, 1]) * pip * 5
            vol = pip * 5
        else:
            drift = 0
            vol = pip * 1.5

        o = price
        c = o + drift + np.random.normal(0, vol)
        h = max(o, c) + abs(np.random.normal(0, vol * 0.5))
        l = min(o, c) - abs(np.random.normal(0, vol * 0.5))

        opens.append(o)
        closes.append(c)
        highs.append(h)
        lows.append(l)
        times.append(t0 + i * 900)

        price = c

    return {
        "time": np.array(times, dtype=np.int64),
        "open": np.array(opens),
        "high": np.array(highs),
        "low": np.array(lows),
        "close": np.array(closes),
    }


# ===================================================================
# Main sweep
# ===================================================================

def sweep_symbol(symbol: str, data: dict) -> dict:
    """Run the full regime-segmented tuning sweep for one symbol."""
    closes = data["close"]
    highs = data["high"]
    lows = data["low"]
    meta = SYMBOL_META[symbol]

    # Compute indicators
    atr = compute_atr(highs, lows, closes, period=14)
    adx = compute_adx(highs, lows, closes, period=14)
    hurst = rolling_hurst(closes, window=50, max_lag=20)
    pp = price_position(closes, window=200)
    regimes = classify_regime(adx, hurst, pp)

    # Regime breakdown
    regime_counts = {}
    for i in range(WARMUP, len(regimes)):
        r = regimes[i]
        regime_counts[r] = regime_counts.get(r, 0) + 1
    total_classified = sum(regime_counts.values())

    regime_breakdown = {}
    for r in ["EXTREME", "TREND", "CHOP", "MIXED"]:
        cnt = regime_counts.get(r, 0)
        regime_breakdown[r] = {
            "bars": cnt,
            "pct": round(cnt / max(1, total_classified) * 100, 1),
        }

    # --- Strategy 1: Static (one config for all bars) ---
    static_config = {
        "step_mult": STATIC_CONFIG["step_mult"],
        "asym_ratio": STATIC_CONFIG["asym_ratio"],
        "alpha": STATIC_CONFIG["alpha"],
        "max_open_per_side": STATIC_CONFIG["max_open_per_side"],
    }
    static_result = simulate_lattice(closes, highs, lows, atr, regimes, static_config, meta)

    # --- Strategy 2: Regime-matched (different config per regime) ---
    regime_matched_total = {"closes": 0, "net_usd": 0.0}
    optimal_by_regime = {}

    for reg_name in ["EXTREME", "TREND", "CHOP", "MIXED"]:
        rp = REGIME_PERSONALITIES[reg_name]

        # Grid search within the regime's personality range
        best_net = -1e18
        best_cfg = None
        best_result = None

        for sm in rp["step_grid"]:
            for al in rp["alpha_grid"]:
                # Asymmetry: use the regime's specified asymmetry
                if "1:1" in rp["asymmetry"]:
                    asym = 1.0
                elif "tight-on-trend" in rp["asymmetry"]:
                    asym = 2.0  # BUY-tight for trend following
                elif "2:1" in rp["asymmetry"]:
                    asym = 1.5  # moderate asymmetry
                else:
                    asym = 1.0

                cfg = {
                    "step_mult": sm,
                    "asym_ratio": asym,
                    "alpha": al,
                    "max_open_per_side": 12,
                }
                res = simulate_lattice(closes, highs, lows, atr, regimes, cfg, meta, regime_filter=reg_name)
                if res["net_usd"] > best_net:
                    best_net = res["net_usd"]
                    best_cfg = cfg
                    best_result = res

        regime_matched_total["closes"] += best_result["closes"]
        regime_matched_total["net_usd"] += best_result["net_usd"]

        asym_label = rp["asymmetry"]
        if best_cfg:
            asym_label = f"{best_cfg['asym_ratio']:.1f}:1" if best_cfg['asym_ratio'] >= 1.0 else f"1:{1.0/best_cfg['asym_ratio']:.1f}"

        optimal_by_regime[reg_name] = {
            "step_mult": best_cfg["step_mult"] if best_cfg else None,
            "asym": asym_label,
            "alpha": best_cfg["alpha"] if best_cfg else None,
            "net_usd": best_result["net_usd"],
            "closes": best_result["closes"],
            "per_close": best_result["per_close"],
        }

    regime_matched_total["net_usd"] = round(regime_matched_total["net_usd"], 2)
    regime_matched_total["per_close"] = round(
        regime_matched_total["net_usd"] / max(1, regime_matched_total["closes"]), 4
    )

    # --- Strategy 3: Regime-gated (only trade favorable regimes) ---
    # Favorable = EXTREME + CHOP (reversion-prone). Skip TREND + MIXED.
    gated_total = {"closes": 0, "net_usd": 0.0}

    for reg_name in ["EXTREME", "CHOP"]:
        rp = REGIME_PERSONALITIES[reg_name]
        best_net = -1e18
        best_cfg = None
        best_result = None

        for sm in rp["step_grid"]:
            for al in rp["alpha_grid"]:
                if "1:1" in rp["asymmetry"]:
                    asym = 1.0
                else:
                    asym = 1.0
                cfg = {"step_mult": sm, "asym_ratio": asym, "alpha": al, "max_open_per_side": 12}
                res = simulate_lattice(closes, highs, lows, atr, regimes, cfg, meta, regime_filter=reg_name)
                if res["net_usd"] > best_net:
                    best_net = res["net_usd"]
                    best_cfg = cfg
                    best_result = res

        if best_result:
            gated_total["closes"] += best_result["closes"]
            gated_total["net_usd"] += best_result["net_usd"]

    gated_total["net_usd"] = round(gated_total["net_usd"], 2)
    gated_total["per_close"] = round(
        gated_total["net_usd"] / max(1, gated_total["closes"]), 4
    )

    return {
        "regime_breakdown": regime_breakdown,
        "strategy_comparison": {
            "static": {
                "closes": static_result["closes"],
                "net_usd": static_result["net_usd"],
                "per_close": static_result["per_close"],
            },
            "regime_matched": {
                "closes": regime_matched_total["closes"],
                "net_usd": regime_matched_total["net_usd"],
                "per_close": regime_matched_total["per_close"],
            },
            "regime_gated": {
                "closes": gated_total["closes"],
                "net_usd": gated_total["net_usd"],
                "per_close": gated_total["per_close"],
            },
        },
        "optimal_by_regime": optimal_by_regime,
    }


def main() -> int:
    print("=" * 120)
    print("HUNGRY HIPPO — REGIME-SEGMENTED EXTREME TUNING SWEEP")
    print('"Extremes are trampolines — trade every regime with the right personality."')
    print("=" * 120)
    print()

    all_results = {}
    aggregate_static = 0.0
    aggregate_matched = 0.0
    aggregate_gated = 0.0

    for sym in SYMBOLS:
        print(f"[{sym}] Loading data...", end="", flush=True)

        # Try MT5 first, then cached data, then synthetic
        data = load_m15_mt5(sym, DAYS)
        source = "MT5"
        if data is None:
            data = load_cached_data(sym)
            source = "cache"
        if data is None:
            data = generate_synthetic(sym)
            source = "synthetic"

        n_bars = len(data["close"])
        print(f" {source} ({n_bars} bars)", flush=True)

        print(f"[{sym}] Running sweep...", end="", flush=True)
        t0 = time.time()
        result = sweep_symbol(sym, data)
        elapsed = time.time() - t0
        print(f" done ({elapsed:.1f}s)", flush=True)

        all_results[sym] = result

        sc = result["strategy_comparison"]
        aggregate_static += sc["static"]["net_usd"]
        aggregate_matched += sc["regime_matched"]["net_usd"]
        aggregate_gated += sc["regime_gated"]["net_usd"]

    # Save
    aggregate = {
        "static_total_net": round(aggregate_static, 2),
        "regime_matched_total_net": round(aggregate_matched, 2),
        "regime_gated_total_net": round(aggregate_gated, 2),
        "improvement_factor": round(
            aggregate_matched / abs(aggregate_static) if aggregate_static != 0 else 0, 2
        ),
    }

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": DAYS,
        "symbols": all_results,
        "aggregate": aggregate,
    }

    reports_dir = REPO / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "hungry_hippo_extreme_tuning_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary table
    print()
    print("=" * 120)
    print(f"{'SYMBOL':<10} {'STATIC$':>10} {'REGIME_MATCHED$':>16} {'REGIME_GATED$':>15} {'IMPROVEMENT':>12} {'VERDICT'}")
    print("-" * 120)

    for sym in SYMBOLS:
        sc = all_results[sym]["strategy_comparison"]
        s = sc["static"]["net_usd"]
        rm = sc["regime_matched"]["net_usd"]
        rg = sc["regime_gated"]["net_usd"]

        if s != 0:
            improvement = rm / abs(s)
        elif rm > 0:
            improvement = float("inf")
        else:
            improvement = 0

        imp_str = f"{improvement:.1f}x" if improvement != float("inf") else "inf"

        # Determine verdict
        if rm > max(s, rg):
            verdict = "Shapeshifter wins"
        elif rg > max(s, rm):
            verdict = "Gated wins"
        elif s > max(rm, rg):
            verdict = "Static wins"
        else:
            verdict = "Tie"

        s_str = f"${s:+.2f}"
        rm_str = f"${rm:+.2f}"
        rg_str = f"${rg:+.2f}"

        print(f"{sym:<10} {s_str:>10} {rm_str:>16} {rg_str:>15} {imp_str:>12} {verdict}")

    print("-" * 120)
    print(f"{'AGGREGATE':<10} ${aggregate_static:>+.2f} ${aggregate_matched:>+.2f} ${aggregate_gated:>+.2f} ", end="")
    if aggregate_static != 0:
        print(f"{aggregate['improvement_factor']:.1f}x", end="")
    print(" Shapeshifter" if aggregate_matched > max(aggregate_static, aggregate_gated) else "")
    print()
    print(f"Results saved to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
