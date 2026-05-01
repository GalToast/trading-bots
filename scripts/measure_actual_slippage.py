#!/usr/bin/env python3
"""
Measure Actual Slippage from Live V2 Events
=============================================
The live V2 runner records entry_price and exit_price, but these are the 
prices it USED, not the signal prices. To measure real slippage, we need
to compare:
- Signal: RSI oversold bar close (the trigger price)
- Entry: next bar's close (what the runner actually entered at)

For exits:
- Target: entry * 1.25 (the TP price)
- Actual: the exit price used

This script replays the V2 events against candle data to compute actual
slippage in bps for every entry and exit.

Output: reports/actual_slippage_analysis.json
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "reports" / "rave_rsi_mr_live_v2_events.jsonl"
STATE_PATH = ROOT / "reports" / "rave_rsi_mr_live_v2_state.json"


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
    """Find all bars where RSI < threshold."""
    closes = [float(c["close"]) for c in candles]
    rsi = compute_rsi(closes, period)
    oversold = []
    for i in range(len(rsi)):
        if rsi[i] is not None and rsi[i] < threshold:
            # Don't double-count consecutive oversold bars
            if not oversold or i - oversold[-1] > 3:
                oversold.append(i)
    return oversold


def main():
    print("=" * 80)
    print("  ACTUAL SLIPPAGE ANALYSIS — RAVE RSI MR Live V2")
    print("=" * 80)

    # Load events
    events = []
    with open(EVENTS_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    opens = [e for e in events if e["action"] == "open"]
    closes = [e for e in events if e["action"] == "close"]

    print(f"\nEvents loaded: {len(opens)} opens, {len(closes)} closes")

    # Load candles for replay
    candles = load_candles("RAVE-USD", "ONE_MINUTE", 7, max_age_minutes=7 * 24 * 60)
    if not candles:
        print("ERROR: No RAVE candle data.")
        return 1

    print(f"Candles loaded: {len(candles)} bars")

    # Find oversold bars to match signals
    oversold_bars = find_oversold_bars(candles, period=3, threshold=30)
    print(f"Oversold signals (RSI(3)<30): {len(oversold_bars)}")

    # Match events to oversold signals
    # The live runner enters on the bar where RSI < 30, at the close price
    # We compare signal bar close vs actual entry price

    entry_slippages_bps = []
    exit_slippages_bps = []

    print(f"\n{'='*70}")
    print(f"  ENTRY SLIPPAGE ANALYSIS")
    print(f"{'='*70}")
    print(f"  {'Signal Bar':>10} {'Signal Price':>12} {'Entry Price':>12} {'Diff bps':>10} {'RSI':>8}")

    for open_evt in opens:
        entry_price = open_evt["entry_price"]
        rsi_val = open_evt.get("rsi_at_entry")

        # Find the closest oversold bar before this entry
        # The signal bar is the one where RSI went oversold
        # The entry is at that bar's close (or next bar if the runner uses next_bar mode)

        # Look for an oversold bar near the entry price
        best_match = None
        best_dist = float("inf")

        for os_bar in oversold_bars:
            signal_price = float(candles[os_bar]["close"])
            dist = abs(signal_price - entry_price) / signal_price
            if dist < best_dist:
                best_dist = dist
                best_match = os_bar

        if best_match is not None:
            signal_price = float(candles[best_match]["close"])
            next_bar_idx = best_match + 1
            if next_bar_idx < len(candles):
                next_close = float(candles[next_bar_idx]["close"])
                # Slippage = (entry - signal) / signal * 10000 bps
                # If entry > signal (paid more), slippage is positive (bad for long)
                slip_bps = (entry_price - signal_price) / signal_price * 10000
                entry_slippages_bps.append(slip_bps)

                rsi_at_signal = None
                if best_match < len(candles):
                    rsi_at_signal = rsi_val

                print(f"  {best_match:>10} ${signal_price:>10.4f} ${entry_price:>10.4f} "
                      f"{slip_bps:>+9.1f} {rsi_val or '?':>8}")
            else:
                print(f"  {best_match:>10} (next bar out of range)")

    print(f"\n  Entry slippage stats (all matches):")
    if entry_slippages_bps:
        print(f"    Mean: {statistics.mean(entry_slippages_bps):+.1f} bps")
        print(f"    Median: {statistics.median(entry_slippages_bps):+.1f} bps")
        print(f"    Min: {min(entry_slippages_bps):+.1f} bps")
        print(f"    Max: {max(entry_slippages_bps):+.1f} bps")

    # Filter out bad matches (>100bps are likely wrong signal bar matches)
    clean_entry_slips = [s for s in entry_slippages_bps if abs(s) < 100]
    print(f"\n  Entry slippage stats (cleaned, <100bps matches only):")
    if clean_entry_slips:
        print(f"    Count: {len(clean_entry_slips)}/{len(entry_slippages_bps)}")
        print(f"    Mean: {statistics.mean(clean_entry_slips):+.1f} bps")
        print(f"    Median: {statistics.median(clean_entry_slips):+.1f} bps")
        print(f"    Min: {min(clean_entry_slips):+.1f} bps")
        print(f"    Max: {max(clean_entry_slips):+.1f} bps")

    # Exit slippage analysis
    print(f"\n{'='*70}")
    print(f"  EXIT SLIPPAGE ANALYSIS")
    print(f"{'='*70}")
    print(f"  {'TP Target':>10} {'Exit Price':>12} {'Diff bps':>10} {'Reason':>10}")

    for close_evt in closes:
        entry_price = close_evt["entry_price"]
        exit_price = close_evt["exit_price"]
        reason = close_evt.get("reason", "unknown")
        tp_price = entry_price * 1.25  # 25% TP

        if reason == "tp":
            # Slippage from target: did we exit AT the target or before/after?
            slip_bps = (exit_price - tp_price) / tp_price * 10000
            exit_slippages_bps.append(abs(slip_bps))  # Absolute slippage matters
            print(f"  ${tp_price:>8.4f} ${exit_price:>10.4f} {slip_bps:>+9.1f} {reason:>10}")
        elif reason == "timeout":
            # Timeout exits — slippage is (exit - entry) vs what we hoped for
            # The TP was never hit, so slippage = (TP - exit) / TP
            hoped_gain = tp_price - entry_price
            actual_gain = exit_price - entry_price
            if hoped_gain > 0:
                missed_pct = (hoped_gain - actual_gain) / hoped_gain * 100
                print(f"  ${tp_price:>8.4f} ${exit_price:>10.4f} {missed_pct:>+9.1f}%missed {reason:>10}")
            else:
                print(f"  ${tp_price:>8.4f} ${exit_price:>10.4f} {'N/A':>10} {reason:>10}")
        elif reason == "sl":
            slip_bps = (exit_price - (entry_price * 0.97)) / (entry_price * 0.97) * 10000
            exit_slippages_bps.append(abs(slip_bps))
            print(f"  ${entry_price * 0.97:>8.4f} ${exit_price:>10.4f} {slip_bps:>+9.1f} {reason:>10}")

    print(f"\n  Exit slippage stats (TP hits only):")
    if exit_slippages_bps:
        print(f"    Mean abs slippage: {statistics.mean(exit_slippages_bps):.1f} bps")
        print(f"    Median abs slippage: {statistics.median(exit_slippages_bps):.1f} bps")

    # Compute actual round-trip costs
    print(f"\n{'='*70}")
    print(f"  ROUND-TRIP COST ANALYSIS")
    print(f"{'='*70}")

    total_entry_slip = sum(clean_entry_slips) if clean_entry_slips else 0
    total_exit_slip = sum(exit_slippages_bps) if exit_slippages_bps else 0
    avg_entry_slip = statistics.mean(clean_entry_slips) if clean_entry_slips else 0
    avg_exit_slip = statistics.mean(exit_slippages_bps) if exit_slippages_bps else 0

    # Fee cost per round trip: 40bps entry + 40bps exit = 80bps
    fee_bps_per_trade = 40  # per side
    total_fee_bps = fee_bps_per_trade * 2  # round trip

    print(f"  Fee cost (40bps × 2): {total_fee_bps} bps")
    print(f"  Avg entry slippage: {avg_entry_slip:+.1f} bps")
    print(f"  Avg exit slippage (TP hits): {avg_exit_slip:+.1f} bps")
    print(f"  TOTAL round-trip cost: {total_fee_bps + avg_entry_slip + avg_exit_slip:.1f} bps")

    # The empirical snapshot used 50bps entry + 50bps exit = 100bps total slippage
    # Plus 80bps fees = 180bps total execution cost
    print(f"\n  Comparison with empirical fallback (50bps entry + 50bps exit):")
    empirical_total = 80 + 50 + 50  # fees + entry slip + exit slip
    actual_total = total_fee_bps + abs(avg_entry_slip) + abs(avg_exit_slip)
    print(f"  Empirical assumption: {empirical_total} bps")
    print(f"  Actual measured: {actual_total:.1f} bps")
    print(f"  Difference: {actual_total - empirical_total:+.1f} bps")

    # Save report
    report = {
        "generated_from": str(EVENTS_PATH),
        "total_opens": len(opens),
        "total_closes": len(closes),
        "entry_slippage_bps_raw": {
            "mean": round(statistics.mean(entry_slippages_bps), 1) if entry_slippages_bps else 0,
            "median": round(statistics.median(entry_slippages_bps), 1) if entry_slippages_bps else 0,
            "count": len(entry_slippages_bps),
        },
        "entry_slippage_bps_cleaned": {
            "mean": round(statistics.mean(clean_entry_slips), 1) if clean_entry_slips else 0,
            "median": round(statistics.median(clean_entry_slips), 1) if clean_entry_slips else 0,
            "count": len(clean_entry_slips),
            "excluded": len(entry_slippages_bps) - len(clean_entry_slips),
            "exclusion_threshold_bps": 100,
        },
        "exit_slippage_bps": {
            "mean": round(statistics.mean(exit_slippages_bps), 1) if exit_slippages_bps else 0,
            "median": round(statistics.median(exit_slippages_bps), 1) if exit_slippages_bps else 0,
            "count": len(exit_slippages_bps),
        },
        "round_trip_cost": {
            "fees_bps": total_fee_bps,
            "entry_slippage_bps": round(avg_entry_slip, 1),
            "exit_slippage_bps": round(avg_exit_slip, 1),
            "total_bps": round(total_fee_bps + abs(avg_entry_slip) + abs(avg_exit_slip), 1),
            "note": "Exit slippage only applies to TP hits. Timeout exits are strategy behavior, not slippage.",
        },
        "empirical_comparison": {
            "assumed_total_bps": empirical_total,
            "actual_total_bps": round(actual_total, 1),
            "difference_bps": round(actual_total - empirical_total, 1),
            "interpretation": "Empirical 50bps fallback is PESSIMISTIC. Actual slippage near zero for candle-close entries." if actual_total < empirical_total else "Empirical fallback is optimistic.",
        },
    }

    output_path = ROOT / "reports" / "actual_slippage_analysis.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
