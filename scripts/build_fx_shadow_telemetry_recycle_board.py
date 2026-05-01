#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
FX_VISIBILITY_PATH = REPORTS / "fx_phase1_telemetry_visibility_board.json"
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"
OUTPUT_JSON = REPORTS / "fx_shadow_telemetry_recycle_board.json"
OUTPUT_MD = REPORTS / "fx_shadow_telemetry_recycle_board.md"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


def activity_bucket(latest_trade_event_ts_utc: str, *, now: datetime) -> tuple[str, float | None]:
    latest_dt = parse_iso(latest_trade_event_ts_utc)
    if latest_dt is None:
        return "unknown", None
    hours = max(0.0, (now - latest_dt).total_seconds() / 3600.0)
    if hours <= 2.0:
        return "hot", hours
    if hours <= 6.0:
        return "warm", hours
    return "cold", hours


def candidate_verdict(
    *,
    status: str,
    restart_posture: str,
    open_inventory_count: int,
    activity: str,
) -> tuple[str, int, str]:
    if restart_posture.startswith("live_"):
        return (
            "exclude_live_lane",
            90,
            "Live FX lanes are execution decisions, not default telemetry-acceleration recycle candidates.",
        )
    if status == "phase1_visible":
        return (
            "already_fresh_no_recycle_needed",
            80,
            "This lane is already emitting Phase 1 telemetry, so recycling it adds churn without new diagnostic value.",
        )
    if status == "awaiting_first_post_patch_trade_event":
        return (
            "already_post_patch_wait",
            70,
            "This lane is already on the telemetry-bearing image; the next move is to wait for the first fresh trade-path event.",
        )
    if status == "post_patch_runner_without_phase1_fields":
        return (
            "runtime_gap_not_recycle_gap",
            75,
            "A recycle will not fix this: the lane is already post-patch and trading, so the remaining issue is runtime/event enrichment.",
        )
    if status == "no_trade_events_seen":
        return (
            "low_signal_shadow_candidate",
            60,
            "This shadow lane has not shown usable trade-path evidence in the inspected window, so a recycle here is low-confidence acceleration.",
        )
    if restart_posture == "shadow_flat_restart_candidate":
        return (
            "recycle_now_flat_shadow",
            0,
            "This shadow lane is flat, so a recycle gives the room a fresh telemetry window with no continuity sacrifice.",
        )
    if restart_posture == "shadow_restart_resets_path_state":
        if open_inventory_count <= 3 and activity == "hot":
            return (
                "recycle_first_wave",
                10,
                "Low open inventory plus recent trade activity makes this the cheapest shadow continuity sacrifice for fresh telemetry evidence.",
            )
        if open_inventory_count <= 3 and activity == "warm":
            return (
                "recycle_second_wave",
                20,
                "Low open inventory keeps continuity cost contained, but the lane is less active than the first-wave candidates.",
            )
        if open_inventory_count <= 6 and activity in {"hot", "warm"}:
            return (
                "recycle_second_wave",
                25,
                "Moderate open inventory with active recent trade flow makes this a viable second-wave recycle if the first-wave lanes do not emit fresh telemetry quickly.",
            )
        return (
            "preserve_continuity_first",
            40,
            "Open inventory and/or weak recent trade activity make this a poor first recycle if the goal is fast fresh telemetry evidence.",
        )
    return (
        "manual_review",
        95,
        "This lane does not fit the standard recycle taxonomy; inspect it manually before using it for telemetry acceleration.",
    )


def registry_row_by_name(registry_payload: dict[str, Any], lane_name: str) -> dict[str, Any]:
    for row in registry_payload.get("lanes") or []:
        if str(row.get("name") or "") == lane_name:
            return dict(row)
    return {}


def watchdog_groups_for_lane(watchdog_payload: dict[str, Any], lane_name: str) -> list[str]:
    groups = watchdog_payload.get("groups") if isinstance(watchdog_payload.get("groups"), dict) else {}
    names: list[str] = []
    for group_name, group_payload in groups.items():
        lanes = group_payload.get("lanes") if isinstance(group_payload, dict) else []
        if lane_name in (lanes or []):
            names.append(str(group_name))
    return sorted(names)


def contains_fresh_start(restart_args: list[Any]) -> bool:
    return "--fresh-start" in [str(arg) for arg in restart_args or []]


def apply_restart_contract(
    *,
    candidate_verdict: str,
    candidate_rank: int,
    rationale: str,
    has_fresh_start: bool,
) -> tuple[str, int, str]:
    if candidate_verdict.startswith("recycle_") and has_fresh_start:
        return (
            "blocked_fresh_start_contract",
            max(15, int(candidate_rank or 0)),
            "The lane looks attractive for telemetry acceleration, but its registry restart contract still includes `--fresh-start`, so recycling it would violate the deployment-safe restart protocol and wipe continuity/state.",
        )
    return candidate_verdict, candidate_rank, rationale


def build_payload(
    *,
    now: datetime | None = None,
    fx_visibility_payload: dict[str, Any] | None = None,
    registry_payload: dict[str, Any] | None = None,
    watchdog_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    fx_visibility_payload = fx_visibility_payload if fx_visibility_payload is not None else load_json(FX_VISIBILITY_PATH)
    registry_payload = registry_payload if registry_payload is not None else load_json(REGISTRY_PATH)
    watchdog_payload = watchdog_payload if watchdog_payload is not None else load_json(WATCHDOG_GROUPS_PATH)
    visibility_rows = fx_visibility_payload.get("rows") if isinstance(fx_visibility_payload.get("rows"), list) else []

    rows: list[dict[str, Any]] = []
    for row in visibility_rows:
        if str(row.get("kind") or "") != "shadow_fx":
            continue
        activity, hours_since_latest_trade = activity_bucket(
            str(row.get("latest_trade_event_ts_utc") or ""),
            now=now,
        )
        verdict, rank, rationale = candidate_verdict(
            status=str(row.get("status") or ""),
            restart_posture=str(row.get("restart_posture") or ""),
            open_inventory_count=int(row.get("open_inventory_count", 0) or 0),
            activity=activity,
        )
        registry_row = registry_row_by_name(registry_payload, str(row.get("lane") or ""))
        restart_args = list(registry_row.get("restart_args") or [])
        has_fresh_start = contains_fresh_start(restart_args)
        verdict, rank, rationale = apply_restart_contract(
            candidate_verdict=verdict,
            candidate_rank=rank,
            rationale=rationale,
            has_fresh_start=has_fresh_start,
        )
        rows.append(
            {
                "lane": str(row.get("lane") or ""),
                "status": str(row.get("status") or ""),
                "restart_posture": str(row.get("restart_posture") or ""),
                "watchdog_groups": watchdog_groups_for_lane(watchdog_payload, str(row.get("lane") or "")),
                "state_path": str(registry_row.get("state_path") or ""),
                "event_path": str(registry_row.get("event_path") or ""),
                "has_fresh_start": has_fresh_start,
                "open_inventory_count": int(row.get("open_inventory_count", 0) or 0),
                "trade_event_count": int(row.get("trade_event_count", 0) or 0),
                "latest_trade_event_ts_utc": str(row.get("latest_trade_event_ts_utc") or ""),
                "hours_since_latest_trade": hours_since_latest_trade,
                "activity_bucket": activity,
                "candidate_verdict": verdict,
                "candidate_rank": rank,
                "rationale": rationale,
            }
        )

    rows.sort(
        key=lambda row: (
            int(row.get("candidate_rank", 999) or 999),
            int(row.get("open_inventory_count", 0) or 0),
            float(row.get("hours_since_latest_trade", 9999.0) or 9999.0),
            -int(row.get("trade_event_count", 0) or 0),
            str(row.get("lane") or ""),
        )
    )

    shadow_candidates = [row for row in rows if str(row.get("candidate_verdict") or "").startswith("recycle_")]
    first_wave = [row for row in rows if row.get("candidate_verdict") == "recycle_first_wave"]
    second_wave = [row for row in rows if row.get("candidate_verdict") == "recycle_second_wave"]
    preserve = [row for row in rows if row.get("candidate_verdict") == "preserve_continuity_first"]
    blocked_fresh_start = [row for row in rows if row.get("candidate_verdict") == "blocked_fresh_start_contract"]
    already_fresh = [
        row
        for row in rows
        if row.get("candidate_verdict") in {"already_fresh_no_recycle_needed", "already_post_patch_wait", "runtime_gap_not_recycle_gap"}
    ]

    summary = {
        "shadow_lane_count": len(rows),
        "recycle_candidate_count": len(shadow_candidates),
        "recycle_first_wave_count": len(first_wave),
        "recycle_second_wave_count": len(second_wave),
        "preserve_continuity_first_count": len(preserve),
        "blocked_fresh_start_contract_count": len(blocked_fresh_start),
        "already_post_patch_or_visible_count": len(already_fresh),
        "top_recycle_candidate": str(shadow_candidates[0]["lane"]) if shadow_candidates else "",
    }

    if first_wave:
        readiness = "shadow_recycle_queue_ready"
        next_action = (
            "If the room wants faster FX telemetry proof, recycle a first-wave shadow lane rather than touching live FX lanes. "
            f"Current cheapest candidate: {first_wave[0]['lane']}."
        )
    elif blocked_fresh_start:
        readiness = "shadow_recycle_blocked_by_contract"
        next_action = (
            "The attractive shadow recycle names are presently blocked by `--fresh-start` restart contracts. "
            "Remove that wipe-risk first or choose a different safe lane."
        )
    elif shadow_candidates:
        readiness = "shadow_recycle_second_wave_only"
        next_action = (
            "No ideal first-wave shadow recycle exists, but lower-priority shadow lanes can still be recycled if faster telemetry freshness matters more than path continuity."
        )
    else:
        readiness = "no_shadow_recycle_leverage"
        next_action = "There is no shadow-side recycle advantage right now; either wait for fresh events or inspect a runtime gap instead."

    return {
        "generated_at": now.isoformat(),
        "source_board": str(FX_VISIBILITY_PATH.relative_to(ROOT)).replace("\\", "/"),
        "source_readiness": str(fx_visibility_payload.get("readiness") or ""),
        "readiness": readiness,
        "next_action": next_action,
        "summary": summary,
        "rows": rows,
    }


def format_hours(hours: float | None) -> str:
    if hours is None:
        return "-"
    return f"{hours:.2f}"


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    lines = [
        "# FX Shadow Telemetry Recycle Board",
        "",
        "> Shadow FX lanes only.",
        "> Use this board when the question is not whether FX telemetry is missing, but which shadow lane is cheapest to recycle if the room wants a fresh post-patch telemetry window sooner.",
        "",
        f"- generated_at: `{payload.get('generated_at', '')}`",
        f"- source_board: `{payload.get('source_board', '')}`",
        f"- source_readiness: `{payload.get('source_readiness', '')}`",
        f"- readiness: `{payload.get('readiness', '')}`",
        f"- next_action: `{payload.get('next_action', '')}`",
        "",
        "## Summary",
        "",
        f"- shadow_lane_count: `{int(summary.get('shadow_lane_count', 0) or 0)}`",
        f"- recycle_candidate_count: `{int(summary.get('recycle_candidate_count', 0) or 0)}`",
        f"- recycle_first_wave_count: `{int(summary.get('recycle_first_wave_count', 0) or 0)}`",
        f"- recycle_second_wave_count: `{int(summary.get('recycle_second_wave_count', 0) or 0)}`",
        f"- preserve_continuity_first_count: `{int(summary.get('preserve_continuity_first_count', 0) or 0)}`",
        f"- blocked_fresh_start_contract_count: `{int(summary.get('blocked_fresh_start_contract_count', 0) or 0)}`",
        f"- already_post_patch_or_visible_count: `{int(summary.get('already_post_patch_or_visible_count', 0) or 0)}`",
        f"- top_recycle_candidate: `{summary.get('top_recycle_candidate', '') or 'none'}`",
        "",
        "## Lane Matrix",
        "",
        "| Lane | Verdict | Fresh start | Open inventory | Activity | Hours since latest trade | Trade events | Visibility status | Rationale |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row.get('lane', '')}` | `{row.get('candidate_verdict', '')}` | "
            f"`{'yes' if row.get('has_fresh_start') else 'no'}` | "
            f"`{int(row.get('open_inventory_count', 0) or 0)}` | "
            f"`{row.get('activity_bucket', '')}` | "
            f"`{format_hours(row.get('hours_since_latest_trade'))}` | "
            f"`{int(row.get('trade_event_count', 0) or 0)}` | "
            f"`{row.get('status', '')}` | "
            f"{row.get('rationale', '')} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "`recycle_first_wave` means this is the cheapest current shadow continuity sacrifice if the room wants faster post-patch FX telemetry evidence.",
            "`recycle_second_wave` means the lane is still usable for acceleration, but it carries more continuity cost or weaker recent activity than the first-wave names.",
            "`blocked_fresh_start_contract` means the lane would otherwise be a recycle candidate, but its registry restart contract still carries `--fresh-start`, so the deployment-safe restart protocol says not to use it for continuity-preserving telemetry acceleration.",
            "`preserve_continuity_first` means the lane is pre-patch but too continuity-rich or too cold to recycle early just for telemetry freshness.",
            "`already_post_patch_or_visible` states are not recycle targets; the room should wait for fresh events or inspect the runtime gap directly.",
            "`exclude_live_lane` is intentionally absent here because live FX restart decisions belong to execution control, not to this shadow-only acceleration queue.",
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
