#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"
REFERENCE_CODE_PATH = ROOT / "scripts" / "tick_penetration_lattice_core.py"
OUTPUT_JSON = REPORTS / "fx_phase1_telemetry_visibility_board.json"
OUTPUT_MD = REPORTS / "fx_phase1_telemetry_visibility_board.md"

PHASE1_FIELDS = [
    "spread_at_entry",
    "entry_context",
    "session_bucket",
    "base_step_px_at_open",
    "same_tick_open_burst_count",
    "same_bar_open_burst_count",
    "anchor_distance_px_at_open",
    "time_to_first_green_seconds",
    "max_favorable_excursion_pnl",
    "max_adverse_excursion_pnl",
    "peak_pnl_before_exit",
    "hold_seconds",
    "first_green_before_fail",
    "reclaimed_trigger_level_seen",
    "retraced_0_25x_step_seen",
    "retraced_0_5x_step_seen",
]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_jsonl(path: Path, *, tail_lines: int = 4000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-tail_lines:]:
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


def iso_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def path_display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except Exception:
        return str(path)


def latest_ts_utc(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        text = str(event.get("ts_utc") or "").strip()
        if text:
            return text
    return ""


def value_present(value: Any) -> bool:
    return value not in (None, "")


def latest_phase1_event_ts(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if any(value_present(event.get(field)) for field in PHASE1_FIELDS):
            return str(event.get("ts_utc") or "")
    return ""


def event_rows_since(events: list[dict[str, Any]], started_at: str) -> list[dict[str, Any]]:
    started_dt = parse_iso(started_at)
    if started_dt is None:
        return list(events)
    keep: list[dict[str, Any]] = []
    for event in events:
        ts = parse_iso(event.get("ts_utc"))
        if ts is None or ts >= started_dt:
            keep.append(event)
    return keep


def close_like(event: dict[str, Any]) -> bool:
    action = str(event.get("action") or "")
    return action == "close_ticket" or action.startswith("escape_")


def classify_lane(
    *,
    lane_name: str,
    runner_started_at: str,
    reference_code_mtime: str,
    events: list[dict[str, Any]],
) -> tuple[str, str]:
    reference_dt = parse_iso(reference_code_mtime)
    started_dt = parse_iso(runner_started_at)
    trade_events = [event for event in events if str(event.get("action") or "") == "open_ticket" or close_like(event)]
    since_runner = event_rows_since(trade_events, runner_started_at)
    phase1_since_runner = [
        event for event in since_runner if any(value_present(event.get(field)) for field in PHASE1_FIELDS)
    ]

    if phase1_since_runner:
        return (
            "phase1_visible",
            "The lane has already emitted at least one post-runner trade event with Phase 1 telemetry fields present.",
        )
    if started_dt is not None and reference_dt is not None and started_dt >= reference_dt:
        if since_runner:
            return (
                "post_patch_runner_without_phase1_fields",
                "The runner started after the telemetry-bearing code, and post-start trade events exist, but none show the expected Phase 1 fields.",
            )
        return (
            "awaiting_first_post_patch_trade_event",
            "The runner started after the telemetry-bearing code, but no post-start open/close-like FX event has arrived yet.",
        )
    if trade_events:
        return (
            "pre_patch_runner_window",
            "The latest reviewed FX trade events come from a runner window that started before the telemetry-bearing code.",
        )
    return (
        "no_trade_events_seen",
        f"No open/close-like FX events were found for {lane_name} in the inspected event window.",
    )


def state_open_inventory_count(state_payload: dict[str, Any]) -> int:
    symbols = state_payload.get("symbols") if isinstance(state_payload.get("symbols"), dict) else {}
    total = 0
    for symbol_state in symbols.values():
        if not isinstance(symbol_state, dict):
            continue
        total += len(symbol_state.get("open_tickets") or [])
    return total


def restart_posture(*, kind: str, open_inventory_count: int) -> str:
    if str(kind or "") == "live_fx":
        if int(open_inventory_count or 0) > 0:
            return "live_open_inventory_rehydratable"
        return "live_flat_restart_candidate"
    if int(open_inventory_count or 0) > 0:
        return "shadow_restart_resets_path_state"
    return "shadow_flat_restart_candidate"


def build_payload(
    *,
    now: datetime | None = None,
    registry_payload: dict[str, Any] | None = None,
    watchdog_payload: dict[str, Any] | None = None,
    state_payloads: dict[str, dict[str, Any]] | None = None,
    event_payloads: dict[str, list[dict[str, Any]]] | None = None,
    reference_code_mtime: str | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    registry_payload = registry_payload if registry_payload is not None else load_json(REGISTRY_PATH)
    watchdog_payload = watchdog_payload if watchdog_payload is not None else load_json(WATCHDOG_GROUPS_PATH)
    state_payloads = state_payloads or {}
    event_payloads = event_payloads or {}
    reference_code_mtime = reference_code_mtime or iso_mtime(REFERENCE_CODE_PATH)

    fx_names = set(
        (
            ((watchdog_payload.get("groups") or {}).get("fx_watchdog") or {}).get("lanes")
            or []
        )
    )
    rows: list[dict[str, Any]] = []
    for lane in registry_payload.get("lanes") or []:
        name = str(lane.get("name") or "")
        if name not in fx_names:
            continue
        state_path = ROOT / str(lane.get("state_path") or "")
        event_path = ROOT / str(lane.get("event_path") or "")
        state_payload = state_payloads.get(name) if name in state_payloads else load_json(state_path)
        events = event_payloads.get(name) if name in event_payloads else load_jsonl(event_path)
        runner = state_payload.get("runner") if isinstance(state_payload.get("runner"), dict) else {}
        runner_started_at = str(runner.get("started_at") or "")
        heartbeat_at = str(runner.get("heartbeat_at") or "")
        kind = str(lane.get("kind") or "")
        trade_events = [event for event in events if str(event.get("action") or "") == "open_ticket" or close_like(event)]
        open_events = [event for event in events if str(event.get("action") or "") == "open_ticket"]
        close_events = [event for event in events if close_like(event)]
        open_inventory_count = state_open_inventory_count(state_payload)
        covered_field_count = sum(1 for field in PHASE1_FIELDS if any(value_present(event.get(field)) for event in events))
        status, rationale = classify_lane(
            lane_name=name,
            runner_started_at=runner_started_at,
            reference_code_mtime=reference_code_mtime,
            events=events,
        )
        rows.append(
            {
                "lane": name,
                "kind": kind,
                "state_path": path_display(state_path),
                "event_path": path_display(event_path),
                "runner_started_at": runner_started_at,
                "heartbeat_at": heartbeat_at,
                "event_log_mtime": iso_mtime(event_path),
                "reference_code_mtime": reference_code_mtime,
                "open_inventory_count": open_inventory_count,
                "restart_posture": restart_posture(kind=kind, open_inventory_count=open_inventory_count),
                "trade_event_count": len(trade_events),
                "open_ticket_count": len(open_events),
                "close_like_count": len(close_events),
                "covered_field_count": covered_field_count,
                "field_count": len(PHASE1_FIELDS),
                "latest_trade_event_ts_utc": latest_ts_utc(trade_events),
                "latest_phase1_event_ts_utc": latest_phase1_event_ts(events),
                "status": status,
                "rationale": rationale,
            }
        )

    rows.sort(key=lambda row: str(row.get("lane") or ""))
    summary = {
        "lane_count": len(rows),
        "phase1_visible_count": sum(1 for row in rows if row["status"] == "phase1_visible"),
        "awaiting_first_post_patch_trade_event_count": sum(
            1 for row in rows if row["status"] == "awaiting_first_post_patch_trade_event"
        ),
        "pre_patch_runner_window_count": sum(1 for row in rows if row["status"] == "pre_patch_runner_window"),
        "post_patch_runner_without_phase1_fields_count": sum(
            1 for row in rows if row["status"] == "post_patch_runner_without_phase1_fields"
        ),
        "no_trade_events_seen_count": sum(1 for row in rows if row["status"] == "no_trade_events_seen"),
        "flat_restart_candidate_count": sum(
            1 for row in rows if row["restart_posture"] in {"live_flat_restart_candidate", "shadow_flat_restart_candidate"}
        ),
        "open_inventory_lane_count": sum(1 for row in rows if int(row.get("open_inventory_count", 0) or 0) > 0),
        "live_rehydratable_restart_count": sum(
            1 for row in rows if row["restart_posture"] == "live_open_inventory_rehydratable"
        ),
        "shadow_path_reset_restart_count": sum(
            1 for row in rows if row["restart_posture"] == "shadow_restart_resets_path_state"
        ),
    }

    if summary["post_patch_runner_without_phase1_fields_count"] > 0:
        readiness = "fx_post_patch_gap_detected"
        next_action = "At least one FX lane is already post-patch and trading, but still lacks visible Phase 1 fields. Inspect the FX runtime path before trusting FX telemetry parity."
    elif summary["awaiting_first_post_patch_trade_event_count"] > 0:
        readiness = "fx_waiting_first_post_patch_trade_event"
        next_action = "At least one FX lane is already post-patch, but the first fresh trade-path event has not arrived yet."
    elif summary["phase1_visible_count"] > 0:
        readiness = "fx_phase1_visible"
        next_action = "FX telemetry is now visible on at least one active lane; review path-shape differences across symbols before making cross-symbol claims."
    else:
        readiness = "fx_pre_patch_runner_windows"
        next_action = "Active FX lanes are still running pre-patch windows, so current FX event logs cannot be used as evidence against the landed telemetry surface."

    return {
        "generated_at": now.isoformat(),
        "reference_code_path": path_display(REFERENCE_CODE_PATH),
        "reference_code_mtime": reference_code_mtime,
        "readiness": readiness,
        "next_action": next_action,
        "summary": summary,
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    lines = [
        "# FX Phase 1 Telemetry Visibility Board",
        "",
        "> Active FX watchdog lanes only.",
        "> Use this board to distinguish pre-patch FX runner windows from genuine post-patch FX telemetry gaps.",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- reference_code_path: `{payload.get('reference_code_path', '')}`",
        f"- reference_code_mtime: `{payload.get('reference_code_mtime', '')}`",
        f"- readiness: `{payload.get('readiness', '')}`",
        f"- next_action: `{payload.get('next_action', '')}`",
        "",
        "## Summary",
        "",
        f"- lane_count: `{int(summary.get('lane_count', 0) or 0)}`",
        f"- phase1_visible_count: `{int(summary.get('phase1_visible_count', 0) or 0)}`",
        f"- awaiting_first_post_patch_trade_event_count: `{int(summary.get('awaiting_first_post_patch_trade_event_count', 0) or 0)}`",
        f"- pre_patch_runner_window_count: `{int(summary.get('pre_patch_runner_window_count', 0) or 0)}`",
        f"- post_patch_runner_without_phase1_fields_count: `{int(summary.get('post_patch_runner_without_phase1_fields_count', 0) or 0)}`",
        f"- no_trade_events_seen_count: `{int(summary.get('no_trade_events_seen_count', 0) or 0)}`",
        f"- flat_restart_candidate_count: `{int(summary.get('flat_restart_candidate_count', 0) or 0)}`",
        f"- open_inventory_lane_count: `{int(summary.get('open_inventory_lane_count', 0) or 0)}`",
        f"- live_rehydratable_restart_count: `{int(summary.get('live_rehydratable_restart_count', 0) or 0)}`",
        f"- shadow_path_reset_restart_count: `{int(summary.get('shadow_path_reset_restart_count', 0) or 0)}`",
        "",
        "## Lane Matrix",
        "",
        "| Lane | Status | Open inventory | Restart posture | Trade events | Phase1 fields | Runner started | Latest trade event | Rationale |",
        "| --- | --- | ---: | --- | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row.get('lane', '')}` | `{row.get('status', '')}` | "
            f"`{int(row.get('open_inventory_count', 0) or 0)}` | "
            f"`{row.get('restart_posture', '')}` | "
            f"`{int(row.get('trade_event_count', 0) or 0)}` | "
            f"`{int(row.get('covered_field_count', 0) or 0)}/{int(row.get('field_count', 0) or 0)}` | "
            f"`{row.get('runner_started_at', '') or '-'}` | "
            f"`{row.get('latest_trade_event_ts_utc', '') or '-'}` | "
            f"{row.get('rationale', '')} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "`pre_patch_runner_window` means the lane is alive, but the reviewed FX trade events belong to a runner that started before the telemetry-bearing core edit.",
            "`awaiting_first_post_patch_trade_event` means the lane has been recycled onto the right code, but it has not produced a fresh open/close-like event yet.",
            "`post_patch_runner_without_phase1_fields` is the real red flag: post-patch FX trade events exist, but the expected telemetry fields are still missing.",
            "`phase1_visible` means at least one active FX lane is already emitting the landed path-shape surface.",
            "`live_open_inventory_rehydratable` means a live FX lane still carries inventory, but the executable restart path is broker-authoritative: a deliberate restart can rehydrate tracked inventory while still breaking runner-window path continuity.",
            "`shadow_restart_resets_path_state` means a shadow lane can be recycled if the room wants telemetry freshness more than continuity, but the current path sample will be broken.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"wrote {OUTPUT_JSON}")
    print(f"wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
