#!/usr/bin/env python3
"""
Signal Overlap Analysis — Tests whether two strategies fire on the same bars.

Answers: "Is adding strategy B to strategy A genuinely additive or just duplicative?"

Usage:
    python scripts/signal_overlap_analysis.py

Tests all pairs of top 10 strategies on the 5 runner coins (RAVE, NOM, GHST, TRU, SUP).
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
# ENTRY FUNCTIONS FOR TOP STRATEGIES
# ==========================================

def _momentum_entry(candles_hist, closes, candle, params):
    lookback = params.get("lookback", 10)
    if len(candles_hist) < lookback + 2:
        return False
    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    return current_high > highest


def _robust_regression_entry(candles_hist, closes, candle, params):
    """Huber/M-estimator regression — outlier-resistant fitting."""
    if len(closes) < 40:
        return False
    period = min(40, len(closes) - 1)
    window = closes[-period:]
    n = len(window)
    x = list(range(n))
    y = window

    # Simple linear regression with outlier resistance (iterative reweighting)
    weights = [1.0] * n
    for _ in range(3):  # 3 iterations of IRLS
        wx = [weights[i] * x[i] for i in range(n)]
        wy = [weights[i] * y[i] for i in range(n)]
        wxx = [weights[i] * x[i] * x[i] for i in range(n)]
        wxy = [weights[i] * x[i] * y[i] for i in range(n)]

        sum_w = sum(weights)
        sum_wx = sum(wx)
        sum_wy = sum(wy)
        sum_wxx = sum(wxx)
        sum_wxy = sum(wxy)

        denom = sum_w * sum_wxx - sum_wx * sum_wx
        if abs(denom) < 1e-10:
            break
        slope = (sum_w * sum_wxy - sum_wx * sum_wy) / denom
        intercept = (sum_wy - slope * sum_wx) / sum_w

        # Update weights (Huber)
        residuals = [y[i] - (slope * x[i] + intercept) for i in range(n)]
        mad = sorted([abs(r) for r in residuals])[n // 2] * 1.4826
        if mad < 1e-10:
            break
        for i in range(n):
            u = abs(residuals[i]) / mad
            weights[i] = 1.0 if u <= 1.345 else 1.345 / u

    predicted = slope * n + intercept
    current_price = closes[-1]

    # Enter when price is below regression line and starting to revert up
    if current_price < predicted * 0.998:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


def _spectral_analysis_entry(candles_hist, closes, candle, params):
    """Power spectral density — finds dominant frequency, enters at cycle bottom."""
    if len(closes) < 50:
        return False
    n = min(64, len(closes) - 1)
    window = closes[-n:]

    # Remove mean
    mean_c = sum(window) / n
    centered = [c - mean_c for c in window]

    # Simple DFT at a few frequencies
    max_power = 0
    dominant_freq = 0
    for k in range(1, n // 4):
        real_part = sum(centered[t] * math.cos(2 * math.pi * k * t / n) for t in range(n))
        imag_part = sum(centered[t] * math.sin(2 * math.pi * k * t / n) for t in range(n))
        power = real_part ** 2 + imag_part ** 2
        if power > max_power:
            max_power = power
            dominant_freq = k

    # Enter at cycle bottom (phase = pi)
    if dominant_freq > 0:
        real_part = sum(centered[t] * math.cos(2 * math.pi * dominant_freq * t / n) for t in range(n))
        imag_part = sum(centered[t] * math.sin(2 * math.pi * dominant_freq * t / n) for t in range(n))
        phase = math.atan2(imag_part, real_part)

        # Phase near pi = bottom of cycle
        if abs(phase - math.pi) < 0.5:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _obv_positive_volume_entry(candles_hist, closes, candle, params):
    """PVI surge: volume-confirmed upward moves."""
    if len(candles_hist) < 30:
        return False
    pvi = 1000.0
    for i in range(1, len(candles_hist)):
        vol = float(candles_hist[i]["volume"])
        prev_vol = float(candles_hist[i - 1]["volume"])
        cl = float(candles_hist[i]["close"])
        prev_cl = float(candles_hist[i - 1]["close"])
        if vol > prev_vol and prev_cl > 0:
            pct = (cl - prev_cl) / prev_cl
            pvi += pvi * pct
    if pvi > 1050 and len(closes) > 1 and closes[-1] > closes[-2]:
        return True
    return False


def _time_decay_entry(candles_hist, closes, candle, params):
    """Signal strength decays with time since trigger."""
    if len(candles_hist) < 30:
        return False
    decay_period = params.get("decay_period", 10)
    recent_returns = []
    for i in range(max(1, len(closes) - decay_period - 1), len(closes) - 1):
        if closes[i] > 0 and closes[i-1] > 0:
            recent_returns.append(abs(closes[i] / closes[i-1] - 1))
    if not recent_returns:
        return False
    avg_return = sum(recent_returns) / len(recent_returns)
    current_return = abs(closes[-1] / closes[-2] - 1) if len(closes) > 1 and closes[-2] > 0 else 0
    if avg_return > 0 and current_return > avg_return * 2.0:
        return True
    if len(recent_returns) >= 3:
        recent_avg = sum(recent_returns[-3:]) / 3
        if recent_avg > avg_return * 1.5 and current_return > avg_return * 1.2:
            if len(closes) > 1 and closes[-1] > closes[-2]:
                return True
    return False


def _ma_atr_entry(candles_hist, closes, candle, params):
    """MA crossover + ATR expansion confirmation."""
    if len(candles_hist) < 50:
        return False
    ma_period = params.get("ma_period", 20)
    atr_period = params.get("atr_period", 14)
    atr_mult = params.get("atr_mult", 1.5)
    if len(closes) < ma_period + 5:
        return False
    ma = sum(closes[-ma_period:]) / ma_period
    ma_prev = sum(closes[-ma_period-1:-1]) / ma_period
    current_price = closes[-1]
    ma_rising = ma > ma_prev
    price_above = current_price > ma
    trs = []
    for i in range(1, len(candles_hist)):
        h = float(candles_hist[i]["high"])
        l = float(candles_hist[i]["low"])
        c_prev = float(candles_hist[i-1]["close"])
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if len(trs) < atr_period + 1:
        return False
    current_atr = sum(trs[-atr_period:]) / atr_period
    prev_atr = sum(trs[-atr_period*2:-atr_period]) / atr_period if len(trs) >= atr_period * 2 else current_atr
    atr_expanding = current_atr > prev_atr * atr_mult if prev_atr > 0 else False
    if price_above and ma_rising and atr_expanding:
        return True
    return False


ENTRY_FUNCS = {
    "momentum": _momentum_entry,
    "robust_regression": _robust_regression_entry,
    "spectral_analysis": _spectral_analysis_entry,
    "obv_positive_volume": _obv_positive_volume_entry,
    "time_decay_signal": _time_decay_entry,
    "ma_atr": _ma_atr_entry,
}


def fetch_candles(client, pid, start, end):
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


def get_signal_bars(candles, entry_fn, params):
    """Get the set of bar indices where a strategy fires."""
    signals = set()
    closes = []
    for i, c in enumerate(candles):
        close = float(c["close"])
        closes.append(close)
        if i >= 20:  # Minimum history
            try:
                if entry_fn(candles[:i+1], closes, c, params):
                    signals.add(i)
            except Exception:
                pass
    return signals


def compute_overlap(signals_a, signals_b, max_distance=3):
    """Compute overlap between two signal sets, with tolerance window."""
    if not signals_a or not signals_b:
        return {"overlap_a": 0, "overlap_b": 0, "unique_a": 0, "unique_b": 0, "overlap_pct_a": 0, "overlap_pct_b": 0}

    # Count signals in B that are within max_distance bars of any signal in A
    overlap_a = 0
    for sa in signals_a:
        for offset in range(-max_distance, max_distance + 1):
            if sa + offset in signals_b:
                overlap_a += 1
                break

    overlap_b = 0
    for sb in signals_b:
        for offset in range(-max_distance, max_distance + 1):
            if sb + offset in signals_a:
                overlap_b += 1
                break

    return {
        "overlap_a": overlap_a,
        "overlap_b": overlap_b,
        "unique_a": len(signals_a) - overlap_a,
        "unique_b": len(signals_b) - overlap_b,
        "total_a": len(signals_a),
        "total_b": len(signals_b),
        "overlap_pct_a": round(overlap_a / len(signals_a) * 100, 1) if signals_a else 0,
        "overlap_pct_b": round(overlap_b / len(signals_b) * 100, 1) if signals_b else 0,
    }


def main():
    start_time = time.time()
    print(f"\n{'='*70}")
    print(f"SIGNAL OVERLAP ANALYSIS — Top 6 Strategies × 5 Runner Coins")
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}\n")

    client = CoinbaseAdvancedClient()
    runner_coins = ["RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD"]
    strategy_names = list(ENTRY_FUNCS.keys())

    # Fetch 30d candles
    now = int(time.time())
    start_ts = now - 30 * 86400
    all_candles = {}
    for coin in runner_coins:
        try:
            candles = fetch_candles(client, coin, start_ts, now)
            if candles:
                all_candles[coin] = candles
                print(f"  {coin}: {len(candles)} candles (30d)")
        except Exception as e:
            print(f"  {coin}: ERROR — {str(e)[:60]}")
        time.sleep(0.2)

    print(f"\nFetched {len(all_candles)} coins")

    # Default params for each strategy
    default_params = {
        "momentum": {"lookback": 10, "tp_pct": 10, "sl_pct": 3, "max_hold": 24},
        "robust_regression": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24},
        "spectral_analysis": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24},
        "obv_positive_volume": {"tp_pct": 8, "sl_pct": 3, "max_hold": 24},
        "time_decay_signal": {"decay_period": 10, "tp_pct": 8, "sl_pct": 3, "max_hold": 24},
        "ma_atr": {"ma_period": 20, "atr_period": 14, "atr_mult": 1.5, "tp_pct": 8, "sl_pct": 3, "max_hold": 24},
    }

    # Compute signal bars for each strategy × coin
    print(f"\nComputing signal bars...")
    signal_map = {}  # {coin: {strategy: set_of_bars}}
    for coin, candles in all_candles.items():
        signal_map[coin] = {}
        for strat in strategy_names:
            bars = get_signal_bars(candles, ENTRY_FUNCS[strat], default_params[strat])
            signal_map[coin][strat] = bars
            print(f"  {coin} / {strat}: {len(bars)} signals")

    # Compute all pairwise overlaps
    print(f"\n{'='*70}")
    print(f"  PAIRWISE OVERLAP ANALYSIS (±3 bar tolerance)")
    print(f"{'='*70}\n")

    all_overlaps = {}
    for coin in all_candles:
        print(f"\n  {'='*50}")
        print(f"  {coin}")
        print(f"  {'='*50}\n")

        coin_overlaps = {}
        for i, s1 in enumerate(strategy_names):
            for s2 in strategy_names[i+1:]:
                bars1 = signal_map[coin][s1]
                bars2 = signal_map[coin][s2]
                overlap = compute_overlap(bars1, bars2)
                coin_overlaps[f"{s1}_vs_{s2}"] = overlap

                # Print significant overlaps
                if overlap["overlap_pct_a"] > 0 or overlap["overlap_pct_b"] > 0:
                    print(f"  {s1:<25} vs {s2:<25}")
                    print(f"    {s1}: {overlap['total_a']} signals, {overlap['overlap_a']} overlap ({overlap['overlap_pct_a']}%), {overlap['unique_a']} unique")
                    print(f"    {s2}: {overlap['total_b']} signals, {overlap['overlap_b']} overlap ({overlap['overlap_pct_b']}%), {overlap['unique_b']} unique")

                    if overlap["unique_a"] > 0 and overlap["unique_b"] > 0:
                        additivity = min(overlap["unique_a"], overlap["unique_b"]) / max(overlap["total_a"], overlap["total_b"])
                        if additivity > 0.3:
                            print(f"    ✅ HIGHLY ADDITIVE: {additivity:.0%} unique signals on both sides")
                        elif additivity > 0.1:
                            print(f"    ⚡ MODERATELY ADDITIVE: {additivity:.0%} unique signals")
                        else:
                            print(f"    ⚠️  LOW ADDITIVITY: {additivity:.0%} unique signals")

        all_overlaps[coin] = coin_overlaps

    # Summary
    print(f"\n{'='*70}")
    print(f"  OVERLAP SUMMARY — Average Across All Coins")
    print(f"{'='*70}\n")

    print(f"  {'Pair':<50} {'Avg Overlap %':<15}")
    print(f"  {'-'*65}")

    pair_averages = {}
    for pair_name in [f"{s1}_vs_{s2}" for i, s1 in enumerate(strategy_names) for s2 in strategy_names[i+1:]]:
        overlaps = [all_overlaps[coin][pair_name] for coin in all_candles if pair_name in all_overlaps.get(coin, {})]
        if overlaps:
            avg_overlap_a = sum(o["overlap_pct_a"] for o in overlaps) / len(overlaps)
            avg_overlap_b = sum(o["overlap_pct_b"] for o in overlaps) / len(overlaps)
            avg_overlap = (avg_overlap_a + avg_overlap_b) / 2
            pair_averages[pair_name] = round(avg_overlap, 1)

    for pair, avg in sorted(pair_averages.items(), key=lambda x: x[1]):
        s1, s2 = pair.split("_vs_")
        if avg < 20:
            marker = "✅ ADDITIVE"
        elif avg < 40:
            marker = "⚡ PARTIAL"
        else:
            marker = "⚠️ OVERLAPPING"
        print(f"  {pair:<50} {avg:>5.1f}%     {marker}")

    # Save results
    out_path = Path(__file__).parent.parent / "reports" / "signal_overlap_analysis.json"
    out_path.parent.mkdir(exist_ok=True)
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.time() - start_time, 1),
        "coins_tested": list(all_candles.keys()),
        "strategies_tested": strategy_names,
        "signal_counts": {coin: {s: len(bars) for s, bars in signal_map[coin].items()} for coin in signal_map},
        "overlaps": all_overlaps,
        "pair_averages": pair_averages,
    }
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*70}")
    print(f"OVERLAP ANALYSIS COMPLETE in {time.time() - start_time:.0f}s")
    print(f"Results: {out_path}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
