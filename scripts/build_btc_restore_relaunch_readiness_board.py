#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
WATCHDOG = REPORTS / "watchdog"

INCIDENT_PATH = REPORTS / "btc_restore_supervision_incident_board.json"
COHORT_PATH = REPORTS / "btc_restore_stale_recurrence_cohort_board.json"
SUPERVISOR_PATH = WATCHDOG / "supervisor_watchdog_board.json"
QUARANTINE_PATH = WATCHDOG / "crypto_watchdog_quarantine_state.json"
OVERNIGHT_PACKET_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"

OUTPUT_JSON = REPORTS / "btc_restore_relaunch_readiness_board.json"
OUTPUT_MD = REPORTS / "btc_restore_relaunch_readiness_board.md"

LANE = "shadow_btcusd_m15_warp_restore_v1"
PACKET_ID = "btc_restore_comparison_shadow"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def age_seconds(value: str | None) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    return max(0.0, (utc_now() - dt).total_seconds())


def find_packet_row(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("packet_id") or "") == PACKET_ID:
            return dict(row)
    return {}


def find_group(payload: dict[str, Any], name: str) -> dict[str, Any]:
    groups = payload.get("groups")
    if not isinstance(groups, list):
        return {}
    for row in groups:
        if isinstance(row, dict) and str(row.get("name") or "") == name:
            return dict(row)
    return {}


def current_quarantine_entry(payload: dict[str, Any]) -> dict[str, Any]:
    lanes = payload.get("lanes")
    if not isinstance(lanes, dict):
        return {}
    row = lanes.get(LANE)
    return dict(row) if isinstance(row, dict) else {}


def build_payload() -> dict[str, Any]:
    incident = load_json(INCIDENT_PATH)
    cohort = load_json(COHORT_PATH)
    supervisor = load_json(SUPERVISOR_PATH)
    quarantine = load_json(QUARANTINE_PATH)
    overnight = load_json(OVERNIGHT_PACKET_PATH)

    incident_summary = dict(incident.get("summary") or {})
    cohort_summary = dict(cohort.get("summary") or {})
    crypto_group = find_group(supervisor, "crypto_watchdog")
    trade_firing = dict(supervisor.get("trade_firing") or {})
    packet_row = find_packet_row(overnight)
    current_quarantine = current_quarantine_entry(quarantine)

    incident_until = str(incident_summary.get("quarantined_until") or "")
    incident_until_dt = parse_iso(incident_until)
    incident_quarantine_active = bool(incident_until_dt and incident_until_dt > utc_now())

    current_until = str(current_quarantine.get("quarantined_until") or "")
    current_until_dt = parse_iso(current_until)
    current_quarantine_active = bool(current_until_dt and current_until_dt > utc_now())

    crypto_not_ok = int(crypto_group.get("not_ok_count") or 0)
    crypto_status_counts = dict(crypto_group.get("status_counts") or {})
    group_turbulence = crypto_not_ok > 0 or int(crypto_status_counts.get("quarantined") or 0) > 0 or int(crypto_status_counts.get("stale_recurrence") or 0) > 0

    registry_pause_note = str(incident_summary.get("registry_pause_note") or packet_row.get("registry_pause_note") or "")
    restore_packet_status = str(packet_row.get("action_status") or "")

    if current_quarantine_active:
        relaunch_gate_status = "blocked_current_quarantine"
    elif restore_packet_status == "hold_runtime_repair_candidate":
        relaunch_gate_status = "blocked_runtime_repair"
    elif group_turbulence:
        relaunch_gate_status = "blocked_group_turbulence"
    else:
        relaunch_gate_status = "ready_for_controlled_relaunch_review"

    leadership_read = [
        (
            f"Restore self-gate: packet status is `{restore_packet_status or incident_summary.get('overnight_action_status') or ''}`"
            + (f", registry pause note is `{registry_pause_note}`." if registry_pause_note else ".")
        ),
        (
            f"Quarantine timing: incident board still remembers `{incident_until or '-'}`"
            + (
                f" and that window is {'still active' if incident_quarantine_active else 'already expired'}."
                if incident_until
                else "."
            )
            + (
                f" Current crypto quarantine state {'still contains' if current_quarantine else 'does not contain'} the restore lane."
            )
        ),
        (
            f"Group turbulence: crypto_watchdog currently reports `not_ok_count={crypto_not_ok}` with status counts "
            f"`{json.dumps(crypto_status_counts, sort_keys=True)}`."
        ),
        (
            "So the relaunch question is two-dimensional: even if the restore quarantine window has expired, the branch is not yet a clean relaunch candidate "
            "while the packet still says runtime-repair hold and crypto_watchdog still has a live non-ok cohort."
        ),
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(INCIDENT_PATH.relative_to(ROOT)),
            str(COHORT_PATH.relative_to(ROOT)),
            str(SUPERVISOR_PATH.relative_to(ROOT)),
            str(QUARANTINE_PATH.relative_to(ROOT)),
            str(OVERNIGHT_PACKET_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "lane": LANE,
            "relaunch_gate_status": relaunch_gate_status,
            "restore_packet_status": restore_packet_status,
            "registry_pause_note": registry_pause_note,
            "incident_quarantined_until": incident_until,
            "incident_quarantine_active": incident_quarantine_active,
            "current_quarantined_until": current_until,
            "current_quarantine_active": current_quarantine_active,
            "current_quarantine_contains_restore": bool(current_quarantine),
            "crypto_watchdog_not_ok_count": crypto_not_ok,
            "crypto_watchdog_status_counts": crypto_status_counts,
            "trade_firing_overall_status": str(trade_firing.get("overall_status") or ""),
            "trade_firing_active_anomaly_count": int(trade_firing.get("active_anomaly_count") or 0),
            "execution_only_stale_residue": int(cohort_summary.get("execution_only_stale_residue") or 0),
        },
        "leadership_read": leadership_read,
        "gate_factors": {
            "restore_incident_summary": incident_summary,
            "restore_packet_row": packet_row,
            "current_quarantine_entry": current_quarantine,
            "cohort_summary": cohort_summary,
            "crypto_watchdog_group": {
                "status": str(crypto_group.get("status") or ""),
                "not_ok_count": crypto_not_ok,
                "status_counts": crypto_status_counts,
                "updated_at": str(crypto_group.get("updated_at") or ""),
                "stale_lanes": list(crypto_group.get("stale_lanes") or [])[:6],
            },
            "trade_firing": {
                "overall_status": str(trade_firing.get("overall_status") or ""),
                "active_anomaly_count": int(trade_firing.get("active_anomaly_count") or 0),
                "last_detected": dict(trade_firing.get("last_detected") or {}),
                "last_recovered": dict(trade_firing.get("last_recovered") or {}),
            },
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    crypto_group = dict(payload.get("gate_factors", {}).get("crypto_watchdog_group") or {})
    trade_firing = dict(payload.get("gate_factors", {}).get("trade_firing") or {})
    lines = [
        "# BTC Restore Relaunch Readiness Board",
        "",
        "This board compresses whether the BTC restore-comparison branch is blocked by its own repair debt, by current crypto-watchdog turbulence, or by both.",
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
            f"- lane: `{summary.get('lane')}`",
            f"- relaunch_gate_status: `{summary.get('relaunch_gate_status')}`",
            f"- restore_packet_status: `{summary.get('restore_packet_status')}`",
            f"- registry_pause_note: `{summary.get('registry_pause_note')}`",
            f"- incident_quarantined_until: `{summary.get('incident_quarantined_until')}`",
            f"- incident_quarantine_active: `{summary.get('incident_quarantine_active')}`",
            f"- current_quarantined_until: `{summary.get('current_quarantined_until')}`",
            f"- current_quarantine_active: `{summary.get('current_quarantine_active')}`",
            f"- current_quarantine_contains_restore: `{summary.get('current_quarantine_contains_restore')}`",
            f"- crypto_watchdog_not_ok_count: `{summary.get('crypto_watchdog_not_ok_count')}`",
            f"- crypto_watchdog_status_counts: `{json.dumps(summary.get('crypto_watchdog_status_counts') or {}, sort_keys=True)}`",
            f"- trade_firing_overall_status: `{summary.get('trade_firing_overall_status')}`",
            f"- trade_firing_active_anomaly_count: `{summary.get('trade_firing_active_anomaly_count')}`",
            f"- execution_only_stale_residue: `{summary.get('execution_only_stale_residue')}`",
            "",
            "## Current Crypto Group Turbulence",
            "",
            f"- group_status: `{crypto_group.get('status')}`",
            f"- updated_at: `{crypto_group.get('updated_at')}`",
            f"- not_ok_count: `{crypto_group.get('not_ok_count')}`",
            f"- status_counts: `{json.dumps(crypto_group.get('status_counts') or {}, sort_keys=True)}`",
            "",
            "| Lane | Status | Reasons |",
            "| --- | --- | --- |",
        ]
    )
    stale_lanes = list(crypto_group.get("stale_lanes") or [])
    if not stale_lanes:
        lines.append("| - | - | - |")
    else:
        for row in stale_lanes:
            reasons = ", ".join(str(item) for item in list(row.get("reasons") or [])[:2]) or "-"
            lines.append(f"| {row.get('name') or '-'} | {row.get('status') or '-'} | {reasons} |")
    lines.extend(
        [
            "",
            "## Trade Firing Context",
            "",
            f"- overall_status: `{trade_firing.get('overall_status')}`",
            f"- active_anomaly_count: `{trade_firing.get('active_anomaly_count')}`",
            f"- last_detected_lane: `{(trade_firing.get('last_detected') or {}).get('lane','')}`",
            f"- last_detected_alert: `{(trade_firing.get('last_detected') or {}).get('execution_alert','')}`",
            f"- last_recovered_lane: `{(trade_firing.get('last_recovered') or {}).get('lane','')}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_json": str(OUTPUT_JSON.relative_to(ROOT)),
                "out_md": str(OUTPUT_MD.relative_to(ROOT)),
                "relaunch_gate_status": payload.get("summary", {}).get("relaunch_gate_status"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
