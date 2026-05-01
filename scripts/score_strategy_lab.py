#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG = ROOT / "trade_behavior_log.jsonl"
LAB_LOG = ROOT / "strategy_lab_events.jsonl"
DEFAULT_LANE = ("USDJPY", "breakout_hold_above_high", "SNIPER", "PRICE")
LOCAL_TZ = ZoneInfo("America/Chicago")


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


def lane_matches(row: dict, lane: tuple[str, str, str, str], trade_log: bool) -> bool:
    symbol, signal_type, mode, regime = lane
    if trade_log:
        return (
            str(row.get("symbol", "")).upper() == symbol
            and str(row.get("entry_signal_type", "")) == signal_type
            and str(row.get("entry_mode", "")).upper() == mode
            and str(row.get("regime_at_entry", "")).upper() == regime
        )
    return (
        str(row.get("symbol", "")).upper() == symbol
        and str(row.get("signal_type", "")) == signal_type
        and str(row.get("mode", "")).upper() == mode
        and str(row.get("regime", "")).upper() == regime
    )


def fmt_pnl(value: float) -> str:
    return f"{value:+.2f}"


def summarize_trade_window(name: str, rows: list[dict]) -> list[str]:
    total = len(rows)
    wins = [r for r in rows if float(r.get("realized_pnl", 0.0) or 0.0) > 0]
    losses = [r for r in rows if float(r.get("realized_pnl", 0.0) or 0.0) < 0]
    total_pnl = sum(float(r.get("realized_pnl", 0.0) or 0.0) for r in rows)
    avg_win = mean(float(r.get("realized_pnl", 0.0) or 0.0) for r in wins) if wins else 0.0
    avg_loss = mean(float(r.get("realized_pnl", 0.0) or 0.0) for r in losses) if losses else 0.0
    first_green_rate = (
        sum(1 for r in rows if r.get("first_green_before_fail")) / total * 100.0 if total else 0.0
    )
    avg_hold = mean(float(r.get("hold_seconds", 0.0) or 0.0) for r in rows) if rows else 0.0
    avg_mfe = mean(float(r.get("max_favorable_excursion_pnl", 0.0) or 0.0) for r in rows) if rows else 0.0
    avg_mae = mean(float(r.get("max_adverse_excursion_pnl", 0.0) or 0.0) for r in rows) if rows else 0.0
    payoff = (avg_win / abs(avg_loss)) if wins and losses and avg_loss != 0 else 0.0
    exit_counts = Counter(str(r.get("exit_reason", "UNKNOWN")).split("(")[0].strip() or "UNKNOWN" for r in rows)

    lines = [
        f"{name}: trades={total} win_rate={(len(wins) / total * 100.0 if total else 0.0):.1f}% "
        f"pnl={fmt_pnl(total_pnl)} expectancy={fmt_pnl(total_pnl / total if total else 0.0)} "
        f"payoff={payoff:.2f} first_green={first_green_rate:.1f}% avg_hold={avg_hold:.0f}s "
        f"avg_mfe={fmt_pnl(avg_mfe)} avg_mae={fmt_pnl(avg_mae)}"
    ]
    if exit_counts:
        lines.append(
            "  exits: "
            + ", ".join(f"{reason}={count}" for reason, count in exit_counts.most_common(5))
        )
    return lines


def summarize_event_window(name: str, rows: list[dict]) -> list[str]:
    if not rows:
        return [f"{name}: no lab events"]
    event_counts = Counter(str(r.get("event_type", "unknown")) for r in rows)
    reason_counts = Counter(str(r.get("reason", "")) for r in rows if r.get("reason"))
    lines = [f"{name}: " + ", ".join(f"{k}={v}" for k, v in event_counts.most_common())]
    if reason_counts:
        lines.append(
            "  reasons: " + ", ".join(f"{k}={v}" for k, v in reason_counts.most_common(6))
        )
    return lines


def recent_events(rows: list[dict], limit: int) -> list[str]:
    if not rows:
        return ["Recent events: none"]
    lines = ["Recent events:"]
    recent = sorted(
        rows,
        key=lambda r: parse_ts(r.get("recorded_at_utc")) or datetime.min.replace(tzinfo=timezone.utc),
    )[-limit:]
    for row in recent:
        ts = parse_ts(row.get("recorded_at_utc"))
        ts_label = ts.astimezone(LOCAL_TZ).strftime("%H:%M:%S") if ts else "?"
        details = []
        for key in ("reason", "confidence", "ticket", "realized_pnl", "exit_reason"):
            if key in row and row.get(key) not in ("", None):
                details.append(f"{key}={row.get(key)}")
        lines.append(f"  {ts_label} {row.get('event_type', '?')}: " + ", ".join(details))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Score one strategy-lab lane from live telemetry.")
    parser.add_argument("--symbol", default=DEFAULT_LANE[0])
    parser.add_argument("--signal", default=DEFAULT_LANE[1])
    parser.add_argument("--mode", default=DEFAULT_LANE[2])
    parser.add_argument("--regime", default=DEFAULT_LANE[3])
    parser.add_argument("--hours", type=float, default=6.0, help="Recent event window in hours")
    parser.add_argument("--recent", type=int, default=12, help="Number of recent lab events to print")
    args = parser.parse_args()

    lane = (args.symbol.upper(), args.signal, args.mode.upper(), args.regime.upper())
    trades = [r for r in load_jsonl(TRADE_LOG) if lane_matches(r, lane, trade_log=True)]
    events = [r for r in load_jsonl(LAB_LOG) if lane_matches(r, lane, trade_log=False)]

    now_utc = datetime.now(timezone.utc)
    today_local = now_utc.astimezone(LOCAL_TZ).date()
    recent_cutoff = now_utc - timedelta(hours=float(args.hours))

    today_trades = []
    recent_trades = []
    for row in trades:
        dt = parse_ts(row.get("exit_time_utc") or row.get("recorded_at_utc"))
        if dt is None:
            continue
        if dt.astimezone(LOCAL_TZ).date() == today_local:
            today_trades.append(row)
        if dt >= recent_cutoff:
            recent_trades.append(row)

    recent_events_rows = []
    for row in events:
        dt = parse_ts(row.get("recorded_at_utc"))
        if dt is not None and dt >= recent_cutoff:
            recent_events_rows.append(row)

    print(f"Strategy lab lane: {lane[0]}|{lane[1]}|{lane[2]}|{lane[3]}")
    print(f"Trade log: {TRADE_LOG}")
    print(f"Lab log:   {LAB_LOG}")
    print()

    for line in summarize_trade_window("Today (local)", today_trades):
        print(line)
    for line in summarize_trade_window(f"Last {args.hours:g}h", recent_trades):
        print(line)
    print()
    for line in summarize_event_window(f"Lab events last {args.hours:g}h", recent_events_rows):
        print(line)
    print()
    for line in recent_events(recent_events_rows, args.recent):
        print(line)


if __name__ == "__main__":
    main()
