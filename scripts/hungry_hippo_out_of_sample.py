#!/usr/bin/env python3
"""Hungry Hippo — Out-of-Sample Test for Shapeshifter Tuning.

Pulls 14 days of M15 bars for GBPUSD, ETHUSD, EURUSD, US30.
Splits into train (days 1-5, first 480 bars) and test (days 6-14, remaining bars).
Runs a simplified tuning sweep on TRAIN only, finds best config,
then applies SAME config to TEST data.

Also runs the STATIC baseline on both splits for comparison.

Usage:
    python scripts/hungry_hippo_out_of_sample.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
UTC = timezone.utc

# 14 days of M15 data; 96 bars/day
TRAIN_BARS = 5 * 96   # 480 — first 5 days
# Test = remaining bars (up to 14 days worth if available)

VOLUME = 0.01
WARMUP = 50

SYMBOLS = ["GBPUSD", "ETHUSD", "EURUSD", "US30"]

# Sweep ranges
STEP_MULT_RANGE = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
ASYM_RATIO_RANGE = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
ALPHA_RANGE = [0.05, 0.1, 0.2, 0.3, 0.5]
MAX_OPEN_RANGE = [6, 12, 15, 20]

# Static baseline
STATIC_CONFIG = {
    "step_mult": 1.0,
    "asym_ratio": 2.0,
    "alpha": 0.5,
    "max_open_per_side": 12,
}

# ---------------------------------------------------------------------------
# Symbol metadata
# ---------------------------------------------------------------------------
SYMBOL_META = {
    "GBPUSD": {"pip": 0.0001, "pip_value": 0.10, "digits": 5},
    "EURUSD": {"pip": 0.0001, "pip_value": 0.10, "digits": 5},
    "ETHUSD": {"pip": 0.01,   "pip_value": 0.01,  "digits": 2},
    "US30":   {"pip": 1.0,    "pip_value": 0.01,  "digits": 2},
}


# ===================================================================
# Technical indicators (copied from hungry_hippo_extreme_tuning.py)
# ===================================================================

def compute_atr(highs, lows, closes, period=14):
    n = len(closes)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr = np.full(n, np.nan, dtype=np.float64)
    if n >= period:
        for i in range(period - 1, n):
            atr[i] = np.mean(tr[i - period + 1:i + 1])
    return atr


def compute_adx(highs, lows, closes, period=14):
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


def hurst_exponent(prices, max_lag=20):
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
    slope, _ = np.polyfit(log_lags, log_rs, 1)
    return float(slope)


def rolling_hurst(closes, window=50, max_lag=20):
    n = len(closes)
    h = np.full(n, np.nan, dtype=np.float64)
    for i in range(window, n):
        h[i] = hurst_exponent(closes[i - window:i], max_lag=max_lag)
    return h


def price_position(closes, window=200):
    n = len(closes)
    pp = np.full(n, np.nan, dtype=np.float64)
    for i in range(window - 1, n):
        w = closes[i - window + 1:i + 1]
        w_min, w_max = np.min(w), np.max(w)
        rng = w_max - w_min
        pp[i] = (closes[i] - w_min) / rng * 100.0 if rng > 0 else 50.0
    return pp


def classify_regime(adx, hurst, pp):
    n = len(adx)
    regimes = np.full(n, "MIXED", dtype="<U8")
    for i in range(n):
        if np.isnan(adx[i]) or np.isnan(hurst[i]) or np.isnan(pp[i]):
            continue
        a, h, p = adx[i], hurst[i], pp[i]
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

def pnl_usd(direction, entry, exit_price, meta):
    pip = meta["pip"]
    pip_value = meta["pip_value"]
    diff = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
    pips = diff / pip
    return pips * pip_value * VOLUME * 100.0


def derive_asymmetric_steps(step, asym_ratio):
    if asym_ratio <= 0:
        return step, step
    if asym_ratio >= 1.0:
        sr = math.sqrt(asym_ratio)
        return step / sr, step * sr
    else:
        sr = math.sqrt(1.0 / asym_ratio)
        return step * sr, step / sr


def simulate_lattice(closes, highs, lows, atr_series, config, meta):
    n = len(closes)
    max_open = config.get("max_open_per_side", 12)
    alpha = config["alpha"]
    step_mult = config["step_mult"]
    asym_ratio = config.get("asym_ratio", 1.0)

    anchor = closes[WARMUP]
    atr_ref = atr_series[WARMUP] if not np.isnan(atr_series[WARMUP]) else abs(closes[WARMUP] - closes[max(0, WARMUP - 1)])
    if atr_ref <= 0:
        atr_ref = closes[WARMUP] * 0.001
    base_step = atr_ref * step_mult
    step_sell, step_buy = derive_asymmetric_steps(base_step, asym_ratio)

    next_sell_level = anchor + step_sell
    next_buy_level = anchor - step_buy

    sell_tickets = []
    buy_tickets = []

    realized_net = 0.0
    realized_closes = 0
    anchor_resets = 0
    peak_pnl = 0.0
    max_dd = 0.0

    for i in range(WARMUP + 1, n):
        bar_high = highs[i]
        bar_low = lows[i]
        bar_close = closes[i]

        cur_atr = atr_series[i]
        if not np.isnan(cur_atr) and cur_atr > 0:
            base_step = cur_atr * step_mult
            step_sell, step_buy = derive_asymmetric_steps(base_step, asym_ratio)

        attempts = 0
        while bar_high >= next_sell_level and len(sell_tickets) < max_open and attempts < 200:
            sell_tickets.append({"entry": next_sell_level, "idx": i})
            next_sell_level += step_sell
            attempts += 1

        attempts = 0
        while bar_low <= next_buy_level and len(buy_tickets) < max_open and attempts < 200:
            buy_tickets.append({"entry": next_buy_level, "idx": i})
            next_buy_level -= step_buy
            attempts += 1

        sell_tickets.sort(key=lambda t: t["entry"], reverse=True)
        while len(sell_tickets) > 1:
            gap_idx = min(1, len(sell_tickets) - 1)
            close_threshold = sell_tickets[gap_idx]["entry"]
            if bar_low > close_threshold:
                break
            outer = sell_tickets[0]
            realized_net += pnl_usd("SELL", outer["entry"], close_threshold, meta)
            realized_closes += 1
            sell_tickets.pop(0)

        buy_tickets.sort(key=lambda t: t["entry"])
        while len(buy_tickets) > 1:
            gap_idx = min(1, len(buy_tickets) - 1)
            close_threshold = buy_tickets[gap_idx]["entry"]
            if bar_high < close_threshold:
                break
            outer = buy_tickets[0]
            realized_net += pnl_usd("BUY", outer["entry"], close_threshold, meta)
            realized_closes += 1
            buy_tickets.pop(0)

        profitable = []
        for t in sell_tickets:
            p = pnl_usd("SELL", t["entry"], bar_close, meta)
            if p > 0:
                profitable.append(("SELL", t))
        for t in buy_tickets:
            p = pnl_usd("BUY", t["entry"], bar_close, meta)
            if p > 0:
                profitable.append(("BUY", t))

        if profitable:
            n_to_close = max(1, int(len(profitable) * alpha))
            profitable.sort(key=lambda x: pnl_usd(x[0], x[1]["entry"], bar_close, meta), reverse=True)
            for direction, ticket in profitable[:n_to_close]:
                realized_net += pnl_usd(direction, ticket["entry"], bar_close, meta)
                realized_closes += 1
                lst = sell_tickets if direction == "SELL" else buy_tickets
                if ticket in lst:
                    lst.remove(ticket)

        if not sell_tickets and not buy_tickets:
            mid = (bar_high + bar_low) / 2.0
            if abs(mid - anchor) >= max(step_sell, step_buy):
                anchor = mid
                next_sell_level = anchor + step_sell
                next_buy_level = anchor - step_buy
                anchor_resets += 1

        peak_pnl = max(peak_pnl, realized_net)
        dd = peak_pnl - realized_net
        max_dd = max(max_dd, dd)

    return {
        "closes": realized_closes,
        "net_usd": round(realized_net, 2),
        "per_close": round(realized_net / max(1, realized_closes), 4),
        "max_dd": round(max_dd, 2),
        "resets": anchor_resets,
    }


# ===================================================================
# MT5 data loading
# ===================================================================

def load_m15_mt5(symbol, days):
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print(f"  MT5 init failed for {symbol}")
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
        print("  MetaTrader5 not installed")
        return None
    except Exception as e:
        print(f"  MT5 error: {e}")
        return None


# ===================================================================
# Out-of-sample sweep
# ===================================================================

def sweep_train_test(symbol, data):
    closes = data["close"]
    highs = data["high"]
    lows = data["low"]
    meta = SYMBOL_META[symbol]
    n_total = len(closes)

    # Compute indicators on FULL data (regime classification needs rolling windows)
    atr_full = compute_atr(highs, lows, closes, period=14)
    adx_full = compute_adx(highs, lows, closes, period=14)
    hurst_full = rolling_hurst(closes, window=50, max_lag=20)
    pp_full = price_position(closes, window=200)
    regimes_full = classify_regime(adx_full, hurst_full, pp_full)

    # Split
    train_end = TRAIN_BARS  # 480
    test_start = TRAIN_BARS

    if n_total <= train_end:
        print(f"  WARN: Only {n_total} bars, need >{train_end} for train+test split")
        return None

    train_closes = closes[:train_end]
    train_highs = highs[:train_end]
    train_lows = lows[:train_end]
    train_atr = atr_full[:train_end]
    train_regimes = regimes_full[:train_end]

    test_closes = closes[test_start:]
    test_highs = highs[test_start:]
    test_lows = lows[test_start:]
    test_atr = atr_full[test_start:]
    test_regimes = regimes_full[test_start:]

    print(f"  Bars: total={n_total}, train={len(train_closes)}, test={len(test_closes)}")

    # --- Sweep on TRAIN ---
    n_combos = len(STEP_MULT_RANGE) * len(ASYM_RATIO_RANGE) * len(ALPHA_RANGE) * len(MAX_OPEN_RANGE)
    print(f"  Sweeping {n_combos} configs on train data...", end="", flush=True)
    t0 = time.time()

    best_net = -1e18
    best_cfg = None
    best_result = None

    for sm, asym, al, mo in product(STEP_MULT_RANGE, ASYM_RATIO_RANGE, ALPHA_RANGE, MAX_OPEN_RANGE):
        cfg = {"step_mult": sm, "asym_ratio": asym, "alpha": al, "max_open_per_side": mo}
        res = simulate_lattice(train_closes, train_highs, train_lows, train_atr, cfg, meta)
        if res["net_usd"] > best_net:
            best_net = res["net_usd"]
            best_cfg = cfg
            best_result = res

    sweep_time = time.time() - t0
    print(f" done ({sweep_time:.1f}s)")

    # --- Apply best config to TEST ---
    test_shapeshifter = simulate_lattice(test_closes, test_highs, test_lows, test_atr, best_cfg, meta)

    # --- Static baseline on both ---
    train_static = simulate_lattice(train_closes, train_highs, train_lows, train_atr, STATIC_CONFIG, meta)
    test_static = simulate_lattice(test_closes, test_highs, test_lows, test_atr, STATIC_CONFIG, meta)

    # --- Compute metrics ---
    train_net_ss = best_result["net_usd"]
    test_net_ss = test_shapeshifter["net_usd"]
    train_net_static = train_static["net_usd"]
    test_net_static = test_static["net_usd"]

    degradation = test_net_ss / train_net_ss if train_net_ss != 0 else float("inf")
    beats_static_test = test_net_ss > test_net_static

    return {
        "n_bars_total": n_total,
        "n_bars_train": len(train_closes),
        "n_bars_test": len(test_closes),
        "sweep_combos": n_combos,
        "sweep_time_seconds": round(sweep_time, 1),
        "best_train_config": best_cfg,
        "train": {
            "shapeshifter": {
                "net_usd": best_result["net_usd"],
                "closes": best_result["closes"],
                "per_close": best_result["per_close"],
                "max_dd": best_result["max_dd"],
            },
            "static": {
                "net_usd": train_static["net_usd"],
                "closes": train_static["closes"],
                "per_close": train_static["per_close"],
                "max_dd": train_static["max_dd"],
            },
        },
        "test": {
            "shapeshifter": {
                "net_usd": test_shapeshifter["net_usd"],
                "closes": test_shapeshifter["closes"],
                "per_close": test_shapeshifter["per_close"],
                "max_dd": test_shapeshifter["max_dd"],
            },
            "static": {
                "net_usd": test_static["net_usd"],
                "closes": test_static["closes"],
                "per_close": test_static["per_close"],
                "max_dd": test_static["max_dd"],
            },
        },
        "degradation_factor": round(degradation, 4) if degradation != float("inf") else "inf",
        "beats_static_on_test": beats_static_test,
        "shapeshifter_wins_train": train_net_ss > train_net_static,
        "shapeshifter_wins_test": test_net_ss > test_net_static,
    }


def main():
    print("=" * 100)
    print("HUNGRY HIPPO — OUT-OF-SAMPLE TEST")
    print("Train: first 5 days (480 bars) | Test: remaining days (up to 9)")
    print("=" * 100)
    print()

    all_results = {}

    for sym in SYMBOLS:
        print(f"\n[{sym}] Loading 14 days M15 from MT5...", end="", flush=True)
        data = load_m15_mt5(sym, days=14)

        if data is None:
            print(" FAILED — skipping")
            all_results[sym] = {"error": "data_unavailable"}
            continue

        n_bars = len(data["close"])
        print(f" {n_bars} bars loaded")

        result = sweep_train_test(sym, data)
        if result is None:
            all_results[sym] = {"error": "insufficient_data"}
            continue

        all_results[sym] = result

        # Print inline summary
        tr = result["train"]
        te = result["test"]
        print(f"  Train: SS=${tr['shapeshifter']['net_usd']:+.2f} vs Static=${tr['static']['net_usd']:+.2f}")
        print(f"  Test:  SS=${te['shapeshifter']['net_usd']:+.2f} vs Static=${te['static']['net_usd']:+.2f}")
        print(f"  Degradation: {result['degradation_factor']}")
        print(f"  Beats static on test: {result['beats_static_on_test']}")

    # --- Aggregate ---
    print("\n" + "=" * 100)
    print("SUMMARY TABLE")
    print("=" * 100)
    print(f"{'SYMBOL':<10} {'TRAIN_SS$':>10} {'TRAIN_STATIC$':>13} {'TEST_SS$':>10} {'TEST_STATIC$':>12} {'DEGRADATION':>12} {'BEATS_STATIC?':>13}")
    print("-" * 100)

    total_train_ss = 0.0
    total_train_static = 0.0
    total_test_ss = 0.0
    total_test_static = 0.0
    wins_count = 0
    symbols_done = 0

    for sym in SYMBOLS:
        r = all_results.get(sym)
        if r is None or "error" in r:
            print(f"{sym:<10} {'N/A':>10} {'N/A':>13} {'N/A':>10} {'N/A':>12} {'N/A':>12} {'N/A':>13}")
            continue
        symbols_done += 1
        tr = r["train"]
        te = r["test"]
        train_ss = tr["shapeshifter"]["net_usd"]
        train_st = tr["static"]["net_usd"]
        test_ss = te["shapeshifter"]["net_usd"]
        test_st = te["static"]["net_usd"]
        deg = r["degradation_factor"]
        beats = r["beats_static_on_test"]

        total_train_ss += train_ss
        total_train_static += train_st
        total_test_ss += test_ss
        total_test_static += test_st
        if beats:
            wins_count += 1

        deg_str = f"{deg:.2f}x" if isinstance(deg, (int, float)) and deg != float("inf") else str(deg)
        print(f"{sym:<10} ${train_ss:>+.2f}    ${train_st:>+.2f}     ${test_ss:>+.2f}    ${test_st:>+.2f}     {deg_str:>12} {'YES' if beats else 'NO':>13}")

    print("-" * 100)
    if symbols_done > 0:
        print(f"{'TOTAL':<10} ${total_train_ss:>+.2f}    ${total_train_static:>+.2f}     ${total_test_ss:>+.2f}    ${total_test_static:>+.2f}")
        print(f"\nShapeshifter beats static on test: {wins_count}/{symbols_done} symbols")

        overall_degradation = total_test_ss / total_train_ss if total_train_ss != 0 else None
        if overall_degradation is not None:
            print(f"Overall degradation factor: {overall_degradation:.2f}x")

        if total_test_ss > total_test_static:
            print("VERDICT: Shapeshifter survives out-of-sample — beats static on aggregate test data")
        else:
            print("VERDICT: Shapeshifter does NOT beat static on aggregate test data — overfitting detected")

    # --- Save ---
    reports_dir = REPO / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "hungry_hippo_out_of_sample_test.json"

    def json_clean(obj):
        """Recursively convert numpy types to JSON-serializable Python types."""
        if isinstance(obj, dict):
            return {k: json_clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [json_clean(v) for v in obj]
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        return obj

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "train_bars": TRAIN_BARS,
        "symbols": json_clean(all_results),
        "aggregate": {
            "train_shapeshifter_total": round(total_train_ss, 2),
            "train_static_total": round(total_train_static, 2),
            "test_shapeshifter_total": round(total_test_ss, 2),
            "test_static_total": round(total_test_static, 2),
            "overall_degradation": round(overall_degradation, 4) if overall_degradation is not None else None,
            "symbols_beating_static": wins_count,
            "total_symbols": symbols_done,
        },
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
