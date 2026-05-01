#!/usr/bin/env python3
"""
Forward Slippage Study — Backfill-Close vs Live-Forward-Open
=============================================================
The live V2 runner uses candle-close prices for entry (backfill-optimal).
In live forward trading, you'd enter at the NEXT bar's open (or with a
market order that gets the next available price).

This script simulates BOTH entry modes on the SAME oversold signals:
1. Backfill-close: enter at signal bar's close (what V2 does)
2. Forward-open: enter at next bar's open (what live trading gets)

Then compares the slippage distributions to answer:
- How much worse is forward-open vs backfill-close?
- What is the honest forward trading cost?
- Does the edge survive realistic forward execution?
- How does session gate (dead hours 0/6/12/19 UTC) affect the numbers?

Output: reports/forward_slippage_analysis.json
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "reports" / "rave_rsi_mr_live_v2_events.jsonl"

SESSION_DEAD_HOURS = {0, 6, 12, 19}


def compute_rsi(closes, period=3):
    """Compute RSI values for all bars."""
    if len(closes) < period + 1:
        return []
    rsi_vals = [50.0] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas]
    losses = [max(0, -d) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    if avg_l > 0:
        rsi_vals[period] = 100 - 100 / (1 + avg_g / avg_l)
    else:
        rsi_vals[period] = 100.0
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l > 0:
            rsi_vals[i + 1] = 100 - 100 / (1 + avg_g / avg_l)
        else:
            rsi_vals[i + 1] = 100.0
    return rsi_vals


def find_oversold_bars(candles, period=3, threshold=30):
    """Find all bars where RSI < threshold, with gap to avoid double-counting."""
    closes = [float(c["close"]) for c in candles]
    rsi = compute_rsi(closes, period)
    oversold = []
    for i in range(len(rsi)):
        if rsi[i] is not None and rsi[i] < threshold:
            if not oversold or i - oversold[-1] > 3:
                oversold.append(i)
    return oversold


def main():
    print("=" * 80)
    print("  FORWARD SLIPPAGE STUDY — Backfill-Close vs Live-Forward-Open")
    print("=" * 80)

    # Load candles
    candles = load_candles("RAVE-USD", "ONE_MINUTE", 7, max_age_minutes=7 * 24 * 60)
    if not candles:
        print("ERROR: No RAVE candle data.")
        return 1

    print(f"\nCandles loaded: {len(candles)} bars")

    # Find all oversold signals in the window
    oversold_bars = find_oversold_bars(candles, period=3, threshold=30)
    print(f"Oversold signals (RSI(3)<30): {len(oversold_bars)}")

    # For each signal, compute BOTH entry modes
    backfill_close_slips = []  # signal bar close vs signal bar close (should be ~0)
    forward_open_slips = []    # next bar open vs signal bar close
    forward_open_prices = []
    signal_prices = []

    PRINT_PER_SIGNAL = False  # Set True to debug individual signals

    if PRINT_PER_SIGNAL:
        print(f"\n{'='*90}")
        print(f"  {'Signal':>8} {'Signal $':>10} {'Next Open $':>12} {'BF-Close bps':>13} {'Fwd-Open bps':>13} {'Gap bps':>10}")
        print(f"{'='*90}")

    for os_idx in oversold_bars:
        signal_close = float(candles[os_idx]["close"])
        next_idx = os_idx + 1

        if next_idx >= len(candles):
            continue

        next_open = float(candles[next_idx]["open"])

        bf_close_slip = (signal_close - signal_close) / signal_close * 10000
        backfill_close_slips.append(bf_close_slip)

        fwd_open_slip = (next_open - signal_close) / signal_close * 10000
        forward_open_slips.append(fwd_open_slip)

        forward_open_prices.append(next_open)
        signal_prices.append(signal_close)

        gap = fwd_open_slip - bf_close_slip

        if PRINT_PER_SIGNAL:
            print(f"  {os_idx:>8} ${signal_close:>9.4f} ${next_open:>11.4f} "
                  f"{bf_close_slip:>+12.1f} {fwd_open_slip:>+12.1f} {gap:>+9.1f}")

    # Also load V2 actual entry prices for comparison
    v2_actual_slips = []
    try:
        with open(EVENTS_PATH, "r") as f:
            events = [json.loads(line.strip()) for line in f if line.strip()]
        opens = [e for e in events if e["action"] == "open"]
        for open_evt in opens:
            entry_price = open_evt["entry_price"]
            # Find closest signal
            best_dist = float("inf")
            best_signal = None
            for i, sp in enumerate(signal_prices):
                dist = abs(sp - entry_price) / sp
                if dist < best_dist:
                    best_dist = dist
                    best_signal = i
            if best_signal is not None and best_dist < 0.01:  # within 1%
                slip = (entry_price - signal_prices[best_signal]) / signal_prices[best_signal] * 10000
                v2_actual_slips.append(slip)
    except FileNotFoundError:
        print("\n  V2 events file not found — skipping actual entry comparison")

    # Statistics
    print(f"\n{'='*80}")
    print(f"  SLIPPAGE DISTRIBUTION COMPARISON")
    print(f"{'='*80}")

    print(f"\n  Backfill-Close (what V2 uses):")
    print(f"    Mean:   {statistics.mean(backfill_close_slips):+.2f} bps")
    print(f"    Median: {statistics.median(backfill_close_slips):+.2f} bps")
    print(f"    Std:    {statistics.pstdev(backfill_close_slips):.2f} bps")
    print(f"    Count:  {len(backfill_close_slips)}")

    print(f"\n  Forward-Open (live forward trading):")
    print(f"    Mean:   {statistics.mean(forward_open_slips):+.2f} bps")
    print(f"    Median: {statistics.median(forward_open_slips):+.2f} bps")
    print(f"    Std:    {statistics.pstdev(forward_open_slips):.2f} bps")
    print(f"    Count:  {len(forward_open_slips)}")

    # Gap analysis
    gaps = [f - b for f, b in zip(forward_open_slips, backfill_close_slips)]
    print(f"\n  Gap (Forward-Open minus Backfill-Close):")
    print(f"    Mean:   {statistics.mean(gaps):+.2f} bps")
    print(f"    Median: {statistics.median(gaps):+.2f} bps")
    print(f"    Std:    {statistics.pstdev(gaps):.2f} bps")

    if v2_actual_slips:
        print(f"\n  V2 Actual Entries (from live events):")
        print(f"    Mean:   {statistics.mean(v2_actual_slips):+.2f} bps")
        print(f"    Median: {statistics.median(v2_actual_slips):+.2f} bps")
        print(f"    Count:  {len(v2_actual_slips)}")

    # Session-gated analysis: filter out dead hours (0, 6, 12, 19 UTC)
    session_forward_slips = []
    session_bf_close_slips = []
    session_count = 0

    for os_idx in oversold_bars:
        next_idx = os_idx + 1
        if next_idx >= len(candles):
            continue

        # Extract UTC hour from candle start time
        candle = candles[os_idx]
        ts = candle.get("start") or candle.get("time", 0)
        if isinstance(ts, (int, float)):
            utc_hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        else:
            # String timestamp
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                utc_hour = dt.hour
            except Exception:
                utc_hour = -1  # Unknown, include it

        if utc_hour not in SESSION_DEAD_HOURS:
            session_forward_slips.append(forward_open_slips[session_count])
            session_bf_close_slips.append(backfill_close_slips[session_count])
            session_count += 1

    print(f"\n{'='*80}")
    print(f"  SESSION-GATED FORWARD SLIPPAGE (dead hours {sorted(SESSION_DEAD_HOURS)} UTC excluded)")
    print(f"{'='*80}")
    print(f"  Session-active signals: {session_count}/{len(forward_open_slips)}")

    if session_forward_slips:
        print(f"\n  Session-gated Forward-Open:")
        print(f"    Mean:   {statistics.mean(session_forward_slips):+.2f} bps")
        print(f"    Median: {statistics.median(session_forward_slips):+.2f} bps")
        print(f"    Std:    {statistics.pstdev(session_forward_slips):.2f} bps")
        print(f"    Count:  {len(session_forward_slips)}")

        session_gap_mean = statistics.mean(session_forward_slips) - statistics.mean(session_bf_close_slips)
        print(f"\n  Session-gated Gap (Forward-Open minus Backfill-Close):")
        print(f"    Mean:   {session_gap_mean:+.2f} bps")

    # Round-trip cost with forward-open entries
    print(f"\n{'='*80}")
    print(f"  HONEST FORWARD ROUND-TRIP COST")
    print(f"{'='*80}")

    fwd_entry_slip_mean = statistics.mean(forward_open_slips)
    fwd_entry_slip_median = statistics.median(forward_open_slips)

    # Fees: 40bps per side = 80bps round trip
    fee_bps = 80

    # Exit slippage: from previous analysis, TP hits are 0bps, timeout exits vary
    # Use the measured 0bps for TP (limit orders fill exactly)
    exit_slip_bps = 0.0  # TP exits are exact; timeout exits are strategy behavior

    # Total cost using MEAN forward slippage (conservative)
    total_mean = fee_bps + abs(fwd_entry_slip_mean) + exit_slip_bps
    # Total cost using MEDIAN forward slippage (typical)
    total_median = fee_bps + abs(fwd_entry_slip_median) + exit_slip_bps

    print(f"  Fee cost (40bps × 2):              {fee_bps} bps")
    print(f"  Forward entry slippage (mean):      {fwd_entry_slip_mean:+.2f} bps")
    print(f"  Forward entry slippage (median):    {fwd_entry_slip_median:+.2f} bps")
    print(f"  Exit slippage (TP hits):            {exit_slip_bps:.1f} bps")
    print(f"  ─────────────────────────────────────────")
    print(f"  TOTAL round-trip (mean):            {total_mean:.1f} bps")
    print(f"  TOTAL round-trip (median):          {total_median:.1f} bps")

    # Compare with previous models
    print(f"\n  Comparison with previous models:")
    print(f"    Empirical fallback:               180 bps (50+50 slip + 80 fees)")
    print(f"    Measured backfill-close:          88 bps (-8+0 slip + 80 fees)")
    print(f"    Forward-open (mean):              {total_mean:.1f} bps")
    print(f"    Forward-open (median):            {total_median:.1f} bps")

    # Session-gated round-trip cost
    if session_forward_slips:
        sess_fwd_mean = statistics.mean(session_forward_slips)
        sess_total = fee_bps + abs(sess_fwd_mean) + exit_slip_bps
        print(f"\n  Session-gated round-trip (mean):    {sess_total:.1f} bps")
        print(f"    (vs {total_mean:.1f} bps ungated — difference: {sess_total - total_mean:+.1f} bps)")

    # Benchmark impact estimate
    print(f"\n  Estimated benchmark impact (7d window, ~24 trades):")
    # Previous measured: $324.17 at 88bps total cost
    # Rough linear scaling: each extra bps costs ~$324/88 * 1 = ~$3.68 per bps
    # This is approximate; real impact depends on trade sizes and hold times
    cost_diff_mean = total_mean - 88
    cost_diff_median = total_median - 88
    est_impact_mean = -cost_diff_mean * 324.17 / 88
    est_impact_median = -cost_diff_median * 324.17 / 88

    print(f"    Previous net (88bps):               $324.17/week")
    print(f"    Estimated with forward-open (mean): ${324.17 + est_impact_mean:.2f}/week")
    print(f"    Estimated with forward-open (median): ${324.17 + est_impact_median:.2f}/week")
    print(f"    (Rough linear estimate; full backtest needed for exact numbers)")

    # Save report
    report = {
        "generated_from": "candle_cache_service (RAVE-USD, ONE_MINUTE, 7d)",
        "oversold_signals_found": len(oversold_bars),
        "signals_analyzed": len(forward_open_slips),
        "backfill_close_slippage_bps": {
            "mean": round(statistics.mean(backfill_close_slips), 2),
            "median": round(statistics.median(backfill_close_slips), 2),
            "std": round(statistics.pstdev(backfill_close_slips), 2),
            "count": len(backfill_close_slips),
        },
        "forward_open_slippage_bps": {
            "mean": round(statistics.mean(forward_open_slips), 2),
            "median": round(statistics.median(forward_open_slips), 2),
            "std": round(statistics.pstdev(forward_open_slips), 2),
            "count": len(forward_open_slips),
            "min": round(min(forward_open_slips), 2),
            "max": round(max(forward_open_slips), 2),
        },
        "gap_forward_minus_backfill_bps": {
            "mean": round(statistics.mean(gaps), 2),
            "median": round(statistics.median(gaps), 2),
            "std": round(statistics.pstdev(gaps), 2),
        },
        "v2_actual_entries_bps": {
            "mean": round(statistics.mean(v2_actual_slips), 2),
            "median": round(statistics.median(v2_actual_slips), 2),
            "count": len(v2_actual_slips),
        } if v2_actual_slips else None,
        "honest_forward_round_trip_cost": {
            "fees_bps": fee_bps,
            "forward_entry_slippage_mean_bps": round(fwd_entry_slip_mean, 2),
            "forward_entry_slippage_median_bps": round(fwd_entry_slip_median, 2),
            "exit_slippage_bps": exit_slip_bps,
            "total_mean_bps": round(total_mean, 1),
            "total_median_bps": round(total_median, 1),
        },
        "model_comparison": {
            "empirical_fallback_bps": 180,
            "measured_backfill_close_bps": 88,
            "forward_open_mean_bps": round(total_mean, 1),
            "forward_open_median_bps": round(total_median, 1),
        },
        "session_gated_analysis": {
            "dead_hours_utc": sorted(SESSION_DEAD_HOURS),
            "session_active_signals": session_count,
            "total_signals": len(forward_open_slips),
            "session_forward_open_mean_bps": round(statistics.mean(session_forward_slips), 2) if session_forward_slips else None,
            "session_forward_open_median_bps": round(statistics.median(session_forward_slips), 2) if session_forward_slips else None,
            "session_round_trip_mean_bps": round(fee_bps + abs(statistics.mean(session_forward_slips)) + exit_slip_bps, 1) if session_forward_slips else None,
            "vs_ungated_diff_bps": round(statistics.mean(session_forward_slips) - fwd_entry_slip_mean, 2) if session_forward_slips else None,
            "note": "Session gate excludes hours 0,6,12,19 UTC. If session slippage differs from ungated, it means dead-hour signals have different order-book behavior.",
        },
        "estimated_benchmark_impact": {
            "previous_net_7d": 324.17,
            "estimated_forward_open_mean": round(324.17 + est_impact_mean, 2),
            "estimated_forward_open_median": round(324.17 + est_impact_median, 2),
            "note": "Rough linear estimate. Full backtest with forward-open entries needed for exact numbers.",
        },
    }

    output_path = ROOT / "reports" / "forward_slippage_analysis.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
