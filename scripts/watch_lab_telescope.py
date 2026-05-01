#!/usr/bin/env python3
"""Real-time telemetry watch for USDJPY breakout lab.

Monitors strategy_lab_events.jsonl for new events and alerts on:
- First new strategy_lab row after last checkpoint
- First opened/exit event with lane metadata
- Give-back % and mfe_capture_pct for completed exits
- Active lane status (control/challenger)

Usage:
  python scripts/watch_lab_telescope.py --poll 5   # poll every 5s
  python scripts/watch_lab_telescope.py --tail      # one-shot tail

Author: local AI-assisted research pass
"""
from __future__ import annotations

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
LAB_LOG = ROOT / "strategy_lab_events.jsonl"
LANE = ("USDJPY", "breakout_hold_above_high", "SNIPER", "PRICE")
LAST_SEEN_MARKER = ROOT / ".lab_telescope_last_seen"


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


def lane_matches(row: dict) -> bool:
    return (
        str(row.get("symbol", "")).upper() == LANE[0]
        and str(row.get("signal_type", "")) == LANE[1]
        and str(row.get("mode", "")).upper() == LANE[2]
        and str(row.get("regime", "")).upper() == LANE[3]
    )


def get_last_seen() -> int:
    if LAST_SEEN_MARKER.exists():
        try:
            return int(LAST_SEEN_MARKER.read_text().strip())
        except (ValueError, OSError):
            pass
    return 0


def save_last_seen(line_count: int) -> None:
    LAST_SEEN_MARKER.write_text(str(line_count))


def analyze_exits(events: list[dict]) -> list[dict]:
    """Extract completed exit events with give-back analysis."""
    exits = []
    for ev in events:
        if ev.get("event_type") == "exit" and lane_matches(ev):
            peak = float(ev.get("peak_pnl_before_exit", 0.0) or 0.0)
            realized = float(ev.get("realized_pnl", 0.0) or 0.0)
            giveback_pct = ((peak - realized) / peak * 100.0) if peak > 0 else 0.0
            mfe_capture = (realized / peak) if peak > 0 else 0.0
            exits.append({
                "ticket": ev.get("ticket"),
                "peak_pnl": peak,
                "realized_pnl": realized,
                "giveback_pct": giveback_pct,
                "mfe_capture_pct": mfe_capture,
                "hold_seconds": ev.get("hold_seconds"),
                "exit_reason": ev.get("exit_reason", ""),
                "first_green": ev.get("first_green_before_fail"),
                "recorded_at": ev.get("recorded_at_utc"),
            })
    return exits


def print_event(ev: dict, prefix: str = "") -> None:
    ts = parse_ts(ev.get("recorded_at_utc"))
    ts_label = ts.strftime("%H:%M:%S") if ts else "??"
    etype = ev.get("event_type", "?")
    details = []
    for key in ("ticket", "remaining_seconds", "realized_pnl", "exit_reason",
                "peak_pnl_before_exit", "hold_seconds", "time_to_first_green_seconds",
                "reason", "holdoff_seconds"):
        if key in ev and ev.get(key) not in ("", None):
            details.append(f"{key}={ev.get(key)}")
    detail_str = ", ".join(details)
    print(f"  [{ts_label}] {prefix}{etype}: {detail_str}")


def one_shot_tail() -> None:
    """Show current state and latest events."""
    events = load_jsonl(LAB_LOG)
    lane_events = [ev for ev in events if lane_matches(ev)]
    exits = analyze_exits(lane_events)

    print("─" * 60)
    print("LAB TELESCOPE — Current State")
    print("─" * 60)
    print(f"Lane: {'|'.join(LANE)}")
    print(f"Total lab events: {len(lane_events)}")
    print(f"Completed exits: {len(exits)}")
    print()

    if exits:
        print("Exit Analysis:")
        for ex in exits:
            ts = parse_ts(ex["recorded_at"])
            ts_label = ts.strftime("%H:%M:%S") if ts else "??"
            print(f"  [{ts_label}] Ticket #{ex['ticket']}")
            print(f"    Peak: {fmt_money(ex['peak_pnl'])} → Exit: {fmt_money(ex['realized_pnl'])}")
            print(f"    Give-back: {ex['giveback_pct']:.1f}% | MFE capture: {ex['mfe_capture_pct']:.1%}")
            print(f"    Hold: {ex['hold_seconds']}s | TTG: {ex.get('first_green')}")
            print(f"    Reason: {ex['exit_reason']}")
            print()

        net = sum(ex["realized_pnl"] for ex in exits)
        avg_gb = mean(ex["giveback_pct"] for ex in exits)
        avg_capture = mean(ex["mfe_capture_pct"] for ex in exits)
        wins = sum(1 for ex in exits if ex["realized_pnl"] > 0)
        print(f"  Summary: {len(exits)} trades | {wins}W/{len(exits)-wins}L")
        print(f"  Net: {fmt_money(net)} | Avg give-back: {avg_gb:.1f}% | Avg capture: {avg_capture:.1%}")
        print()

    # Show last 10 events
    recent = lane_events[-10:]
    print("Recent events (last 10):")
    for ev in recent:
        print_event(ev)
    print()


def watch_loop(poll_seconds: float = 5.0) -> None:
    """Poll for new events and alert on material changes."""
    print("─" * 60)
    print("LAB TELESCOPE — Real-time Watch")
    print(f"Poll interval: {poll_seconds}s")
    print("Alert on: new entries, exits, holdoff completions")
    print("─" * 60)
    print()

    last_count = get_last_seen()
    alert_count = 0

    try:
        while True:
            events = load_jsonl(LAB_LOG)
            lane_events = [ev for ev in events if lane_matches(ev)]
            new_events = lane_events[last_count:]

            if new_events:
                # Check for material events
                for ev in new_events:
                    etype = ev.get("event_type", "")
                    alert = False
                    prefix = ""

                    if etype in ("opened", "pre_open"):
                        alert = True
                        prefix = "🟢 "
                    elif etype == "exit":
                        alert = True
                        prefix = "🔴 "
                        peak = float(ev.get("peak_pnl_before_exit", 0.0) or 0.0)
                        realized = float(ev.get("realized_pnl", 0.0) or 0.0)
                        giveback = ((peak - realized) / peak * 100.0) if peak > 0 else 0.0
                        capture = (realized / peak) if peak > 0 else 0.0
                        print(f"  📊 EXIT: Ticket #{ev.get('ticket')}")
                        print(f"    Peak: {fmt_money(peak)} → Exit: {fmt_money(realized)}")
                        print(f"    Give-back: {giveback:.1f}% | MFE capture: {capture:.1%}")
                        print(f"    Hold: {ev.get('hold_seconds')}s")

                    elif etype == "entry_admitted":
                        alert = True
                        prefix = "✅ "
                    elif etype == "entry_holdoff_expired":
                        alert = True
                        prefix = "❌ "
                    elif etype == "entry_holdoff_started":
                        alert = True
                        prefix = "⏱  "

                    if alert:
                        alert_count += 1
                        print_event(ev, prefix=prefix)
                        print()

                last_count = len(lane_events)
                save_last_seen(last_count)

            time.sleep(poll_seconds)

    except KeyboardInterrupt:
        print()
        print(f"Watch ended. {alert_count} alerts fired. Last seen: {last_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lab telescope — real-time USDJPY breakout monitor")
    parser.add_argument("--poll", type=float, default=5.0, help="Poll interval in seconds")
    parser.add_argument("--tail", action="store_true", help="One-shot tail and exit")
    parser.add_argument("--reset", action="store_true", help="Reset last-seen marker")
    args = parser.parse_args()

    if args.reset and LAST_SEEN_MARKER.exists():
        LAST_SEEN_MARKER.unlink()
        print("Last-seen marker cleared.")
        return

    if args.tail:
        one_shot_tail()
    else:
        watch_loop(args.poll)


if __name__ == "__main__":
    main()
