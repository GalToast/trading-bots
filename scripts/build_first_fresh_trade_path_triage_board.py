#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
WATCHDOG_REPORT_JSON = REPORTS / "watchdog" / "crypto_watchdog_report.json"
ETH_BOARD_JSON = REPORTS / "eth_atr_runtime_status_board.json"
SHAPESHIFTER_BOARD_JSON = REPORTS / "structure_shapeshifter_proof_board.json"
OUTPUT_JSON = REPORTS / "first_fresh_trade_path_triage_board.json"
OUTPUT_MD = REPORTS / "first_fresh_trade_path_triage_board.md"

OPEN_ACTION = "open_ticket"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except Exception:
        return str(path)


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds_from_iso(value: Any, *, now: datetime) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def _is_close_like(event: dict[str, Any]) -> bool:
    action = str(event.get("action") or "")
    return action == "close_ticket" or action.startswith("escape_")


def event_rows_since(events: list[dict[str, Any]], started_at: str) -> list[dict[str, Any]]:
    started_dt = parse_iso(started_at)
    if started_dt is None:
        return list(events)
    filtered: list[dict[str, Any]] = []
    for event in events:
        ts = parse_iso(event.get("ts_utc"))
        if ts is None or ts >= started_dt:
            filtered.append(event)
    return filtered


def classify_first_trade_path(events_since_start: list[dict[str, Any]]) -> dict[str, Any]:
    open_events = [event for event in events_since_start if str(event.get("action") or "") == OPEN_ACTION]
    close_events = [event for event in events_since_start if _is_close_like(event)]
    first_runtime_event = events_since_start[0] if events_since_start else {}
    first_open = open_events[0] if open_events else {}
    first_close = close_events[0] if close_events else {}

    if not events_since_start:
        verdict = "awaiting_post_restart_runtime_event"
        rationale = "No runtime event exists yet in the current post-restart window."
    elif not open_events and not close_events:
        verdict = "awaiting_first_trade_path_event"
        rationale = "Post-restart runtime is alive, but no open_ticket or close-like event exists yet."
    elif open_events and not close_events:
        verdict = "first_path_opened_waiting_close"
        rationale = "A fresh open_ticket exists, but the first post-restart path has not closed yet."
    else:
        try:
            realized_pnl = float(first_close.get("realized_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            realized_pnl = 0.0
        saw_green = bool(first_close.get("first_green_before_fail")) or first_close.get("time_to_first_green_seconds") not in (None, "")
        if realized_pnl < 0.0 and not saw_green:
            verdict = "never_green_toxic_continuation"
            rationale = "The first close-like path realized a loss without any recorded first-green transition."
        elif realized_pnl < 0.0 and saw_green:
            verdict = "went_green_failed_monetization"
            rationale = "The first close-like path went green but still realized a loss."
        elif realized_pnl >= 0.0 and saw_green:
            verdict = "green_and_monetized"
            rationale = "The first close-like path reached first green and exited non-negative."
        else:
            verdict = "closed_without_recorded_green"
            rationale = "The first close-like path exited non-negative without a recorded first-green transition."

    return {
        "verdict": verdict,
        "rationale": rationale,
        "first_runtime_event_action": str(first_runtime_event.get("action") or ""),
        "first_runtime_event_ts_utc": str(first_runtime_event.get("ts_utc") or ""),
        "first_open_ts_utc": str(first_open.get("ts_utc") or ""),
        "first_open_direction": str(first_open.get("direction") or ""),
        "first_open_entry_context": str(first_open.get("entry_context") or ""),
        "first_close_ts_utc": str(first_close.get("ts_utc") or ""),
        "first_close_action": str(first_close.get("action") or ""),
        "first_close_direction": str(first_close.get("direction") or ""),
        "first_close_realized_pnl": first_close.get("realized_pnl"),
        "first_close_time_to_first_green_seconds": first_close.get("time_to_first_green_seconds"),
        "first_close_peak_pnl_before_exit": first_close.get("peak_pnl_before_exit"),
    }


def build_lane_row(watchdog_row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    event_path = Path(str(watchdog_row.get("event_path") or ""))
    events = load_jsonl(event_path) if event_path else []
    runner = watchdog_row.get("runner") if isinstance(watchdog_row.get("runner"), dict) else {}
    started_at = str(runner.get("started_at") or "")
    events_since_start = event_rows_since(events, started_at)
    triage = classify_first_trade_path(events_since_start)
    return {
        "lane": str(watchdog_row.get("name") or ""),
        "watchdog_status": str(watchdog_row.get("status") or ""),
        "runner_pid": int((runner.get("pid") or 0) if isinstance(runner, dict) else 0),
        "runner_started_at": started_at,
        "runner_heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "runner_heartbeat_age_seconds": age_seconds_from_iso(runner.get("heartbeat_at"), now=now),
        "event_path": display_path(event_path),
        "events_since_runner_start": len(events_since_start),
        "first_runtime_event_action": triage["first_runtime_event_action"],
        "first_runtime_event_ts_utc": triage["first_runtime_event_ts_utc"],
        "verdict": triage["verdict"],
        "rationale": triage["rationale"],
        "first_open_ts_utc": triage["first_open_ts_utc"],
        "first_open_direction": triage["first_open_direction"],
        "first_open_entry_context": triage["first_open_entry_context"],
        "first_close_ts_utc": triage["first_close_ts_utc"],
        "first_close_action": triage["first_close_action"],
        "first_close_direction": triage["first_close_direction"],
        "first_close_realized_pnl": triage["first_close_realized_pnl"],
        "first_close_time_to_first_green_seconds": triage["first_close_time_to_first_green_seconds"],
        "first_close_peak_pnl_before_exit": triage["first_close_peak_pnl_before_exit"],
    }


def build_payload(
    *,
    now: datetime | None = None,
    watchdog_payload: dict[str, Any] | None = None,
    eth_payload: dict[str, Any] | None = None,
    shapeshifter_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    watchdog_payload = watchdog_payload if watchdog_payload is not None else load_json(WATCHDOG_REPORT_JSON)
    eth_payload = eth_payload if eth_payload is not None else load_json(ETH_BOARD_JSON)
    shapeshifter_payload = shapeshifter_payload if shapeshifter_payload is not None else load_json(SHAPESHIFTER_BOARD_JSON)

    watchdog_rows = watchdog_payload.get("rows") if isinstance(watchdog_payload.get("rows"), list) else []
    watchdog_by_lane = {
        str(row.get("name") or ""): row
        for row in watchdog_rows
        if isinstance(row, dict) and str(row.get("name") or "")
    }
    eth_rows = eth_payload.get("active_rows") if isinstance(eth_payload.get("active_rows"), list) else []
    monitored_lanes = [str(row.get("lane") or "") for row in eth_rows if isinstance(row, dict)]
    shapeshifter_lane = str(shapeshifter_payload.get("lane_name") or "")
    if shapeshifter_lane:
        monitored_lanes.append(shapeshifter_lane)

    lane_rows = [
        build_lane_row(watchdog_by_lane[lane_name], now=now)
        for lane_name in monitored_lanes
        if lane_name in watchdog_by_lane
    ]

    if any(row["verdict"] in {
        "never_green_toxic_continuation",
        "went_green_failed_monetization",
        "green_and_monetized",
        "closed_without_recorded_green",
    } for row in lane_rows):
        overall_status = "first_trade_path_available"
        next_action = "Read the first close-like verdicts now; at least one monitored lane has produced a real post-restart trade path."
    elif any(row["verdict"] == "first_path_opened_waiting_close" for row in lane_rows):
        overall_status = "trade_path_open_waiting_close"
        next_action = "At least one monitored lane has opened a fresh post-restart path. Wait for the first close-like event before judging quality."
    elif any(row["verdict"] == "awaiting_first_trade_path_event" for row in lane_rows):
        overall_status = "waiting_for_trade_path"
        next_action = "Runtime is active after restart, but the monitored lanes have not emitted a fresh open/close-like event yet."
    else:
        overall_status = "waiting_for_post_restart_runtime_event"
        next_action = "At least one monitored lane has not emitted any runtime event in the current post-restart window yet."

    return {
        "generated_at": now.isoformat(),
        "overall_status": overall_status,
        "next_action": next_action,
        "lane_count": len(lane_rows),
        "lanes": lane_rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# First Fresh Trade-Path Triage Board",
        "",
        "> Current runtime generated board.",
        "> Use this to answer the first post-restart proof question fast: are we still waiting for a trade path, waiting for the first close, or already looking at a real first-path verdict?",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- overall_status: `{payload.get('overall_status', '')}`",
        f"- next_action: `{payload.get('next_action', '')}`",
        f"- lane_count: `{int(payload.get('lane_count', 0) or 0)}`",
        "",
        "| Lane | Watchdog | PID | First Runtime Event | Verdict | First Open | First Close |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for row in payload.get("lanes") or []:
        lines.append(
            f"| `{row.get('lane', '')}` | `{row.get('watchdog_status', '') or '-'}` | `{int(row.get('runner_pid', 0) or 0)}` | "
            f"`{row.get('first_runtime_event_action', '') or '-'} @ {row.get('first_runtime_event_ts_utc', '') or '-'}` | "
            f"`{row.get('verdict', '')}` | "
            f"`{row.get('first_open_ts_utc', '') or '-'}` | "
            f"`{row.get('first_close_ts_utc', '') or '-'}` |"
        )

    lines.extend(["", "## Lane Detail", ""])
    for row in payload.get("lanes") or []:
        lines.extend(
            [
                f"### `{row.get('lane', '')}`",
                "",
                f"- watchdog_status: `{row.get('watchdog_status', '') or '-'}`",
                f"- runner_started_at: `{row.get('runner_started_at', '') or 'missing'}`",
                f"- events_since_runner_start: `{int(row.get('events_since_runner_start', 0) or 0)}`",
                f"- first_runtime_event_action: `{row.get('first_runtime_event_action', '') or 'missing'}`",
                f"- first_runtime_event_ts_utc: `{row.get('first_runtime_event_ts_utc', '') or 'missing'}`",
                f"- verdict: `{row.get('verdict', '')}`",
                f"- rationale: `{row.get('rationale', '')}`",
                f"- first_open_ts_utc: `{row.get('first_open_ts_utc', '') or 'missing'}`",
                f"- first_open_direction: `{row.get('first_open_direction', '') or 'missing'}`",
                f"- first_open_entry_context: `{row.get('first_open_entry_context', '') or 'missing'}`",
                f"- first_close_ts_utc: `{row.get('first_close_ts_utc', '') or 'missing'}`",
                f"- first_close_action: `{row.get('first_close_action', '') or 'missing'}`",
                f"- first_close_direction: `{row.get('first_close_direction', '') or 'missing'}`",
                f"- first_close_realized_pnl: `{row.get('first_close_realized_pnl', 'missing')}`",
                f"- first_close_time_to_first_green_seconds: `{row.get('first_close_time_to_first_green_seconds', 'missing')}`",
                f"- first_close_peak_pnl_before_exit: `{row.get('first_close_peak_pnl_before_exit', 'missing')}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Interpretation",
            "",
            "This board is intentionally narrow: it is about the *first* post-restart trade path only.",
            "Use `awaiting_post_restart_runtime_event` when the runner is fresh but the journal has not advanced at all in the current window.",
            "Use `awaiting_first_trade_path_event` when runtime is alive after restart but there is still no `open_ticket` or close-like event.",
            "Use `first_path_opened_waiting_close` when the first post-restart path exists but has not closed yet.",
            "Once a close-like event appears, the verdict compresses immediately to the shortest honest causal read: never green, went green but failed monetization, green and monetized, or closed without recorded green.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
