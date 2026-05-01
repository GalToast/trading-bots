#!/usr/bin/env python3
"""Hungry Hippo — Wide Parameter Sweep.

Runs a MUCH wider parameter sweep than the original tuning to find
if there are better configs than the regime-matched sweep found.

Parameters:
  step_mult:       [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
  asymmetry_ratio: [0.25, 0.33, 0.5, 0.67, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
  alpha:           [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
  max_open:        [3, 6, 9, 12, 15, 20]

Total: 11 x 10 x 11 x 6 = 7,260 combos per symbol.
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
DAYS = 7
VOLUME = 0.01
WARMUP = 50

SYMBOLS = ["GBPUSD", "ETHUSD", "EURUSD", "US30"]

SYMBOL_META = {
    "GBPUSD": {"pip": 0.0001, "pip_value": 0.10, "digits": 5},
    "EURUSD": {"pip": 0.0001, "pip_value": 0.10, "digits": 5},
    "ETHUSD": {"pip": 0.01, "pip_value": 0.01, "digits": 2},
    "US30":   {"pip": 1.0, "pip_value": 0.01, "digits": 2},
}

# Original optimal configs from the regime-matched sweep (for comparison)
ORIGINAL_OPTIMALS = {
    "EXTREME": {"step_mult": 2.5, "asym_ratio": 1.0, "alpha": 0.4, "max_open_per_side": 12},
    "TREND":   {"step_mult": 2.0, "asym_ratio": 2.0, "alpha": 0.8, "max_open_per_side": 12},
    "CHOP":    {"step_mult": 1.0, "asym_ratio": 1.5, "alpha": 0.5, "max_open_per_side": 12},
    "MIXED":   {"step_mult": 2.0, "asym_ratio": 1.0, "alpha": 0.2, "max_open_per_side": 12},
}

# Wide sweep parameter grids
STEP_MULT_GRID     = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
ASYM_RATIO_GRID    = [0.25, 0.33, 0.5, 0.67, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]
ALPHA_GRID         = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
MAX_OPEN_GRID      = [3, 6, 9, 12, 15, 20]

# ===================================================================
# Technical indicators (same as original)
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
# Bar-level lattice simulation (same as original)
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
    atr_ref = atr_series[WARMUP] if not np.isnan(atr_series[WARMUP]) else abs(closes[WARMUP] - closes[WARMUP - 1])
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
    running_pnl = 0.0
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
            pnl = pnl_usd("SELL", outer["entry"], close_threshold, meta)
            realized_net += pnl
            realized_closes += 1
            sell_tickets.pop(0)

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
            profitable.sort(key=lambda x: pnl_usd(x[0], x[1]["entry"], bar_close, meta), reverse=True)
            for direction, ticket in profitable[:n_to_close]:
                pnl = pnl_usd(direction, ticket["entry"], bar_close, meta)
                realized_net += pnl
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
# MT5 data loading
# ===================================================================

def load_m15_mt5(symbol, days):
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return None
        end_utc = datetime.now(UTC)
        start_utc = end_utc - timedelta(days=days)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start_utc, end_utc)
        mt5.shutdown()
        if rates is None or len(rates) == 0:
            return None
        return {
            "high": np.array([float(r[2]) for r in rates]),
            "low": np.array([float(r[3]) for r in rates]),
            "close": np.array([float(r[4]) for r in rates]),
        }
    except Exception:
        return None


def generate_synthetic(symbol, n_bars=700):
    np.random.seed(hash(symbol) % 2**32)
    meta = SYMBOL_META[symbol]
    pip = meta["pip"]
    base_prices = {"GBPUSD": 1.2700, "EURUSD": 1.0850, "ETHUSD": 1700, "US30": 42000}
    price = base_prices.get(symbol, 1.0)
    closes, highs, lows = [], [], []
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
        closes.append(c)
        highs.append(h)
        lows.append(l)
        price = c
    return {"close": np.array(closes), "high": np.array(highs), "low": np.array(lows)}


# ===================================================================
# Wide sweep
# ===================================================================

def wide_sweep_symbol(symbol, data):
    """Run the wide parameter sweep for one symbol."""
    closes = data["close"]
    highs = data["high"]
    lows = data["low"]
    meta = SYMBOL_META[symbol]

    atr = compute_atr(highs, lows, closes, period=14)
    adx = compute_adx(highs, lows, closes, period=14)
    hurst = rolling_hurst(closes, window=50, max_lag=20)
    pp = price_position(closes, window=200)
    regimes = classify_regime(adx, hurst, pp)

    total_combos = len(STEP_MULT_GRID) * len(ASYM_RATIO_GRID) * len(ALPHA_GRID) * len(MAX_OPEN_GRID)
    print(f"  [{symbol}] Running {total_combos} combinations...", flush=True)

    # Store all results sorted by net PnL
    all_results = []

    for step_mult, asym_ratio, alpha, max_open in product(STEP_MULT_GRID, ASYM_RATIO_GRID, ALPHA_GRID, MAX_OPEN_GRID):
        config = {
            "step_mult": step_mult,
            "asym_ratio": asym_ratio,
            "alpha": alpha,
            "max_open_per_side": max_open,
        }
        res = simulate_lattice(closes, highs, lows, atr, config, meta)
        all_results.append({
            "step_mult": step_mult,
            "asym_ratio": asym_ratio,
            "alpha": alpha,
            "max_open_per_side": max_open,
            "closes": res["closes"],
            "net_usd": res["net_usd"],
            "per_close": res["per_close"],
            "max_dd": res["max_dd"],
        })

    # Sort by net PnL descending
    all_results.sort(key=lambda r: r["net_usd"], reverse=True)

    # Also sort by $/close descending
    by_per_close = sorted(all_results, key=lambda r: r["per_close"], reverse=True)

    # Check original optimal configs
    original_results = {}
    for reg_name, cfg in ORIGINAL_OPTIMALS.items():
        res = simulate_lattice(closes, highs, lows, atr, cfg, meta)
        original_results[reg_name] = {
            **cfg,
            "closes": res["closes"],
            "net_usd": res["net_usd"],
            "per_close": res["per_close"],
            "max_dd": res["max_dd"],
        }

    # Check if originals are still in top 10 by net PnL
    top10_net = set()
    for r in all_results[:10]:
        key = (r["step_mult"], r["asym_ratio"], r["alpha"], r["max_open_per_side"])
        top10_net.add(key)

    original_in_top10 = {}
    for reg_name, cfg in ORIGINAL_OPTIMALS.items():
        key = (cfg["step_mult"], cfg["asym_ratio"], cfg["alpha"], cfg["max_open_per_side"])
        original_in_top10[reg_name] = key in top10_net

    return {
        "total_combos": total_combos,
        "top5_by_net_pnl": all_results[:5],
        "top5_by_per_close": by_per_close[:5],
        "overall_best_net": all_results[0],
        "overall_best_per_close": by_per_close[0],
        "original_optimal_configs": ORIGINAL_OPTIMALS,
        "original_optimal_results": original_results,
        "original_in_top10_by_net": original_in_top10,
    }


def main():
    print("=" * 120)
    print("HUNGRY HIPPO — WIDE PARAMETER SWEEP")
    print(f"Testing {len(STEP_MULT_GRID)} x {len(ASYM_RATIO_GRID)} x {len(ALPHA_GRID)} x {len(MAX_OPEN_GRID)} = "
          f"{len(STEP_MULT_GRID) * len(ASYM_RATIO_GRID) * len(ALPHA_GRID) * len(MAX_OPEN_GRID)} combos per symbol")
    print("=" * 120)
    print()

    all_results = {}

    for sym in SYMBOLS:
        print(f"[{sym}] Loading data...", end="", flush=True)
        data = load_m15_mt5(sym, DAYS)
        source = "MT5"
        if data is None:
            data = generate_synthetic(sym)
            source = "synthetic (MT5 unavailable)"
        n_bars = len(data["close"])
        print(f" {source} ({n_bars} bars)", flush=True)

        t0 = time.time()
        result = wide_sweep_symbol(sym, data)
        elapsed = time.time() - t0
        print(f"[{sym}] Sweep complete in {elapsed:.1f}s", flush=True)

        all_results[sym] = result

        # Print top 5 for this symbol
        print(f"\n  [{sym}] TOP 5 BY NET PnL:")
        for i, r in enumerate(result["top5_by_net_pnl"], 1):
            print(f"    {i}. step={r['step_mult']} asym={r['asym_ratio']} alpha={r['alpha']} "
                  f"max_open={r['max_open_per_side']} -> net=${r['net_usd']:+.2f} "
                  f"($/close=${r['per_close']:+.4f}) closes={r['closes']}")

        print(f"\n  [{sym}] TOP 5 BY $/CLOSE:")
        for i, r in enumerate(result["top5_by_per_close"], 1):
            print(f"    {i}. step={r['step_mult']} asym={r['asym_ratio']} alpha={r['alpha']} "
                  f"max_open={r['max_open_per_side']} -> net=${r['net_usd']:+.2f} "
                  f"($/close=${r['per_close']:+.4f}) closes={r['closes']}")

        print(f"\n  [{sym}] Original optimal configs in top 10 by net PnL:")
        for reg_name, in_top10 in result["original_in_top10_by_net"].items():
            status = "YES" if in_top10 else "NO (original sweep missed global optimum!)"
            print(f"    {reg_name}: {status}")
        print()

    # Save results
    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": DAYS,
        "parameter_grids": {
            "step_mult": STEP_MULT_GRID,
            "asymmetry_ratio": ASYM_RATIO_GRID,
            "alpha": ALPHA_GRID,
            "max_open_per_side": MAX_OPEN_GRID,
        },
        "total_combos_per_symbol": len(STEP_MULT_GRID) * len(ASYM_RATIO_GRID) * len(ALPHA_GRID) * len(MAX_OPEN_GRID),
        "symbols": all_results,
    }

    reports_dir = REPO / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "hungry_hippo_wide_sweep_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print("=" * 120)
    print(f"Results saved to: {out_path}")

    # Summary: overall findings
    print("\nSUMMARY:")
    for sym in SYMBOLS:
        r = all_results[sym]
        best_net = r["overall_best_net"]
        best_pc = r["overall_best_per_close"]
        print(f"  {sym}:")
        print(f"    Best by net PnL:    step={best_net['step_mult']} asym={best_net['asym_ratio']} "
              f"alpha={best_net['alpha']} max_open={best_net['max_open_per_side']} "
              f"-> net=${best_net['net_usd']:+.2f}")
        print(f"    Best by $/close:    step={best_pc['step_mult']} asym={best_pc['asym_ratio']} "
              f"alpha={best_pc['alpha']} max_open={best_pc['max_open_per_side']} "
              f"-> $/close=${best_pc['per_close']:+.4f} (net=${best_pc['net_usd']:+.2f})")
        any_missed = not all(r["original_in_top10_by_net"].values())
        if any_missed:
            print(f"    ** Original sweep MISSED the global optimum for some regimes! **")
        else:
            print(f"    Original configs all still in top 10 — original sweep was close to optimal.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
