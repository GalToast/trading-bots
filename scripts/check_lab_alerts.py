#!/usr/bin/env python3
"""One-shot check for new lab events since last check. Writes JSON alert file."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB_LOG = ROOT / "strategy_lab_events.jsonl"
ALERT_FILE = ROOT / "reports" / "lab_alerts.json"
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


def main() -> None:
    events = load_jsonl(LAB_LOG)
    lane_events = [
        ev for ev in events
        if (str(ev.get("symbol", "")).upper() == LANE[0]
            and str(ev.get("signal_type", "")) == LANE[1]
            and str(ev.get("mode", "")).upper() == LANE[2]
            and str(ev.get("regime", "")).upper() == LANE[3])
    ]

    # Load last check state
    if ALERT_FILE.exists():
        with ALERT_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)
        last_count = state.get("last_event_count", 0)
    else:
        state = {"alerts": [], "last_event_count": 0, "last_check": ""}
        last_count = 0

    new_events = lane_events[last_count:]

    if not new_events:
        print(f"No new events. Total lane events: {len(lane_events)}, last checked: {last_count}")
        return

    alerts = []
    for ev in new_events:
        etype = ev.get("event_type", "")
        if etype in ("opened", "exit", "entry_admitted", "entry_holdoff_expired",
                      "entry_holdoff_started", "exit_challenger_triggered"):
            alert = {
                "event_type": etype,
                "timestamp": ev.get("recorded_at_utc"),
                "ticket": ev.get("ticket"),
            }
            if etype == "exit":
                peak = float(ev.get("peak_pnl_before_exit", 0.0) or 0.0)
                realized = float(ev.get("realized_pnl", 0.0) or 0.0)
                giveback = ((peak - realized) / peak * 100.0) if peak > 0 else 0.0
                capture = (realized / peak) if peak > 0 else 0.0
                alert["peak_pnl"] = peak
                alert["realized_pnl"] = realized
                alert["giveback_pct"] = round(giveback, 1)
                alert["mfe_capture_pct"] = round(capture, 1)
                alert["hold_seconds"] = ev.get("hold_seconds")
                alert["exit_reason"] = ev.get("exit_reason", "")
            alerts.append(alert)

    if alerts:
        state["alerts"].extend(alerts)
        state["last_event_count"] = len(lane_events)
        state["last_check"] = datetime.now(timezone.utc).isoformat()

        with ALERT_FILE.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        print(f"ALERT: {len(alerts)} new material events detected:")
        for a in alerts:
            print(f"  {a['event_type']} at {a['timestamp']}")
            if a.get("realized_pnl") is not None:
                print(f"    Peak: ${a['peak_pnl']:+.2f} -> Exit: ${a['realized_pnl']:+.2f}")
                print(f"    Give-back: {a['giveback_pct']:.1f}% | MFE capture: {a['mfe_capture_pct']:.1%}")
    else:
        print(f"No material new events (checked {len(new_events)} raw events)")

    print(f"Total lane events: {len(lane_events)}, last checked: {len(lane_events)}")


if __name__ == "__main__":
    main()
