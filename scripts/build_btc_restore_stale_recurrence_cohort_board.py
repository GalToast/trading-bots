#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
WATCHDOG = REPORTS / "watchdog"
CONFIGS = ROOT / "configs"

EXECUTION_PATH = REPORTS / "execution_monitor_report.json"
CRYPTO_REPORT_PATH = WATCHDOG / "crypto_watchdog_report.json"
FX_REPORT_PATH = WATCHDOG / "fx_watchdog_report.json"
SHADOW_REPORT_PATH = WATCHDOG / "shadow_watchdog_report.json"
CRYPTO_LOOP_STATE_PATH = WATCHDOG / "crypto_watchdog_loop_state.json"
CRYPTO_QUARANTINE_PATH = WATCHDOG / "crypto_watchdog_quarantine_state.json"
BTC_INCIDENT_PATH = REPORTS / "btc_restore_supervision_incident_board.json"
REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"

OUTPUT_JSON = REPORTS / "btc_restore_stale_recurrence_cohort_board.json"
OUTPUT_MD = REPORTS / "btc_restore_stale_recurrence_cohort_board.md"

LANE = "shadow_btcusd_m15_warp_restore_v1"


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


def registry_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("lanes")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if name:
            out[name] = row
    return out


def report_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def report_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in report_rows(payload):
        name = str(row.get("name") or row.get("lane") or "")
        if name:
            out[name] = row
    return out


def execution_stale_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("watchdog_status") or "") == "stale_recurrence":
            out.append(row)
    return out


def build_quarantine_rows(
    quarantine_payload: dict[str, Any],
    crypto_rows: dict[str, dict[str, Any]],
    registry_rows: dict[str, dict[str, Any]],
    loop_lane_set: set[str],
) -> list[dict[str, Any]]:
    lanes = quarantine_payload.get("lanes")
    if not isinstance(lanes, dict):
        return []
    out: list[dict[str, Any]] = []
    for name, qrow in lanes.items():
        if not isinstance(qrow, dict):
            continue
        report_row = dict(crypto_rows.get(name) or {})
        registry_row = dict(registry_rows.get(name) or {})
        reasons = list(report_row.get("reasons") or [])
        out.append(
            {
                "lane": name,
                "kind": str(qrow.get("kind") or report_row.get("kind") or ""),
                "enabled": bool(report_row.get("enabled", registry_row.get("enabled", False))),
                "currently_in_crypto_watchdog_lane_set": name in loop_lane_set,
                "report_status": str(report_row.get("status") or ""),
                "heartbeat_at": str(report_row.get("heartbeat_at") or ""),
                "heartbeat_age_seconds": age_seconds(str(report_row.get("heartbeat_at") or "")),
                "source_tick_lag_seconds": report_row.get("source_tick_lag_seconds"),
                "source_tick_recurrence": bool(report_row.get("source_tick_recurrence")),
                "source_tick_recurrence_reset_at": str(report_row.get("source_tick_recurrence_reset_at") or ""),
                "quarantined_at": str(qrow.get("quarantined_at") or ""),
                "quarantined_until": str(qrow.get("quarantined_until") or ""),
                "reason": str(qrow.get("reason") or ""),
                "restart_count_window": int(qrow.get("restart_count_window") or 0),
                "registry_pause_note": str(registry_row.get("pause_note") or ""),
                "report_reasons": [str(item) for item in reasons],
            }
        )
    out.sort(key=lambda row: (0 if row["lane"] == LANE else 1, str(row["lane"])))
    return out


def build_active_non_ok_rows(
    label: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in report_rows(payload):
        status = str(row.get("status") or "")
        if status not in {"stale_recurrence", "quarantined"}:
            continue
        out.append(
            {
                "group": label,
                "lane": str(row.get("name") or row.get("lane") or ""),
                "status": status,
                "enabled": bool(row.get("enabled", False)),
                "heartbeat_at": str(row.get("heartbeat_at") or ""),
                "heartbeat_age_seconds": age_seconds(str(row.get("heartbeat_at") or "")),
                "source_tick_lag_seconds": row.get("source_tick_lag_seconds"),
                "source_tick_recurrence": bool(row.get("source_tick_recurrence")),
                "source_tick_recurrence_reset_at": str(row.get("source_tick_recurrence_reset_at") or ""),
                "reasons": [str(item) for item in list(row.get("reasons") or [])],
            }
        )
    out.sort(key=lambda row: (row["group"], row["lane"]))
    return out


def build_execution_only_residue_rows(
    execution_rows: list[dict[str, Any]],
    active_names: set[str],
    quarantine_names: set[str],
    registry_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in execution_rows:
        lane = str(row.get("lane") or "")
        if not lane or lane in active_names or lane in quarantine_names:
            continue
        registry_row = dict(registry_rows.get(lane) or {})
        out.append(
            {
                "lane": lane,
                "kind": str(row.get("kind") or ""),
                "watchdog_status": str(row.get("watchdog_status") or ""),
                "heartbeat_at": str(row.get("heartbeat_at") or ""),
                "heartbeat_age_seconds": age_seconds(str(row.get("heartbeat_at") or "")),
                "clean_forward_source": str(row.get("clean_forward_source") or ""),
                "pre_start_state_carry_closes": int(row.get("pre_start_state_carry_closes") or 0),
                "pre_start_state_carry_realized_usd": float(row.get("pre_start_state_carry_realized_usd") or 0.0),
                "registry_enabled": bool(registry_row.get("enabled", False)),
                "registry_pause_note": str(registry_row.get("pause_note") or ""),
            }
        )
    out.sort(key=lambda row: (str(row["heartbeat_at"]), row["lane"]), reverse=True)
    return out


def build_payload() -> dict[str, Any]:
    execution = load_json(EXECUTION_PATH)
    crypto_report = load_json(CRYPTO_REPORT_PATH)
    fx_report = load_json(FX_REPORT_PATH)
    shadow_report = load_json(SHADOW_REPORT_PATH)
    crypto_loop = load_json(CRYPTO_LOOP_STATE_PATH)
    crypto_quarantine = load_json(CRYPTO_QUARANTINE_PATH)
    btc_incident = load_json(BTC_INCIDENT_PATH)
    registry = load_json(REGISTRY_PATH)

    registry_rows = registry_map(registry)
    crypto_rows = report_map(crypto_report)
    loop_lane_set = {str(item) for item in list(crypto_loop.get("lanes") or [])}

    quarantine_rows = build_quarantine_rows(crypto_quarantine, crypto_rows, registry_rows, loop_lane_set)
    crypto_non_ok = build_active_non_ok_rows("crypto_watchdog", crypto_report)
    fx_non_ok = build_active_non_ok_rows("fx_watchdog", fx_report)
    shadow_non_ok = build_active_non_ok_rows("shadow_watchdog", shadow_report)
    active_non_ok_rows = crypto_non_ok + fx_non_ok + shadow_non_ok
    active_non_ok_names = {str(row["lane"]) for row in active_non_ok_rows}
    quarantine_names = {str(row["lane"]) for row in quarantine_rows}

    stale_rows = execution_stale_rows(execution)
    residue_rows = build_execution_only_residue_rows(stale_rows, active_non_ok_names, quarantine_names, registry_rows)

    incident_summary = dict(btc_incident.get("summary") or {})
    current_group_counts = {
        "crypto_watchdog_non_ok": len(crypto_non_ok),
        "fx_watchdog_non_ok": len(fx_non_ok),
        "shadow_watchdog_non_ok": len(shadow_non_ok),
        "execution_stale_recurrence_total": len(stale_rows),
        "execution_only_stale_residue": len(residue_rows),
        "crypto_quarantine_total": len(quarantine_rows),
    }

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(EXECUTION_PATH.relative_to(ROOT)),
            str(CRYPTO_REPORT_PATH.relative_to(ROOT)),
            str(FX_REPORT_PATH.relative_to(ROOT)),
            str(SHADOW_REPORT_PATH.relative_to(ROOT)),
            str(CRYPTO_LOOP_STATE_PATH.relative_to(ROOT)),
            str(CRYPTO_QUARANTINE_PATH.relative_to(ROOT)),
            str(BTC_INCIDENT_PATH.relative_to(ROOT)),
            str(REGISTRY_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "restore_lane": LANE,
            "restore_incident_status": str(incident_summary.get("overnight_action_status") or ""),
            "restore_currently_in_crypto_watchdog_lane_set": bool(incident_summary.get("currently_in_crypto_watchdog_lane_set")),
            "restore_quarantine_reason": str(incident_summary.get("quarantine_reason") or ""),
            "restore_quarantined_until": str(incident_summary.get("quarantined_until") or ""),
            **current_group_counts,
        },
        "leadership_read": [
            (
                "BTC restore is not the only current crypto restart-storm quarantine: "
                f"{max(0, len(quarantine_rows) - 1)} other crypto lanes share the same quarantine bucket."
            ),
            (
                "BTC restore is still structurally distinct inside that cohort: it is already registry-disabled and outside "
                "the current crypto_watchdog lane set, while the other quarantined rows remain enabled in-loop."
            ),
            (
                "Most `execution_monitor_report` stale-recurrence rows are older execution-only residue from disabled lanes, "
                f"not fresh active incidents (`residue_count={len(residue_rows)}` vs `crypto_quarantine_total={len(quarantine_rows)}`)."
            ),
            (
                "So the current problem looks crypto-family-local rather than repo-wide, but restore is not a generic stale row: "
                "it sits at the intersection of the active crypto restart-storm class and a packet/topology-specific removal from supervision."
            ),
        ],
        "cohort_summary": current_group_counts,
        "crypto_quarantine_cohort": quarantine_rows,
        "active_watchdog_non_ok_rows": active_non_ok_rows,
        "execution_only_stale_residue": residue_rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# BTC Restore Stale-Recurrence Cohort Board",
        "",
        "This board separates the current BTC restore supervision incident from the broader stale-recurrence residue so Task `#75` can tell whether it is fixing a lane-local packet defect or a wider active watchdog/feed class.",
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
            f"- restore_lane: `{summary.get('restore_lane')}`",
            f"- restore_incident_status: `{summary.get('restore_incident_status')}`",
            f"- restore_currently_in_crypto_watchdog_lane_set: `{summary.get('restore_currently_in_crypto_watchdog_lane_set')}`",
            f"- restore_quarantine_reason: `{summary.get('restore_quarantine_reason')}`",
            f"- restore_quarantined_until: `{summary.get('restore_quarantined_until')}`",
            f"- crypto_watchdog_non_ok: `{summary.get('crypto_watchdog_non_ok')}`",
            f"- fx_watchdog_non_ok: `{summary.get('fx_watchdog_non_ok')}`",
            f"- shadow_watchdog_non_ok: `{summary.get('shadow_watchdog_non_ok')}`",
            f"- crypto_quarantine_total: `{summary.get('crypto_quarantine_total')}`",
            f"- execution_stale_recurrence_total: `{summary.get('execution_stale_recurrence_total')}`",
            f"- execution_only_stale_residue: `{summary.get('execution_only_stale_residue')}`",
            "",
            "## Crypto Quarantine Cohort",
            "",
            "| Lane | Enabled | In Crypto Loop | Report Status | Quarantine Reason | Lag (s) | Recurrence | Pause Note |",
            "| --- | --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    quarantine_rows = list(payload.get("crypto_quarantine_cohort") or [])
    if not quarantine_rows:
        lines.append("| - | - | - | - | - | - | - | - |")
    else:
        for row in quarantine_rows:
            lag = row.get("source_tick_lag_seconds")
            lag_text = "-" if lag in (None, "") else str(lag)
            lines.append(
                f"| {row['lane']} | {row['enabled']} | {row['currently_in_crypto_watchdog_lane_set']} | "
                f"{row['report_status'] or '-'} | {row['reason'] or '-'} | {lag_text} | {row['source_tick_recurrence']} | "
                f"{row['registry_pause_note'] or '-'} |"
            )
    lines.extend(
        [
            "",
            "## Active Watchdog Non-OK Rows",
            "",
            "| Group | Lane | Status | Enabled | Lag (s) | Recurrence | Reasons |",
            "| --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    active_rows = list(payload.get("active_watchdog_non_ok_rows") or [])
    if not active_rows:
        lines.append("| - | - | - | - | - | - | - |")
    else:
        for row in active_rows:
            lag = row.get("source_tick_lag_seconds")
            lag_text = "-" if lag in (None, "") else str(lag)
            reasons = ", ".join(str(item) for item in list(row.get("reasons") or [])[:2]) or "-"
            lines.append(
                f"| {row['group']} | {row['lane']} | {row['status']} | {row['enabled']} | {lag_text} | "
                f"{row['source_tick_recurrence']} | {reasons} |"
            )
    lines.extend(
        [
            "",
            "## Execution-Only Stale Residue",
            "",
            "| Lane | Kind | Heartbeat | Carry Closes | Carry USD | Registry Enabled | Pause Note |",
            "| --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    residue_rows = list(payload.get("execution_only_stale_residue") or [])
    if not residue_rows:
        lines.append("| - | - | - | - | - | - | - |")
    else:
        for row in residue_rows:
            lines.append(
                f"| {row['lane']} | {row['kind']} | {row['heartbeat_at'] or '-'} | {row['pre_start_state_carry_closes']} | "
                f"{row['pre_start_state_carry_realized_usd']} | {row['registry_enabled']} | {row['registry_pause_note'] or '-'} |"
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
                "crypto_quarantine_total": payload.get("summary", {}).get("crypto_quarantine_total", 0),
                "execution_only_stale_residue": payload.get("summary", {}).get("execution_only_stale_residue", 0),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
