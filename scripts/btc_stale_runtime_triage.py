#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
WATCHDOG_REPORTS = REPORTS / "watchdog"
CONFIGS = ROOT / "configs"

BTC_TICK_AUDIT_PATH = REPORTS / "btc_tick_source_audit.json"
EXECUTION_MONITOR_PATH = REPORTS / "execution_monitor_report.json"
LIVE_MAGIC_SCOPE_AUDIT_PATH = REPORTS / "live_magic_scope_audit.json"
REGISTRY_PATH = CONFIGS / "penetration_lattice_runner_registry.json"
WATCHDOG_GROUPS_PATH = CONFIGS / "watchdog_groups.json"
OUTPUT_JSON_PATH = REPORTS / "btc_stale_runtime_triage.json"
OUTPUT_MD_PATH = REPORTS / "btc_stale_runtime_triage.md"

MT5_BTC_KINDS = {"live_crypto", "shadow_crypto", "shadow_crypto_candidate"}
STALE_VERDICTS = {"stale_runtime", "shared_history_needs_validation", "unknown_runtime", "tick_path_unclear"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def parse_iso(raw: str | None) -> datetime | None:
    text = str(raw or "").strip()
    if not text or text.lower() == "none":
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def minutes_until(raw: str | None) -> float | None:
    ts = parse_iso(raw)
    if ts is None:
        return None
    return round((ts - utc_now()).total_seconds() / 60.0, 1)


def read_registry() -> dict[str, dict[str, Any]]:
    payload = load_optional_json(REGISTRY_PATH) or {}
    out: dict[str, dict[str, Any]] = {}
    for lane in list(payload.get("lanes") or []):
        if not isinstance(lane, dict):
            continue
        name = str(lane.get("name") or "").strip()
        if name:
            out[name] = lane
    return out


def read_watchdog_groups() -> dict[str, str]:
    payload = load_optional_json(WATCHDOG_GROUPS_PATH) or {}
    lane_to_group: dict[str, str] = {}
    for group_name, group_cfg in dict(payload.get("groups") or {}).items():
        for lane_name in list((group_cfg or {}).get("lanes") or []):
            lane_to_group[str(lane_name)] = str(group_name)
    return lane_to_group


def read_watchdog_rows() -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for path in WATCHDOG_REPORTS.glob("*_report.json"):
        payload = load_optional_json(path)
        if not isinstance(payload, dict):
            continue
        group_name = path.stem.removesuffix("_report")
        for row in list(payload.get("rows") or []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            copy = dict(row)
            copy["watchdog_group"] = group_name
            copy["watchdog_report_path"] = str(path.relative_to(ROOT))
            indexed[name] = copy
    return indexed


def read_execution_rows() -> dict[str, dict[str, Any]]:
    payload = load_optional_json(EXECUTION_MONITOR_PATH) or {}
    indexed: dict[str, dict[str, Any]] = {}
    for row in list(payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            indexed[lane] = row
    return indexed


def read_live_magic_scope_rows() -> dict[str, dict[str, Any]]:
    payload = load_optional_json(LIVE_MAGIC_SCOPE_AUDIT_PATH) or {}
    indexed: dict[str, dict[str, Any]] = {}
    for row in list(payload.get("rows") or []):
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if lane:
            indexed[lane] = row
    return indexed


def coerce_enabled(raw: Any) -> str:
    if raw is True:
        return "enabled"
    if raw is False:
        return "disabled"
    return "unspecified"


def classify_action(
    *,
    verdict: str,
    watchdog_status: str,
    registry_enabled: str,
    watchdog_group: str,
    open_count: int,
    broker_scoped_open_count: int,
    broker_total_open_count: int,
    scope_status: str,
    quarantine_reason: str,
    reasons: list[str],
) -> tuple[int, str, str, str]:
    reason_blob = " ".join(reasons).lower()
    quarantine_blob = quarantine_reason.lower()

    if verdict == "stale_runtime" and open_count > 0 and broker_scoped_open_count > 0:
        return (
            0,
            "inspect_live_carry_now",
            "urgent",
            "Stale runtime still shows open inventory, so the first question is broker carry and reconcile state, not feeder blame.",
        )

    if verdict == "stale_runtime" and open_count > 0 and broker_total_open_count == 0:
        return (
            0,
            "clear_stale_state_or_document_parked",
            "high",
            "The runtime still carries paused state inventory, but broker-authoritative scope is already flat, so this is stale state or parked-state documentation debt rather than active broker carry.",
        )

    if verdict == "stale_runtime" and registry_enabled == "enabled" and not watchdog_group:
        return (
            1,
            "wire_watchdog_or_disable",
            "high",
            "The lane is enabled but stale outside any watchdog group, so supervision hygiene is the blocker.",
        )

    if verdict == "stale_runtime" and scope_status == "scoped_mismatch":
        return (
            2,
            "inspect_rehydration_or_scope",
            "medium",
            "Managed state and broker scope disagree, so the next operator question is rehydration/scope truth rather than strategy quality.",
        )

    if verdict == "stale_runtime" and registry_enabled == "disabled":
        return (
            3,
            "leave_parked_offline",
            "medium",
            "The stale lane is already disabled; treat it as parked until the team explicitly wants a fresh relaunch.",
        )

    if watchdog_status == "quarantined" and (
        "restart_storm" in quarantine_blob or "risk_resets" in reason_blob or "forward=lagging" in reason_blob
    ):
        return (
            4,
            "keep_quarantined_no_promotion",
            "medium",
            "Watchdog already isolated the lane for restart pressure and/or negative forward behavior; do not treat it as a feeder fix candidate.",
        )

    if verdict in {"direct_tick_live", "shared_history_live_tick_backed"} and watchdog_status in {"", "ok", "quarantined"}:
        return (
            5,
            "watch_only_honest_ticks",
            "low",
            "Current evidence still shows honest live-tick behavior, so this row should not be used to support a systemic dead-feed story.",
        )

    if verdict in STALE_VERDICTS:
        return (
            6,
            "inspect_runtime_path",
            "medium",
            "The runtime is stale or unclear and needs a narrower owner decision before any strategy conclusion.",
        )

    return (
        7,
        "watch_only",
        "low",
        "No immediate operator action is implied by the current evidence.",
    )


def build_row(
    audit_row: dict[str, Any],
    execution_rows: dict[str, dict[str, Any]],
    live_magic_scope_rows: dict[str, dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    watchdog_groups: dict[str, str],
    watchdog_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    lane_name = str(audit_row.get("lane_name") or "")
    execution_row = dict(execution_rows.get(lane_name) or {})
    live_magic_scope_row = dict(live_magic_scope_rows.get(lane_name) or {})
    registry_row = dict(registry.get(lane_name) or {})
    watchdog_row = dict(watchdog_rows.get(lane_name) or {})

    watchdog_group = str(watchdog_row.get("watchdog_group") or watchdog_groups.get(lane_name) or "")
    watchdog_status = str(
        watchdog_row.get("status")
        or execution_row.get("watchdog_status")
        or audit_row.get("watchdog_status")
        or ""
    )
    registry_enabled = coerce_enabled(registry_row.get("enabled"))
    open_count = int(execution_row.get("open_count") or audit_row.get("open_count") or 0)
    broker_scoped_open_count = int(
        execution_row.get("broker_scoped_open_count")
        or live_magic_scope_row.get("broker_scoped_open_count")
        or 0
    )
    broker_total_open_count = int(
        execution_row.get("broker_magic_open_count")
        or live_magic_scope_row.get("broker_total_open_count")
        or 0
    )
    scope_status = str(live_magic_scope_row.get("scope_status") or "")
    reasons = [str(item) for item in list(watchdog_row.get("reasons") or []) if str(item).strip()]
    quarantine_reason = str(watchdog_row.get("quarantine_reason") or "")
    quarantine_until = str(watchdog_row.get("quarantine_until") or "")
    action_rank, recommended_action, severity, action_reason = classify_action(
        verdict=str(audit_row.get("verdict") or ""),
        watchdog_status=watchdog_status,
        registry_enabled=registry_enabled,
        watchdog_group=watchdog_group,
        open_count=open_count,
        broker_scoped_open_count=broker_scoped_open_count,
        broker_total_open_count=broker_total_open_count,
        scope_status=scope_status,
        quarantine_reason=quarantine_reason,
        reasons=reasons,
    )

    evidence = [
        f"verdict={audit_row.get('verdict')}",
        f"watchdog={watchdog_status or 'none'}",
        f"group={watchdog_group or 'none'}",
        f"registry={registry_enabled}",
        f"open={open_count}",
        f"broker_scoped={broker_scoped_open_count}",
        f"broker_total={broker_total_open_count}",
    ]

    heartbeat_age_minutes = audit_row.get("heartbeat_age_minutes")
    if heartbeat_age_minutes is not None:
        evidence.append(f"heartbeat_age_min={heartbeat_age_minutes}")

    notes = str(execution_row.get("notes") or "").strip()
    if notes and notes != "-":
        evidence.append(f"notes={notes}")
    if scope_status:
        evidence.append(f"scope_status={scope_status}")
    if quarantine_reason:
        evidence.append(f"quarantine_reason={quarantine_reason}")
    if reasons:
        evidence.extend(f"watchdog_reason={item}" for item in reasons[:2])

    return {
        "lane_name": lane_name,
        "kind": str(audit_row.get("kind") or ""),
        "verdict": str(audit_row.get("verdict") or ""),
        "verdict_reason": str(audit_row.get("verdict_reason") or ""),
        "watchdog_status": watchdog_status,
        "watchdog_group": watchdog_group,
        "watchdog_report_path": str(watchdog_row.get("watchdog_report_path") or ""),
        "registry_enabled": registry_enabled,
        "heartbeat_age_minutes": heartbeat_age_minutes,
        "open_count": open_count,
        "broker_scoped_open_count": broker_scoped_open_count,
        "broker_total_open_count": broker_total_open_count,
        "scope_status": scope_status,
        "scope_recommended_action": str(live_magic_scope_row.get("recommended_action") or ""),
        "close_count": int(execution_row.get("close_count") or audit_row.get("realized_closes") or 0),
        "shared_price_max_age_ms": audit_row.get("shared_price_max_age_ms"),
        "latest_tick_source_last": str(audit_row.get("latest_tick_source_last") or ""),
        "latest_tick_append_source_last": str(audit_row.get("latest_tick_append_source_last") or ""),
        "tick_history_source_last": str(audit_row.get("tick_history_source_last") or ""),
        "notes": notes,
        "quarantine_reason": quarantine_reason,
        "quarantine_until": quarantine_until,
        "quarantine_minutes_remaining": minutes_until(quarantine_until),
        "watchdog_reasons": reasons,
        "recommended_action": recommended_action,
        "severity": severity,
        "action_reason": action_reason,
        "action_rank": action_rank,
        "evidence": evidence,
    }


def build_payload() -> dict[str, Any]:
    audit_payload = load_optional_json(BTC_TICK_AUDIT_PATH) or {}
    audit_rows = [
        row for row in list(audit_payload.get("rows") or [])
        if isinstance(row, dict) and str(row.get("kind") or "") in MT5_BTC_KINDS
    ]

    execution_rows = read_execution_rows()
    live_magic_scope_rows = read_live_magic_scope_rows()
    registry = read_registry()
    watchdog_groups = read_watchdog_groups()
    watchdog_rows = read_watchdog_rows()

    triage_rows = [
        build_row(row, execution_rows, live_magic_scope_rows, registry, watchdog_groups, watchdog_rows)
        for row in audit_rows
    ]
    triage_rows.sort(key=lambda row: (row["action_rank"], row["lane_name"]))

    action_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for row in triage_rows:
        action = row["recommended_action"]
        severity = row["severity"]
        action_counts[action] = action_counts.get(action, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    priority_actions = [row for row in triage_rows if row["action_rank"] <= 3][:6]

    leadership_read = [
        "The current BTC problem bucket is stale or badly supervised runtime state, not a blanket shared-feeder failure.",
        "live_btcusd_m5_warp_probation_941780 still shows paused state inventory, but current broker-authoritative scope is already flat, so the work is stale-state truth and parked-state hygiene rather than emergency broker cleanup.",
        "The H1 step canaries and BTC M15 on20 are not dead-tick proof rows; they are fresh but correctly quarantined for restart storms, risk resets, and negative forward behavior.",
        "Healthy direct/shared-live BTC rows should be kept as counterexamples so the room does not escalate the wrong root cause.",
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(BTC_TICK_AUDIT_PATH.relative_to(ROOT)),
            str(EXECUTION_MONITOR_PATH.relative_to(ROOT)),
            str(LIVE_MAGIC_SCOPE_AUDIT_PATH.relative_to(ROOT)),
            str(REGISTRY_PATH.relative_to(ROOT)),
            str(WATCHDOG_GROUPS_PATH.relative_to(ROOT)),
            "reports/watchdog/*_report.json",
        ],
        "leadership_read": leadership_read,
        "summary": {
            "lane_count": len(triage_rows),
            "severity_counts": severity_counts,
            "action_counts": action_counts,
            "stale_runtime_rows": sum(1 for row in triage_rows if row["verdict"] == "stale_runtime"),
            "quarantined_rows": sum(1 for row in triage_rows if row["watchdog_status"] == "quarantined"),
            "watch_only_rows": sum(1 for row in triage_rows if row["recommended_action"] == "watch_only_honest_ticks"),
        },
        "priority_actions": priority_actions,
        "rows": triage_rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# BTC Stale Runtime Triage",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Scope: MT5 BTC-family runtime and supervision state only. This excludes Coinbase BTC sleeves so the room can focus on the current BTCUSD execution path.",
        "- Purpose: separate genuinely stale/unsupervised BTC rows from fresh-but-quarantined canaries and healthy live-tick lanes, then assign a concrete operator action.",
        "",
        "## Leadership Read",
        "",
    ]

    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Lane count: `{summary.get('lane_count', 0)}`")
    lines.append(
        f"- Severity counts: `{'; '.join(f'{k}={v}' for k, v in dict(summary.get('severity_counts') or {}).items())}`"
    )
    lines.append(
        f"- Action counts: `{'; '.join(f'{k}={v}' for k, v in dict(summary.get('action_counts') or {}).items())}`"
    )
    lines.append(f"- Stale runtime rows: `{summary.get('stale_runtime_rows', 0)}`")
    lines.append(f"- Quarantined rows: `{summary.get('quarantined_rows', 0)}`")
    lines.append(f"- Watch-only rows: `{summary.get('watch_only_rows', 0)}`")

    lines.extend(["", "## Priority Actions", ""])
    for row in list(payload.get("priority_actions") or []):
        lines.append(f"### {row['lane_name']}")
        lines.append(f"- Severity: `{row['severity']}`")
        lines.append(f"- Action: `{row['recommended_action']}`")
        lines.append(f"- Why: {row['action_reason']}")
        lines.append(
            f"- Runtime: `verdict={row['verdict']}; watchdog={row['watchdog_status'] or 'none'}; group={row['watchdog_group'] or 'none'}; registry={row['registry_enabled']}; open={row['open_count']}; broker_scoped={row['broker_scoped_open_count']}; closes={row['close_count']}`"
        )
        if row.get("scope_status"):
            lines.append(
                f"- Scope: `status={row['scope_status']}; action={row.get('scope_recommended_action') or 'none'}; broker_total={row['broker_total_open_count']}`"
            )
        if row.get("quarantine_reason"):
            lines.append(
                f"- Quarantine: `{row['quarantine_reason']}` until `{row.get('quarantine_until') or ''}`"
            )
        if row.get("notes"):
            lines.append(f"- Notes: `{row['notes']}`")
        if row.get("watchdog_reasons"):
            lines.append(f"- Watchdog reasons: `{'; '.join(row['watchdog_reasons'])}`")
        lines.append("")

    lines.extend(["## Lane Triage", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### {row['lane_name']}")
        lines.append(f"- Action: `{row['recommended_action']}` (`{row['severity']}`)")
        lines.append(f"- Verdict: `{row['verdict']}` — {row['verdict_reason']}")
        lines.append(
            f"- Runtime: `watchdog={row['watchdog_status'] or 'none'}; group={row['watchdog_group'] or 'none'}; registry={row['registry_enabled']}; open={row['open_count']}; broker_scoped={row['broker_scoped_open_count']}; broker_total={row['broker_total_open_count']}; closes={row['close_count']}`"
        )
        if row.get("scope_status"):
            lines.append(
                f"- Scope: `status={row['scope_status']}; action={row.get('scope_recommended_action') or 'none'}`"
            )
        lines.append(
            f"- Tick path: `shared_price_max_age_ms={row['shared_price_max_age_ms']}; latest={row['latest_tick_source_last']}; append={row['latest_tick_append_source_last']}; history={row['tick_history_source_last']}`"
        )
        if row.get("heartbeat_age_minutes") is not None:
            lines.append(f"- Heartbeat age: `{row['heartbeat_age_minutes']} min`")
        if row.get("quarantine_reason"):
            lines.append(
                f"- Quarantine: `{row['quarantine_reason']}` until `{row.get('quarantine_until') or ''}`"
            )
        if row.get("notes"):
            lines.append(f"- Notes: `{row['notes']}`")
        if row.get("watchdog_reasons"):
            lines.append(f"- Watchdog reasons: `{'; '.join(row['watchdog_reasons'])}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    payload = build_payload()
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH.relative_to(ROOT)} and {OUTPUT_MD_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
