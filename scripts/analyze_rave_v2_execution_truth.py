#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "rave_rsi_mr_live_v2_state.json"
EVENTS_PATH = ROOT / "reports" / "rave_rsi_mr_live_v2_events.jsonl"
OUT_JSON_PATH = ROOT / "reports" / "rave_v2_execution_truth.json"
OUT_MD_PATH = ROOT / "reports" / "rave_v2_execution_truth.md"
STARTUP_WINDOW_SECONDS = 120.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def classify_phase(row: dict[str, Any], *, started_at: datetime | None) -> str:
    explicit = str(row.get("phase") or "").strip()
    if explicit:
        return explicit
    event_at = parse_iso(str(row.get("ts_utc") or ""))
    if event_at is None or started_at is None:
        return "unknown"
    if event_at <= started_at + timedelta(seconds=STARTUP_WINDOW_SECONDS):
        return "startup_backfill"
    return "live_forward"


def build_report() -> dict[str, Any]:
    state = load_json(STATE_PATH) or {}
    events = load_jsonl(EVENTS_PATH)
    started_at = parse_iso(str(state.get("started_at") or ""))

    phase_counts: dict[str, int] = {}
    action_phase_counts: dict[str, dict[str, int]] = {}
    explicit_phase_events = 0
    event_times: list[datetime] = []

    for row in events:
        phase = classify_phase(row, started_at=started_at)
        if row.get("phase"):
            explicit_phase_events += 1
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        action = str(row.get("action") or "unknown")
        per_action = action_phase_counts.setdefault(action, {})
        per_action[phase] = per_action.get(phase, 0) + 1
        event_at = parse_iso(str(row.get("ts_utc") or ""))
        if event_at is not None:
            event_times.append(event_at)

    startup_event_count = phase_counts.get("startup_backfill", 0)
    forward_event_count = phase_counts.get("live_forward", 0)
    if events and startup_event_count == len(events):
        provenance = "startup_backfill_only"
    elif events and forward_event_count == len(events):
        provenance = "forward_only"
    elif forward_event_count > 0 and startup_event_count > 0:
        provenance = "mixed"
    elif events:
        provenance = "unclassified"
    else:
        provenance = "no_events"

    earliest_event = min(event_times) if event_times else None
    latest_event = max(event_times) if event_times else None
    span_seconds = (
        round(max(0.0, (latest_event - earliest_event).total_seconds()), 1)
        if earliest_event is not None and latest_event is not None
        else None
    )

    warning = ""
    if provenance == "startup_backfill_only":
        warning = (
            "All observed V2 events are startup replay artifacts. "
            "Current slippage numbers are not yet forward-aged execution truth."
        )
    elif provenance == "mixed":
        warning = (
            "V2 events mix startup replay and forward activity. "
            "Forward-only slippage should be filtered before using it as benchmark truth."
        )
    elif provenance == "unclassified":
        warning = "Execution provenance could not be determined for all events."

    return {
        "generated_at": utc_now_iso(),
        "started_at": started_at.isoformat() if started_at is not None else None,
        "startup_window_seconds": STARTUP_WINDOW_SECONDS,
        "events_path": str(EVENTS_PATH),
        "state_path": str(STATE_PATH),
        "execution_truth": {
            "provenance": provenance,
            "warning": warning,
            "explicit_phase_events": explicit_phase_events,
            "explicit_phase_coverage_pct": round((explicit_phase_events / len(events)) * 100.0, 2) if events else 0.0,
            "total_events": len(events),
            "startup_event_count": startup_event_count,
            "forward_event_count": forward_event_count,
            "unknown_event_count": phase_counts.get("unknown", 0),
            "phase_counts": phase_counts,
            "action_phase_counts": action_phase_counts,
            "earliest_event_at": earliest_event.isoformat() if earliest_event is not None else None,
            "latest_event_at": latest_event.isoformat() if latest_event is not None else None,
            "event_span_seconds": span_seconds,
        },
    }


def render_md(payload: dict[str, Any]) -> str:
    truth = payload["execution_truth"]
    lines = [
        "# RAVE V2 Execution Truth",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        f"- Provenance: `{truth['provenance']}`",
        f"- Total events: `{truth['total_events']}`",
        f"- Startup replay events: `{truth['startup_event_count']}`",
        f"- Forward events: `{truth['forward_event_count']}`",
        f"- Explicit phase coverage %: `{truth['explicit_phase_coverage_pct']}`",
    ]
    if truth["warning"]:
        lines.extend(["", f"- Warning: `{truth['warning']}`"])
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_report()
    OUT_JSON_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD_PATH.write_text(render_md(payload), encoding="utf-8")
    print(json.dumps(payload["execution_truth"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
