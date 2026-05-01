#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "reports" / "watchdog" / "incident_ledger.json"
OUT_MD = ROOT / "reports" / "watchdog" / "incident_ledger.md"
TRADE_FIRING_COMPACT_WINDOW_SECONDS = 600
BOOTSTRAP_REPAIR_WINDOW_SECONDS = 20
WATCHDOG_LAUNCHER_EVENT_PATHS = {
    "crypto_watchdog": ROOT / "reports" / "watchdog" / "crypto_watchdog_launcher_events.jsonl",
    "crypto_launcher": ROOT / "reports" / "watchdog" / "crypto_watchdog_launcher_events.jsonl",
    "fx_watchdog": ROOT / "reports" / "watchdog" / "fx_watchdog_launcher_events.jsonl",
    "fx_launcher": ROOT / "reports" / "watchdog" / "fx_watchdog_launcher_events.jsonl",
    "shadow_watchdog": ROOT / "reports" / "watchdog" / "shadow_watchdog_launcher_events.jsonl",
    "shadow_launcher": ROOT / "reports" / "watchdog" / "shadow_watchdog_launcher_events.jsonl",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def load_jsonl(path: Path, limit: int = 500) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def add_group_alert_rows(rows: list[dict[str, Any]], path: Path, source_name: str) -> None:
    for row in load_jsonl(path):
        event_type = str(row.get("event_type") or "")
        if event_type not in {"failure_detected", "recovered", "recovery_failed", "self_test"}:
            continue
        rows.append(
            {
                "ts_utc": str(row.get("ts_utc") or ""),
                "source": source_name,
                "scope": str(row.get("group_name") or source_name),
                "event": event_type,
                "severity": str(row.get("severity") or ""),
                "target": str(row.get("group_name") or source_name),
                "detail": str(row.get("reason") or ""),
            }
        )


def add_trade_firing_rows(rows: list[dict[str, Any]], path: Path) -> None:
    for row in load_jsonl(path):
        event_type = str(row.get("event_type") or "")
        if event_type not in {"trade_firing_anomaly_detected", "trade_firing_anomaly_recovered"}:
            continue
        detail = ""
        if event_type == "trade_firing_anomaly_detected":
            detail = ", ".join(
                part
                for part in [
                    str(row.get("execution_alert") or ""),
                    str(row.get("parity_alert") or ""),
                    str(row.get("notes") or ""),
                ]
                if part
            )
        else:
            detail = str(row.get("recovered_alert") or "")
        rows.append(
            {
                "ts_utc": str(row.get("ts_utc") or ""),
                "source": "trade_firing_monitor",
                "scope": str(row.get("kind") or "trade_firing"),
                "event": event_type,
                "severity": str(row.get("severity") or ""),
                "target": str(row.get("lane") or ""),
                "detail": detail,
            }
        )


def add_launcher_rows(rows: list[dict[str, Any]], path: Path, source_name: str) -> None:
    for row in load_jsonl(path):
        event_type = str(row.get("event_type") or "")
        if event_type not in {"launch_failed", "attach_failed", "child_exited"}:
            continue
        detail = str(row.get("reason") or "")
        if event_type == "child_exited":
            detail = f"exit_code={row.get('exit_code')}"
        rows.append(
            {
                "ts_utc": str(row.get("ts_utc") or ""),
                "source": source_name,
                "scope": "launcher",
                "event": event_type,
                "severity": "warning" if event_type == "child_exited" else "critical",
                "target": str(row.get("child_pid") or ""),
                "detail": detail,
            }
        )


def add_watchdog_rows(rows: list[dict[str, Any]], path: Path, source_name: str) -> None:
    for row in load_jsonl(path):
        action = str(row.get("action") or "")
        if action not in {"watchdog_cleanup", "watchdog_restart", "watchdog_stop_disabled", "watchdog_quarantine"}:
            continue
        target = str(row.get("lane") or "")
        detail = ""
        if action == "watchdog_cleanup":
            pids = ",".join(str(pid) for pid in (row.get("stopped_pids") or []))
            detail = f"{row.get('reason') or ''} pids={pids}".strip()
        elif action == "watchdog_restart":
            detail = f"started_pid={row.get('started_pid')}"
        elif action == "watchdog_stop_disabled":
            pids = ",".join(str(pid) for pid in (row.get("prior_pids") or []))
            detail = f"stopped_disabled pids={pids}".strip()
        elif action == "watchdog_quarantine":
            detail = (
                f"{row.get('reason') or ''}"
                f" until={row.get('quarantined_until') or ''}"
                f" restarts={row.get('restart_count_window') or 0}"
            ).strip()
        rows.append(
            {
                "ts_utc": str(row.get("ts_utc") or ""),
                "source": source_name,
                "scope": str(row.get("kind") or "lane"),
                "event": action,
                "severity": "warning",
                "target": target,
                "detail": detail,
            }
        )


def compact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    recent_trade_keys: dict[tuple[str, str, str, str], datetime] = {}
    for row in rows:
        source = str(row.get("source") or "")
        event = str(row.get("event") or "")
        target = str(row.get("target") or "")
        detail = str(row.get("detail") or "")
        ts = parse_iso(str(row.get("ts_utc") or ""))
        if source == "trade_firing_monitor" and ts is not None:
            key = (source, event, target, detail)
            prior = recent_trade_keys.get(key)
            if prior is not None and abs((prior - ts).total_seconds()) <= TRADE_FIRING_COMPACT_WINDOW_SECONDS:
                continue
            recent_trade_keys[key] = ts
        kept.append(row)
    return kept


def cluster_key(row: dict[str, Any]) -> tuple[str, str]:
    source = str(row.get("source") or "")
    event = str(row.get("event") or "")
    if source == "trade_firing_monitor":
        return (source, "trade_firing_incident")
    if event in {"failure_detected", "recovered", "recovery_failed", "self_test"}:
        return (source, "group_health_incident")
    if event in {"watchdog_restart", "watchdog_cleanup", "watchdog_stop_disabled", "watchdog_quarantine"}:
        return (source, "lane_repair_wave")
    if source.endswith("_launcher"):
        return (source, "launcher_incident")
    return (source, event or "incident")


def cluster_gap_seconds(row: dict[str, Any]) -> int:
    source = str(row.get("source") or "")
    if source == "trade_firing_monitor":
        return 900
    if str(row.get("event") or "") in {"watchdog_restart", "watchdog_cleanup", "watchdog_stop_disabled", "watchdog_quarantine"}:
        return 1800
    return 600


def load_launcher_bootstrap_windows() -> dict[str, list[datetime]]:
    windows: dict[str, list[datetime]] = {}
    for source, path in WATCHDOG_LAUNCHER_EVENT_PATHS.items():
        starts: list[datetime] = []
        for row in load_jsonl(path):
            if str(row.get("event_type") or "") != "child_started":
                continue
            started_at = parse_iso(str(row.get("ts_utc") or ""))
            if started_at is not None:
                starts.append(started_at)
        windows[source] = sorted(starts)
    return windows


def cluster_bootstrap_context(
    source: str,
    family: str,
    start_dt: datetime,
    end_dt: datetime,
    bootstrap_windows: dict[str, list[datetime]],
) -> dict[str, Any]:
    if family != "lane_repair_wave":
        if family != "launcher_incident":
            return {}
        for started_at in bootstrap_windows.get(source, []):
            delta_restart = (started_at - end_dt).total_seconds()
            if 0.0 <= delta_restart <= BOOTSTRAP_REPAIR_WINDOW_SECONDS:
                return {
                    "restart_started_at": started_at.isoformat(),
                    "seconds_until_restart": round(delta_restart, 1),
                }
        return {}
    for started_at in bootstrap_windows.get(source, []):
        delta_start = (start_dt - started_at).total_seconds()
        delta_end = (end_dt - started_at).total_seconds()
        if delta_start < 0.0 or delta_end < 0.0:
            continue
        if delta_start <= BOOTSTRAP_REPAIR_WINDOW_SECONDS and delta_end <= BOOTSTRAP_REPAIR_WINDOW_SECONDS:
            return {
                "bootstrap_started_at": started_at.isoformat(),
                "seconds_since_bootstrap_start": round(delta_start, 1),
                "seconds_until_bootstrap_end": round(delta_end, 1),
            }
    return {}


def build_clusters(rows: list[dict[str, Any]], bootstrap_windows: dict[str, list[datetime]] | None = None) -> list[dict[str, Any]]:
    bootstrap_windows = bootstrap_windows or load_launcher_bootstrap_windows()
    ascending = sorted(
        rows,
        key=lambda row: parse_iso(str(row.get("ts_utc") or "")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    clusters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in ascending:
        row_dt = parse_iso(str(row.get("ts_utc") or ""))
        if row_dt is None:
            continue
        key = cluster_key(row)
        gap = cluster_gap_seconds(row)
        if (
            current is None
            or current["cluster_key"] != key
            or (row_dt - current["end_dt"]).total_seconds() > gap
        ):
            if current is not None:
                clusters.append(current)
            current = {
                "cluster_key": key,
                "source": str(row.get("source") or ""),
                "family": key[1],
                "start_dt": row_dt,
                "end_dt": row_dt,
                "start_at": str(row.get("ts_utc") or ""),
                "end_at": str(row.get("ts_utc") or ""),
                "row_count": 0,
                "event_counts": {},
                "targets": set(),
                "details": [],
            }
        assert current is not None
        current["end_dt"] = row_dt
        current["end_at"] = str(row.get("ts_utc") or "")
        current["row_count"] += 1
        event = str(row.get("event") or "")
        current["event_counts"][event] = int(current["event_counts"].get(event, 0)) + 1
        target = str(row.get("target") or "")
        if target:
            current["targets"].add(target)
        detail = str(row.get("detail") or "")
        if detail and detail not in current["details"]:
            current["details"].append(detail)
    if current is not None:
        clusters.append(current)
    rendered: list[dict[str, Any]] = []
    for cluster in reversed(clusters):
        bootstrap_context = cluster_bootstrap_context(
            str(cluster["source"] or ""),
            str(cluster["family"] or ""),
            cluster["start_dt"],
            cluster["end_dt"],
            bootstrap_windows,
        )
        family = str(cluster["family"] or "")
        if bootstrap_context:
            if family == "lane_repair_wave":
                family = "bootstrap_recovery_wave"
            elif family == "launcher_incident":
                family = "launcher_recycle"
        rendered.append(
            {
                "source": cluster["source"],
                "family": family,
                "start_at": cluster["start_at"],
                "end_at": cluster["end_at"],
                "row_count": int(cluster["row_count"]),
                "event_counts": dict(sorted(cluster["event_counts"].items())),
                "target_count": len(cluster["targets"]),
                "targets": sorted(cluster["targets"])[:8],
                "details": cluster["details"][:3],
                "bootstrap_context": bootstrap_context,
            }
        )
    return rendered[:20]


def build_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    add_group_alert_rows(rows, ROOT / "reports" / "watchdog" / "crypto_watchdog_alerts.jsonl", "crypto_watchdog")
    add_group_alert_rows(rows, ROOT / "reports" / "watchdog" / "fx_watchdog_alerts.jsonl", "fx_watchdog")
    add_group_alert_rows(rows, ROOT / "reports" / "watchdog" / "shadow_watchdog_alerts.jsonl", "shadow_watchdog")
    add_trade_firing_rows(rows, ROOT / "reports" / "watchdog" / "trade_firing_alerts.jsonl")

    add_launcher_rows(rows, ROOT / "reports" / "watchdog" / "crypto_watchdog_launcher_events.jsonl", "crypto_launcher")
    add_launcher_rows(rows, ROOT / "reports" / "watchdog" / "fx_watchdog_launcher_events.jsonl", "fx_launcher")
    add_launcher_rows(rows, ROOT / "reports" / "watchdog" / "shadow_watchdog_launcher_events.jsonl", "shadow_launcher")

    add_watchdog_rows(rows, ROOT / "reports" / "penetration_lattice_runner_watchdog_events.jsonl", "crypto_watchdog")
    add_watchdog_rows(rows, ROOT / "reports" / "watchdog" / "fx_watchdog_events.jsonl", "fx_watchdog")
    add_watchdog_rows(rows, ROOT / "reports" / "watchdog" / "shadow_watchdog_events.jsonl", "shadow_watchdog")

    rows.sort(key=lambda row: parse_iso(str(row.get("ts_utc") or "")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    rows = compact_rows(rows)
    return rows[:120]


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Watchdog Incident Ledger",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        f"Incident clusters: `{len(payload['clusters'])}`",
        "",
        "## Incident Clusters",
        "",
    ]
    if payload["clusters"]:
        lines.extend(
            [
                "| Source | Family | Start (UTC) | End (UTC) | Rows | Targets | Events | Notes |",
                "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
            ]
        )
        for cluster in payload["clusters"]:
            events = ", ".join(f"{name}={count}" for name, count in cluster.get("event_counts", {}).items()) or "-"
            notes = "; ".join(cluster.get("details") or []) or "-"
            lines.append(
                f"| {cluster.get('source') or '-'} | {cluster.get('family') or '-'} | {cluster.get('start_at') or '-'} | "
                f"{cluster.get('end_at') or '-'} | {cluster.get('row_count') or 0} | {cluster.get('target_count') or 0} | {events} | {notes} |"
            )
        lines.extend(["", "## Raw Rows", ""])
    else:
        lines.extend(["No clustered incidents.", "", "## Raw Rows", ""])
    lines.extend(
        [
        f"Rows: `{len(payload['rows'])}`",
        "",
        "| Time (UTC) | Source | Scope | Event | Target | Detail |",
        "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            f"| {row.get('ts_utc') or '-'} | {row.get('source') or '-'} | {row.get('scope') or '-'} | "
            f"{row.get('event') or '-'} | {row.get('target') or '-'} | {row.get('detail') or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = build_rows()
    payload = {"generated_at": utc_now_iso(), "rows": rows, "clusters": build_clusters(rows)}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(render_md(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "out_json": str(OUT_JSON.relative_to(ROOT)),
                "out_md": str(OUT_MD.relative_to(ROOT)),
                "rows": len(payload["rows"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
