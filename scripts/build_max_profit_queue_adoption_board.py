#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

QUEUE_PACKET_PATH = REPORTS / "max_profit_queue_contract_packet.json"
ADAPTIVE_QUEUE_PATH = REPORTS / "adaptive_lab_queue.json"
OUTPUT_JSON_PATH = REPORTS / "max_profit_queue_adoption_board.json"
OUTPUT_MD_PATH = REPORTS / "max_profit_queue_adoption_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_exact_queue_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    for task in tasks:
        if str(task.get("task_id") or "") == task_id:
            return dict(task)
    return {}


def find_symbol_queue_tasks(tasks: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    needle = symbol.lower()
    matches: list[dict[str, Any]] = []
    for task in tasks:
        haystack = f"{task.get('task_id', '')} {task.get('title', '')}".lower()
        if needle and needle in haystack:
            matches.append(dict(task))
    return matches


def adoption_status(*, exact_task: dict[str, Any], symbol_tasks: list[dict[str, Any]]) -> str:
    if exact_task:
        return "proposal_adopted_in_queue"
    if symbol_tasks:
        return "proposal_missing_symbol_has_other_queue_work"
    return "proposal_missing_from_queue"


def adoption_read(
    *,
    symbol: str,
    task_id: str,
    exact_task: dict[str, Any],
    symbol_tasks: list[dict[str, Any]],
    proposal_status: str,
) -> str:
    if exact_task:
        return (
            f"`{symbol}` already has the proposed queue row `{task_id}` in `adaptive_lab_queue` "
            f"with status `{exact_task.get('status', '')}`."
        )
    if symbol_tasks:
        related = [str(task.get("task_id") or "") for task in symbol_tasks]
        return (
            f"`{symbol}` still does not have the proposed queue row `{task_id}`, but the live queue already has "
            f"related symbol work `{related}`. Treat this as adoption debt, not symbol neglect."
        )
    return (
        f"`{symbol}` has no exact queue adoption yet and no other live queue rows. Current packet state is "
        f"`{proposal_status}`, so this is pure missing adoption debt."
    )


def build_row(packet_row: dict[str, Any], adaptive_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    symbol = str(packet_row.get("symbol") or "")
    task_id = str(packet_row.get("task_id") or "")
    exact_task = find_exact_queue_task(adaptive_tasks, task_id)
    symbol_tasks = find_symbol_queue_tasks(adaptive_tasks, symbol)
    status = adoption_status(exact_task=exact_task, symbol_tasks=symbol_tasks)
    return {
        "proposal_rank": int(packet_row.get("proposal_rank") or 0),
        "symbol": symbol,
        "task_id": task_id,
        "title": str(packet_row.get("title") or ""),
        "lane": str(packet_row.get("lane") or ""),
        "next_action_class": str(packet_row.get("next_action_class") or ""),
        "proposal_status": str(packet_row.get("proposal_status") or ""),
        "queue_adoption_status": status,
        "queue_task_status": str(exact_task.get("status") or ""),
        "queue_task_lane": str(exact_task.get("lane") or ""),
        "queue_task_priority": exact_task.get("priority"),
        "related_symbol_queue_task_ids": [str(task.get("task_id") or "") for task in symbol_tasks],
        "related_symbol_queue_statuses": [str(task.get("status") or "") for task in symbol_tasks],
        "adoption_read": adoption_read(
            symbol=symbol,
            task_id=task_id,
            exact_task=exact_task,
            symbol_tasks=symbol_tasks,
            proposal_status=str(packet_row.get("proposal_status") or ""),
        ),
    }


def build_payload(queue_packet: dict[str, Any], adaptive_queue: dict[str, Any]) -> dict[str, Any]:
    adaptive_tasks = [dict(task) for task in list(adaptive_queue.get("tasks") or []) if isinstance(task, dict)]
    rows = [
        build_row(dict(row), adaptive_tasks)
        for row in list(queue_packet.get("rows") or [])
        if isinstance(row, dict)
    ]
    adopted_symbols = [row["symbol"] for row in rows if row["queue_adoption_status"] == "proposal_adopted_in_queue"]
    missing_symbols = [row["symbol"] for row in rows if row["queue_adoption_status"] != "proposal_adopted_in_queue"]
    highest_missing_symbol = next((row["symbol"] for row in rows if row["queue_adoption_status"] != "proposal_adopted_in_queue"), "")
    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(QUEUE_PACKET_PATH.relative_to(ROOT)),
            str(ADAPTIVE_QUEUE_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "proposal_count": len(rows),
            "adopted_count": len(adopted_symbols),
            "missing_count": len(missing_symbols),
            "adopted_symbols": adopted_symbols,
            "missing_symbols": missing_symbols,
            "highest_missing_symbol": highest_missing_symbol,
            "adoption_read": (
                f"Highest missing queue adoption is `{highest_missing_symbol}`."
                if highest_missing_symbol
                else "All current queue-contract proposals are already adopted in the adaptive queue."
            ),
        },
        "leadership_read": [
            (
                f"Queue adoption currently reads `{len(adopted_symbols)}/{len(rows)}` proposals already present in `adaptive_lab_queue`."
                if rows
                else "No queue-contract proposals are currently available to audit."
            ),
            (
                f"Highest missing queue adoption is `{highest_missing_symbol}`."
                if highest_missing_symbol
                else "No missing queue adoption remains."
            ),
            "Promote missing packet rows into the real queue before inventing lower-signal max-profit backlog.",
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It does not modify `adaptive_lab_queue`; it compares the proposed queue packet against the current queue surface.",
            "Use it to separate missing queue adoption from already-adopted work so the room does not confuse proposal quality with actual planning-state adoption.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Max Profit Queue Adoption Board",
        "",
        "This board compares the proposed max-profit queue-contract packet against the current adaptive queue.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- adopted_count: `{summary.get('adopted_count', 0)}`",
        f"- missing_count: `{summary.get('missing_count', 0)}`",
        f"- highest_missing_symbol: `{summary.get('highest_missing_symbol', '')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Adoption Table",
            "",
            "| Rank | Symbol | Proposal Status | Queue Adoption Status | Task Id | Queue Status | Related Symbol Tasks |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['proposal_rank']}` | `{row['symbol']}` | `{row['proposal_status']}` | "
            f"`{row['queue_adoption_status']}` | `{row['task_id']}` | `{row['queue_task_status']}` | "
            f"`{row['related_symbol_queue_task_ids']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row['symbol']}",
                "",
                f"- proposal_rank: `{row['proposal_rank']}`",
                f"- proposal_status: `{row['proposal_status']}`",
                f"- queue_adoption_status: `{row['queue_adoption_status']}`",
                f"- task_id: `{row['task_id']}`",
                f"- title: `{row['title']}`",
                f"- lane: `{row['lane']}`",
                f"- next_action_class: `{row['next_action_class']}`",
                f"- queue_task_status: `{row['queue_task_status']}`",
                f"- queue_task_lane: `{row['queue_task_lane']}`",
                f"- related_symbol_queue_task_ids: `{row['related_symbol_queue_task_ids']}`",
                f"- related_symbol_queue_statuses: `{row['related_symbol_queue_statuses']}`",
                f"- adoption_read: {row['adoption_read']}",
                "",
            ]
        )

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(load_json(QUEUE_PACKET_PATH), load_json(ADAPTIVE_QUEUE_PATH))
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
