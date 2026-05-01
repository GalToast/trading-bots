#!/usr/bin/env python3
"""Build an operator-facing promotion queue from Hungry Hippo governance surfaces."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = ROOT / "reports" / "hungry_hippo_shapeshifter_guardrail_audit.json"
SESSION_TABLE_PATH = ROOT / "reports" / "session_regime_step_table_v2.json"
OUTPUT_JSON = ROOT / "reports" / "hungry_hippo_promotion_queue.json"
OUTPUT_MD = ROOT / "reports" / "hungry_hippo_promotion_queue.md"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_window_hours(window: str | None) -> set[int] | None:
    if not window or window == "None":
        return None

    hours: set[int] = set()
    for segment in window.split("+"):
        start_text, end_text = segment.split("-", 1)
        start_hour = int(start_text.split(":", 1)[0])
        end_hour = int(end_text.split(":", 1)[0])
        hours.update(range(start_hour, end_hour))
    return hours


def infer_next_action(row: dict[str, Any], current_hour_utc: int, session_window: str | None) -> tuple[str, int, str]:
    status = str(row.get("status") or "")
    symbol = str(row.get("symbol") or "")
    notes = list(row.get("notes") or [])

    if status == "contradiction":
        return "reconcile_live_path", 1, notes[0] if notes else "Selector/shapeshifter conflicts with the validated deploy path."

    if status == "promotable_now":
        hours = parse_window_hours(session_window)
        if hours is not None and current_hour_utc not in hours:
            return "wait_for_session_window", 2, f"Governance is clean, but current UTC hour `{current_hour_utc}` sits outside `{session_window}`."
        return "promote_candidate", 2, "Governance surfaces align and the symbol is inside its active session."

    if status == "blocked_by_guardrail":
        if symbol == "BTCUSD":
            return "hold_until_buy_realign", 1, "BTC SELL hold gate remains active, so promotion work stays blocked."
        return "unblock_guardrails_first", 3, notes[0] if notes else "Current rearm guardrails block promotion."

    if status == "uncovered":
        return "add_canonical_coverage", 4, notes[0] if notes else "Canonical regime or rearm coverage is still missing."

    return "manual_review", 5, notes[0] if notes else "Needs manual review."


def build_payload() -> dict[str, Any]:
    audit_payload = load_json(AUDIT_PATH)
    session_payload = load_json(SESSION_TABLE_PATH)

    current_hour_utc = datetime.now(timezone.utc).hour
    session_windows = dict(session_payload.get("session_windows") or {})

    queue_rows = []
    for row in list(audit_payload.get("rows") or []):
        symbol = str(row.get("symbol") or "")
        session_window = str((session_windows.get(symbol) or {}).get("window") or "None")
        next_action, priority, why = infer_next_action(row, current_hour_utc, session_window)
        queue_rows.append(
            {
                "symbol": symbol,
                "status": row.get("status"),
                "next_action": next_action,
                "priority": priority,
                "selector_personality": row.get("selector_personality"),
                "selector_control_mode": row.get("selector_control_mode"),
                "regime_control_mode": row.get("regime_control_mode"),
                "rearm_guardrail_status": row.get("rearm_guardrail_status"),
                "auto_rearm_allowed": row.get("auto_rearm_allowed"),
                "session_window": session_window,
                "why": why,
            }
        )

    queue_rows.sort(key=lambda row: (int(row["priority"]), str(row["symbol"])))
    summary = {
        "symbol_count": len(queue_rows),
        "action_counts": {
            action: sum(1 for row in queue_rows if row["next_action"] == action)
            for action in sorted({row["next_action"] for row in queue_rows})
        },
        "priority_1_symbols": [row["symbol"] for row in queue_rows if row["priority"] == 1],
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_hour_utc": current_hour_utc,
        "source_paths": {
            "shapeshifter_guardrail_audit": str(AUDIT_PATH.relative_to(ROOT)),
            "session_table": str(SESSION_TABLE_PATH.relative_to(ROOT)),
        },
        "summary": summary,
        "rows": queue_rows,
        "notes": [
            "This queue translates the shapeshifter guardrail audit into next actions for operators and collaborators.",
            "Promote_candidate still means shadow/promotion-ready under current governance, not guaranteed live deployment approval.",
        ],
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Hungry Hippo Promotion Queue",
        "",
        "This queue turns the current shapeshifter guardrail audit into next actions.",
        "",
        "## Current Read",
        "",
        f"- current UTC hour: `{payload['current_hour_utc']}`",
        f"- action counts: `{payload['summary']['action_counts']}`",
        f"- priority 1 symbols: `{payload['summary']['priority_1_symbols']}`",
        "",
        "## Queue",
        "",
        "| Priority | Symbol | Next Action | Personality | Regime | Rearm | Session | Why |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['priority']} | {row['symbol']} | `{row['next_action']}` | {row['selector_personality']} | "
            f"{row['regime_control_mode'] or 'uncovered'} | `{row['rearm_guardrail_status']}` | "
            f"{row['session_window']} | {row['why']} |"
        )

    lines.extend(["", "## Notes", ""])
    for note in payload["notes"]:
        lines.append(f"- {note}")

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload)
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
