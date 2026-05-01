#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
EXECUTION_REPORT_JSON = ROOT / "reports" / "execution_monitor_report.json"
OUT_JSON = ROOT / "reports" / "watchdog" / "trade_firing_board.json"
OUT_MD = ROOT / "reports" / "watchdog" / "trade_firing_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_payload() -> dict[str, Any]:
    report = load_json(EXECUTION_REPORT_JSON)
    rows = report.get("rows") if isinstance(report, dict) else []
    if not isinstance(rows, list):
        rows = []
    interesting: list[dict[str, Any]] = []
    probable = 0
    suspected = 0
    parity = 0
    notes_count = 0
    support_counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        alert = str(row.get("execution_alert") or "")
        parity_alert = str(row.get("parity_alert") or "")
        notes = str(row.get("notes") or "-")
        exact_fire_support = str(row.get("exact_fire_support") or "unknown")
        support_counts[exact_fire_support] = int(support_counts.get(exact_fire_support, 0)) + 1
        if alert == "probable_missed_open":
            probable += 1
        elif alert == "suspected_missed_open":
            suspected += 1
        if parity_alert:
            parity += 1
        if notes and notes != "-":
            notes_count += 1
        if alert or parity_alert or (notes and notes != "-"):
            interesting.append(
                {
                    "lane": str(row.get("lane") or ""),
                    "kind": str(row.get("kind") or ""),
                    "watchdog_status": str(row.get("watchdog_status") or ""),
                    "open_count": int(row.get("open_count") or 0),
                    "close_count": int(row.get("close_count") or 0),
                    "last_trade_event_at": str(row.get("last_trade_event_at") or ""),
                    "trigger_now": str(row.get("trigger_now") or ""),
                    "trigger_age_seconds": row.get("trigger_age_seconds"),
                    "execution_alert": alert,
                    "raw_execution_alert": str(row.get("raw_execution_alert") or ""),
                    "execution_evidence_quality": str(row.get("execution_evidence_quality") or ""),
                    "parity_alert": parity_alert,
                    "exact_fire_support": exact_fire_support,
                    "notes": notes,
                }
            )
    def sort_rank(row: dict[str, Any]) -> tuple[int, str]:
        execution_alert = str(row.get("execution_alert") or "")
        parity_alert = str(row.get("parity_alert") or "")
        if execution_alert == "probable_missed_open":
            return (0, str(row.get("lane") or ""))
        if execution_alert == "suspected_missed_open":
            return (1, str(row.get("lane") or ""))
        if parity_alert:
            return (2, str(row.get("lane") or ""))
        return (3, str(row.get("lane") or ""))

    interesting.sort(key=sort_rank)
    return {
        "generated_at": utc_now_iso(),
        "execution_report_generated_at": str(report.get("generated_at") or ""),
        "rows_total": len(rows),
        "interesting_rows": interesting,
        "interesting_count": len(interesting),
        "probable_missed_open_count": probable,
        "suspected_missed_open_count": suspected,
        "parity_alert_count": parity,
        "notes_count": notes_count,
        "exact_fire_support_counts": support_counts,
        "overall_status": "alert" if probable > 0 else ("watch" if suspected > 0 or parity > 0 else "ok"),
    }


def render_md(payload: dict[str, Any]) -> str:
    lines = [
        "# Trade Firing Board",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        f"Execution report generated: `{payload['execution_report_generated_at'] or '-'}`",
        "",
        f"Overall status: `{payload['overall_status']}`",
        "",
        f"- Probable missed opens: `{payload['probable_missed_open_count']}`",
        f"- Suspected missed opens: `{payload['suspected_missed_open_count']}`",
        f"- Parity alerts: `{payload['parity_alert_count']}`",
        f"- Rows with notes: `{payload['notes_count']}`",
        f"- Exact-fire coverage: `{json.dumps(payload['exact_fire_support_counts'], sort_keys=True)}`",
        "",
        "| Lane | Kind | Watchdog | Open | Closes | Last Trade Event | Trigger Now | Trigger Age (s) | Alert | Raw Alert | Evidence | Parity | Exact-Fire | Notes |",
        "| --- | --- | --- | ---: | ---: | --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    if not payload["interesting_rows"]:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - | all clear |")
    else:
        for row in payload["interesting_rows"]:
            trigger_age = row.get("trigger_age_seconds")
            trigger_age_text = "-" if trigger_age in ("", None) else str(trigger_age)
            lines.append(
                f"| {row['lane']} | {row['kind']} | {row['watchdog_status']} | {row['open_count']} | {row['close_count']} | "
                f"{row['last_trade_event_at'] or '-'} | {row['trigger_now'] or '-'} | {trigger_age_text} | {row['execution_alert'] or '-'} | "
                f"{row['raw_execution_alert'] or '-'} | {row['execution_evidence_quality'] or '-'} | {row['parity_alert'] or '-'} | {row['exact_fire_support'] or '-'} | {row['notes'] or '-'} |"
            )
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
                "interesting_count": payload["interesting_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
