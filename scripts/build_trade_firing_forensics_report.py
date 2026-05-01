#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
ALERTS_JSONL = ROOT / "reports" / "watchdog" / "trade_firing_alerts.jsonl"
OUT_DIR = ROOT / "reports" / "watchdog"
REPORT_JSON = OUT_DIR / "trade_firing_forensics_report.json"
REPORT_MD = OUT_DIR / "trade_firing_forensics_report.md"


TARGET_EVENT = "trade_firing_anomaly_detected"
RECOVERY_EVENT = "trade_firing_anomaly_recovered"


@dataclass
class LanePaths:
    lane: str
    event_path: Path | None
    state_path: Path | None
    out_log_path: Path
    err_log_path: Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_utc(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def resolve_relative_path(base_dir: Path, raw: str) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate
    base_candidate = (base_dir / candidate).resolve()
    if base_candidate.exists():
        return base_candidate
    root_candidate = (ROOT / candidate).resolve()
    if root_candidate.exists():
        return root_candidate
    return base_candidate


def read_registry_paths(registry_path: Path, *, watchdog_dir: Path = OUT_DIR) -> dict[str, LanePaths]:
    payload = load_json(registry_path)
    lanes = payload.get("lanes") if isinstance(payload, dict) else []
    base_dir = registry_path.resolve().parent
    out: dict[str, LanePaths] = {}
    for lane in lanes or []:
        if not isinstance(lane, dict):
            continue
        name = str(lane.get("name") or "").strip()
        if not name:
            continue
        state_rel = str(lane.get("state_path") or "").strip()
        event_rel = str(lane.get("event_path") or "").strip()
        out[name] = LanePaths(
            lane=name,
            state_path=resolve_relative_path(base_dir, state_rel),
            event_path=resolve_relative_path(base_dir, event_rel),
            out_log_path=watchdog_dir / f"{name}.out.log",
            err_log_path=watchdog_dir / f"{name}.err.log",
        )
    return out


def file_meta(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "exists": False,
            "path": str(path) if path else "",
            "length": 0,
            "last_write_utc": "",
        }
    return {
        "exists": True,
        "path": str(path),
        "length": int(path.stat().st_size),
        "last_write_utc": fmt_utc(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)),
    }


def state_runner_heartbeat(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    payload = load_json(path)
    runner = payload.get("runner") if isinstance(payload, dict) else {}
    if not isinstance(runner, dict):
        return ""
    return str(runner.get("heartbeat_at") or "")


def classify_lane(
    *,
    event_meta: dict[str, Any],
    out_log_meta: dict[str, Any],
    detected_count: int,
    recovered_count: int,
    first_detect_at: datetime | None,
) -> str:
    event_write_dt = parse_iso(event_meta.get("last_write_utc"))
    wrote_after_detect = bool(first_detect_at and event_write_dt and event_write_dt >= first_detect_at)
    zero_out_log = int(out_log_meta.get("length") or 0) == 0
    if detected_count <= 0:
        return "no_detected_anomaly"
    if recovered_count > 0 and zero_out_log and not wrote_after_detect:
        return "transient_probable_missed_open_without_lane_event_proof"
    if zero_out_log and not wrote_after_detect:
        return "probable_missed_open_without_lane_event_proof"
    if wrote_after_detect:
        return "lane_activity_present_after_alert"
    return "needs_manual_review"


def build_report(
    *,
    alerts_path: Path = ALERTS_JSONL,
    registry_path: Path = REGISTRY_PATH,
) -> dict[str, Any]:
    watchdog_dir = alerts_path.resolve().parent
    registry = read_registry_paths(registry_path, watchdog_dir=watchdog_dir)
    alerts = read_jsonl(alerts_path)
    lane_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in alerts:
        lane = str(row.get("lane") or "").strip()
        if lane:
            lane_events[lane].append(row)

    lane_rows: list[dict[str, Any]] = []
    first_detect_global: datetime | None = None
    last_recover_global: datetime | None = None
    for lane, events in sorted(lane_events.items()):
        detected = [row for row in events if str(row.get("event_type") or "") == TARGET_EVENT]
        recovered = [row for row in events if str(row.get("event_type") or "") == RECOVERY_EVENT]
        first_detect_at = min((parse_iso(row.get("ts_utc")) for row in detected), default=None)
        last_detect_at = max((parse_iso(row.get("ts_utc")) for row in detected), default=None)
        last_recover_at = max((parse_iso(row.get("ts_utc")) for row in recovered), default=None)
        if first_detect_at and (first_detect_global is None or first_detect_at < first_detect_global):
            first_detect_global = first_detect_at
        if last_recover_at and (last_recover_global is None or last_recover_at > last_recover_global):
            last_recover_global = last_recover_at

        paths = registry.get(
            lane,
            LanePaths(
                lane=lane,
                event_path=None,
                state_path=None,
                out_log_path=OUT_DIR / f"{lane}.out.log",
                err_log_path=OUT_DIR / f"{lane}.err.log",
            ),
        )
        event_meta = file_meta(paths.event_path)
        state_meta = file_meta(paths.state_path)
        out_log_meta = file_meta(paths.out_log_path)
        err_log_meta = file_meta(paths.err_log_path)

        trigger_values = sorted({str(row.get("trigger_now") or "") for row in detected if row.get("trigger_now")})
        trigger_ages = [float(row.get("trigger_age_seconds") or 0.0) for row in detected if row.get("trigger_age_seconds") not in ("", None)]
        first_detect_meta = {
            "ts_utc": fmt_utc(first_detect_at),
            "trigger_now": str(detected[0].get("trigger_now") or "") if detected else "",
            "trigger_age_seconds": float(detected[0].get("trigger_age_seconds") or 0.0) if detected and detected[0].get("trigger_age_seconds") not in ("", None) else "",
        }

        row = {
            "lane": lane,
            "detected_count": len(detected),
            "recovered_count": len(recovered),
            "first_detect_at": fmt_utc(first_detect_at),
            "last_detect_at": fmt_utc(last_detect_at),
            "last_recover_at": fmt_utc(last_recover_at),
            "trigger_values": trigger_values,
            "min_trigger_age_seconds": min(trigger_ages) if trigger_ages else "",
            "max_trigger_age_seconds": max(trigger_ages) if trigger_ages else "",
            "first_detect": first_detect_meta,
            "event_file": event_meta,
            "state_file": state_meta,
            "state_runner_heartbeat_at": state_runner_heartbeat(paths.state_path),
            "out_log": out_log_meta,
            "err_log": err_log_meta,
        }
        row["classification"] = classify_lane(
            event_meta=event_meta,
            out_log_meta=out_log_meta,
            detected_count=len(detected),
            recovered_count=len(recovered),
            first_detect_at=first_detect_at,
        )
        lane_rows.append(row)

    report = {
        "generated_at": utc_now_iso(),
        "source_alerts_path": str(alerts_path),
        "source_registry_path": str(registry_path),
        "incident_window": {
            "first_detect_at": fmt_utc(first_detect_global),
            "last_recover_at": fmt_utc(last_recover_global),
        },
        "lane_count": len(lane_rows),
        "summary": {
            "transient_without_lane_event_proof_count": sum(
                1 for row in lane_rows if row["classification"] == "transient_probable_missed_open_without_lane_event_proof"
            ),
            "lane_activity_present_after_alert_count": sum(
                1 for row in lane_rows if row["classification"] == "lane_activity_present_after_alert"
            ),
            "probable_without_lane_event_proof_count": sum(
                1 for row in lane_rows if row["classification"] == "probable_missed_open_without_lane_event_proof"
            ),
        },
        "lanes": lane_rows,
    }
    return report


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Trade Firing Forensics Report",
        "",
        f"Generated: `{report.get('generated_at', '')}`",
        "",
        f"Incident window: `{report['incident_window'].get('first_detect_at', '')} -> {report['incident_window'].get('last_recover_at', '')}`",
        "",
        "| Lane | First Detect | Last Recover | Trigger(s) | Event Last Write | State Heartbeat | Out Log Bytes | Classification |",
        "| --- | --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in report.get("lanes", []):
        triggers = ", ".join(row.get("trigger_values", [])) or "-"
        lines.append(
            f"| {row['lane']} | {row['first_detect_at'] or '-'} | {row['last_recover_at'] or '-'} | "
            f"{triggers} | {row['event_file'].get('last_write_utc') or '-'} | {row.get('state_runner_heartbeat_at') or '-'} | "
            f"{row['out_log'].get('length', 0)} | {row['classification']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = build_report()
    write_json(REPORT_JSON, report)
    write_markdown(report, REPORT_MD)
    print(f"Wrote {REPORT_JSON}")
    print(f"Wrote {REPORT_MD}")


if __name__ == "__main__":
    main()
