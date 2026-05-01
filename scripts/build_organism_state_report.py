#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
EXECUTION_REPORT_JSON = ROOT / "reports" / "execution_monitor_report.json"
WATCHDOG_REPORT_JSON = ROOT / "reports" / "penetration_lattice_runner_watchdog.json"
WATCHDOG_GROUPS_CONFIG = ROOT / "configs" / "watchdog_groups.json"
RUNNER_REGISTRY_JSON = ROOT / "configs" / "penetration_lattice_runner_registry.json"
BTC_CONCENTRATION_JSON = ROOT / "reports" / "live_btcusd_concentration_board.json"
REPORT_JSON = ROOT / "reports" / "organism_state.json"
REPORT_MD = ROOT / "reports" / "organism_state.md"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            payload, _ = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8", errors="ignore"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    except Exception:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def watchdog_report_paths() -> list[Path]:
    payload = load_json(WATCHDOG_GROUPS_CONFIG)
    groups = payload.get("groups") if isinstance(payload, dict) else {}
    paths: list[Path] = []
    if isinstance(groups, dict):
        paths.extend(
            ROOT / "reports" / "watchdog" / f"{str(group_name)}_report.json"
            for group_name in sorted(groups.keys())
            if str(group_name or "").strip()
        )
    paths.append(WATCHDOG_REPORT_JSON)
    return paths


def format_money(value: Any) -> str:
    try:
        return f"{float(value):+,.2f}"
    except Exception:
        return "-"


def format_plain_number(value: Any) -> str:
    try:
        return str(int(value))
    except Exception:
        return "-"


def format_recent_incident(incident: dict[str, Any]) -> str:
    old_status = str(incident.get("old_status") or "-")
    new_status = str(incident.get("new_status") or "-")
    reasons = incident.get("reasons") or []
    reason_text = "; ".join(str(reason) for reason in reasons if str(reason).strip()) or "-"
    return f"{old_status}->{new_status} ({reason_text})"


def triage_action(forward_status: str) -> str:
    status = str(forward_status or "").strip().lower()
    if status == "live_reference":
        return "reference"
    if status.startswith("holding_up"):
        return "keep"
    if status.startswith("lagging"):
        return "review_demote"
    if status in {"seeded_negative", "bootstrap_negative"}:
        return "watch_seed_negative"
    if status == "seeded_positive":
        return "watch_seed_positive"
    if status == "seeded_in_position":
        return "wait_first_close"
    return "watch"


def merged_rows() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    execution_payload = load_json(EXECUTION_REPORT_JSON)
    execution_rows = execution_payload.get("rows") if isinstance(execution_payload.get("rows"), list) else []
    watchdog_by_name: dict[str, dict[str, Any]] = {}
    recent_incidents: list[dict[str, Any]] = []
    seen_incidents: set[str] = set()
    for path in watchdog_report_paths():
        watchdog_payload = load_json(path)
        watchdog_rows = watchdog_payload.get("rows") if isinstance(watchdog_payload.get("rows"), list) else []
        for row in watchdog_rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if name and name not in watchdog_by_name:
                watchdog_by_name[name] = row
        payload_incidents = (
            watchdog_payload.get("recent_incidents")
            if isinstance(watchdog_payload.get("recent_incidents"), list)
            else []
        )
        for row in payload_incidents:
            if not isinstance(row, dict):
                continue
            key = json.dumps(row, sort_keys=True, ensure_ascii=True)
            if key in seen_incidents:
                continue
            seen_incidents.add(key)
            recent_incidents.append(row)
    return [row for row in execution_rows if isinstance(row, dict)], watchdog_by_name, recent_incidents


def registry_rows_by_lane(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("lanes") if isinstance(payload, dict) else []
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("name") or "").strip()
        if lane:
            mapped[lane] = row
    return mapped


def is_registry_enabled(registry_row: dict[str, Any]) -> bool:
    if not registry_row:
        return True
    return bool(registry_row.get("enabled", True))


def build_live_table_row(
    row: dict[str, Any],
    watchdog_row: dict[str, Any],
    registry_row: dict[str, Any],
) -> dict[str, Any]:
    scoreboard = watchdog_row.get("scoreboard_total") if isinstance(watchdog_row.get("scoreboard_total"), dict) else {}
    pause_note = str(registry_row.get("pause_note") or row.get("pause_note") or "").strip()
    return {
        "lane": str(row.get("lane") or registry_row.get("name") or ""),
        "kind": str(row.get("kind") or registry_row.get("kind") or ""),
        "enabled": is_registry_enabled(registry_row),
        "pause_note": pause_note,
        "watchdog_status": str(row.get("watchdog_status") or ("paused" if pause_note or registry_row else "")),
        "realized_usd": scoreboard.get("realized_usd", scoreboard.get("modeled_realized_usd", "")),
        "floating_usd": scoreboard.get("floating_usd", ""),
        "net_usd": scoreboard.get("net_usd", ""),
        "closes": scoreboard.get("closes", row.get("close_count", "")),
        "open_count": scoreboard.get("open_count", row.get("open_count", "")),
        "notes": str(row.get("notes") or "-"),
    }


def build_payload() -> dict[str, Any]:
    execution_rows, watchdog_by_name, recent_incidents = merged_rows()
    btc_concentration = load_json(BTC_CONCENTRATION_JSON)
    registry_rows = registry_rows_by_lane(load_json(RUNNER_REGISTRY_JSON))

    probable_count = sum(1 for row in execution_rows if bool(row.get("probable_missed_open")))
    suspected_count = sum(1 for row in execution_rows if bool(row.get("suspected_missed_open")))
    execution_live_rows = [row for row in execution_rows if str(row.get("kind") or "").startswith("live")]
    execution_live_rows.sort(key=lambda row: str(row.get("lane") or ""))

    live_table: list[dict[str, Any]] = []
    paused_live_table: list[dict[str, Any]] = []
    live_risk_rows: list[dict[str, Any]] = []
    seen_live_lanes: set[str] = set()
    for row in execution_live_rows:
        lane = str(row.get("lane") or "")
        seen_live_lanes.add(lane)
        watchdog_row = watchdog_by_name.get(lane) or {}
        registry_row = registry_rows.get(lane) or {}
        live_row = build_live_table_row(row, watchdog_row, registry_row)
        if not live_row["enabled"]:
            paused_live_table.append(live_row)
            continue

        live_table.append(live_row)
        try:
            net_usd = float(live_row.get("net_usd") or 0.0)
        except Exception:
            net_usd = 0.0
        if (
            net_usd < 0.0
            or "close_event_gap=" in live_row["notes"]
            or "clean_forward_since_repair=-" in live_row["notes"]
            or "broker_scope_outside_lane=" in live_row["notes"]
        ):
            live_risk_rows.append(live_row)

    for lane, registry_row in sorted(registry_rows.items()):
        if lane in seen_live_lanes:
            continue
        if not str(registry_row.get("kind") or "").startswith("live"):
            continue
        if is_registry_enabled(registry_row):
            continue
        paused_live_table.append(build_live_table_row({}, {}, registry_row))

    concentration_summary = btc_concentration.get("summary") if isinstance(btc_concentration.get("summary"), dict) else {}
    triggered_thresholds = concentration_summary.get("triggered_thresholds") if isinstance(concentration_summary.get("triggered_thresholds"), list) else []
    if concentration_summary:
        concentration_note = (
            f"combined_btc_floating={format_money(concentration_summary.get('combined_floating_usd'))} "
            f"net={format_money(concentration_summary.get('combined_net_usd'))} "
            f"triggers={','.join(str(item) for item in triggered_thresholds) or 'none'}"
        )
        live_risk_rows.insert(
            0,
            {
                "lane": "combined_btc_live_concentration",
                "kind": "live_crypto_cluster",
                "watchdog_status": "n/a",
                "realized_usd": concentration_summary.get("combined_realized_usd", ""),
                "floating_usd": concentration_summary.get("combined_floating_usd", ""),
                "net_usd": concentration_summary.get("combined_net_usd", ""),
                "closes": "-",
                "open_count": concentration_summary.get("combined_open_count", ""),
                "notes": concentration_note,
            },
        )

    gate_rows: list[dict[str, Any]] = []
    for row in execution_rows:
        lane = str(row.get("lane") or "")
        fx_grad = row.get("fx_graduation") if isinstance(row.get("fx_graduation"), dict) else {}
        crypto_grad = row.get("crypto_readiness") if isinstance(row.get("crypto_readiness"), dict) else {}
        crypto_probe = row.get("crypto_probe_readiness") if isinstance(row.get("crypto_probe_readiness"), dict) else {}
        proof = row.get("proof_readiness") if isinstance(row.get("proof_readiness"), dict) else {}
        source = fx_grad or crypto_grad or crypto_probe or proof
        if not source:
            continue
        gate_rows.append(
            {
                "lane": lane,
                "kind": str(row.get("kind") or ""),
                "status": str(
                    source.get("readiness")
                    or source.get("forward_status")
                    or source.get("current_gate")
                    or "-"
                ),
                "progress": str(source.get("progress_label") or source.get("role") or "-"),
                "next_gate": str(source.get("next_gate") or source.get("deployment_posture") or "-"),
                "notes": str(row.get("notes") or "-"),
            }
        )
    gate_rows.sort(key=lambda row: row["lane"])

    forward_rows: list[dict[str, Any]] = []
    for row in execution_rows:
        forward_review = row.get("forward_review") if isinstance(row.get("forward_review"), dict) else {}
        forward_status = str(forward_review.get("forward_status") or "").strip()
        if not forward_status:
            continue
        lane = str(row.get("lane") or "")
        forward_rows.append(
            {
                "lane": lane,
                "kind": str(row.get("kind") or ""),
                "forward_status": forward_status,
                "action": triage_action(forward_status),
                "realized_net_usd": forward_review.get("realized_net_usd", ""),
                "realized_delta_usd": forward_review.get("realized_delta_usd", ""),
                "closes": forward_review.get("realized_closes", forward_review.get("closes", "")),
                "open_count": forward_review.get("open_count", row.get("open_count", "")),
                "notes": str(row.get("notes") or "-"),
            }
        )
    forward_rows.sort(key=lambda row: (row["action"], row["lane"]))

    return {
        "generated_at": utc_now_iso(),
        "summary": {
            "watchdog_non_ok_count": sum(1 for row in watchdog_by_name.values() if str(row.get("status") or "") != "ok"),
            "execution_probable_missed_open_count": probable_count,
            "execution_suspected_missed_open_count": suspected_count,
            "live_lane_count": len(live_table),
            "paused_live_lane_count": len(paused_live_table),
            "forward_triage_count": len(forward_rows),
            "gate_watch_count": len(gate_rows),
            "btc_concentration_triggers": triggered_thresholds,
            "btc_combined_floating_usd": concentration_summary.get("combined_floating_usd", ""),
            "btc_combined_net_usd": concentration_summary.get("combined_net_usd", ""),
        },
        "live_lanes": live_table,
        "paused_live_lanes": paused_live_table,
        "live_risks": live_risk_rows,
        "gate_watch": gate_rows,
        "forward_triage": forward_rows,
        "recent_incidents": [row for row in recent_incidents if isinstance(row, dict)],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    live_rows = payload.get("live_lanes") if isinstance(payload.get("live_lanes"), list) else []
    paused_live_rows = payload.get("paused_live_lanes") if isinstance(payload.get("paused_live_lanes"), list) else []
    live_risk_rows = payload.get("live_risks") if isinstance(payload.get("live_risks"), list) else []
    gate_rows = payload.get("gate_watch") if isinstance(payload.get("gate_watch"), list) else []
    forward_rows = payload.get("forward_triage") if isinstance(payload.get("forward_triage"), list) else []
    recent_incidents = payload.get("recent_incidents") if isinstance(payload.get("recent_incidents"), list) else []

    lines = [
        "# Organism State",
        "",
        "> Current runtime generated board.",
        "",
        f"Generated: `{payload.get('generated_at') or '-'}`",
        "",
        "## Executive Summary",
        "",
        f"- Watchdog non-ok lanes: `{summary.get('watchdog_non_ok_count', 0)}`",
        f"- Execution missed-open alerts: `probable={summary.get('execution_probable_missed_open_count', 0)}` / `suspected={summary.get('execution_suspected_missed_open_count', 0)}`",
        f"- Live lanes tracked: `active={summary.get('live_lane_count', 0)}` / `paused={summary.get('paused_live_lane_count', 0)}`",
        f"- Gate-watch lanes: `{summary.get('gate_watch_count', 0)}`",
        f"- Forward-triage lanes: `{summary.get('forward_triage_count', 0)}`",
        f"- BTC concentration: `floating={format_money(summary.get('btc_combined_floating_usd'))}` / `net={format_money(summary.get('btc_combined_net_usd'))}` / `triggers={','.join(str(item) for item in (summary.get('btc_concentration_triggers') or [])) or 'none'}`",
        "",
        "## Live Lanes",
        "",
        "| Lane | Realized $ | Floating $ | Net $ | Closes | Open | Watchdog | Notes |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in live_rows:
        lines.append(
            f"| {row['lane']} | {format_money(row['realized_usd'])} | {format_money(row['floating_usd'])} | {format_money(row['net_usd'])} | {format_plain_number(row['closes'])} | {format_plain_number(row['open_count'])} | {row['watchdog_status'] or '-'} | {row['notes']} |"
        )

    lines.extend(
        [
            "",
            "## Paused / Disabled Live Lanes",
            "",
            "| Lane | Realized $ | Floating $ | Net $ | Closes | Open | Watchdog | Pause Note |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    if paused_live_rows:
        for row in paused_live_rows:
            lines.append(
                f"| {row['lane']} | {format_money(row['realized_usd'])} | {format_money(row['floating_usd'])} | {format_money(row['net_usd'])} | {format_plain_number(row['closes'])} | {format_plain_number(row['open_count'])} | {row['watchdog_status'] or '-'} | {row['pause_note'] or '-'} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | none |")

    lines.extend(
        [
            "",
            "## Live Risk Watch",
            "",
            "| Lane | Net $ | Open | Notes |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    if live_risk_rows:
        for row in live_risk_rows:
            lines.append(
                f"| {row['lane']} | {format_money(row['net_usd'])} | {format_plain_number(row['open_count'])} | {row['notes']} |"
            )
    else:
        lines.append("| - | - | - | none |")

    lines.extend(
        [
            "",
            "## Gate Watch",
            "",
            "| Lane | Status | Progress | Next Gate | Notes |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in gate_rows:
        lines.append(
            f"| {row['lane']} | {row['status']} | {row['progress']} | {row['next_gate']} | {row['notes']} |"
        )

    lines.extend(
        [
            "",
            "## Forward Triage",
            "",
            "_Action is an operator hint inferred from forward status, not an automatic kill switch._",
            "",
            "| Lane | Forward Status | Action | Realized $ | Delta $ | Closes | Open | Notes |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in forward_rows:
        lines.append(
            f"| {row['lane']} | {row['forward_status']} | {row['action']} | {format_money(row['realized_net_usd'])} | {format_money(row['realized_delta_usd'])} | {format_plain_number(row['closes'])} | {format_plain_number(row['open_count'])} | {row['notes']} |"
        )

    lines.extend(
        [
            "",
            "## Recent Incidents",
            "",
            "| Lane | Transition | Heartbeat Age (s) |",
            "| --- | --- | ---: |",
        ]
    )
    if recent_incidents:
        for row in recent_incidents[:10]:
            lines.append(
                f"| {str(row.get('lane') or '-')} | {format_recent_incident(row)} | {format_plain_number(row.get('heartbeat_age_seconds'))} |"
            )
    else:
        lines.append("| - | none | - |")

    return "\n".join(lines) + "\n"


def main() -> None:
    payload = build_payload()
    write_json(REPORT_JSON, payload)
    REPORT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "json_path": str(REPORT_JSON),
                "md_path": str(REPORT_MD),
                "live_lane_count": payload["summary"]["live_lane_count"],
                "paused_live_lane_count": payload["summary"]["paused_live_lane_count"],
                "forward_triage_count": payload["summary"]["forward_triage_count"],
                "gate_watch_count": payload["summary"]["gate_watch_count"],
                "watchdog_non_ok_count": payload["summary"]["watchdog_non_ok_count"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
