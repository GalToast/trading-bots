#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

CONTRACT_GAP_PATH = REPORTS / "max_profit_contract_gap_board.json"
OUTPUT_JSON_PATH = REPORTS / "max_profit_queue_contract_packet.json"
OUTPUT_MD_PATH = REPORTS / "max_profit_queue_contract_packet.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def proposal_status(row: dict[str, Any]) -> str:
    runtime_status = str(row.get("best_challenger_runtime_status") or "")
    if runtime_status in {"forward_proof_started", "already_running_monitor_only"}:
        return "proposal_ready"
    if runtime_status == "not_launched_yet":
        return "proposal_ready_for_launch_contract"
    return "proposal_needs_review"


def proposal_read(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "")
    runtime_status = str(row.get("best_challenger_runtime_status") or "")
    next_action_class = str(row.get("proposed_next_action_class") or "")
    if runtime_status == "forward_proof_started":
        return (
            f"`{symbol}` already has live proof motion, so the queue packet can be promoted immediately as "
            f"`{next_action_class}` without waiting for a fresh launch debate."
        )
    if runtime_status == "already_running_monitor_only":
        return (
            f"`{symbol}` already has a running challenger; the missing packet is for proof-quality or telemetry work, "
            f"not a new lane launch."
        )
    if runtime_status == "not_launched_yet":
        return (
            f"`{symbol}` still needs an explicit launch contract, but the queue packet shape is already clear enough "
            f"to promote as `{next_action_class}`."
        )
    return f"`{symbol}` still needs manual review before this proposed queue packet should be promoted."


def build_packet_row(rank: int, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_rank": rank,
        "symbol": str(row.get("symbol") or ""),
        "proposal_status": proposal_status(row),
        "task_id": str(row.get("proposed_queue_task_id") or ""),
        "title": str(row.get("proposed_queue_title") or ""),
        "lane": str(row.get("proposed_queue_lane") or ""),
        "next_action_class": str(row.get("proposed_next_action_class") or ""),
        "best_challenger_lane": str(row.get("best_challenger_lane") or ""),
        "best_challenger_family": str(row.get("best_challenger_family") or ""),
        "best_challenger_candidate_class": str(row.get("best_challenger_candidate_class") or ""),
        "best_challenger_runtime_status": str(row.get("best_challenger_runtime_status") or ""),
        "seat_verdict": str(row.get("seat_verdict") or ""),
        "seat_unblocker_action": str(row.get("seat_unblocker_action") or ""),
        "proposal_read": proposal_read(row),
    }


def build_payload(contract_gap_board: dict[str, Any]) -> dict[str, Any]:
    rows = [
        build_packet_row(idx + 1, dict(row))
        for idx, row in enumerate(list(contract_gap_board.get("rows") or []))
        if isinstance(row, dict)
    ]
    highest_ready_symbol = next((row["symbol"] for row in rows if row["proposal_status"].startswith("proposal_ready")), "")
    return {
        "generated_at": utc_now_iso(),
        "sources": [str(CONTRACT_GAP_PATH.relative_to(ROOT))],
        "summary": {
            "proposal_count": len(rows),
            "proposal_symbols": [row["symbol"] for row in rows],
            "highest_ready_symbol": highest_ready_symbol,
            "proposal_read": (
                f"Highest immediate queue-contract promotion candidate is `{highest_ready_symbol}`."
                if highest_ready_symbol
                else "No queue-contract proposals are currently ready."
            ),
        },
        "leadership_read": [
            (
                f"Highest immediate queue-contract promotion candidate is `{highest_ready_symbol}`."
                if highest_ready_symbol
                else "No queue-contract proposals are currently ready."
            ),
            "This packet is the structured handoff from passive contract-gap diagnosis to actual queue-row creation.",
            "Promote `proposal_ready` rows before inventing new lower-signal contract work.",
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It does not write into `adaptive_lab_queue`; it only packages queue-row proposals from the contract-gap backlog.",
            "Use this board when converting contract debt into actual queue tasks so task ids, titles, lanes, and action classes stay consistent.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Max Profit Queue Contract Packet",
        "",
        "This board packages the current contract-gap backlog into concrete proposed queue rows.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- proposal_symbols: `{summary.get('proposal_symbols', [])}`",
        f"- highest_ready_symbol: `{summary.get('highest_ready_symbol', '')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Proposal Table",
            "",
            "| Rank | Symbol | Status | Task Id | Lane | Next Action Class | Challenger Status |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['proposal_rank']}` | `{row['symbol']}` | `{row['proposal_status']}` | "
            f"`{row['task_id']}` | `{row['lane']}` | `{row['next_action_class']}` | "
            f"`{row['best_challenger_candidate_class']}` / `{row['best_challenger_runtime_status']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row['symbol']}",
                "",
                f"- proposal_rank: `{row['proposal_rank']}`",
                f"- proposal_status: `{row['proposal_status']}`",
                f"- task_id: `{row['task_id']}`",
                f"- title: `{row['title']}`",
                f"- lane: `{row['lane']}`",
                f"- next_action_class: `{row['next_action_class']}`",
                f"- seat_verdict: `{row['seat_verdict']}`",
                f"- seat_unblocker_action: `{row['seat_unblocker_action']}`",
                f"- best_challenger_lane: `{row['best_challenger_lane']}`",
                f"- best_challenger_family: `{row['best_challenger_family']}`",
                f"- best_challenger_candidate_class: `{row['best_challenger_candidate_class']}`",
                f"- best_challenger_runtime_status: `{row['best_challenger_runtime_status']}`",
                f"- proposal_read: {row['proposal_read']}",
                "",
            ]
        )

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(load_json(CONTRACT_GAP_PATH))
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
