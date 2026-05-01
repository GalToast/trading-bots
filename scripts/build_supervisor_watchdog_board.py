#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "reports" / "watchdog" / "supervisor_watchdog_board.json"
OUT_MD = ROOT / "reports" / "watchdog" / "supervisor_watchdog_board.md"
TRADE_FIRING_BOARD_JSON = ROOT / "reports" / "watchdog" / "trade_firing_board.json"
TRADE_FIRING_ALERTS_JSONL = ROOT / "reports" / "watchdog" / "trade_firing_alerts.jsonl"
TRADE_FIRING_ALERT_STATE_JSON = ROOT / "reports" / "watchdog" / "trade_firing_alert_state.json"

GROUPS = {
    "crypto_watchdog": {
        "label": "Crypto",
        "loop_state": ROOT / "reports" / "watchdog" / "crypto_watchdog_loop_state.json",
        "report_json": ROOT / "reports" / "watchdog" / "crypto_watchdog_report.json",
        "alerts_jsonl": ROOT / "reports" / "watchdog" / "crypto_watchdog_alerts.jsonl",
        "launcher_state": ROOT / "reports" / "watchdog" / "crypto_watchdog_launcher_state.json",
    },
    "fx_watchdog": {
        "label": "FX",
        "loop_state": ROOT / "reports" / "watchdog" / "fx_watchdog_loop_state.json",
        "report_json": ROOT / "reports" / "watchdog" / "fx_watchdog_report.json",
        "alerts_jsonl": ROOT / "reports" / "watchdog" / "fx_watchdog_alerts.jsonl",
        "launcher_state": ROOT / "reports" / "watchdog" / "fx_watchdog_launcher_state.json",
    },
    "shadow_watchdog": {
        "label": "Shadow",
        "loop_state": ROOT / "reports" / "watchdog" / "shadow_watchdog_loop_state.json",
        "report_json": ROOT / "reports" / "watchdog" / "shadow_watchdog_report.json",
        "alerts_jsonl": ROOT / "reports" / "watchdog" / "shadow_watchdog_alerts.jsonl",
        "launcher_state": ROOT / "reports" / "watchdog" / "shadow_watchdog_launcher_state.json",
    },
}


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


def load_jsonl_tail(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def latest_alert(path: Path) -> dict[str, Any]:
    rows = load_jsonl_tail(path, limit=50)
    if not rows:
        return {}
    return rows[-1]


def latest_alert_of_type(path: Path, event_types: set[str]) -> dict[str, Any]:
    rows = load_jsonl_tail(path, limit=200)
    for row in reversed(rows):
        if str(row.get("event_type") or "") in event_types:
            return row
    return {}


def stale_rows(report_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in report_rows if str(row.get("status") or "") != "ok"]


def summarize_recent_noisy_lanes(rows: list[dict[str, Any]], active_lanes: set[str]) -> list[dict[str, Any]]:
    lane_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        lane = str(row.get("lane") or "")
        if not lane:
            continue
        event_type = str(row.get("event_type") or "")
        entry = lane_map.setdefault(
            lane,
            {
                "lane": lane,
                "detected_count": 0,
                "recovered_count": 0,
                "last_event_at": "",
                "last_event_type": "",
                "last_alert_code": "",
                "active": lane in active_lanes,
            },
        )
        if event_type == "trade_firing_anomaly_detected":
            entry["detected_count"] += 1
            entry["last_alert_code"] = str(row.get("execution_alert") or row.get("parity_alert") or entry["last_alert_code"])
        elif event_type == "trade_firing_anomaly_recovered":
            entry["recovered_count"] += 1
            entry["last_alert_code"] = str(row.get("recovered_alert") or entry["last_alert_code"])
        ts_utc = str(row.get("ts_utc") or "")
        if ts_utc and ts_utc >= str(entry["last_event_at"]):
            entry["last_event_at"] = ts_utc
            entry["last_event_type"] = event_type
    items = list(lane_map.values())
    items.sort(
        key=lambda item: (
            0 if item["active"] else 1,
            -(int(item["detected_count"]) + int(item["recovered_count"])),
            str(item["last_event_at"]),
            str(item["lane"]),
        )
    )
    return items[:5]


def summarize_trade_firing() -> dict[str, Any]:
    board = load_json(TRADE_FIRING_BOARD_JSON)
    state = load_json(TRADE_FIRING_ALERT_STATE_JSON)
    alert_rows = load_jsonl_tail(TRADE_FIRING_ALERTS_JSONL, limit=300)
    last_detected = latest_alert_of_type(TRADE_FIRING_ALERTS_JSONL, {"trade_firing_anomaly_detected"})
    last_recovered = latest_alert_of_type(TRADE_FIRING_ALERTS_JSONL, {"trade_firing_anomaly_recovered"})
    interesting_rows = board.get("interesting_rows") or []
    if not isinstance(interesting_rows, list):
        interesting_rows = []
    active_anomalies = state.get("active_anomalies") or []
    if not isinstance(active_anomalies, list):
        active_anomalies = []
    active_lanes = {str(row.get("lane") or "") for row in active_anomalies if isinstance(row, dict)}
    cooldowns = state.get("cooldowns") or []
    if not isinstance(cooldowns, list):
        cooldowns = []
    cooldown_rows = []
    for row in cooldowns:
        if not isinstance(row, dict):
            continue
        cooldown_rows.append(
            {
                "lane": str(row.get("lane") or ""),
                "alert_code": str(row.get("alert_code") or ""),
                "transition": str(row.get("transition") or ""),
                "active": bool(row.get("active")),
                "remaining_seconds": int(row.get("remaining_seconds") or 0),
                "last_emitted_at": str(row.get("last_emitted_at") or ""),
                "next_allowed_at": str(row.get("next_allowed_at") or ""),
            }
        )
    cooldown_rows.sort(
        key=lambda row: (
            0 if row["active"] else 1,
            -int(row["remaining_seconds"]),
            str(row["last_emitted_at"]),
            str(row["lane"]),
        )
    )
    recent_noisy_lanes = summarize_recent_noisy_lanes(alert_rows, active_lanes)
    return {
        "overall_status": str(board.get("overall_status") or "missing"),
        "generated_at": str(board.get("generated_at") or ""),
        "generated_age_seconds": age_seconds(str(board.get("generated_at") or "")),
        "last_evaluated_at": str(state.get("last_evaluated_at") or state.get("updated_at") or ""),
        "last_evaluated_age_seconds": age_seconds(str(state.get("last_evaluated_at") or state.get("updated_at") or "")),
        "last_clean_check_at": str(state.get("last_clean_check_at") or ""),
        "last_clean_check_age_seconds": age_seconds(str(state.get("last_clean_check_at") or "")),
        "evaluation_status": str(state.get("evaluation_status") or "missing"),
        "active_anomaly_count": int(state.get("active_anomaly_count") or 0),
        "probable_missed_open_count": int(board.get("probable_missed_open_count") or 0),
        "suspected_missed_open_count": int(board.get("suspected_missed_open_count") or 0),
        "parity_alert_count": int(board.get("parity_alert_count") or 0),
        "interesting_count": int(board.get("interesting_count") or 0),
        "last_detected": last_detected,
        "last_recovered": last_recovered,
        "active_anomalies": active_anomalies[:5],
        "cooldown_window_seconds": int(state.get("cooldown_window_seconds") or 0),
        "cooldowns": cooldown_rows[:8],
        "recent_noisy_lanes": recent_noisy_lanes,
        "interesting_rows": interesting_rows[:5],
    }


def summarize_group(name: str, cfg: dict[str, Path | str]) -> dict[str, Any]:
    loop_state = load_json(Path(cfg["loop_state"]))
    report = load_json(Path(cfg["report_json"]))
    launcher_state = load_json(Path(cfg["launcher_state"]))
    alerts_path = Path(cfg["alerts_jsonl"])
    latest = latest_alert(alerts_path)
    last_failure = latest_alert_of_type(alerts_path, {"failure_detected", "recovery_failed"})
    last_recovery = latest_alert_of_type(alerts_path, {"recovered"})
    rows = report.get("rows") or []
    stale = stale_rows(rows if isinstance(rows, list) else [])
    loop_updated_at = str(loop_state.get("updated_at") or "")
    report_generated_at = str(report.get("generated_at") or "")
    counts = loop_state.get("status_counts") or {}
    if not isinstance(counts, dict):
        counts = {}
    ok_count = int(counts.get("ok") or 0)
    not_ok_count = sum(int(v or 0) for k, v in counts.items() if str(k) != "ok")
    return {
        "name": name,
        "label": str(cfg["label"]),
        "status": str(loop_state.get("status") or "missing"),
        "updated_at": loop_updated_at,
        "updated_age_seconds": age_seconds(loop_updated_at),
        "report_generated_at": report_generated_at,
        "report_generated_age_seconds": age_seconds(report_generated_at),
        "rows_total": int(loop_state.get("rows_total") or len(rows) or 0),
        "status_counts": counts,
        "ok_count": ok_count,
        "not_ok_count": not_ok_count,
        "stale_lanes": [
            {
                "name": str(row.get("name") or ""),
                "status": str(row.get("status") or ""),
                "reasons": [str(item) for item in (row.get("reasons") or [])],
                "heartbeat_age_seconds": row.get("heartbeat_age_seconds"),
                "process_ids": row.get("process_ids") or [],
            }
            for row in stale
        ],
        "latest_alert": latest,
        "last_failure": last_failure,
        "last_recovery": last_recovery,
        "launcher": {
            "status": str(launcher_state.get("status") or ""),
            "child_pid": int(launcher_state.get("child_pid") or 0),
            "wrapper_pid": int(launcher_state.get("wrapper_pid") or 0),
            "launch_mode": str(launcher_state.get("launch_mode") or ""),
            "loop_status": str(launcher_state.get("loop_status") or ""),
            "loop_state_age_seconds": launcher_state.get("loop_state_age_seconds"),
        },
    }


def board_status(groups: list[dict[str, Any]], trade_firing: dict[str, Any]) -> str:
    if not groups:
        return "missing"
    if any(group["status"] != "ok" for group in groups):
        return "degraded"
    trade_status = str(trade_firing.get("overall_status") or "")
    if trade_status == "alert":
        return "alert"
    if trade_status == "watch":
        return "watch"
    return "ok"


def render_md(payload: dict[str, Any]) -> str:
    groups = payload["groups"]
    trade_firing = payload["trade_firing"]
    trade_age = trade_firing["generated_age_seconds"]
    trade_age_text = "-" if trade_age is None else f"{trade_age:.1f}"
    trade_eval_age = trade_firing["last_evaluated_age_seconds"]
    trade_eval_age_text = "-" if trade_eval_age is None else f"{trade_eval_age:.1f}"
    trade_clean_age = trade_firing["last_clean_check_age_seconds"]
    trade_clean_age_text = "-" if trade_clean_age is None else f"{trade_clean_age:.1f}"
    lines = [
        "# Supervisor Watchdog Board",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        f"Overall status: `{payload['overall_status']}`",
        "",
        "| Group | Status | OK / Total | Updated Age (s) | Launcher Child PID | Last Failure | Last Recovery |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for group in groups:
        last_failure = group["last_failure"]
        last_recovery = group["last_recovery"]
        failure_text = "none"
        recovery_text = "none"
        if last_failure:
            failure_reason = str(last_failure.get("reason") or "")
            failure_ts = str(last_failure.get("ts_utc") or "")
            failure_text = failure_ts if not failure_reason else f"{failure_ts} {failure_reason}"
        if last_recovery:
            recovery_reason = str(last_recovery.get("reason") or "")
            recovery_ts = str(last_recovery.get("ts_utc") or "")
            recovery_text = recovery_ts if not recovery_reason else f"{recovery_ts} {recovery_reason}"
        age = group["updated_age_seconds"]
        age_text = "-" if age is None else f"{age:.1f}"
        lines.append(
            f"| {group['label']} | {group['status']} | {group['ok_count']} / {group['rows_total']} | "
            f"{age_text} | {group['launcher']['child_pid'] or '-'} | {failure_text} | {recovery_text} |"
        )
    for group in groups:
        stale = group["stale_lanes"]
        last_failure = group["last_failure"]
        last_recovery = group["last_recovery"]
        lines.extend(
            [
                "",
                f"## {group['label']}",
                "",
                f"- Loop status: `{group['status']}`",
                f"- Rows: `{group['rows_total']}`",
                f"- Status counts: `{json.dumps(group['status_counts'], sort_keys=True)}`",
                f"- Launcher: `status={group['launcher']['status'] or 'unknown'}` `child_pid={group['launcher']['child_pid'] or 0}` `wrapper_pid={group['launcher']['wrapper_pid'] or 0}`",
                f"- Last failure: `{str(last_failure.get('ts_utc') or 'none')}` `{str(last_failure.get('reason') or '')}`",
                f"- Last recovery: `{str(last_recovery.get('ts_utc') or 'none')}` `{str(last_recovery.get('reason') or '')}`",
            ]
        )
        if stale:
            lines.append("- Not-ok lanes:")
            for row in stale:
                reason = "; ".join(row["reasons"]) if row["reasons"] else "no_reason_recorded"
                lines.append(
                    f"  - `{row['name']}` `{row['status']}` heartbeat_age={row['heartbeat_age_seconds']} reasons={reason}"
                )
        else:
            lines.append("- Not-ok lanes: none")
    last_detected = trade_firing["last_detected"]
    last_recovered = trade_firing["last_recovered"]
    lines.extend(
        [
            "",
            "## Trade Firing",
            "",
            f"- Status: `{trade_firing['overall_status']}`",
            f"- Evaluator status: `{trade_firing['evaluation_status']}`",
            f"- Generated age (s): `{trade_age_text}`",
            f"- Last evaluated: `{trade_firing['last_evaluated_at'] or 'none'}` age_s=`{trade_eval_age_text}`",
            f"- Last clean check: `{trade_firing['last_clean_check_at'] or 'none'}` age_s=`{trade_clean_age_text}`",
            f"- Probable missed opens: `{trade_firing['probable_missed_open_count']}`",
            f"- Suspected missed opens: `{trade_firing['suspected_missed_open_count']}`",
            f"- Parity alerts: `{trade_firing['parity_alert_count']}`",
            f"- Active anomalies: `{trade_firing['active_anomaly_count']}`",
            f"- Interesting rows: `{trade_firing['interesting_count']}`",
            f"- Last anomaly: `{str(last_detected.get('ts_utc') or 'none')}` `{str(last_detected.get('lane') or '')}` `{str(last_detected.get('execution_alert') or last_detected.get('parity_alert') or '')}`",
            f"- Last recovery: `{str(last_recovered.get('ts_utc') or 'none')}` `{str(last_recovered.get('lane') or '')}` `{str(last_recovered.get('recovered_alert') or '')}`",
        ]
    )
    if trade_firing["active_anomalies"]:
        lines.append("- Active anomaly rows:")
        for row in trade_firing["active_anomalies"]:
            lines.append(
                f"  - `{str(row.get('lane') or '')}` `{str(row.get('alert_code') or '')}` severity=`{str(row.get('severity') or '')}` watchdog=`{str(row.get('watchdog_status') or '')}`"
            )
    else:
        lines.append("- Active anomaly rows: none")
    if trade_firing["cooldowns"]:
        lines.append(f"- Alert cooldowns (`{trade_firing['cooldown_window_seconds']}`s window):")
        for row in trade_firing["cooldowns"]:
            lines.append(
                f"  - `{row['lane']}` `{row['transition']}` `{row['alert_code']}` remaining_s=`{row['remaining_seconds']}` active=`{str(row['active']).lower()}` next=`{row['next_allowed_at'] or 'none'}`"
            )
    else:
        lines.append("- Alert cooldowns: none")
    if trade_firing["recent_noisy_lanes"]:
        lines.append("- Recent noisy lanes:")
        for row in trade_firing["recent_noisy_lanes"]:
            lines.append(
                f"  - `{row['lane']}` active=`{str(row['active']).lower()}` detected=`{row['detected_count']}` recovered=`{row['recovered_count']}` last=`{row['last_event_type'] or 'none'}` at=`{row['last_event_at'] or 'none'}` alert=`{row['last_alert_code'] or 'none'}`"
            )
    else:
        lines.append("- Recent noisy lanes: none")
    if trade_firing["interesting_rows"]:
        lines.append("- Hot rows:")
        for row in trade_firing["interesting_rows"]:
            lines.append(
                f"  - `{str(row.get('lane') or '')}` `{str(row.get('execution_alert') or row.get('parity_alert') or 'note')}` `{str(row.get('notes') or '-')}`"
            )
    else:
        lines.append("- Hot rows: none")
    return "\n".join(lines) + "\n"


def build_board() -> dict[str, Any]:
    groups = [summarize_group(name, cfg) for name, cfg in GROUPS.items()]
    trade_firing = summarize_trade_firing()
    total_rows = sum(group["rows_total"] for group in groups)
    total_ok = sum(group["ok_count"] for group in groups)
    return {
        "generated_at": utc_now_iso(),
        "overall_status": board_status(groups, trade_firing),
        "total_rows": total_rows,
        "total_ok": total_ok,
        "total_not_ok": max(0, total_rows - total_ok),
        "groups": groups,
        "trade_firing": trade_firing,
    }


def main() -> int:
    payload = build_board()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(render_md(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "overall_status": payload["overall_status"],
                "total_ok": payload["total_ok"],
                "total_rows": payload["total_rows"],
                "out_json": str(OUT_JSON.relative_to(ROOT)),
                "out_md": str(OUT_MD.relative_to(ROOT)),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
