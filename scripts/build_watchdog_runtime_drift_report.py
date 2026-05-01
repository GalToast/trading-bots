#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
WATCHDOG_GROUPS_PATH = ROOT / "configs" / "watchdog_groups.json"
REPORT_JSON = ROOT / "reports" / "watchdog" / "watchdog_runtime_drift_report.json"
REPORT_MD = ROOT / "reports" / "watchdog" / "watchdog_runtime_drift_report.md"


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def loop_state_path(reports_dir: Path, group_name: str) -> Path:
    return reports_dir / f"{group_name}_loop_state.json"


def parse_iso(value: str | None) -> datetime | None:
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


def build_group_row(
    group_name: str,
    group_payload: dict[str, Any],
    loop_payload: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    configured_lanes = [str(lane) for lane in (group_payload.get("lanes") or []) if str(lane or "").strip()]
    running_lanes = [str(lane) for lane in ((loop_payload or {}).get("lanes") or []) if str(lane or "").strip()]
    missing_lanes = [lane for lane in configured_lanes if lane not in running_lanes]
    extra_lanes = [lane for lane in running_lanes if lane not in configured_lanes]
    loop_present = isinstance(loop_payload, dict)
    loop_updated_at = str((loop_payload or {}).get("updated_at") or "")
    loop_status = str((loop_payload or {}).get("status") or ("missing_loop_state" if not loop_present else ""))
    updated_dt = parse_iso(loop_updated_at)
    interval_seconds = float((loop_payload or {}).get("interval_seconds") or 0.0)
    stale_after_seconds = max(120.0, interval_seconds * 2.0) if interval_seconds > 0 else 120.0
    loop_state_age_seconds = None if updated_dt is None else max(0.0, (now - updated_dt).total_seconds())
    loop_state_stale = loop_present and (loop_state_age_seconds is None or loop_state_age_seconds > stale_after_seconds)
    configured_lane_count = len(configured_lanes)
    running_lane_count = len(running_lanes)
    empty_configured_group = configured_lane_count == 0
    retired_residue = empty_configured_group and loop_state_stale
    verdict = "aligned"
    if loop_state_stale and loop_status:
        loop_status = f"stale_{loop_status}"
    if empty_configured_group and not loop_present:
        loop_status = "empty_configured"
    if retired_residue and loop_status:
        loop_status = f"retired_{loop_status}"
        verdict = "retired_residue"
    drift = False
    if empty_configured_group:
        drift = (not retired_residue) and running_lane_count > 0
    else:
        drift = (not loop_present) or loop_state_stale or bool(missing_lanes) or bool(extra_lanes)
    if drift:
        verdict = "drift"
    return {
        "group": group_name,
        "label": str(group_payload.get("label") or group_name),
        "verdict": verdict,
        "loop_state_present": loop_present,
        "loop_status": loop_status,
        "loop_updated_at": loop_updated_at,
        "loop_started_at": str((loop_payload or {}).get("loop_started_at") or ""),
        "loop_state_age_seconds": loop_state_age_seconds,
        "loop_state_stale": loop_state_stale,
        "configured_lane_count": configured_lane_count,
        "running_lane_count": running_lane_count,
        "missing_lanes": missing_lanes,
        "extra_lanes": extra_lanes,
        "retired_residue": retired_residue,
        "drift": drift,
    }


def build_report(
    *,
    config_path: Path = WATCHDOG_GROUPS_PATH,
    reports_dir: Path = REPORT_JSON.parent,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    config_payload = load_json(config_path)
    groups_payload = ((config_payload or {}).get("groups") or {}) if isinstance(config_payload, dict) else {}

    rows: list[dict[str, Any]] = []
    for group_name in sorted(str(name) for name in groups_payload.keys()):
        group_payload = groups_payload.get(group_name)
        if not isinstance(group_payload, dict):
            continue
        rows.append(
            build_group_row(
                group_name,
                group_payload,
                load_json(loop_state_path(reports_dir, group_name)),
                now=now,
            )
        )

    drift_groups = [row["group"] for row in rows if bool(row.get("drift"))]
    retired_residue_groups = [row["group"] for row in rows if bool(row.get("retired_residue"))]
    missing_loop_state_groups = [
        row["group"]
        for row in rows
        if not bool(row.get("loop_state_present")) and int(row.get("configured_lane_count") or 0) > 0
    ]
    return {
        "generated_at": now.isoformat(),
        "status": "drift_detected" if drift_groups else ("ok_with_retired_residue" if retired_residue_groups else "ok"),
        "group_count": len(rows),
        "aligned_group_count": sum(
            1 for row in rows if not bool(row.get("drift")) and not bool(row.get("retired_residue"))
        ),
        "drift_group_count": len(drift_groups),
        "retired_residue_group_count": len(retired_residue_groups),
        "missing_loop_state_group_count": len(missing_loop_state_groups),
        "drift_groups": drift_groups,
        "retired_residue_groups": retired_residue_groups,
        "missing_loop_state_groups": missing_loop_state_groups,
        "groups": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Watchdog Runtime Drift",
        "",
        f"Generated: `{payload.get('generated_at', '')}`",
        "",
        f"- Status: `{payload.get('status', '')}`",
        f"- Groups: `{payload.get('group_count', 0)}` total, `{payload.get('aligned_group_count', 0)}` aligned, `{payload.get('drift_group_count', 0)}` drifted, `{payload.get('retired_residue_group_count', 0)}` retired residue",
        f"- Missing loop-state files: `{payload.get('missing_loop_state_group_count', 0)}`",
        "- Meaning: compares `configs/watchdog_groups.json` against the currently running `reports/watchdog/*_loop_state.json` lane snapshots",
        "- Operator action: relaunch only the affected watchdog group wrapper if the drift is intentional to pick up config changes; do not assume config edits hot-reload into already-running loops",
        "- Retired residue: an empty configured group can still have a stale old loop-state file after the lane pack was retired; treat that as cleanup residue, not a live relaunch target",
        "",
        "| Group | Verdict | Loop Status | Configured | Running | Missing | Extra |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in payload.get("groups") or []:
        missing = ", ".join(row.get("missing_lanes") or []) or "-"
        extra = ", ".join(row.get("extra_lanes") or []) or "-"
        lines.append(
            f"| {row.get('group', '')} | {row.get('verdict', '')} | {row.get('loop_status', '')} | "
            f"{row.get('configured_lane_count', 0)} | {row.get('running_lane_count', 0)} | "
            f"{missing} | {extra} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_report()
    write_json(REPORT_JSON, payload)
    write_text(REPORT_MD, render_markdown(payload))
    print(f"wrote {REPORT_JSON}")
    print(f"wrote {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
