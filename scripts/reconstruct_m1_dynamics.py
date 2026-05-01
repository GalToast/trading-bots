#!/usr/bin/env python3
"""Reconstruct M1-level trade dynamics from milestone data in the trade log.

Since we don't have persisted OHLCV data, this script estimates per-minute
price paths from the milestone fields in trade_behavior_log.jsonl:
- time_to_0_25_atr_seconds, time_to_0_5_atr_seconds, time_to_1_0_atr_seconds
- time_to_minus_0_35_atr_seconds
- max_favorable_excursion_pnl, max_adverse_excursion_pnl
- peak_pnl_before_exit, realized_pnl, hold_seconds
- first_green_before_fail

For each trade, it builds an estimated PnL-per-minute curve and aggregates
across all trades to produce:
1. Average PnL curve (per-minute from entry)
2. Distribution of first-green times by signal type
3. Peak formation rate (how fast do peaks form?)
4. Give-back velocity (how fast after peak does price reverse?)
5. Per-signal, per-mode profiles

Usage: python scripts/reconstruct_m1_dynamics.py [--symbol USDJPY] [--signal breakout_hold_above_high]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median, stdev

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def estimate_pnl_curve(trade: dict, n_minutes: int = 15) -> list[float]:
    """Estimate a per-minute PnL curve for a trade using milestone data.

    Uses the time_to_*_atr fields to reconstruct key inflection points,
    then interpolates between them. Returns a list of estimated PnL values
    at each minute from entry (0 to n_minutes-1).
    """
    hold_sec = float(trade.get("hold_seconds", 0) or 0)
    peak = float(trade.get("max_favorable_excursion_pnl", 0) or 0)
    adverse = float(trade.get("max_adverse_excursion_pnl", 0) or 0)
    realized = float(trade.get("realized_pnl", 0) or 0)
    exit_pnl = realized
    first_green = trade.get("first_green_before_fail", False)

    # Milestone times (None if not reached)
    t_025 = trade.get("time_to_0_25_atr_seconds")
    t_05 = trade.get("time_to_0_5_atr_seconds")
    t_10 = trade.get("time_to_1_0_atr_seconds")
    t_neg = trade.get("time_to_minus_0_35_atr_seconds")

    # Convert milestone times to PnL estimates
    # 0.25 ATR ~ 25% of peak (rough approximation)
    # 0.5 ATR ~ 50% of peak
    # 1.0 ATR ~ 100% of peak (or peak itself)
    milestones = []  # (time_sec, pnl_estimate)
    milestones.append((0, 0.0))  # entry

    if t_neg is not None:
        # Went negative first, estimate -0.35 ATR worth of adverse
        milestones.append((float(t_neg), -adverse))

    if t_025 is not None and peak > 0:
        milestones.append((float(t_025), peak * 0.25))
    if t_05 is not None and peak > 0:
        milestones.append((float(t_05), peak * 0.50))
    if t_10 is not None and peak > 0:
        milestones.append((float(t_10), peak * 1.0))

    # Peak time is roughly the max of the positive milestones
    if milestones:
        peak_time = max(t for t, p in milestones if p > 0) if any(p > 0 for _, p in milestones) else hold_sec * 0.5
    else:
        peak_time = hold_sec * 0.5

    milestones.append((peak_time, peak))
    milestones.append((hold_sec, exit_pnl))

    # Sort by time
    milestones.sort(key=lambda x: x[0])

    # Interpolate to per-minute resolution
    curve = []
    for minute in range(n_minutes):
        t_sec = minute * 60
        # Find surrounding milestones
        for i in range(len(milestones) - 1):
            t0, p0 = milestones[i]
            t1, p1 = milestones[i + 1]
            if t0 <= t_sec <= t1:
                if t1 == t0:
                    curve.append(p0)
                else:
                    frac = (t_sec - t0) / (t1 - t0)
                    curve.append(p0 + frac * (p1 - p0))
                break
        else:
            # Beyond last milestone — flat
            curve.append(milestones[-1][1])

    return curve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--signal", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--minutes", type=int, default=15)
    args = parser.parse_args()

    trades = load_jsonl(TRADE_LOG)

    # Filter
    if args.symbol:
        trades = [t for t in trades if str(t.get("symbol", "")).upper() == args.symbol.upper()]
    if args.signal:
        trades = [t for t in trades if str(t.get("entry_signal_type", "")) == args.signal]
    if args.mode:
        trades = [t for t in trades if str(t.get("entry_mode", "")).upper() == args.mode.upper()]

    print(f"M1 dynamics reconstruction — {len(trades)} trades")
    if args.symbol:
        print(f"  Symbol: {args.symbol}")
    if args.signal:
        print(f"  Signal: {args.signal}")
    if args.mode:
        print(f"  Mode: {args.mode}")
    print()

    # Build curves
    curves: dict[str, list[list[float]]] = defaultdict(list)
    all_curves: list[list[float]] = []

    for t in trades:
        hold_sec = float(t.get("hold_seconds", 0) or 0)
        n_min = max(int(hold_sec / 60) + 1, args.minutes)
        curve = estimate_pnl_curve(t, n_minutes=n_min)
        key = f"{t.get('entry_signal_type', '?')}|{t.get('entry_mode', '?')}"
        curves[key].append(curve[:args.minutes])
        all_curves.append(curve[:args.minutes])

    # Aggregate: average curve per signal|mode
    print(f"{'Minute':>6} | {'All (avg)':>10} | {'All (med)':>10} | {'Winners':>10} | {'Losers':>10} | {'N':>4}")
    print("-" * 70)
    for m in range(args.minutes):
        vals = [c[m] for c in all_curves if len(c) > m]
        wins = [c[m] for c in all_curves if len(c) > m and float(trades[all_curves.index(c)].get("realized_pnl", 0) or 0) > 0]
        loses = [c[m] for c in all_curves if len(c) > m and float(trades[all_curves.index(c)].get("realized_pnl", 0) or 0) <= 0]
        avg = mean(vals) if vals else 0
        med = median(vals) if vals else 0
        w_avg = mean(wins) if wins else 0
        l_avg = mean(loses) if loses else 0
        print(f"  T+{m:02d}m | ${avg:+8.3f} | ${med:+8.3f} | ${w_avg:+8.3f} | ${l_avg:+8.3f} | {len(vals):4d}")

    print()
    print("Per-signal|mode profiles (avg PnL at each minute):")
    print()

    for key, group_curves in sorted(curves.items()):
        if len(group_curves) < 3:
            continue
        avg_curve = [mean([c[m] for c in group_curves if len(c) > m]) for m in range(args.minutes)]
        wins = sum(1 for c in group_curves if c[-1] > 0)
        net = sum(c[-1] for c in group_curves if c)
        print(f"  {key} (n={len(group_curves)}, wr={wins/len(group_curves)*100:.0f}%, net=${net:+.2f}):")
        for m in range(args.minutes):
            bar = "█" * max(0, int(avg_curve[m] * 20)) if avg_curve[m] > 0 else "░" * max(0, int(-avg_curve[m] * 20))
            print(f"    T+{m:02d}m ${avg_curve[m]:+6.3f} {bar}")
        print()

    # First-green distribution
    fg_times = []
    for t in trades:
        if t.get("first_green_before_fail"):
            for milestone_key in ("time_to_0_25_atr_seconds",):
                v = t.get(milestone_key)
                if v is not None:
                    fg_times.append(float(v))
                    break

    if fg_times:
        print(f"First-green time distribution (n={len(fg_times)}):")
        print(f"  Mean: {mean(fg_times):.0f}s, Median: {median(fg_times):.0f}s")
        print(f"  P25: {sorted(fg_times)[len(fg_times)//4]:.0f}s, P75: {sorted(fg_times)[3*len(fg_times)//4]:.0f}s")
        print()

    # Peak formation rate
    peak_times = []
    for t in trades:
        hold = float(t.get("hold_seconds", 0) or 0)
        if hold > 0 and float(t.get("max_favorable_excursion_pnl", 0) or 0) > 0:
            # Estimate peak time from milestones
            t_10 = t.get("time_to_1_0_atr_seconds")
            t_05 = t.get("time_to_0_5_atr_seconds")
            t_025 = t.get("time_to_0_25_atr_seconds")
            if t_10 is not None:
                peak_times.append(float(t_10))
            elif t_05 is not None:
                peak_times.append(float(t_05) * 1.5)
            elif t_025 is not None:
                peak_times.append(float(t_025) * 3.0)
            else:
                peak_times.append(hold * 0.5)

    if peak_times:
        print(f"Peak formation time (n={len(peak_times)}):")
        print(f"  Mean: {mean(peak_times):.0f}s, Median: {median(peak_times):.0f}s")
        print(f"  <30s: {sum(1 for t in peak_times if t < 30)}/{len(peak_times)}")
        print(f"  30-60s: {sum(1 for t in peak_times if 30 <= t < 60)}/{len(peak_times)}")
        print(f"  60-120s: {sum(1 for t in peak_times if 60 <= t < 120)}/{len(peak_times)}")
        print(f"  >120s: {sum(1 for t in peak_times if t >= 120)}/{len(peak_times)}")
        print()


if __name__ == "__main__":
    main()
