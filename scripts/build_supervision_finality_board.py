#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supervision_policy import POLICY_VERSION, policy_snapshot


ROOT = Path(__file__).resolve().parent.parent
SUPERVISOR_BOARD_JSON = ROOT / "reports" / "watchdog" / "supervisor_watchdog_board.json"
INCIDENT_LEDGER_JSON = ROOT / "reports" / "watchdog" / "incident_ledger.json"
EXECUTION_REPORT_JSON = ROOT / "reports" / "execution_monitor_report.json"
TRADE_FIRING_STATE_JSON = ROOT / "reports" / "watchdog" / "trade_firing_alert_state.json"
OUT_JSON = ROOT / "reports" / "watchdog" / "supervision_finality_board.json"
OUT_MD = ROOT / "reports" / "watchdog" / "supervision_finality_board.md"


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


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def exact_fire_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for row in rows:
        support = str(row.get("exact_fire_support") or "unknown")
        counts[support] = int(counts.get(support, 0)) + 1
        examples.setdefault(support, [])
        lane = str(row.get("lane") or "")
        if lane and len(examples[support]) < 5:
            examples[support].append(lane)
    return {"counts": counts, "examples": examples}


def quarantined_lanes(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in groups:
        for lane in group.get("stale_lanes") or []:
            if str(lane.get("status") or "") != "quarantined":
                continue
            rows.append(
                {
                    "group": str(group.get("label") or ""),
                    "lane": str(lane.get("name") or ""),
                    "status": str(lane.get("status") or ""),
                    "reasons": [str(item) for item in (lane.get("reasons") or [])],
                }
            )
    return rows


def self_check(supervisor: dict[str, Any], trade_state: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    board_age = age_seconds(str(supervisor.get("generated_at") or ""))
    if board_age is None or board_age > 180.0:
        failures.append("supervisor_board_stale")
    groups = supervisor.get("groups") or []
    for group in groups:
        if str(group.get("status") or "") != "ok":
            failures.append(f"group_not_ok:{group.get('name') or group.get('label')}")
        updated_age = group.get("updated_age_seconds")
        if updated_age is None or float(updated_age) > 180.0:
            failures.append(f"group_state_stale:{group.get('name') or group.get('label')}")
    evaluated_age = age_seconds(str(trade_state.get("last_evaluated_at") or trade_state.get("updated_at") or ""))
    if evaluated_age is None or evaluated_age > 180.0:
        failures.append("trade_firing_state_stale")
    if int(trade_state.get("active_anomaly_count") or 0) > 0:
        failures.append("active_trade_firing_anomaly")
    status = "ok" if not failures else "degraded"
    return {
        "status": status,
        "checked_at": utc_now_iso(),
        "failures": failures,
        "board_age_seconds": board_age,
        "trade_firing_state_age_seconds": evaluated_age,
    }


def build_payload() -> dict[str, Any]:
    supervisor = load_json(SUPERVISOR_BOARD_JSON)
    ledger = load_json(INCIDENT_LEDGER_JSON)
    execution = load_json(EXECUTION_REPORT_JSON)
    trade_state = load_json(TRADE_FIRING_STATE_JSON)
    execution_rows = execution.get("rows") if isinstance(execution.get("rows"), list) else []
    ledger_clusters = ledger.get("clusters") if isinstance(ledger.get("clusters"), list) else []
    recent_clusters = [
        cluster
        for cluster in ledger_clusters
        if str(cluster.get("family") or "") not in {"bootstrap_recovery_wave", "launcher_recycle"}
    ]
    bootstrap_clusters = [cluster for cluster in ledger_clusters if str(cluster.get("family") or "") == "bootstrap_recovery_wave"]
    maintenance_clusters = [cluster for cluster in ledger_clusters if str(cluster.get("family") or "") == "launcher_recycle"]
    quarantine_rows = quarantined_lanes(supervisor.get("groups") or [])
    exact_fire = exact_fire_summary([row for row in execution_rows if isinstance(row, dict)])
    check = self_check(supervisor, trade_state)
    overall_status = "ok"
    if str(supervisor.get("overall_status") or "") != "ok":
        overall_status = "degraded"
    if str(check["status"]) != "ok":
        overall_status = "degraded"
    if quarantine_rows:
        overall_status = "degraded"
    return {
        "generated_at": utc_now_iso(),
        "policy_version": POLICY_VERSION,
        "overall_status": overall_status,
        "self_check": check,
        "policy_rows": policy_snapshot(),
        "supervisor_overall_status": str(supervisor.get("overall_status") or "missing"),
        "trade_firing_status": str(((supervisor.get("trade_firing") or {}) if isinstance(supervisor, dict) else {}).get("overall_status") or "missing"),
        "quarantined_lanes": quarantine_rows,
        "recent_clusters": recent_clusters[:8],
        "bootstrap_clusters": bootstrap_clusters[:8],
        "maintenance_clusters": maintenance_clusters[:8],
        "exact_fire": exact_fire,
        "residual_risks": [
            "machine offline, frozen, or network disconnected",
            "Windows Task Scheduler or the interactive-session model fails before user-space recovery can run",
            "lanes marked state_parity_only are not exact-trigger verified yet",
        ],
    }


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Supervision Finality Board",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        f"Overall status: `{payload['overall_status']}`",
        f"Policy version: `{payload['policy_version']}`",
        "",
        "## Self Check",
        "",
        f"- Status: `{payload['self_check']['status']}`",
        f"- Checked at: `{payload['self_check']['checked_at']}`",
        f"- Failures: `{', '.join(payload['self_check']['failures']) if payload['self_check']['failures'] else 'none'}`",
        "",
        "## Exact-Fire Coverage",
        "",
        f"- Counts: `{json.dumps(payload['exact_fire']['counts'], sort_keys=True)}`",
    ]
    for support, lanes in sorted(payload["exact_fire"]["examples"].items()):
        lines.append(f"- {support}: `{', '.join(lanes) if lanes else 'none'}`")
    lines.extend(
        [
            "",
            "## Quarantine",
            "",
        ]
    )
    if payload["quarantined_lanes"]:
        for row in payload["quarantined_lanes"]:
            lines.append(
                f"- `{row['group']}` `{row['lane']}` `{'; '.join(row['reasons']) or row['status']}`"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Incident Clusters",
            "",
        ]
    )
    if payload["recent_clusters"]:
        for cluster in payload["recent_clusters"]:
            lines.append(
                f"- `{cluster['source']}` `{cluster['family']}` rows=`{cluster['row_count']}` targets=`{cluster['target_count']}` span=`{cluster['start_at']} -> {cluster['end_at']}`"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Bootstrap Recoveries",
            "",
        ]
    )
    if payload["bootstrap_clusters"]:
        for cluster in payload["bootstrap_clusters"]:
            context = cluster.get("bootstrap_context") or {}
            bootstrap_started_at = str(context.get("bootstrap_started_at") or "")
            since_start = context.get("seconds_since_bootstrap_start")
            if bootstrap_started_at and since_start is not None:
                lines.append(
                    f"- `{cluster['source']}` rows=`{cluster['row_count']}` targets=`{cluster['target_count']}` "
                    f"span=`{cluster['start_at']} -> {cluster['end_at']}` bootstrap_started=`{bootstrap_started_at}` "
                    f"repair_started_after=`{float(since_start):.1f}s`"
                )
            else:
                lines.append(
                    f"- `{cluster['source']}` rows=`{cluster['row_count']}` targets=`{cluster['target_count']}` span=`{cluster['start_at']} -> {cluster['end_at']}`"
                )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Maintenance Recoveries",
            "",
        ]
    )
    if payload["maintenance_clusters"]:
        for cluster in payload["maintenance_clusters"]:
            context = cluster.get("bootstrap_context") or {}
            restart_started_at = str(context.get("restart_started_at") or "")
            seconds_until_restart = context.get("seconds_until_restart")
            if restart_started_at and seconds_until_restart is not None:
                lines.append(
                    f"- `{cluster['source']}` span=`{cluster['start_at']} -> {cluster['end_at']}` "
                    f"restart_started=`{restart_started_at}` restart_delay=`{float(seconds_until_restart):.1f}s`"
                )
            else:
                lines.append(f"- `{cluster['source']}` span=`{cluster['start_at']} -> {cluster['end_at']}`")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "| Scope | Restart Window (s) | Max Restarts | Quarantine (s) | Exact-Fire |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["policy_rows"]:
        lines.append(
            f"| {row['scope']} | {row['restart_storm_window_seconds']} | {row['restart_storm_max_restarts']} | {row['quarantine_seconds']} | {row['exact_fire_support']} |"
        )
    lines.extend(
        [
            "",
            "## Residual External Risks",
            "",
        ]
    )
    for risk in payload["residual_risks"]:
        lines.append(f"- {risk}")
    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(render_md(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_json": str(OUT_JSON.relative_to(ROOT)),
                "out_md": str(OUT_MD.relative_to(ROOT)),
                "overall_status": payload["overall_status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
