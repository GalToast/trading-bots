#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

QUEUE_PATH = REPORTS / "adaptive_lab_queue.json"
BRANCH_PATH = REPORTS / "adaptive_btc_branch_decision_board.json"
RESTORE_PATH = REPORTS / "btc_m15_warp_restore_board.json"
RUNTIME_AUDIT_PATH = REPORTS / "btc_adaptive_runtime_audit.json"
NZD_PROBE_PATH = REPORTS / "nzdusd_transfer_probe.json"
GBP_PACKET_PATH = REPORTS / "gbpusd_adaptive_shadow_packet.json"
PROOF_PATH = REPORTS / "adaptive_lattice_proof_board.json"
EXECUTION_PATH = REPORTS / "execution_monitor_report.json"
REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"
QUARANTINE_PATH = REPORTS / "watchdog" / "crypto_watchdog_quarantine_state.json"

OUTPUT_JSON = REPORTS / "adaptive_overnight_launch_packet_board.json"
OUTPUT_MD = REPORTS / "adaptive_overnight_launch_packet_board.md"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return load_json(path)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_report_path(value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value)
    return candidate if candidate.is_absolute() else ROOT / candidate


def first_path_triage(events: list[dict[str, Any]]) -> dict[str, Any]:
    open_events = [event for event in events if str(event.get("action") or "") == "open_ticket"]
    close_events = [
        event
        for event in events
        if str(event.get("action") or "") in {"close_ticket", "forced_unwind", "offensive_close", "escape_close"}
    ]
    first_open = open_events[0] if open_events else {}
    first_close = close_events[0] if close_events else {}
    first_close_dt = parse_iso_utc(str(first_close.get("ts_utc") or ""))
    first_path_open_events = list(open_events)
    if first_close_dt is not None:
        bounded_opens: list[dict[str, Any]] = []
        for event in open_events:
            event_dt = parse_iso_utc(str(event.get("ts_utc") or ""))
            if event_dt is not None and event_dt <= first_close_dt:
                bounded_opens.append(event)
        if bounded_opens:
            first_path_open_events = bounded_opens

    first_path_same_bar_burst = max(
        (
            int(event.get("same_bar_open_burst_count_at_open") or event.get("same_bar_open_burst_count") or 0)
            for event in first_path_open_events
        ),
        default=0,
    )
    first_path_same_tick_burst = max(
        (
            int(event.get("same_tick_open_burst_count_at_open") or event.get("same_tick_open_burst_count") or 0)
            for event in first_path_open_events
        ),
        default=0,
    )

    if not open_events and not close_events:
        verdict = "awaiting_first_trade_path_event"
        rationale = "No open_ticket or close-like event exists yet in the direct packet log."
    elif open_events and not close_events:
        verdict = "first_path_opened_waiting_close"
        rationale = "A direct packet open_ticket exists, but no close-like event has completed the first path yet."
    else:
        try:
            realized_pnl = float(first_close.get("realized_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            realized_pnl = 0.0
        saw_green = bool(first_close.get("first_green_before_fail")) or first_close.get("time_to_first_green_seconds") not in (
            None,
            "",
        )
        if realized_pnl < 0.0 and not saw_green:
            verdict = "never_green_toxic_continuation"
            rationale = "The first close-like event realized a loss without any recorded first-green transition."
        elif realized_pnl < 0.0 and saw_green:
            verdict = "went_green_failed_monetization"
            rationale = "The first close-like event went green before exit but still realized a loss."
        elif realized_pnl >= 0.0 and saw_green:
            verdict = "green_and_monetized"
            rationale = "The first close-like event reached first green and exited non-negative."
        else:
            verdict = "closed_without_recorded_green"
            rationale = "The first close-like event exited non-negative without a recorded first-green transition."

    return {
        "verdict": verdict,
        "rationale": rationale,
        "first_open_ts_utc": str(first_open.get("ts_utc") or ""),
        "first_open_direction": str(first_open.get("direction") or ""),
        "first_open_entry_context": str(first_open.get("entry_context") or ""),
        "first_open_regime_at_entry": str(first_open.get("regime_at_entry") or ""),
        "first_open_same_bar_open_burst_count": first_path_same_bar_burst,
        "first_open_same_tick_open_burst_count": first_path_same_tick_burst,
        "first_close_ts_utc": str(first_close.get("ts_utc") or ""),
        "first_close_action": str(first_close.get("action") or ""),
        "first_close_realized_pnl": first_close.get("realized_pnl"),
        "first_close_time_to_first_green_seconds": first_close.get("time_to_first_green_seconds"),
    }


def guarded_admission_triage(
    events: list[dict[str, Any]],
    *,
    guard_open_admission_enabled: bool | None,
    pre_start_event_count: int = 0,
) -> dict[str, Any]:
    guarded_events = [
        event for event in events if str(event.get("action") or "") == "open_guarded_admission"
    ]
    close_events = [
        event
        for event in events
        if str(event.get("action") or "") in {"close_ticket", "forced_unwind", "offensive_close", "escape_close"}
    ]
    first_close_dt = parse_iso_utc(str(close_events[0].get("ts_utc") or "")) if close_events else None
    first_path_guarded_events = list(guarded_events)
    if first_close_dt is not None:
        bounded_guarded_events: list[dict[str, Any]] = []
        for event in guarded_events:
            event_dt = parse_iso_utc(str(event.get("ts_utc") or ""))
            if event_dt is not None and event_dt <= first_close_dt:
                bounded_guarded_events.append(event)
        first_path_guarded_events = bounded_guarded_events

    latest_event = dict(guarded_events[-1]) if guarded_events else {}
    first_path_latest = dict(first_path_guarded_events[-1]) if first_path_guarded_events else {}

    if guarded_events:
        status = "observed_current_run"
        read = (
            f"`open_guarded_admission` fired `{len(guarded_events)}` time(s) in the current run, "
            f"most recently at `{latest_event.get('ts_utc', '')}` during stage "
            f"`{latest_event.get('stage', '') or 'n/a'}`."
        )
    elif pre_start_event_count > 0:
        status = "pre_start_only"
        read = (
            f"`open_guarded_admission` appears `{pre_start_event_count}` time(s) in pre-start file history, "
            "but not in the current run yet."
        )
    elif guard_open_admission_enabled is True:
        status = "guard_enabled_waiting_trigger"
        read = "The current artifact exposes `guard_open_admission=true`, but no guarded-admission event has fired yet."
    elif guard_open_admission_enabled is False:
        status = "runtime_explicitly_not_guarded"
        read = "The current artifact explicitly exposes `guard_open_admission=false`, so guarded-open is not active on this packet."
    else:
        status = "runtime_visibility_missing"
        read = "The current artifact exposes neither `guard_open_admission` nor any `open_guarded_admission` event yet."

    return {
        "status": status,
        "read": read,
        "guard_open_admission_enabled": guard_open_admission_enabled,
        "current_run_event_count": len(guarded_events),
        "pre_start_event_count": int(pre_start_event_count),
        "first_path_event_count": len(first_path_guarded_events),
        "latest_ts_utc": str(latest_event.get("ts_utc") or ""),
        "latest_stage": str(latest_event.get("stage") or ""),
        "latest_direction": str(latest_event.get("direction") or ""),
        "latest_trigger_level": latest_event.get("trigger_level"),
        "first_path_latest_ts_utc": str(first_path_latest.get("ts_utc") or ""),
        "first_path_latest_stage": str(first_path_latest.get("stage") or ""),
    }


def find_execution_row(payload: dict[str, Any], lane_name: str) -> dict[str, Any] | None:
    for row in list(payload.get("rows") or []):
        if str(row.get("lane") or "") == lane_name:
            return dict(row)
    return None


def find_registry_lane(payload: dict[str, Any], lane_name: str) -> dict[str, Any] | None:
    for lane in list(payload.get("lanes") or []):
        if str(lane.get("name") or "") == lane_name:
            return dict(lane)
    return None


def find_quarantine_lane(payload: dict[str, Any], lane_name: str) -> dict[str, Any] | None:
    lanes = payload.get("lanes") or {}
    if not isinstance(lanes, dict):
        return None
    lane = lanes.get(lane_name)
    if not isinstance(lane, dict):
        return None
    return dict(lane)


def command_from_registry_lane(registry_lane: dict[str, Any] | None) -> list[str]:
    restart_args = list((registry_lane or {}).get("restart_args") or [])
    if not restart_args:
        return []
    return ["python", *restart_args]


def inspect_packet_artifacts(state_path_value: str | None, event_path_value: str | None) -> dict[str, Any]:
    state_path = resolve_report_path(state_path_value)
    event_path = resolve_report_path(event_path_value)
    state_payload = load_json_if_exists(state_path)
    runner = dict(state_payload.get("runner") or {})
    metadata = dict(state_payload.get("metadata") or {})

    open_positions = 0
    symbol_guard_values: list[bool] = []
    for symbol_state in list((state_payload.get("symbols") or {}).values()):
        if isinstance(symbol_state, dict):
            open_positions += len(list(symbol_state.get("open_tickets") or symbol_state.get("positions") or []))
            if "guard_open_admission" in symbol_state:
                symbol_guard_values.append(bool(symbol_state.get("guard_open_admission")))

    guard_open_admission_enabled = metadata.get("guard_open_admission")
    if guard_open_admission_enabled is None and symbol_guard_values:
        guard_open_admission_enabled = symbol_guard_values[0]
    if guard_open_admission_enabled is not None:
        guard_open_admission_enabled = bool(guard_open_admission_enabled)

    event_open_count = 0
    event_close_like_count = 0
    last_event_ts = ""
    parsed_events: list[dict[str, Any]] = []
    if event_path and event_path.exists():
        for raw_line in event_path.read_text(encoding="utf-8").splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            parsed_events.append(event)
            action = str(event.get("action") or "")
            if action == "open_ticket":
                event_open_count += 1
            if action in {"close_ticket", "forced_unwind", "offensive_close", "escape_close"}:
                event_close_like_count += 1
            ts_utc = str(event.get("ts_utc") or "")
            if ts_utc:
                last_event_ts = ts_utc

    heartbeat_at = str(runner.get("heartbeat_at") or "")
    started_at = str(runner.get("started_at") or "")
    started_dt = parse_iso_utc(started_at)
    current_window_events = []
    pre_start_trade_opens = 0
    pre_start_trade_closes = 0
    pre_start_guarded_admission_events = 0
    for event in parsed_events:
        event_ts = parse_iso_utc(str(event.get("ts_utc") or ""))
        action = str(event.get("action") or "")
        if started_dt is not None and event_ts is not None and event_ts < started_dt:
            if action == "open_ticket":
                pre_start_trade_opens += 1
            if action in {"close_ticket", "forced_unwind", "offensive_close", "escape_close"}:
                pre_start_trade_closes += 1
            if action == "open_guarded_admission":
                pre_start_guarded_admission_events += 1
            continue
        current_window_events.append(event)

    current_window_open_count = 0
    current_window_close_count = 0
    current_window_last_event_ts = ""
    for event in current_window_events:
        action = str(event.get("action") or "")
        if action == "open_ticket":
            current_window_open_count += 1
        if action in {"close_ticket", "forced_unwind", "offensive_close", "escape_close"}:
            current_window_close_count += 1
        ts_utc = str(event.get("ts_utc") or "")
        if ts_utc:
            current_window_last_event_ts = ts_utc

    started = bool(started_at or heartbeat_at or event_open_count or event_close_like_count)

    return {
        "started": started,
        "state_path_exists": bool(state_path and state_path.exists()),
        "event_path_exists": bool(event_path and event_path.exists()),
        "guard_open_admission_enabled": guard_open_admission_enabled,
        "runner_pid": int(runner.get("pid") or 0) if runner.get("pid") else 0,
        "runner_started_at": started_at,
        "runner_heartbeat_at": heartbeat_at,
        "runner_heartbeat_age_seconds": None if iso_age_seconds(heartbeat_at) is None else round(float(iso_age_seconds(heartbeat_at)), 1),
        "direct_open_positions": open_positions,
        "event_trade_opens": current_window_open_count,
        "event_trade_closes": current_window_close_count,
        "last_event_ts": current_window_last_event_ts,
        "last_event_age_seconds": None
        if iso_age_seconds(current_window_last_event_ts) is None
        else round(float(iso_age_seconds(current_window_last_event_ts)), 1),
        "pre_start_trade_opens": pre_start_trade_opens,
        "pre_start_trade_closes": pre_start_trade_closes,
        "mt5_identity_ok": bool((metadata.get("mt5_connection") or {}).get("identity_ok")),
        "first_path_triage": first_path_triage(current_window_events),
        "guarded_admission_triage": guarded_admission_triage(
            current_window_events,
            guard_open_admission_enabled=guard_open_admission_enabled,
            pre_start_event_count=pre_start_guarded_admission_events,
        ),
    }


def row_packet(
    *,
    packet_id: str,
    title: str,
    lane_name: str,
    action_status: str,
    action_read: str,
    why: str,
    registry_lane: dict[str, Any] | None,
    execution_row: dict[str, Any] | None,
    packet_artifacts: dict[str, Any] | None = None,
    command: list[str] | None = None,
    authority_inputs: list[str] | None = None,
) -> dict[str, Any]:
    registry_present = registry_lane is not None
    execution_present = execution_row is not None
    packet_artifacts = packet_artifacts or {}
    artifact_started = bool(packet_artifacts.get("started"))
    artifact_open_count = int(packet_artifacts.get("direct_open_positions") or 0)
    artifact_trade_opens = int(packet_artifacts.get("event_trade_opens") or 0)
    artifact_trade_closes = int(packet_artifacts.get("event_trade_closes") or 0)
    artifact_pre_start_trade_opens = int(packet_artifacts.get("pre_start_trade_opens") or 0)
    artifact_pre_start_trade_closes = int(packet_artifacts.get("pre_start_trade_closes") or 0)
    triage = dict(packet_artifacts.get("first_path_triage") or {})
    guarded_triage = dict(packet_artifacts.get("guarded_admission_triage") or {})
    return {
        "packet_id": packet_id,
        "title": title,
        "lane_name": lane_name,
        "action_status": action_status,
        "action_read": action_read,
        "why": why,
        "registry_present": registry_present,
        "registry_enabled": bool(registry_lane.get("enabled", True)) if registry_present else False,
        "registry_pause_note": str(registry_lane.get("pause_note") or "") if registry_present else "",
        "execution_present": execution_present,
        "execution_watchdog_status": str(execution_row.get("watchdog_status") or "") if execution_present else "",
        "execution_open_count": int(execution_row.get("open_count") or 0) if execution_present else 0,
        "execution_trade_opens": int(execution_row.get("event_trade_opens") or 0) if execution_present else 0,
        "execution_trade_closes": int(execution_row.get("event_trade_closes") or 0) if execution_present else 0,
        "artifact_started": artifact_started,
        "artifact_runner_pid": int(packet_artifacts.get("runner_pid") or 0),
        "artifact_runner_started_at": str(packet_artifacts.get("runner_started_at") or ""),
        "artifact_runner_heartbeat_at": str(packet_artifacts.get("runner_heartbeat_at") or ""),
        "artifact_runner_heartbeat_age_seconds": packet_artifacts.get("runner_heartbeat_age_seconds"),
        "artifact_open_count": artifact_open_count,
        "artifact_trade_opens": artifact_trade_opens,
        "artifact_trade_closes": artifact_trade_closes,
        "artifact_pre_start_trade_opens": artifact_pre_start_trade_opens,
        "artifact_pre_start_trade_closes": artifact_pre_start_trade_closes,
        "artifact_last_event_ts": str(packet_artifacts.get("last_event_ts") or ""),
        "artifact_last_event_age_seconds": packet_artifacts.get("last_event_age_seconds"),
        "artifact_mt5_identity_ok": bool(packet_artifacts.get("mt5_identity_ok")),
        "artifact_guard_open_admission_enabled": packet_artifacts.get("guard_open_admission_enabled"),
        "first_path_verdict": str(triage.get("verdict") or ""),
        "first_path_rationale": str(triage.get("rationale") or ""),
        "first_path_open_ts_utc": str(triage.get("first_open_ts_utc") or ""),
        "first_path_open_direction": str(triage.get("first_open_direction") or ""),
        "first_path_open_entry_context": str(triage.get("first_open_entry_context") or ""),
        "first_path_open_regime_at_entry": str(triage.get("first_open_regime_at_entry") or ""),
        "first_path_open_same_bar_open_burst_count": int(triage.get("first_open_same_bar_open_burst_count") or 0),
        "first_path_open_same_tick_open_burst_count": int(triage.get("first_open_same_tick_open_burst_count") or 0),
        "first_path_close_ts_utc": str(triage.get("first_close_ts_utc") or ""),
        "first_path_close_action": str(triage.get("first_close_action") or ""),
        "first_path_close_realized_pnl": triage.get("first_close_realized_pnl"),
        "first_path_close_time_to_first_green_seconds": triage.get("first_close_time_to_first_green_seconds"),
        "guarded_admission_status": str(guarded_triage.get("status") or ""),
        "guarded_admission_read": str(guarded_triage.get("read") or ""),
        "guarded_admission_event_count": int(guarded_triage.get("current_run_event_count") or 0),
        "guarded_admission_pre_start_event_count": int(guarded_triage.get("pre_start_event_count") or 0),
        "guarded_admission_first_path_event_count": int(guarded_triage.get("first_path_event_count") or 0),
        "guarded_admission_latest_ts_utc": str(guarded_triage.get("latest_ts_utc") or ""),
        "guarded_admission_latest_stage": str(guarded_triage.get("latest_stage") or ""),
        "guarded_admission_first_path_latest_ts_utc": str(guarded_triage.get("first_path_latest_ts_utc") or ""),
        "guarded_admission_first_path_latest_stage": str(guarded_triage.get("first_path_latest_stage") or ""),
        "command": command or [],
        "authority_inputs": authority_inputs or [],
    }


def classify_restore_packet(
    *,
    queue_summary: dict[str, Any],
    branch_summary: dict[str, Any],
    restore_candidate: dict[str, Any],
    registry_lane: dict[str, Any] | None,
    execution_row: dict[str, Any] | None,
    packet_artifacts: dict[str, Any],
    quarantine_lane: dict[str, Any] | None,
) -> tuple[str, str, str, dict[str, Any]]:
    registry_enabled = bool(registry_lane.get("enabled", True)) if registry_lane else False
    pause_note = str(registry_lane.get("pause_note") or "") if registry_lane else ""
    execution_watchdog_status = str(execution_row.get("watchdog_status") or "") if execution_row else ""
    quarantine_lane = quarantine_lane or {}
    quarantine_reason = str(quarantine_lane.get("reason") or "")
    quarantined_until = str(quarantine_lane.get("quarantined_until") or "")
    artifact_started = bool(packet_artifacts.get("started"))
    restore_disabled_or_quarantined = (
        (registry_lane is not None and not registry_enabled)
        or bool(quarantine_lane)
        or execution_watchdog_status == "quarantined"
        or "quarantine" in pause_note.lower()
    )

    if restore_disabled_or_quarantined:
        packet_artifacts = dict(packet_artifacts)
        packet_artifacts["first_path_triage"] = {
            "verdict": "inactive_after_supervision_failure",
            "rationale": "Direct packet artifacts exist from the failed restore attempt, but the lane is disabled and outside the current healthy overnight set.",
            "first_open_ts_utc": "",
            "first_open_direction": "",
            "first_open_entry_context": "",
            "first_open_regime_at_entry": "",
            "first_close_ts_utc": "",
            "first_close_action": "",
            "first_close_realized_pnl": None,
            "first_close_time_to_first_green_seconds": None,
        }
        why_parts = [
            "Restore-comparison remains the recommended BTC control branch, but it is not part of tonight's healthy supervised set.",
        ]
        if quarantine_reason:
            why_parts.append(
                f"Last watchdog verdict was `{quarantine_reason}`"
                + (f" until `{quarantined_until}`." if quarantined_until else ".")
            )
        elif pause_note:
            why_parts.append(f"Registry is paused with note `{pause_note}`.")
        if artifact_started:
            why_parts.append(
                f"Direct packet residue still shows runner start `{packet_artifacts.get('runner_started_at')}` with stale artifact heartbeat age `{packet_artifacts.get('runner_heartbeat_age_seconds')}`s."
            )
        if packet_artifacts.get("pre_start_trade_opens") or packet_artifacts.get("pre_start_trade_closes"):
            why_parts.append(
                f"Event files still carry pre-start history `{packet_artifacts.get('pre_start_trade_opens')}` / `{packet_artifacts.get('pre_start_trade_closes')}`, so artifact presence is not current-run proof."
            )
        return (
            "hold_runtime_repair_candidate",
            "recommended BTC control branch is paused pending runtime repair",
            " ".join(why_parts),
            packet_artifacts,
        )

    if artifact_started:
        return (
            "already_running_monitor_only",
            "restore-comparison shadow already started from its direct packet",
            (
                f"Restore-comparison shadow already started at `{packet_artifacts.get('runner_started_at')}` "
                f"with heartbeat age `{packet_artifacts.get('runner_heartbeat_age_seconds')}`s, "
                f"current-run artifact trades `{packet_artifacts.get('event_trade_opens')}` open / `{packet_artifacts.get('event_trade_closes')}` close-like"
                f" (pre-start file history `{packet_artifacts.get('pre_start_trade_opens')}` / `{packet_artifacts.get('pre_start_trade_closes')}`)."
            ),
            packet_artifacts,
        )

    return (
        "launch_now_manual_packet",
        "ready launch packet exists but lane is not yet registered",
        (
            f"{restore_candidate.get('action') or 'Launch the restore comparison shadow.'} "
            f"Queue ready task is `{queue_summary.get('highest_priority_ready_task_id')}` and BTC branch recommendation is `{branch_summary.get('recommended_branch_id')}`."
        ),
        packet_artifacts,
    )


def build_payload(
    queue: dict[str, Any] | None = None,
    branch: dict[str, Any] | None = None,
    restore: dict[str, Any] | None = None,
    runtime_audit: dict[str, Any] | None = None,
    nzd_probe: dict[str, Any] | None = None,
    gbpusd_packet: dict[str, Any] | None = None,
    proof: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    quarantine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    queue = queue or load_json(QUEUE_PATH)
    branch = branch or load_json(BRANCH_PATH)
    restore = restore or load_json(RESTORE_PATH)
    runtime_audit = runtime_audit or load_json(RUNTIME_AUDIT_PATH)
    nzd_probe = nzd_probe or load_json(NZD_PROBE_PATH)
    gbpusd_packet = gbpusd_packet or load_json_if_exists(GBP_PACKET_PATH)
    proof = proof or load_json(PROOF_PATH)
    execution = execution or load_json(EXECUTION_PATH)
    registry = registry or load_json(REGISTRY_PATH)
    quarantine = load_json_if_exists(QUARANTINE_PATH) if quarantine is None else quarantine

    queue_summary = dict(queue.get("summary") or {})
    branch_summary = dict(branch.get("summary") or {})
    restore_candidate = dict(restore.get("restore_candidate") or {})
    runtime_summary = dict(runtime_audit.get("summary") or {})
    runtime_lane = dict(runtime_audit.get("runtime_lane") or {})
    nzd_summary = dict(nzd_probe.get("summary") or {})
    nzd_runtime_lane = dict(nzd_probe.get("runtime_lane") or {})
    proof_rows = {str(row.get("symbol") or ""): row for row in list(proof.get("rows") or [])}

    restore_lane_name = str(restore_candidate.get("lane") or "shadow_btcusd_m15_warp_restore_v1")
    parked_lane_name = str(runtime_audit.get("lane_name") or runtime_lane.get("lane_name") or "shadow_btcusd_m15_adaptive_regime")
    nzd_lane_name = str(nzd_runtime_lane.get("lane_name") or "shadow_nzdusd_m15_asym")
    restore_registry_lane = find_registry_lane(registry, restore_lane_name)
    restore_execution_row = find_execution_row(execution, restore_lane_name)
    restore_quarantine_lane = find_quarantine_lane(quarantine, restore_lane_name)
    restore_artifacts = inspect_packet_artifacts(
        str(restore_candidate.get("state_path") or ""),
        str(restore_candidate.get("event_path") or ""),
    )
    restore_action_status, restore_action_read, restore_why, restore_artifacts = classify_restore_packet(
        queue_summary=queue_summary,
        branch_summary=branch_summary,
        restore_candidate=restore_candidate,
        registry_lane=restore_registry_lane,
        execution_row=restore_execution_row,
        packet_artifacts=restore_artifacts,
        quarantine_lane=restore_quarantine_lane,
    )

    rows = [
        row_packet(
            packet_id="btc_restore_comparison_shadow",
            title="Launch the BTC M15 warp restore comparison shadow",
            lane_name=restore_lane_name,
            action_status=restore_action_status,
            action_read=restore_action_read,
            why=restore_why,
            registry_lane=restore_registry_lane,
            execution_row=restore_execution_row,
            packet_artifacts=restore_artifacts,
            command=list(restore_candidate.get("command") or []),
            authority_inputs=[
                "reports/adaptive_lab_queue.json",
                "reports/adaptive_btc_branch_decision_board.json",
                "reports/btc_m15_warp_restore_board.json",
            ],
        ),
        row_packet(
            packet_id="btc_parked_adaptive_artifact",
            title="Keep the parked BTC adaptive artifact in hold/manual-review only",
            lane_name=parked_lane_name,
            action_status="hold_parked_artifact",
            action_read="parked direct-live artifact remains historical context only",
            why=str(runtime_summary.get("completion_read") or ""),
            registry_lane=find_registry_lane(registry, parked_lane_name),
            execution_row=find_execution_row(execution, parked_lane_name),
            authority_inputs=[
                "reports/btc_adaptive_runtime_audit.json",
                "reports/adaptive_btc_branch_decision_board.json",
            ],
        ),
        row_packet(
            packet_id="nzdusd_transfer_probe",
            title="Keep the NZDUSD adapt-first transfer probe running",
            lane_name=nzd_lane_name,
            action_status="already_running_monitor_only",
            action_read="shadow lane is already running under research-only posture",
            why=str(nzd_summary.get("completion_read") or ""),
            registry_lane=find_registry_lane(registry, nzd_lane_name),
            execution_row=find_execution_row(execution, nzd_lane_name),
            authority_inputs=[
                "reports/nzdusd_transfer_probe.json",
                "reports/adaptive_transfer_board.json",
                "reports/adaptive_lab_queue.json",
            ],
        ),
    ]

    gbp_queue_task = next(
        (
            dict(task)
            for task in list(queue.get("tasks") or [])
            if str(task.get("task_id") or "") == "gbpusd_adaptive_comparison_packet"
        ),
        {},
    )
    gbp_packet_summary = dict(gbpusd_packet.get("summary") or {})
    gbp_packet_contract = dict(gbpusd_packet.get("packet_contract") or {})
    if gbp_queue_task and gbp_packet_summary.get("packet_defined"):
        gbp_lane_name = str(gbp_packet_contract.get("lane_name") or "shadow_gbpusd_m15_trend_harvest_v1")
        gbp_registry_lane = find_registry_lane(registry, gbp_lane_name)
        gbp_execution_row = find_execution_row(execution, gbp_lane_name)
        gbp_artifacts = inspect_packet_artifacts(
            str(gbp_packet_contract.get("state_path") or ""),
            str(gbp_packet_contract.get("event_path") or ""),
        )
        gbp_action_status = "hold_launch_packet_defined_not_started"
        gbp_action_read = "adaptive GBP packet is defined but intentionally held until the first deliberate shadow launch"
        gbp_queue_why = str(gbp_queue_task.get("why") or "").strip()
        gbp_completion_read = str(gbp_packet_summary.get("completion_read") or "").strip()
        gbp_why_parts = [gbp_queue_why] if gbp_queue_why else []
        if gbp_completion_read and gbp_completion_read not in gbp_queue_why:
            gbp_why_parts.append(gbp_completion_read)
        gbp_why = " ".join(gbp_why_parts)
        if gbp_artifacts.get("started") or (gbp_execution_row and str(gbp_execution_row.get("watchdog_status") or "") == "ok"):
            gbp_action_status = "already_running_monitor_only"
            gbp_action_read = "adaptive trend-harvest shadow already running under the dedicated GBP packet"
            gbp_why = (
                f"{gbp_why} Current runtime lane `{gbp_lane_name}` is already supervised with watchdog status "
                f"`{str(gbp_execution_row.get('watchdog_status') or '')}` and open_count `{int(gbp_execution_row.get('open_count') or 0)}`."
            )
        else:
            gbp_why = (
                f"{gbp_why} Packet state/event paths are dedicated to `{gbp_lane_name}` and still show no first-launch proof, "
                "so this row stays explicit hold-not-started instead of borrowing the old GBP asym runtime."
            ).strip()
        rows.append(
            row_packet(
                packet_id="gbpusd_adaptive_comparison_packet",
                title=str(gbp_queue_task.get("title") or "Build the GBPUSD adaptive comparison packet against the incumbent live seat"),
                lane_name=gbp_lane_name,
                action_status=gbp_action_status,
                action_read=gbp_action_read,
                why=gbp_why,
                registry_lane=gbp_registry_lane,
                execution_row=gbp_execution_row,
                packet_artifacts=gbp_artifacts,
                command=list(gbp_packet_contract.get("command") or []),
                authority_inputs=[
                    "reports/adaptive_lab_queue.json",
                    "reports/gbpusd_adaptive_shadow_packet.json",
                    "reports/adaptive_incumbent_study_board.json",
                ],
            )
        )

    usdjpy_row = dict(proof_rows.get("USDJPY") or {})
    usdjpy_queue_task = next(
        (
            dict(task)
            for task in list(queue.get("tasks") or [])
            if str(task.get("task_id") or "") == "usdjpy_bounded_forward_proof"
        ),
        {},
    )
    canonical_usdjpy_lane = "shadow_usdjpy_gap2"
    canonical_usdjpy_registry = find_registry_lane(registry, canonical_usdjpy_lane)
    canonical_usdjpy_execution = find_execution_row(execution, canonical_usdjpy_lane)
    if usdjpy_queue_task:
        rows.append(
            row_packet(
                packet_id="usdjpy_bounded_forward_proof",
                title=str(usdjpy_queue_task.get("title") or "Run fresh USDJPY bounded forward proof under the restored friction-survivor branch"),
                lane_name=canonical_usdjpy_lane,
                action_status="launch_now_manual_packet",
                action_read="bounded proof relaunch packet is now explicit and ready for manual relaunch",
                why=(
                    f"{str(usdjpy_queue_task.get('why') or '')} Using `{canonical_usdjpy_lane}` as the canonical bounded relaunch contract."
                ).strip(),
                registry_lane=canonical_usdjpy_registry,
                execution_row=canonical_usdjpy_execution,
                command=command_from_registry_lane(canonical_usdjpy_registry),
                authority_inputs=[
                    "reports/adaptive_lab_queue.json",
                    "reports/adaptive_lattice_proof_board.json",
                    "configs/penetration_lattice_runner_registry.json",
                ],
            )
        )

    for lane_name, title in (
        ("shadow_usdjpy_gap2", "Hold USDJPY bounded gap2 proof lane until explicit relaunch"),
        ("shadow_usdjpy_shallow03", "Hold USDJPY bounded shallow03 proof lane until explicit relaunch"),
    ):
        if usdjpy_queue_task and lane_name == canonical_usdjpy_lane:
            continue
        rows.append(
            row_packet(
                packet_id=lane_name,
                title=title,
                lane_name=lane_name,
                action_status="hold_disabled_proof_candidate",
                action_read="bounded proof remains a candidate but is not the current adaptive launch packet",
                why=(
                    f"Proof stage is `{usdjpy_row.get('stage') or ''}` with source stage `{usdjpy_row.get('source_stage') or ''}`. "
                    "Current passive truth says the old runtime blocker is historical, but this specific bounded lane is not the promoted overnight relaunch packet."
                ).strip(),
                registry_lane=find_registry_lane(registry, lane_name),
                execution_row=find_execution_row(execution, lane_name),
                authority_inputs=[
                    "reports/adaptive_lattice_proof_board.json",
                    "reports/adaptive_lab_queue.json",
                ],
            )
        )

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["action_status"]] = counts.get(row["action_status"], 0) + 1

    launch_now = [row["lane_name"] for row in rows if row["action_status"] == "launch_now_manual_packet"]
    running_now = [row["lane_name"] for row in rows if row["action_status"] == "already_running_monitor_only"]
    hold_now = [row["lane_name"] for row in rows if row["action_status"].startswith("hold_")]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(QUEUE_PATH.relative_to(ROOT)),
            str(BRANCH_PATH.relative_to(ROOT)),
            str(RESTORE_PATH.relative_to(ROOT)),
            str(RUNTIME_AUDIT_PATH.relative_to(ROOT)),
            str(NZD_PROBE_PATH.relative_to(ROOT)),
            str(GBP_PACKET_PATH.relative_to(ROOT)),
            str(PROOF_PATH.relative_to(ROOT)),
            str(EXECUTION_PATH.relative_to(ROOT)),
            str(REGISTRY_PATH.relative_to(ROOT)),
            str(QUARANTINE_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "packet_count": len(rows),
            "counts_by_action_status": counts,
            "launch_now_lanes": launch_now,
            "already_running_lanes": running_now,
            "hold_lanes": hold_now,
        },
        "leadership_read": [
            (
                f"Adaptive launch-now packet is `{launch_now[0]}`."
                if launch_now
                else "No adaptive lane is currently marked launch-now."
            ),
            (
                f"Adaptive lane already running overnight is `{running_now[0]}`."
                if running_now
                else "No adaptive lane is currently marked already-running."
            ),
            (
                f"BTC restore first-path verdict is `{rows[0].get('first_path_verdict')}`: {rows[0].get('first_path_rationale')}"
                if rows and str(rows[0].get("packet_id") or "") == "btc_restore_comparison_shadow" and str(rows[0].get("first_path_verdict") or "")
                else "No adaptive direct-packet first-path verdict is available yet."
            ),
            (
                f"BTC restore guarded-admission runtime is `{rows[0].get('guarded_admission_status')}`: {rows[0].get('guarded_admission_read')}"
                if rows and str(rows[0].get("packet_id") or "") == "btc_restore_comparison_shadow"
                else "No guarded-admission runtime evidence is available yet."
            ),
            "Treat the parked BTC adaptive artifact as historical runtime context only; do not relaunch it as tonight's adaptive proof.",
            (
                "GBPUSD adaptive comparison now has an explicit overnight packet row tied to the incumbent live seat."
                if gbp_queue_task
                else "GBPUSD adaptive comparison packet is not currently loaded."
            ),
            (
                "USDJPY now has one explicit bounded-proof relaunch packet and any remaining bounded rows should be treated as historical alternates."
                if usdjpy_queue_task
                else "USDJPY bounded proof lanes remain hold-only until an explicit bounded-proof relaunch packet is promoted."
            ),
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It does not launch, stop, or rewrite any lane.",
            "Use it as the adaptive-only overnight packet while runtime/trade-firing work is evaluated separately.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Adaptive Overnight Launch Packet Board",
        "",
        "This board compresses current adaptive authority into one operator-facing overnight launch read.",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for line in list(payload.get("leadership_read") or []):
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- packet_count: `{summary.get('packet_count')}`",
            f"- counts_by_action_status: `{summary.get('counts_by_action_status')}`",
            f"- launch_now_lanes: `{summary.get('launch_now_lanes')}`",
            f"- already_running_lanes: `{summary.get('already_running_lanes')}`",
            f"- hold_lanes: `{summary.get('hold_lanes')}`",
            "",
            "## Rows",
            "",
            "| Packet | Lane | Action | Registry | Exec | Watchdog | Why |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['packet_id']}` | `{row['lane_name']}` | `{row['action_status']}` | "
            f"`{row['registry_present']}/{row['registry_enabled']}` | `{row['execution_present']}` | "
            f"`{row['execution_watchdog_status'] or '-'}` | {row['why']} |"
        )
    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### {row['packet_id']}")
        lines.append(f"- title: `{row['title']}`")
        lines.append(f"- lane_name: `{row['lane_name']}`")
        lines.append(f"- action_status: `{row['action_status']}`")
        lines.append(f"- action_read: {row['action_read']}")
        lines.append(f"- why: {row['why']}")
        lines.append(f"- registry_present: `{row['registry_present']}`")
        lines.append(f"- registry_enabled: `{row['registry_enabled']}`")
        lines.append(f"- registry_pause_note: `{row['registry_pause_note']}`")
        lines.append(f"- execution_present: `{row['execution_present']}`")
        lines.append(f"- execution_watchdog_status: `{row['execution_watchdog_status']}`")
        lines.append(f"- execution_open_count: `{row['execution_open_count']}`")
        lines.append(f"- execution_trade_opens: `{row['execution_trade_opens']}`")
        lines.append(f"- execution_trade_closes: `{row['execution_trade_closes']}`")
        lines.append(f"- artifact_started: `{row['artifact_started']}`")
        lines.append(f"- artifact_runner_pid: `{row['artifact_runner_pid']}`")
        lines.append(f"- artifact_runner_started_at: `{row['artifact_runner_started_at']}`")
        lines.append(f"- artifact_runner_heartbeat_at: `{row['artifact_runner_heartbeat_at']}`")
        lines.append(f"- artifact_runner_heartbeat_age_seconds: `{row['artifact_runner_heartbeat_age_seconds']}`")
        lines.append(f"- artifact_open_count: `{row['artifact_open_count']}`")
        lines.append(f"- artifact_trade_opens: `{row['artifact_trade_opens']}`")
        lines.append(f"- artifact_trade_closes: `{row['artifact_trade_closes']}`")
        lines.append(f"- artifact_pre_start_trade_opens: `{row['artifact_pre_start_trade_opens']}`")
        lines.append(f"- artifact_pre_start_trade_closes: `{row['artifact_pre_start_trade_closes']}`")
        lines.append(f"- artifact_last_event_ts: `{row['artifact_last_event_ts']}`")
        lines.append(f"- artifact_last_event_age_seconds: `{row['artifact_last_event_age_seconds']}`")
        lines.append(f"- artifact_guard_open_admission_enabled: `{row['artifact_guard_open_admission_enabled']}`")
        lines.append(f"- first_path_verdict: `{row['first_path_verdict']}`")
        lines.append(f"- first_path_rationale: {row['first_path_rationale']}")
        lines.append(f"- first_path_open_ts_utc: `{row['first_path_open_ts_utc']}`")
        lines.append(f"- first_path_open_direction: `{row['first_path_open_direction']}`")
        lines.append(f"- first_path_open_entry_context: `{row['first_path_open_entry_context']}`")
        lines.append(f"- first_path_open_regime_at_entry: `{row['first_path_open_regime_at_entry']}`")
        lines.append(f"- first_path_open_same_bar_open_burst_count: `{row['first_path_open_same_bar_open_burst_count']}`")
        lines.append(f"- first_path_open_same_tick_open_burst_count: `{row['first_path_open_same_tick_open_burst_count']}`")
        lines.append(f"- first_path_close_ts_utc: `{row['first_path_close_ts_utc']}`")
        lines.append(f"- first_path_close_action: `{row['first_path_close_action']}`")
        lines.append(f"- first_path_close_realized_pnl: `{row['first_path_close_realized_pnl']}`")
        lines.append(f"- first_path_close_time_to_first_green_seconds: `{row['first_path_close_time_to_first_green_seconds']}`")
        lines.append(f"- guarded_admission_status: `{row['guarded_admission_status']}`")
        lines.append(f"- guarded_admission_read: {row['guarded_admission_read']}")
        lines.append(f"- guarded_admission_event_count: `{row['guarded_admission_event_count']}`")
        lines.append(f"- guarded_admission_pre_start_event_count: `{row['guarded_admission_pre_start_event_count']}`")
        lines.append(f"- guarded_admission_first_path_event_count: `{row['guarded_admission_first_path_event_count']}`")
        lines.append(f"- guarded_admission_latest_ts_utc: `{row['guarded_admission_latest_ts_utc']}`")
        lines.append(f"- guarded_admission_latest_stage: `{row['guarded_admission_latest_stage']}`")
        lines.append(f"- guarded_admission_first_path_latest_ts_utc: `{row['guarded_admission_first_path_latest_ts_utc']}`")
        lines.append(f"- guarded_admission_first_path_latest_stage: `{row['guarded_admission_first_path_latest_stage']}`")
        if row.get("authority_inputs"):
            lines.append("- authority_inputs: " + ", ".join(f"`{item}`" for item in row["authority_inputs"]))
        if row.get("command"):
            lines.append("- command: `" + " ".join(str(item) for item in row["command"]) + "`")
        lines.append("")
    lines.extend(["## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
