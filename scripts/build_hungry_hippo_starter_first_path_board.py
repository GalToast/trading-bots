#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

WATCH_BOARD_PATH = REPORTS / "hungry_hippo_forward_shadow_watch_board.json"
PACKET_BOARD_PATH = REPORTS / "hungry_hippo_first_proof_launch_packet_board.json"
ROLLOUT_GATE_PATH = REPORTS / "hungry_hippo_parallel_rollout_gate_board.json"
OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_starter_first_path_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_starter_first_path_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
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


def relative_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def find_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    clean_symbol = str(symbol or "").upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() == clean_symbol:
            return dict(row)
    return None


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def close_like(event: dict[str, Any]) -> bool:
    action = str(event.get("action") or "")
    return action == "close_ticket" or action.startswith("escape_") or action == "forced_unwind"


def realized_pnl_from_close(event: dict[str, Any]) -> float:
    for key in ("realized_pnl", "realized_pnl_usd", "profit_usd", "close_pnl", "net_pnl"):
        if key in event:
            return as_float(event.get(key))
    return 0.0


def opening_shape_verdict(first_open_events: list[dict[str, Any]]) -> str:
    if not first_open_events:
        return "no_open_cluster"
    same_tick_max = max(as_int(event.get("same_tick_open_burst_count")) for event in first_open_events)
    contexts = {str(event.get("entry_context") or "") for event in first_open_events}
    sessions = {str(event.get("session_bucket") or "") for event in first_open_events}
    if same_tick_max >= 5 and "off_session" in sessions and any("wide_spread" in item for item in contexts):
        return "burst_off_session_wide_spread"
    if same_tick_max >= 5:
        return "burst_clustered_open"
    return "small_or_staggered_open"


def classify_first_path(
    *,
    open_events: list[dict[str, Any]],
    close_events: list[dict[str, Any]],
    open_tickets: list[dict[str, Any]],
) -> tuple[str, str]:
    green_seen_count = sum(1 for ticket in open_tickets if bool(ticket.get("first_green_seen")))
    if not open_events and not close_events:
        return (
            "awaiting_first_open",
            "The starter lane has not emitted a starter-path open yet.",
        )
    if open_events and not close_events:
        if green_seen_count > 0:
            return (
                "opened_green_waiting_close",
                "The starter path has already gone green on current open inventory, but no close-like event has landed yet.",
            )
        return (
            "opened_waiting_close",
            "The starter path has opened, but it has not recorded green or a close-like event yet.",
        )

    first_close = close_events[0]
    realized_pnl = realized_pnl_from_close(first_close)
    saw_green = (
        bool(first_close.get("first_green_before_fail"))
        or first_close.get("time_to_first_green_seconds") not in (None, "")
        or as_float(first_close.get("peak_pnl_before_exit")) > 0.0
    )
    if realized_pnl < 0.0 and not saw_green:
        return (
            "first_close_never_green_loss",
            "The first close-like path realized a loss without any recorded green transition.",
        )
    if realized_pnl < 0.0 and saw_green:
        return (
            "first_close_went_green_failed_monetization",
            "The first close-like path went green but still realized a loss.",
        )
    if realized_pnl >= 0.0 and saw_green:
        return (
            "first_close_green_and_monetized",
            "The first close-like path went green and exited non-negative.",
        )
    return (
        "first_close_without_recorded_green",
        "The first close-like path exited without a recorded green transition.",
    )


def summarize_first_path(
    *,
    symbol: str,
    state_payload: dict[str, Any],
    event_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    symbol_state = dict(((state_payload.get("symbols") or {}).get(symbol) or {}))
    open_tickets = list(symbol_state.get("open_tickets") or [])
    open_events = [event for event in event_rows if str(event.get("action") or "") == "open_ticket"]
    close_events = [event for event in event_rows if close_like(event)]
    first_open_ts = str((open_events[0] if open_events else {}).get("ts_utc") or "")
    first_open_time_msc = (open_events[0] if open_events else {}).get("time_msc")
    if first_open_time_msc not in (None, ""):
        first_open_events = [event for event in open_events if event.get("time_msc") == first_open_time_msc]
    else:
        first_open_events = [event for event in open_events if str(event.get("ts_utc") or "") == first_open_ts] if first_open_ts else []

    verdict, rationale = classify_first_path(
        open_events=open_events,
        close_events=close_events,
        open_tickets=open_tickets,
    )
    first_green_seen_count = sum(1 for ticket in open_tickets if bool(ticket.get("first_green_seen")))

    return {
        "verdict": verdict,
        "rationale": rationale,
        "first_open_ts_utc": first_open_ts,
        "first_close_ts_utc": str((close_events[0] if close_events else {}).get("ts_utc") or ""),
        "first_close_action": str((close_events[0] if close_events else {}).get("action") or ""),
        "first_close_realized_pnl": realized_pnl_from_close(close_events[0]) if close_events else 0.0,
        "first_cohort_open_count": len(first_open_events),
        "first_cohort_same_tick_burst_max": max((as_int(event.get("same_tick_open_burst_count")) for event in first_open_events), default=0),
        "first_cohort_same_bar_burst_max": max((as_int(event.get("same_bar_open_burst_count")) for event in first_open_events), default=0),
        "first_cohort_session_buckets": sorted({str(event.get("session_bucket") or "") for event in first_open_events if str(event.get("session_bucket") or "")}),
        "first_cohort_entry_contexts": sorted({str(event.get("entry_context") or "") for event in first_open_events if str(event.get("entry_context") or "")}),
        "first_cohort_regimes": sorted({str(event.get("regime_at_entry") or "") for event in first_open_events if str(event.get("regime_at_entry") or "")}),
        "first_cohort_opening_shape_verdict": opening_shape_verdict(first_open_events),
        "first_cohort_max_spread_at_entry": max((as_float(event.get("spread_at_entry")) for event in first_open_events), default=0.0),
        "current_open_count": len(open_tickets),
        "current_first_green_seen_count": first_green_seen_count,
        "current_peak_pnl_before_exit_max": max((as_float(ticket.get("peak_pnl_before_exit")) for ticket in open_tickets), default=0.0),
        "current_mfe_pnl_max": max((as_float(ticket.get("max_favorable_excursion_pnl")) for ticket in open_tickets), default=0.0),
        "current_mae_pnl_min": min((as_float(ticket.get("max_adverse_excursion_pnl")) for ticket in open_tickets), default=0.0),
        "realized_closes": as_int(symbol_state.get("realized_closes")),
        "realized_net_usd": as_float(symbol_state.get("realized_net_usd")),
        "rearm_opens": as_int(symbol_state.get("rearm_opens")),
    }


def build_payload(
    watch_payload: dict[str, Any],
    packet_payload: dict[str, Any],
    rollout_payload: dict[str, Any],
) -> dict[str, Any]:
    starter_symbol = str((packet_payload.get("summary") or {}).get("starter_candidate_symbol") or "")
    packet_rows = list(packet_payload.get("rows") or [])
    watch_rows = list(watch_payload.get("rows") or [])
    rollout_rows = list(rollout_payload.get("rows") or [])

    packet_row = find_symbol(packet_rows, starter_symbol) or {}
    watch_row = find_symbol(watch_rows, starter_symbol) or {}
    state_path_text = str(packet_row.get("state_path") or watch_row.get("state_path") or "")
    event_path_text = str(packet_row.get("event_path") or watch_row.get("event_path") or "")
    state_path = ROOT / Path(state_path_text.replace("\\", "/")) if state_path_text else Path()
    event_path = ROOT / Path(event_path_text.replace("\\", "/")) if event_path_text else Path()
    state_payload = load_json(state_path) if state_path_text else {}
    event_rows = load_jsonl(event_path) if event_path_text else []
    first_path = summarize_first_path(
        symbol=starter_symbol,
        state_payload=state_payload,
        event_rows=event_rows,
    ) if starter_symbol else {}
    slot1_row = dict(rollout_rows[0] if rollout_rows else {})

    leadership_read = []
    if starter_symbol:
        leadership_read.append(
            f"Current HH starter symbol is `{starter_symbol}` and slot `#1` remains blocked until fresh forward proof exists."
        )
    if first_path:
        leadership_read.append(
            f"Starter first-path verdict is `{first_path.get('verdict')}`: {first_path.get('rationale')}"
        )
        leadership_read.append(
            f"Opening shape is `{first_path.get('first_cohort_opening_shape_verdict')}` with `first_cohort_open_count={first_path.get('first_cohort_open_count')}` and `current_first_green_seen_count={first_path.get('current_first_green_seen_count')}`."
        )
    if slot1_row:
        leadership_read.append(
            f"Unlock doctrine is unchanged: `{slot1_row.get('current_status')}` until the starter path produces enough realized forward evidence."
        )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(WATCH_BOARD_PATH.relative_to(ROOT)),
            str(PACKET_BOARD_PATH.relative_to(ROOT)),
            str(ROLLOUT_GATE_PATH.relative_to(ROOT)),
            relative_path_text(state_path) if state_path_text else "",
            relative_path_text(event_path) if event_path_text else "",
        ],
        "summary": {
            "starter_symbol": starter_symbol,
            "starter_runtime_state": str(watch_row.get("runtime_state") or packet_row.get("runtime_state") or ""),
            "starter_launch_readiness": str(packet_row.get("launch_readiness") or ""),
            "starter_first_path_verdict": str(first_path.get("verdict") or ""),
            "starter_realized_closes": first_path.get("realized_closes", 0),
            "starter_realized_net_usd": first_path.get("realized_net_usd", 0.0),
            "slot1_unlock_status": str(slot1_row.get("current_status") or ""),
            "current_max_honest_active_lanes": int((rollout_payload.get("summary") or {}).get("current_max_honest_active_lanes") or 0),
        },
        "leadership_read": leadership_read,
        "starter": {
            "symbol": starter_symbol,
            "runtime_state": str(watch_row.get("runtime_state") or packet_row.get("runtime_state") or ""),
            "launch_readiness": str(packet_row.get("launch_readiness") or ""),
            "config_path": str(packet_row.get("config_path") or watch_row.get("config_path") or ""),
            "state_path": relative_path_text(state_path) if state_path_text else "",
            "event_path": relative_path_text(event_path) if event_path_text else "",
            "slot1_unlock_status": str(slot1_row.get("current_status") or ""),
            "slot1_blocker_reason": str(slot1_row.get("blocker_reason") or ""),
            **first_path,
        },
        "notes": [
            "This is a starter-path support surface, not a launch or promotion authority by itself.",
            "Use it to answer the narrow question: did slot #1 merely open, go green without monetizing, or actually produce a first close that changes the unlock conversation?",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    starter = dict(payload.get("starter") or {})
    lines = [
        "# Hungry Hippo Starter First-Path Board",
        "",
        "> Current starter-lane path triage for the tiny-account Hungry Hippo rollout.",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- starter_symbol: `{summary.get('starter_symbol')}`",
            f"- starter_runtime_state: `{summary.get('starter_runtime_state')}`",
            f"- starter_launch_readiness: `{summary.get('starter_launch_readiness')}`",
            f"- starter_first_path_verdict: `{summary.get('starter_first_path_verdict')}`",
            f"- starter_realized_closes: `{summary.get('starter_realized_closes')}`",
            f"- starter_realized_net_usd: `{summary.get('starter_realized_net_usd')}`",
            f"- slot1_unlock_status: `{summary.get('slot1_unlock_status')}`",
            f"- current_max_honest_active_lanes: `{summary.get('current_max_honest_active_lanes')}`",
            "",
            "## Starter Detail",
            "",
            f"- runtime_state: `{starter.get('runtime_state')}`",
            f"- launch_readiness: `{starter.get('launch_readiness')}`",
            f"- config_path: `{starter.get('config_path')}`",
            f"- state_path: `{starter.get('state_path')}`",
            f"- event_path: `{starter.get('event_path')}`",
            f"- first_path_verdict: `{starter.get('verdict')}`",
            f"- first_path_rationale: {starter.get('rationale')}",
            f"- first_open_ts_utc: `{starter.get('first_open_ts_utc') or 'missing'}`",
            f"- first_close_ts_utc: `{starter.get('first_close_ts_utc') or 'missing'}`",
            f"- first_close_realized_pnl: `{starter.get('first_close_realized_pnl')}`",
            f"- first_cohort_open_count: `{starter.get('first_cohort_open_count')}`",
            f"- first_cohort_same_tick_burst_max: `{starter.get('first_cohort_same_tick_burst_max')}`",
            f"- first_cohort_opening_shape_verdict: `{starter.get('first_cohort_opening_shape_verdict')}`",
            f"- first_cohort_session_buckets: `{starter.get('first_cohort_session_buckets')}`",
            f"- first_cohort_entry_contexts: `{starter.get('first_cohort_entry_contexts')}`",
            f"- first_cohort_regimes: `{starter.get('first_cohort_regimes')}`",
            f"- first_cohort_max_spread_at_entry: `{starter.get('first_cohort_max_spread_at_entry')}`",
            f"- current_open_count: `{starter.get('current_open_count')}`",
            f"- current_first_green_seen_count: `{starter.get('current_first_green_seen_count')}`",
            f"- current_peak_pnl_before_exit_max: `{starter.get('current_peak_pnl_before_exit_max')}`",
            f"- current_mfe_pnl_max: `{starter.get('current_mfe_pnl_max')}`",
            f"- current_mae_pnl_min: `{starter.get('current_mae_pnl_min')}`",
            f"- slot1_unlock_status: `{starter.get('slot1_unlock_status')}`",
            f"- slot1_blocker_reason: {starter.get('slot1_blocker_reason')}",
            "",
            "## Notes",
            "",
        ]
    )
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    payload = build_payload(
        load_json(WATCH_BOARD_PATH),
        load_json(PACKET_BOARD_PATH),
        load_json(ROLLOUT_GATE_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
