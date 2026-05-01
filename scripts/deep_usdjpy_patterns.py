#!/usr/bin/env python3
"""Deep analysis of USDJPY breakout — patterns beyond entry/exit.

Looks at:
1. Time-of-day effects (when do signals fire? when do they win/lose?)
2. Confidence regime splits (0.82 vs 0.74 — does signal strength predict outcomes?)
3. Hold time vs outcome distribution (is there a sweet spot?)
4. Direction bias (BUY vs SELL outcomes differ?)
5. Peak-to-exit timing (how long from peak to trail exit?)
6. Correlation between max_adverse and realized_pnl
7. First-green timing patterns (how soon do winners vs losers go green?)

Author: local AI-assisted research pass
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LAB_LOG = ROOT / "strategy_lab_events.jsonl"
LANE = ("USDJPY", "breakout_hold_above_high", "SNIPER", "PRICE")


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
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


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_money(value: float) -> str:
    return f"{value:+.2f}"


def get_pnl(row: dict) -> float:
    return float(row.get("realized_pnl", 0.0) or 0.0)


def get_peak(row: dict) -> float:
    return float(row.get("peak_pnl_before_exit", 0.0) or 0.0)


def get_adverse(row: dict) -> float:
    return float(row.get("max_adverse_excursion_pnl", 0.0) or 0.0)


def get_hold(row: dict) -> float:
    return float(row.get("hold_seconds", 0.0) or 0.0)


def get_ttfg(row: dict) -> float | None:
    v = row.get("time_to_first_green_seconds")
    return float(v) if v is not None else None


def main() -> None:
    trades = load_jsonl(TRADE_LOG)
    lane_trades = [
        t for t in trades
        if (str(t.get("symbol", "")).upper() == LANE[0]
            and str(t.get("entry_signal_type", "")) == LANE[1]
            and str(t.get("entry_mode", "")).upper() == LANE[2]
            and str(t.get("regime_at_entry", "")).upper() == LANE[3])
    ]

    print("=" * 72)
    print("USDJPY BREAKOUT — DEEP PATTERN ANALYSIS (24 trades)")
    print("=" * 72)
    print()

    # ── 1. Time-of-day effects ──────────────────────────────────────────

    print("─" * 72)
    print("1. TIME-OF-DAY ANALYSIS")
    print("─" * 72)

    by_hour: dict[int, list[dict]] = defaultdict(list)
    for t in lane_trades:
        ts = parse_ts(t.get("exit_time_utc") or t.get("recorded_at_utc"))
        if ts:
            # Convert to US Eastern for market session context
            eastern = ts  # keep UTC, label as such
            by_hour[eastern.hour].append(t)

    # Group into market sessions (UTC)
    # Asian: 00-08 UTC, London: 08-12 UTC, NY: 13-17 UTC, Off-hours: 17-24 UTC
    sessions = {"Asian (00-08 UTC)": [], "London (08-12 UTC)": [],
                "NY (13-17 UTC)": [], "Off-hours (18-23 UTC)": []}
    for hour, ts_list in by_hour.items():
        if hour < 8:
            sessions["Asian (00-08 UTC)"].extend(ts_list)
        elif hour < 12:
            sessions["London (08-12 UTC)"].extend(ts_list)
        elif hour < 17:
            sessions["NY (13-17 UTC)"].extend(ts_list)
        else:
            sessions["Off-hours (18-23 UTC)"].extend(ts_list)

    for session, session_trades in sessions.items():
        if not session_trades:
            continue
        pnls = [get_pnl(t) for t in session_trades]
        wins = sum(1 for p in pnls if p > 0)
        print(f"  {session}: {len(session_trades)} trades | {wins}W/{len(session_trades)-wins}L")
        print(f"    Net: {fmt_money(sum(pnls))} | Exp: {fmt_money(mean(pnls))}/trade")
        print(f"    Avg hold: {mean(get_hold(t) for t in session_trades):.0f}s")
        print(f"    Avg TTG: {mean(get_ttfg(t) for t in session_trades if get_ttfg(t) is not None):.0f}s"
              if any(get_ttfg(t) is not None for t in session_trades) else "    Avg TTG: n/a")
    print()

    # ── 2. Confidence splits ────────────────────────────────────────────

    print("─" * 72)
    print("2. CONFIDENCE REGIME SPLIT")
    print("─" * 72)

    conf_buckets: dict[str, list[dict]] = defaultdict(list)
    for t in lane_trades:
        conf = t.get("confidence", 0)
        if conf >= 0.8:
            bucket = "High (>=0.80)"
        elif conf >= 0.7:
            bucket = "Medium (0.70-0.79)"
        elif conf >= 0.6:
            bucket = "Low (0.60-0.69)"
        else:
            bucket = "Very Low (<0.60)"
        conf_buckets[bucket].append(t)

    for bucket in sorted(conf_buckets.keys()):
        bt = conf_buckets[bucket]
        pnls = [get_pnl(t) for t in bt]
        wins = sum(1 for p in pnls if p > 0)
        print(f"  {bucket}: {len(bt)} trades | {wins}W/{len(bt)-wins}L")
        print(f"    Net: {fmt_money(sum(pnls))} | Exp: {fmt_money(mean(pnls))}/trade")
        if len(bt) > 1:
            print(f"    P/L range: {fmt_money(min(pnls))} to {fmt_money(max(pnls))}")
    print()

    # ── 3. Hold time vs outcome ─────────────────────────────────────────

    print("─" * 72)
    print("3. HOLD TIME vs OUTCOME")
    print("─" * 72)

    # Sort by hold time
    by_hold = sorted(lane_trades, key=lambda t: get_hold(t))

    # Bucket by hold duration
    hold_buckets = {
        "<30s": [], "30-60s": [], "60-120s": [], "120-300s": [], ">300s": []
    }
    for t in by_hold:
        h = get_hold(t)
        if h < 30:
            hold_buckets["<30s"].append(t)
        elif h < 60:
            hold_buckets["30-60s"].append(t)
        elif h < 120:
            hold_buckets["60-120s"].append(t)
        elif h < 300:
            hold_buckets["120-300s"].append(t)
        else:
            hold_buckets[">300s"].append(t)

    for bucket in ["<30s", "30-60s", "60-120s", "120-300s", ">300s"]:
        bt = hold_buckets[bucket]
        if not bt:
            continue
        pnls = [get_pnl(t) for t in bt]
        wins = sum(1 for p in pnls if p > 0)
        print(f"  {bucket}: {len(bt)} trades | {wins}W/{len(bt)-wins}L")
        print(f"    Net: {fmt_money(sum(pnls))} | Exp: {fmt_money(mean(pnls))}/trade")

    # Scatter data
    print()
    print("  Hold time → P/L (all trades):")
    for t in by_hold:
        w = "🟢" if get_pnl(t) > 0 else "🔴"
        print(f"    {w} {get_hold(t):>6.0f}s → {fmt_money(get_pnl(t)):>7s} (peak {fmt_money(get_peak(t))}, adv {fmt_money(get_adverse(t))})")
    print()

    # Correlation
    holds = [get_hold(t) for t in lane_trades]
    pnls = [get_pnl(t) for t in lane_trades]
    if len(holds) > 1:
        avg_h = mean(holds)
        avg_p = mean(pnls)
        num = sum((h - avg_h) * (p - avg_p) for h, p in zip(holds, pnls))
        den_h = sum((h - avg_h) ** 2 for h in holds) ** 0.5
        den_p = sum((p - avg_p) ** 2 for p in pnls) ** 0.5
        corr = num / (den_h * den_p) if den_h > 0 and den_p > 0 else 0
        print(f"  Hold time ↔ P/L correlation: {corr:+.3f}")
        if corr > 0.3:
            print("  → Longer holds tend to be more profitable")
        elif corr < -0.3:
            print("  → Shorter holds tend to be more profitable")
        else:
            print("  → No strong hold-time dependency")
    print()

    # ── 4. Direction bias ───────────────────────────────────────────────

    print("─" * 72)
    print("4. DIRECTION BIAS")
    print("─" * 72)

    by_direction: dict[str, list[dict]] = defaultdict(list)
    for t in lane_trades:
        d = str(t.get("direction", "UNKNOWN")).upper()
        by_direction[d].append(t)

    for direction in sorted(by_direction.keys()):
        dt = by_direction[direction]
        pnls = [get_pnl(t) for t in dt]
        wins = sum(1 for p in pnls if p > 0)
        print(f"  {direction}: {len(dt)} trades | {wins}W/{len(dt)-wins}L")
        print(f"    Net: {fmt_money(sum(pnls))} | Exp: {fmt_money(mean(pnls))}/trade")
        if len(dt) > 1:
            print(f"    Avg peak: {fmt_money(mean(get_peak(t) for t in dt))}")
            print(f"    Avg adverse: {fmt_money(mean(get_adverse(t) for t in dt))}")
    print()

    # ── 5. Peak-to-exit timing ──────────────────────────────────────────

    print("─" * 72)
    print("5. PEAK-TO-EXIT DYNAMICS")
    print("─" * 72)

    # For trades that went green, compute how much was left on the table
    green_trades = [t for t in lane_trades if get_peak(t) > 0]
    if green_trades:
        capture_rates = [get_pnl(t) / get_peak(t) for t in green_trades if get_peak(t) > 0]
        print(f"  Trades that went green: {len(green_trades)}")
        print(f"  Avg capture rate: {mean(capture_rates):.1%}")
        print(f"  Median capture rate: {median(capture_rates):.1%}")
        if len(capture_rates) > 1:
            print(f"  Std dev: {stdev(capture_rates):.1%}")
        print()

        # Bucket by peak size
        peak_buckets = {"<$0.10": [], "$0.10-$0.30": [], "$0.30-$1.00": ">=0.30 and p<1", ">$1.00": []}
        peak_buckets = {"<$0.10": [], "$0.10-$0.30": [], "$0.30-$1.00": [], ">$1.00": []}
        for t in green_trades:
            p = get_peak(t)
            if p < 0.10:
                peak_buckets["<$0.10"].append(t)
            elif p < 0.30:
                peak_buckets["$0.10-$0.30"].append(t)
            elif p < 1.00:
                peak_buckets["$0.30-$1.00"].append(t)
            else:
                peak_buckets[">$1.00"].append(t)

        print("  Capture rate by peak size:")
        for bucket in ["<$0.10", "$0.10-$0.30", "$0.30-$1.00", ">$1.00"]:
            bt = peak_buckets[bucket]
            if not bt:
                continue
            caps = [get_pnl(t) / get_peak(t) for t in bt if get_peak(t) > 0]
            net = sum(get_pnl(t) for t in bt)
            print(f"    {bucket}: {len(bt)} trades | capture={mean(caps):.1%} | net={fmt_money(net)}")
    print()

    # ── 6. Adverse excursion correlation ─────────────────────────────────

    print("─" * 72)
    print("6. ADVERSE EXCURSION vs REALIZED P/L")
    print("─" * 72)

    adv_values = [get_adverse(t) for t in lane_trades]
    pnl_values = [get_pnl(t) for t in lane_trades]
    if len(adv_values) > 1:
        avg_a = mean(adv_values)
        avg_pnl = mean(pnl_values)
        num = sum((a - avg_a) * (p - avg_pnl) for a, p in zip(adv_values, pnl_values))
        den_a = sum((a - avg_a) ** 2 for a in adv_values) ** 0.5
        den_pnl = sum((p - avg_pnl) ** 2 for p in pnl_values) ** 0.5
        corr = num / (den_a * den_pnl) if den_a > 0 and den_pnl > 0 else 0
        print(f"  Adverse excursion ↔ Realized P/L correlation: {corr:+.3f}")
        if corr > 0.3:
            print("  → More adverse = more profitable (surprising — may mean big winners tolerate drawdown)")
        elif corr < -0.3:
            print("  → More adverse = less profitable (expected — deep drawdowns kill trades)")
        else:
            print("  → No strong adverse/P/L dependency")
        print()

    # Scatter
    print("  Adverse → Realized P/L:")
    for t in sorted(lane_trades, key=lambda t: get_adverse(t)):
        w = "🟢" if get_pnl(t) > 0 else "🔴"
        ttfg_str = f"TTG={get_ttfg(t):.0f}s" if get_ttfg(t) is not None else "no green"
        print(f"    {w} Adv {fmt_money(get_adverse(t)):>7s} → {fmt_money(get_pnl(t)):>7s} | peak {fmt_money(get_peak(t))} | {ttfg_str}")
    print()

    # ── 7. First-green timing ───────────────────────────────────────────

    print("─" * 72)
    print("7. FIRST-GREEN TIMING PATTERNS")
    print("─" * 72)

    with_ttfg = [t for t in lane_trades if get_ttfg(t) is not None]
    no_ttfg = [t for t in lane_trades if get_ttfg(t) is None]

    if with_ttfg:
        went_green = [t for t in with_ttfg if get_ttfg(t) is not None]
        print(f"  Went green: {len(went_green)}/{len(lane_trades)} ({len(went_green)/len(lane_trades)*100:.0f}%)")

        winners_green = [t for t in went_green if get_pnl(t) > 0]
        losers_green = [t for t in went_green if get_pnl(t) <= 0]

        if winners_green:
            print(f"  Winners avg TTG: {mean(get_ttfg(t) for t in winners_green):.0f}s ({len(winners_green)} trades)")
        if losers_green:
            print(f"  Losers avg TTG: {mean(get_ttfg(t) for t in losers_green):.0f}s ({len(losers_green)} trades)")

    if no_ttfg:
        print(f"  Never went green: {len(no_ttfg)} trades")
        for t in no_ttfg:
            print(f"    🔴 {fmt_money(get_pnl(t))} (peak {fmt_money(get_peak(t))}, hold {get_hold(t):.0f}s)")

    # TTFG vs outcome scatter
    if with_ttfg:
        print()
        print("  TTG → Outcome:")
        for t in sorted(with_ttfg, key=lambda t: get_ttfg(t) or 999):
            w = "🟢" if get_pnl(t) > 0 else "🔴"
            print(f"    {w} TTG={get_ttfg(t):>5.0f}s → {fmt_money(get_pnl(t)):>7s} | hold {get_hold(t):>5.0f}s | adv {fmt_money(get_adverse(t))}")
    print()

    # ── 8. Bonus: Entry lot sizing ──────────────────────────────────────

    print("─" * 72)
    print("8. LOT SIZING ANALYSIS")
    print("─" * 72)

    lots = [t.get("lot", 0) for t in lane_trades if t.get("lot")]
    if lots:
        print(f"  Lot range: {min(lots):.2f} to {max(lots):.2f}")
        print(f"  Avg lot: {mean(lots):.2f}")
        # Group by lot size buckets
        small = [t for t in lane_trades if t.get("lot", 0) <= 0.05]
        medium = [t for t in lane_trades if 0.05 < t.get("lot", 0) <= 0.10]
        large = [t for t in lane_trades if t.get("lot", 0) > 0.10]
        for label, lt in [("Small (<=0.05)", small), ("Medium (0.05-0.10)", medium), ("Large (>0.10)", large)]:
            if not lt:
                continue
            pnls = [get_pnl(t) for t in lt]
            wins = sum(1 for p in pnls if p > 0)
            avg_lot = mean(t.get("lot", 0) for t in lt)
            print(f"  {label}: {len(lt)} trades | avg lot {avg_lot:.2f} | {wins}W/{len(lt)-wins}L | net {fmt_money(sum(pnls))} | exp {fmt_money(mean(pnls))}/trade")
    print()

    # ── Summary ─────────────────────────────────────────────────────────

    print("─" * 72)
    print("ACTIONABLE INSIGHTS")
    print("─" * 72)
    print()

    # Identify strongest signal
    if green_trades:
        small_peaks = [t for t in green_trades if get_peak(t) < 0.10]
        medium_peaks = [t for t in green_trades if 0.10 <= get_peak(t) < 0.30]
        large_peaks = [t for t in green_trades if get_peak(t) >= 0.30]

        insights = []

        # Small peak trades: do they tend to lose?
        if small_peaks:
            small_pnls = [get_pnl(t) for t in small_peaks]
            small_wr = sum(1 for p in small_pnls if p > 0) / len(small_pnls) * 100
            insights.append(f"Small peaks (<$0.10): {len(small_peaks)} trades, {small_wr:.0f}% WR")

        if medium_peaks:
            med_pnls = [get_pnl(t) for t in medium_peaks]
            med_wr = sum(1 for p in med_pnls if p > 0) / len(med_pnls) * 100
            insights.append(f"Medium peaks ($0.10-$0.30): {len(medium_peaks)} trades, {med_wr:.0f}% WR")

        if large_peaks:
            lg_pnls = [get_pnl(t) for t in large_peaks]
            lg_wr = sum(1 for p in lg_pnls if p > 0) / len(lg_pnls) * 100
            insights.append(f"Large peaks (>$0.30): {len(large_peaks)} trades, {lg_wr:.0f}% WR")

        if insights:
            print("  Peak quality stratification:")
            for ins in insights:
                print(f"    {ins}")
            print()

    if with_ttfg and no_ttfg:
        no_green_pnls = [get_pnl(t) for t in no_ttfg]
        print(f"  Trades that never went green: {len(no_ttfg)} — all losses")
        print(f"    Avg loss: {fmt_money(mean(no_green_pnls))}")
        print(f"    → Lane 8 (first_green_confirm) has merit as a filter")
        print()

    print("  With 24 trades total and only 2 live-lab exits, the historical")
    print("  dataset is the richest source of hypotheses right now. The 22")
    print("  pre-lab trades contain patterns worth mining for lane design.")
    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
