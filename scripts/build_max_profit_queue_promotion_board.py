#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

QUEUE_PACKET_PATH = REPORTS / "max_profit_queue_contract_packet.json"
QUEUE_ADOPTION_PATH = REPORTS / "max_profit_queue_adoption_board.json"
OUTPUT_JSON_PATH = REPORTS / "max_profit_queue_promotion_board.json"
OUTPUT_MD_PATH = REPORTS / "max_profit_queue_promotion_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_row(adoption_row: dict[str, Any], packet_row: dict[str, Any]) -> dict[str, Any]:
    proposal_status = str(adoption_row.get("proposal_status") or packet_row.get("proposal_status") or "")
    adoption_status = str(adoption_row.get("queue_adoption_status") or "")
    if adoption_status == "proposal_adopted_in_queue":
        promotion_class = "already_adopted_monitor_only"
    elif adoption_status == "proposal_missing_symbol_has_other_queue_work":
        promotion_class = "add_contract_row_alongside_existing_symbol_work"
    elif proposal_status == "proposal_ready_for_launch_contract":
        promotion_class = "add_launch_contract_row"
    elif proposal_status == "proposal_ready":
        promotion_class = "promote_to_queue_now"
    else:
        promotion_class = "manual_review"

    symbol = str(adoption_row.get("symbol") or packet_row.get("symbol") or "")
    task_id = str(adoption_row.get("task_id") or packet_row.get("task_id") or "")
    related_ids = list(adoption_row.get("related_symbol_queue_task_ids") or [])

    if promotion_class == "promote_to_queue_now":
        promotion_read = (
            f"`{symbol}` has a ready queue packet and no current adoption in `adaptive_lab_queue`, so "
            f"`{task_id}` should be promoted next."
        )
    elif promotion_class == "add_launch_contract_row":
        promotion_read = (
            f"`{symbol}` still needs a launch-contract row in the queue; promote `{task_id}` once the room wants "
            "to formalize first-seat proof work."
        )
    elif promotion_class == "add_contract_row_alongside_existing_symbol_work":
        promotion_read = (
            f"`{symbol}` already has related queue work `{related_ids}`, but the explicit max-profit contract row "
            f"`{task_id}` is still missing and should be added alongside it."
        )
    elif promotion_class == "already_adopted_monitor_only":
        promotion_read = f"`{symbol}` already has its proposed queue row adopted; no promotion work remains."
    else:
        promotion_read = f"`{symbol}` still needs manual review before a queue-promotion decision."

    return {
        "promotion_rank": int(adoption_row.get("proposal_rank") or packet_row.get("proposal_rank") or 0),
        "symbol": symbol,
        "task_id": task_id,
        "title": str(adoption_row.get("title") or packet_row.get("title") or ""),
        "lane": str(adoption_row.get("lane") or packet_row.get("lane") or ""),
        "proposal_status": proposal_status,
        "queue_adoption_status": adoption_status,
        "promotion_class": promotion_class,
        "next_action_class": str(adoption_row.get("next_action_class") or packet_row.get("next_action_class") or ""),
        "related_symbol_queue_task_ids": related_ids,
        "promotion_read": promotion_read,
    }


def build_payload(queue_packet: dict[str, Any], queue_adoption: dict[str, Any]) -> dict[str, Any]:
    packet_rows = {
        str(row.get("symbol") or ""): dict(row)
        for row in list(queue_packet.get("rows") or [])
        if isinstance(row, dict)
    }
    rows = [
        build_row(dict(row), packet_rows.get(str(row.get("symbol") or ""), {}))
        for row in list(queue_adoption.get("rows") or [])
        if isinstance(row, dict)
    ]
    promotable = [row for row in rows if row["promotion_class"] != "already_adopted_monitor_only"]
    highest_promotion_symbol = next((row["symbol"] for row in promotable), "")
    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(QUEUE_PACKET_PATH.relative_to(ROOT)),
            str(QUEUE_ADOPTION_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "promotion_candidate_count": len(promotable),
            "highest_promotion_symbol": highest_promotion_symbol,
            "promotion_symbols": [row["symbol"] for row in promotable],
            "promotion_read": (
                f"Highest queue-promotion candidate is `{highest_promotion_symbol}`."
                if highest_promotion_symbol
                else "No queue-promotion candidates remain."
            ),
        },
        "leadership_read": [
            (
                f"Highest queue-promotion candidate is `{highest_promotion_symbol}`."
                if highest_promotion_symbol
                else "No queue-promotion candidates remain."
            ),
            "This board is the final bridge from queue-contract proposal quality to actual queue insertion order.",
            "Promote `promote_to_queue_now` rows before lower-signal launch-contract or manual-review rows.",
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It does not mutate `adaptive_lab_queue`; it turns queue-packet + queue-adoption truth into a promotion order.",
            "Use it when the room needs one explicit answer to 'which missing max-profit queue row should be inserted next?'.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Max Profit Queue Promotion Board",
        "",
        "This board turns queue packet and queue adoption truth into one explicit promotion order.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- promotion_candidate_count: `{summary.get('promotion_candidate_count', 0)}`",
        f"- highest_promotion_symbol: `{summary.get('highest_promotion_symbol', '')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Promotion Table",
            "",
            "| Rank | Symbol | Proposal Status | Adoption Status | Promotion Class | Task Id |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['promotion_rank']}` | `{row['symbol']}` | `{row['proposal_status']}` | "
            f"`{row['queue_adoption_status']}` | `{row['promotion_class']}` | `{row['task_id']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row['symbol']}",
                "",
                f"- promotion_rank: `{row['promotion_rank']}`",
                f"- proposal_status: `{row['proposal_status']}`",
                f"- queue_adoption_status: `{row['queue_adoption_status']}`",
                f"- promotion_class: `{row['promotion_class']}`",
                f"- task_id: `{row['task_id']}`",
                f"- title: `{row['title']}`",
                f"- lane: `{row['lane']}`",
                f"- next_action_class: `{row['next_action_class']}`",
                f"- related_symbol_queue_task_ids: `{row['related_symbol_queue_task_ids']}`",
                f"- promotion_read: {row['promotion_read']}",
                "",
            ]
        )

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(load_json(QUEUE_PACKET_PATH), load_json(QUEUE_ADOPTION_PATH))
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
