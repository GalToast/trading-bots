#!/usr/bin/env python3
"""Project when lanes hit promotion/kill gates.

Given current signal frequency and trade outcomes, estimates:
- How many more trades until promotion (12 trades, exp > control + $0.03)
- How many more trades until kill (8 trades, exp <= -$0.05)
- Expected time to gates based on observed signal rate

Also projects the exit experiment timeline:
- exit_60_floor03: how many live trades needed to confirm vs baseline
- exit_75_floor03: expected performance if it were live now

Author: local AI-assisted research pass
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LAB_LOG = ROOT / "strategy_lab_events.jsonl"
LANE = ("USDJPY", "breakout_hold_above_high", "SNIPER", "PRICE")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def get_pnl(row: dict) -> float:
    return float(row.get("realized_pnl", 0.0) or 0.0)


def get_peak(row: dict) -> float:
    return float(row.get("peak_pnl_before_exit", 0.0) or 0.0)


def parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


def fmt_money(v: float) -> str:
    return f"${v:+.2f}"


def main() -> None:
    trades = load_jsonl(TRADE_LOG)
    lab_events = load_jsonl(LAB_LOG)

    lane_trades = [
        t for t in trades
        if (str(t.get("symbol", "")).upper() == LANE[0]
            and str(t.get("entry_signal_type", "")) == LANE[1]
            and str(t.get("entry_mode", "")).upper() == LANE[2]
            and str(t.get("regime_at_entry", "")).upper() == LANE[3])
    ]

    print("=" * 72)
    print("PROMOTION/KILL GATE PROJECTION")
    print("=" * 72)
    print()

    # ── 1. Signal frequency analysis ─────────────────────────────────────

    # From trade log: find first and last trade timestamps
    timestamps = []
    for t in lane_trades:
        ts = parse_ts(t.get("exit_time_utc") or t.get("recorded_at_utc"))
        if ts:
            timestamps.append(ts)

    if len(timestamps) >= 2:
        timestamps.sort()
        first_ts = timestamps[0]
        last_ts = timestamps[-1]
        span_hours = (last_ts - first_ts).total_seconds() / 3600
        total_trades = len(timestamps)
        trades_per_hour = total_trades / max(span_hours, 0.1)
        trades_per_day = trades_per_hour * 24

        print(f"Signal frequency (from {total_trades} historical trades):")
        print(f"  First trade: {first_ts.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  Last trade:  {last_ts.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  Span: {span_hours:.1f} hours ({span_hours/24:.1f} days)")
        print(f"  Rate: {trades_per_hour:.2f} trades/hour | {trades_per_day:.1f} trades/day")
        print()

        # From lab events: how many holdoff cycles in recent window?
        lab_starts = [e for e in lab_events if e.get("event_type") == "entry_holdoff_started"]
        if len(lab_starts) >= 2:
            lab_first = parse_ts(lab_starts[0]["recorded_at_utc"])
            lab_last = parse_ts(lab_starts[-1]["recorded_at_utc"])
            lab_span_min = (lab_last - lab_first).total_seconds() / 60
            lab_count = len(lab_starts)
            lab_rate = lab_count / max(lab_span_min, 1) * 60  # per hour
            print(f"Lab signal frequency (recent):")
            print(f"  {lab_count} holdoff cycles in {lab_span_min:.0f} minutes")
            print(f"  Rate: {lab_rate:.2f} signals/hour")
            print()
    else:
        print("Insufficient data for frequency analysis")
        print()

    # ── 2. Current lane metrics ──────────────────────────────────────────

    pnls = [get_pnl(t) for t in lane_trades]
    wins = sum(1 for p in pnls if p > 0)
    current_exp = mean(pnls) if pnls else 0
    current_wr = wins / len(pnls) * 100 if pnls else 0
    net = sum(pnls)

    print(f"Current lane performance ({len(lane_trades)} trades):")
    print(f"  Net: {fmt_money(net)} | Exp: {fmt_money(current_exp)}/trade")
    print(f"  WR: {current_wr:.1f}% | Wins: {wins}/{len(lane_trades)}")
    print()

    # ── 3. Exit experiment counterfactuals ───────────────────────────────

    print("─" * 72)
    print("EXIT EXPERIMENT PROJECTIONS")
    print("─" * 72)
    print()

    # exit_60_floor03
    adj_60 = []
    for t in lane_trades:
        peak = get_peak(t)
        pnl = get_pnl(t)
        if peak > 0.03:
            adj_60.append(max(pnl, peak * 0.60))
        else:
            adj_60.append(pnl)

    exp_60 = mean(adj_60) if adj_60 else 0
    net_60 = sum(adj_60)
    wins_60 = sum(1 for p in adj_60 if p > 0)

    print(f"  exit_60_floor03 (current live challenger):")
    print(f"    Projected: {fmt_money(net_60)} net | {fmt_money(exp_60)}/trade | {wins_60}/{len(lane_trades)} wins")
    print(f"    vs control: {fmt_money(exp_60 - current_exp)}/trade improvement")
    print()

    # exit_75_floor03
    adj_75 = []
    for t in lane_trades:
        peak = get_peak(t)
        pnl = get_pnl(t)
        if peak > 0.03:
            adj_75.append(max(pnl, peak * 0.75))
        else:
            adj_75.append(pnl)

    exp_75 = mean(adj_75) if adj_75 else 0
    net_75 = sum(adj_75)
    wins_75 = sum(1 for p in adj_75 if p > 0)

    print(f"  exit_75_floor03 (strongest backtest candidate):")
    print(f"    Projected: {fmt_money(net_75)} net | {fmt_money(exp_75)}/trade | {wins_75}/{len(lane_trades)} wins")
    print(f"    vs control: {fmt_money(exp_75 - current_exp)}/trade improvement")
    print()

    # ── 4. Promotion gate timeline ───────────────────────────────────────

    print("─" * 72)
    print("PROMOTION GATE TIMELINE")
    print("─" * 72)
    print()

    control_exp = 0.14  # from backtest
    promote_threshold = control_exp + 0.03  # $0.17/trade

    if exp_75 > promote_threshold:
        # How many trades needed to be confident?
        print(f"  exit_75_floor03 meets promotion threshold:")
        print(f"    Current proj: {fmt_money(exp_75)}/trade (threshold: {fmt_money(promote_threshold)})")
        print(f"    Margin above control: {fmt_money(exp_75 - control_exp)}/trade")
        print()

        # Monte Carlo simulation: how many trades to hit 12 with confidence?
        import random
        random.seed(42)
        n_sims = 1000
        trades_to_promote = []

        for sim in range(n_sims):
            sampled = []
            for _ in range(100):
                sampled.append(random.choice(adj_75))
                if len(sampled) >= 12:
                    exp_sampled = mean(sampled)
                    if exp_sampled > promote_threshold and sum(sampled) > 0:
                        trades_to_promote.append(len(sampled))
                        break

        if trades_to_promote:
            median_needed = sorted(trades_to_promote)[len(trades_to_promote) // 2]
            pct_90 = sorted(trades_to_promote)[int(len(trades_to_promote) * 0.9)]
            print(f"  Monte Carlo ({n_sims} sims):")
            print(f"    Median trades to promote: {median_needed}")
            print(f"    90th percentile: {pct_90}")

            if 'trades_per_day' in dir():
                median_hours = median_needed / max(trades_per_hour, 0.01)
                p90_hours = pct_90 / max(trades_per_hour, 0.01)
                print(f"    Estimated time (median): {median_hours:.1f}h ({median_hours/24:.1f} days)")
                print(f"    Estimated time (90th %ile): {p90_hours:.1f}h ({p90_hours/24:.1f} days)")
        print()

    if exp_60 > promote_threshold:
        print(f"  exit_60_floor03 also meets promotion threshold:")
        print(f"    Current proj: {fmt_money(exp_60)}/trade (threshold: {fmt_money(promote_threshold)})")
        print()

    # ── 5. Kill gate risk ────────────────────────────────────────────────

    print("─" * 72)
    print("KILL GATE RISK")
    print("─" * 72)
    print()

    kill_threshold = -0.05

    if current_exp > kill_threshold:
        print(f"  Current lane is SAFE: {fmt_money(current_exp)}/trade > {fmt_money(kill_threshold)} kill threshold")
        print(f"  Margin: {fmt_money(current_exp - kill_threshold)}/trade above kill")
    else:
        print(f"  ⚠ Current lane is at RISK: {fmt_money(current_exp)}/trade <= {fmt_money(kill_threshold)}")

    print()

    # ── 6. Session-aware projection ──────────────────────────────────────

    print("─" * 72)
    print("SESSION-AWARE SIGNAL PROJECTION")
    print("─" * 72)
    print()

    by_session = {}
    for t in lane_trades:
        ts = parse_ts(t.get("exit_time_utc") or t.get("recorded_at_utc"))
        if ts:
            hour = ts.hour
            if hour < 8:
                sess = "Asian (00-08 UTC)"
            elif hour < 13:
                sess = "London (08-13 UTC)"
            elif hour < 17:
                sess = "NY (13-17 UTC)"
            else:
                sess = "Off-hours (17-24 UTC)"
            if sess not in by_session:
                by_session[sess] = []
            by_session[sess].append(t)

    for sess in sorted(by_session.keys()):
        st = by_session[sess]
        spnls = [get_pnl(t) for t in st]
        swr = sum(1 for p in spnls if p > 0) / len(spnls) * 100
        print(f"  {sess}: {len(st)} trades | {swr:.0f}% WR | net {fmt_money(sum(spnls))} | exp {fmt_money(mean(spnls))}/trade")

    print()

    # Best session for promotion
    if by_session:
        best_sess = max(by_session.keys(), key=lambda s: mean(get_pnl(t) for t in by_session[s]))
        print(f"  Best session: {best_sess}")
        print(f"  Recommendation: prioritize live experiments during {best_sess}")

    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
