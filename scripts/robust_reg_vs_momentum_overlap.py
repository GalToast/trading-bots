#!/usr/bin/env python3
"""
Robust Regression vs Momentum Overlap Analysis — On runner coins.

Tests whether robust_regression fires on DIFFERENT bars than momentum
for RAVE, NOM, GHST, TRU, SUP. Low overlap = genuinely complementary.
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "robust_reg_vs_momentum_overlap.json"

COINS = ["RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD"]
WINDOW_DAYS = 30


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
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


def get_momentum_signals(candles, lookback=15):
    """Get timestamps of momentum signals."""
    signals = set()
    hist = []
    candle_hist = []
    for i, c in enumerate(candles):
        close = float(c["close"])
        high = float(c["high"])
        hist.append(close)
        candle_hist.append(c)
        if len(hist) > 500:
            hist = hist[-500:]
        if len(candle_hist) > lookback + 1:
            recent_high = max(float(x["high"]) for x in candle_hist[-(lookback+1):-1])
            if high > recent_high:
                signals.add(int(c["start"]))
    return signals


def get_robust_reg_signals(candles, reg_period=20):
    """Get timestamps of robust regression signals.
    Robust regression: fit a line to recent returns, enter when
    predicted return is significantly negative (mean reversion setup).
    """
    signals = set()
    closes = []
    for c in candles:
        close = float(c["close"])
        closes.append(close)
        if len(closes) < reg_period + 5:
            continue

        # Simple robust regression: median-based linear fit
        recent = closes[-reg_period:]
        n = len(recent)
        x = list(range(n))
        y = recent

        # Median slope (Theil-Sen estimator)
        slopes = []
        for i in range(0, n-1, 2):
            if x[i+1] - x[i] != 0:
                slopes.append((y[i+1] - y[i]) / (x[i+1] - x[i]))
        if not slopes:
            continue
        med_slope = sorted(slopes)[len(slopes)//2]
        med_y = sorted(y)[len(y)//2]
        med_x = sorted(x)[len(x)//2]
        intercept = med_y - med_slope * med_x

        # Predicted next return
        predicted = med_slope * (n) + intercept
        actual = y[-1]
        deviation = (predicted - actual) / actual

        # Signal when predicted return is significantly below current (mean reversion buy)
        if deviation < -0.02:  # 2% below
            signals.add(int(c["start"]))
    return signals


def analyze_overlap(mom_signals, rr_signals):
    """Analyze overlap between two signal sets."""
    overlap = mom_signals & rr_signals
    mom_only = mom_signals - rr_signals
    rr_only = rr_signals - mom_signals
    total = mom_signals | rr_signals

    return {
        "momentum_signals": len(mom_signals),
        "robust_reg_signals": len(rr_signals),
        "overlap": len(overlap),
        "momentum_only": len(mom_only),
        "robust_reg_only": len(rr_only),
        "total_unique": len(total),
        "overlap_pct_mom": round(len(overlap) / max(1, len(mom_signals)) * 100, 1),
        "overlap_pct_rr": round(len(overlap) / max(1, len(rr_signals)) * 100, 1),
        "overlap_pct_total": round(len(overlap) / max(1, len(total)) * 100, 1),
    }


def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"ROBUST REGRESSION vs MOMENTUM OVERLAP — {WINDOW_DAYS}d", flush=True)
    print(f"=" * 70, flush=True)

    results = {}
    for coin in COINS:
        print(f"\nFetching {coin}...", flush=True)
        candles = fetch_candles(client, coin, start, now)
        print(f"  {coin}: {len(candles)} candles", flush=True)

        if len(candles) < 100:
            continue

        # Get signals
        mom_signals = get_momentum_signals(candles, lookback=15)
        rr_signals = get_robust_reg_signals(candles, reg_period=20)

        overlap = analyze_overlap(mom_signals, rr_signals)
        results[coin] = overlap

        print(f"  Momentum: {overlap['momentum_signals']} signals", flush=True)
        print(f"  Robust Reg: {overlap['robust_reg_signals']} signals", flush=True)
        print(f"  Overlap: {overlap['overlap']} ({overlap['overlap_pct_total']:.1f}%)", flush=True)
        print(f"  RR only: {overlap['robust_reg_only']} unique signals", flush=True)

    # Summary
    print(f"\n{'='*70}", flush=True)
    print(f"SUMMARY", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"{'Coin':<14} | {'Mom':>5} | {'RR':>5} | {'Overlap':>7} | {'Overlap%':>8} | {'RR Only':>7} | Verdict", flush=True)
    print(f"{'-'*14}-+-{'-'*5}-+-{'-'*5}-+-{'-'*7}-+-{'-'*8}-+-{'-'*7}-+-{'-'*12}", flush=True)

    for coin, data in results.items():
        verdict = "✅ COMPLEMENTARY" if data['overlap_pct_total'] < 30 else "⚠️ MODERATE" if data['overlap_pct_total'] < 50 else "❌ REDUNDANT"
        print(f"{coin:<14} | {data['momentum_signals']:>5} | {data['robust_reg_signals']:>5} | "
              f"{data['overlap']:>7} | {data['overlap_pct_total']:>7.1f}% | "
              f"{data['robust_reg_only']:>7} | {verdict}", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "coins": COINS,
        "results": results,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
