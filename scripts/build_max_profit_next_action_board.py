#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

SEAT_BOARD_PATH = REPORTS / "per_symbol_live_seat_board.json"
QUEUE_PATH = REPORTS / "adaptive_lab_queue.json"
GUARDED_CONTRACT_PATH = REPORTS / "guarded_toxic_flow_contract_board.json"
OUTPUT_JSON_PATH = REPORTS / "max_profit_next_action_board.json"
OUTPUT_MD_PATH = REPORTS / "max_profit_next_action_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def posture_from_row(row: dict[str, Any]) -> tuple[str, str]:
    execution_gate = str(row.get("seat_execution_gate_status") or "")
    execution_gate_read = str(row.get("seat_execution_gate_read") or "")
    actionability = str(row.get("seat_actionability_status") or "")
    contract_gap = str(row.get("seat_contract_gap_status") or "")
    queue_alignment = str(row.get("seat_queue_alignment_status") or "")

    if execution_gate == "ready_for_seat_execution":
        return (
            "launch_now",
            execution_gate_read or "Seat execution gate says this symbol is ready for execution.",
        )
    if execution_gate == "queue_backed_preparatory_only":
        return (
            "preparatory_only",
            execution_gate_read or "Seat execution gate says this symbol is still preparatory.",
        )
    if execution_gate == "actionable_but_missing_queue_contract":
        return (
            "queue_contract_missing",
            execution_gate_read or "Seat execution gate says this symbol still needs a queue contract.",
        )
    if execution_gate == "blocked_by_queue_contract":
        return (
            "blocked",
            execution_gate_read or "Seat execution gate says the queue-backed contract is still blocked.",
        )

    if actionability == "queue_ready_actionable":
        return (
            "launch_now",
            "Queue-backed and already action-ready on the live-seat surface.",
        )
    if actionability == "queue_ready_preparatory_only":
        return (
            "preparatory_only",
            "Queue-backed, but still an earlier-stage control or doctrine seam than an immediate executable seat move.",
        )
    if contract_gap == "actionable_missing_queue_contract":
        return (
            "queue_contract_missing",
            "Actionable on the seat surface, but still missing a queue-backed max-profit contract.",
        )
    if actionability == "blocked_by_queue_contract" or queue_alignment == "queue_blocked_aligned":
        return (
            "blocked",
            "The room already has a queue contract here, but it is blocked rather than executable.",
        )
    return (
        "observe_only",
        "Not currently an executable or queue-backed max-profit action.",
    )


def posture_rank(posture: str) -> int:
    ranks = {
        "launch_now": 0,
        "preparatory_only": 1,
        "queue_contract_missing": 2,
        "blocked": 3,
        "observe_only": 4,
    }
    return ranks.get(posture, 9)


def build_row(
    *,
    seat_row: dict[str, Any],
    task_by_id: dict[str, dict[str, Any]],
    guarded_by_symbol: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    symbol = str(seat_row.get("symbol") or "")
    queue_task_id = str(
        seat_row.get("seat_unblocker_queue_task_id")
        or seat_row.get("queue_task_id")
        or ""
    )
    task = dict(task_by_id.get(queue_task_id) or {})
    guarded = dict(guarded_by_symbol.get(symbol) or {})
    posture, posture_read = posture_from_row(seat_row)
    queue_priority = parse_int(task.get("priority"), default=10**6) if task else 10**6
    seat_priority = parse_int(seat_row.get("seat_unblocker_priority_rank"), default=10**6)

    guarded_contract = dict(guarded.get("contract") or {})
    guarded_contract_active = bool(guarded)
    overlay_contract_status = str(seat_row.get("seat_overlay_contract_status") or "")
    overlay_contract_read = str(seat_row.get("seat_overlay_contract_read") or "")
    overlay_launch_bridge_status = str(seat_row.get("seat_overlay_launch_bridge_status") or "")
    overlay_launch_bridge_read = str(seat_row.get("seat_overlay_launch_bridge_read") or "")
    execution_gate_status = str(seat_row.get("seat_execution_gate_status") or "")
    execution_gate_read = str(seat_row.get("seat_execution_gate_read") or "")
    guarded_overlay = (
        f"Guarded-toxic-flow contract says `{guarded_contract.get('verdict', '')}`: {guarded_contract.get('read', '')}"
        if guarded_contract_active
        else ""
    )

    launch_read = ""
    if posture == "launch_now":
        launch_read = (
            f"`{symbol}` is the highest-signal executable seam when queue-backed actionability, "
            f"task readiness, and current seat posture all agree."
        )
    elif posture == "preparatory_only":
        launch_read = (
            f"`{symbol}` is still important, but the room should treat it as preparatory control work "
            f"before an actual max-profit launch claim."
        )
        if execution_gate_status:
            launch_read += f" Seat execution gate currently reads `{execution_gate_status}`."
        if overlay_launch_bridge_status and overlay_launch_bridge_status != "no_overlay_launch_bridge_needed":
            launch_read += f" Overlay bridge remains `{overlay_launch_bridge_status}`."
    elif posture == "queue_contract_missing":
        launch_read = (
            f"`{symbol}` looks actionable on the seat surface, but the room still lacks a queue-backed "
            f"planning contract for it."
        )
    elif posture == "blocked":
        launch_read = f"`{symbol}` already has a queue contract, but it is blocked rather than executable."
    else:
        launch_read = f"`{symbol}` is not currently an executable max-profit next action."

    return {
        "symbol": symbol,
        "seat_verdict": str(seat_row.get("seat_verdict") or ""),
        "seat_unblocker_action": str(seat_row.get("seat_unblocker_action") or ""),
        "seat_actionability_status": str(seat_row.get("seat_actionability_status") or ""),
        "seat_contract_gap_status": str(seat_row.get("seat_contract_gap_status") or ""),
        "seat_queue_alignment_status": str(seat_row.get("seat_queue_alignment_status") or ""),
        "seat_execution_gate_status": execution_gate_status,
        "seat_execution_gate_read": execution_gate_read,
        "seat_overlay_contract_status": overlay_contract_status,
        "seat_overlay_contract_read": overlay_contract_read,
        "seat_overlay_launch_bridge_status": overlay_launch_bridge_status,
        "seat_overlay_launch_bridge_read": overlay_launch_bridge_read,
        "queue_task_id": queue_task_id,
        "queue_task_title": str(
            seat_row.get("seat_unblocker_queue_task_title")
            or task.get("title")
            or ""
        ),
        "queue_task_status": str(
            seat_row.get("seat_unblocker_queue_task_status")
            or task.get("status")
            or seat_row.get("queue_task_status")
            or ""
        ),
        "queue_task_priority": None if queue_priority == 10**6 else queue_priority,
        "queue_lane": str(
            seat_row.get("seat_unblocker_queue_task_lane")
            or task.get("lane")
            or ""
        ),
        "profit_mode": str(task.get("profit_mode") or ""),
        "next_action_class": str(
            seat_row.get("seat_unblocker_queue_task_next_action_class")
            or task.get("next_action_class")
            or ""
        ),
        "max_profit_posture": posture,
        "max_profit_posture_read": posture_read,
        "guarded_contract_active": guarded_contract_active,
        "guarded_contract_verdict": str(guarded_contract.get("verdict") or ""),
        "guarded_contract_read": guarded_overlay,
        "launch_read": launch_read,
        "sort_key": [posture_rank(posture), queue_priority, seat_priority, symbol],
    }


def build_payload(
    *,
    seat_board: dict[str, Any],
    adaptive_queue: dict[str, Any],
    guarded_contract: dict[str, Any],
) -> dict[str, Any]:
    seat_rows = [dict(row) for row in list(seat_board.get("rows") or []) if isinstance(row, dict)]
    task_by_id = {
        str(row.get("task_id") or ""): dict(row)
        for row in list(adaptive_queue.get("tasks") or [])
        if isinstance(row, dict)
    }
    guarded_by_symbol = {
        str(row.get("symbol") or ""): dict(row)
        for row in list(guarded_contract.get("rows") or [])
        if isinstance(row, dict)
    }

    rows = [
        build_row(
            seat_row=seat_row,
            task_by_id=task_by_id,
            guarded_by_symbol=guarded_by_symbol,
        )
        for seat_row in seat_rows
    ]
    rows.sort(key=lambda row: row["sort_key"])
    for row in rows:
        row.pop("sort_key", None)

    launch_now_symbols = [row["symbol"] for row in rows if row["max_profit_posture"] == "launch_now"]
    preparatory_symbols = [row["symbol"] for row in rows if row["max_profit_posture"] == "preparatory_only"]
    queue_contract_missing_symbols = [row["symbol"] for row in rows if row["max_profit_posture"] == "queue_contract_missing"]
    blocked_symbols = [row["symbol"] for row in rows if row["max_profit_posture"] == "blocked"]

    highest_launch_now_symbol = launch_now_symbols[0] if launch_now_symbols else ""
    highest_preparatory_symbol = preparatory_symbols[0] if preparatory_symbols else ""
    execution_ready_symbols = [
        row["symbol"]
        for row in rows
        if row.get("seat_execution_gate_status") == "ready_for_seat_execution"
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(SEAT_BOARD_PATH.relative_to(ROOT)),
            str(QUEUE_PATH.relative_to(ROOT)),
            str(GUARDED_CONTRACT_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "launch_now_symbols": launch_now_symbols,
            "preparatory_symbols": preparatory_symbols,
            "queue_contract_missing_symbols": queue_contract_missing_symbols,
            "blocked_symbols": blocked_symbols,
            "highest_launch_now_symbol": highest_launch_now_symbol,
            "highest_preparatory_symbol": highest_preparatory_symbol,
            "execution_ready_symbols": execution_ready_symbols,
            "contract_read": (
                f"Highest current executable max-profit symbol is `{highest_launch_now_symbol}`."
                if highest_launch_now_symbol
                else "No symbol is currently launch-now ready."
            ),
        },
        "leadership_read": [
            (
                f"Highest executable queue-backed max-profit symbol is `{highest_launch_now_symbol}`."
                if highest_launch_now_symbol
                else "No symbol is currently both queue-backed and launch-now executable."
            ),
            (
                f"Top preparatory seam is `{highest_preparatory_symbol}`; queue priority exists, but doctrine still says control or path-safety work comes first."
                if highest_preparatory_symbol
                else "No preparatory-only seam is currently ahead of execution."
            ),
            f"Seat execution-ready symbols are `{execution_ready_symbols}`.",
            f"Actionable but still missing queue contracts are `{queue_contract_missing_symbols}`.",
            f"Blocked queue-backed symbols are `{blocked_symbols}`.",
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It does not override the seat board or adaptive queue; it only merges them into one max-profit next-action read.",
            "Use `launch_now` rows for immediate execution attention, `preparatory_only` rows for control/doctrine-first work, and `queue_contract_missing` rows for contract-formation debt.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Max Profit Next Action Board",
        "",
        "This board merges seat actionability, adaptive queue posture, and guarded-toxic-flow doctrine into one executable max-profit next-action read.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- launch_now_symbols: `{summary.get('launch_now_symbols', [])}`",
        f"- preparatory_symbols: `{summary.get('preparatory_symbols', [])}`",
        f"- queue_contract_missing_symbols: `{summary.get('queue_contract_missing_symbols', [])}`",
        f"- blocked_symbols: `{summary.get('blocked_symbols', [])}`",
        f"- execution_ready_symbols: `{summary.get('execution_ready_symbols', [])}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Priority Table",
            "",
            "| Symbol | Posture | Execution Gate | Queue Task | Queue Status | Seat Actionability | Contract Gap | Profit Mode |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['symbol']}` | `{row['max_profit_posture']}` | `{row['seat_execution_gate_status'] or '-'}` | `{row['queue_task_id'] or '-'}` | "
            f"`{row['queue_task_status'] or '-'}` | `{row['seat_actionability_status']}` | "
            f"`{row['seat_contract_gap_status']}` | `{row['profit_mode'] or '-'}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row['symbol']}",
                "",
                f"- max_profit_posture: `{row['max_profit_posture']}`",
                f"- max_profit_posture_read: {row['max_profit_posture_read']}",
                f"- seat_verdict: `{row['seat_verdict']}`",
                f"- seat_unblocker_action: `{row['seat_unblocker_action']}`",
                f"- seat_actionability_status: `{row['seat_actionability_status']}`",
                f"- seat_contract_gap_status: `{row['seat_contract_gap_status']}`",
                f"- seat_queue_alignment_status: `{row['seat_queue_alignment_status']}`",
                f"- seat_execution_gate_status: `{row['seat_execution_gate_status']}`",
                f"- seat_execution_gate_read: {row['seat_execution_gate_read']}",
                f"- seat_overlay_contract_status: `{row['seat_overlay_contract_status']}`",
                f"- seat_overlay_contract_read: {row['seat_overlay_contract_read']}",
                f"- seat_overlay_launch_bridge_status: `{row['seat_overlay_launch_bridge_status']}`",
                f"- seat_overlay_launch_bridge_read: {row['seat_overlay_launch_bridge_read']}",
                f"- queue_task_id: `{row['queue_task_id'] or ''}`",
                f"- queue_task_title: `{row['queue_task_title'] or ''}`",
                f"- queue_task_status: `{row['queue_task_status'] or ''}`",
                f"- queue_task_priority: `{row['queue_task_priority']}`",
                f"- queue_lane: `{row['queue_lane'] or ''}`",
                f"- profit_mode: `{row['profit_mode'] or ''}`",
                f"- next_action_class: `{row['next_action_class'] or ''}`",
                f"- launch_read: {row['launch_read']}",
            ]
        )
        if row.get("guarded_contract_active"):
            lines.append(f"- guarded_contract_verdict: `{row['guarded_contract_verdict']}`")
            lines.append(f"- guarded_contract_read: {row['guarded_contract_read']}")
        lines.append("")

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        seat_board=load_json(SEAT_BOARD_PATH),
        adaptive_queue=load_json(QUEUE_PATH),
        guarded_contract=load_json(GUARDED_CONTRACT_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
