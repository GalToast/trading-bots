#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TASK_STORE_PATH = ROOT / "war_room_tasks.json"

NEXT_ACTION_PATH = REPORTS / "max_profit_next_action_board.json"
QUEUE_PACKET_PATH = REPORTS / "max_profit_queue_contract_packet.json"
QUEUE_ADOPTION_PATH = REPORTS / "max_profit_queue_adoption_board.json"
QUEUE_PROMOTION_PATH = REPORTS / "max_profit_queue_promotion_board.json"
BTC_CONTROL_PATH = REPORTS / "btc_execution_ready_control_contract_board.json"
OUTPUT_JSON_PATH = REPORTS / "max_profit_taskboard_bridge.json"
OUTPUT_MD_PATH = REPORTS / "max_profit_taskboard_bridge.md"

EXECUTION_READY_DECISION_ID = 12
QUEUE_ADOPTION_DECISION_ID = 13
TRACKED_TASK_IDS = [82, 83, 84, 85, 86, 87, 88, 89]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def task_by_id(task_store: dict[str, Any], task_id: int) -> dict[str, Any]:
    for task in list(task_store.get("tasks") or []):
        if int(task.get("id") or 0) == task_id:
            return dict(task)
    raise KeyError(f"task not found: {task_id}")


def decision_by_id(task_store: dict[str, Any], decision_id: int) -> dict[str, Any]:
    for decision in list(task_store.get("decisions") or []):
        if int(decision.get("id") or 0) == decision_id:
            return dict(decision)
    raise KeyError(f"decision not found: {decision_id}")


def recommended_symbol(decision: dict[str, Any]) -> str:
    mapping = {
        "advance_gbpusd_first": "GBPUSD",
        "advance_usdjpy_first": "USDJPY",
        "adopt_usdcad_first": "USDCAD",
        "adopt_audusd_first": "AUDUSD",
        "adopt_xrpusd_first": "XRPUSD",
        "adopt_nzdusd_first": "NZDUSD",
    }
    return mapping.get(str(decision.get("recommended_option") or ""), "")


def preferred_queue_symbol(
    queue_packet_board: dict[str, Any],
    queue_adoption_board: dict[str, Any],
    queue_promotion_board: dict[str, Any],
) -> str:
    for candidate in [
        str(queue_promotion_board.get("summary", {}).get("highest_promotion_symbol") or ""),
        str(queue_adoption_board.get("summary", {}).get("highest_missing_symbol") or ""),
        str(queue_packet_board.get("summary", {}).get("highest_ready_symbol") or ""),
    ]:
        if candidate:
            return candidate
    return ""


def index_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("symbol") or ""): dict(row)
        for row in list(payload.get("rows") or [])
        if isinstance(row, dict)
    }


def build_execution_ready_row(task: dict[str, Any], decision: dict[str, Any], next_action: dict[str, Any]) -> dict[str, Any]:
    symbol = str(task.get("evidence", {}).get("symbol") or "")
    recommended = symbol == recommended_symbol(decision)
    bridge_status = (
        "recommended_default_waiting_decision" if recommended else "alternate_execution_ready_waiting_decision"
    )
    taskboard_read = (
        f"Decision `{decision.get('id')}` is still open, but its current default is `{decision.get('recommended_option')}`. "
        f"That keeps task `{task.get('id')}` as the first execution-ready seat seam to unblock once the room accepts or closes the decision."
        if recommended
        else f"Decision `{decision.get('id')}` currently recommends `{decision.get('recommended_option')}`, so task `{task.get('id')}` stays behind "
        f"`{recommended_symbol(decision)}` unless the room explicitly chooses the alternate or runs both in parallel."
    )
    return {
        "bridge_rank": 2 if recommended else 4,
        "task_id": int(task.get("id") or 0),
        "symbol": symbol,
        "task_group": "execution_ready_seat",
        "title": str(task.get("title") or ""),
        "task_status": str(task.get("status") or ""),
        "task_owner": str(task.get("owner") or ""),
        "blocking_decision_id": str(task.get("blocking_decision_id") or ""),
        "decision_status": str(decision.get("status") or ""),
        "decision_recommended_option": str(decision.get("recommended_option") or ""),
        "bridge_status": bridge_status,
        "seat_execution_gate_status": str(next_action.get("seat_execution_gate_status") or ""),
        "queue_task_id": str(next_action.get("queue_task_id") or ""),
        "next_action_class": str(next_action.get("next_action_class") or ""),
        "max_profit_posture": str(next_action.get("max_profit_posture") or ""),
        "taskboard_read": taskboard_read,
        "source_read": str(next_action.get("launch_read") or ""),
    }


def build_umbrella_row(
    task: dict[str, Any],
    decision: dict[str, Any],
    *,
    surface_symbols: list[str],
    surface_recommended_symbol: str,
    promotion_statuses: dict[str, str],
) -> dict[str, Any]:
    evidence = dict(task.get("evidence") or {})
    child_tasks = list(evidence.get("child_tasks") or [])
    decision_symbol = recommended_symbol(decision)
    symbols = list(surface_symbols or evidence.get("symbols") or [])
    highest_missing_symbol = surface_recommended_symbol or str(evidence.get("highest_missing_symbol") or decision_symbol)
    if not promotion_statuses:
        promotion_statuses = dict(evidence.get("promotion_statuses") or {})
    if decision_symbol == highest_missing_symbol:
        taskboard_read = (
            f"Task `{task.get('id')}` tracks `{symbols}` as one umbrella, and decision `{decision.get('id')}` still points first to `{highest_missing_symbol}`."
        )
    else:
        taskboard_read = (
            f"Task `{task.get('id')}` tracks the current missing queue rows `{symbols}`, but decision `{decision.get('id')}` still defaults to "
            f"`{decision.get('recommended_option')}` for `{decision_symbol}`. Treat `{highest_missing_symbol}` as the real first direct queue action until the decision store is refreshed."
        )
    return {
        "bridge_rank": 5,
        "task_id": int(task.get("id") or 0),
        "symbol": "QUEUE_BACKLOG",
        "task_group": "queue_contract_umbrella",
        "title": str(task.get("title") or ""),
        "task_status": str(task.get("status") or ""),
        "task_owner": str(task.get("owner") or ""),
        "blocking_decision_id": str(task.get("blocking_decision_id") or ""),
        "decision_status": str(decision.get("status") or ""),
        "decision_recommended_option": str(decision.get("recommended_option") or ""),
        "bridge_status": "umbrella_waiting_recommended_queue_pick",
        "seat_execution_gate_status": str(evidence.get("seat_execution_gate_status") or ""),
        "queue_task_id": ",".join(str(item) for item in child_tasks),
        "next_action_class": "sequence_child_queue_contracts",
        "max_profit_posture": "queue_contract_missing",
        "taskboard_read": taskboard_read,
        "source_read": f"Current promotion map is `{promotion_statuses}`.",
    }


def build_queue_contract_row(
    task: dict[str, Any],
    decision: dict[str, Any],
    surface_recommended_symbol: str,
    packet: dict[str, Any],
    adoption: dict[str, Any],
    promotion: dict[str, Any],
    next_action: dict[str, Any],
) -> dict[str, Any]:
    symbol = str(task.get("evidence", {}).get("symbol") or "")
    decision_symbol = recommended_symbol(decision)
    decision_recommended = symbol == decision_symbol
    surface_recommended = symbol == surface_recommended_symbol
    has_surface_contract = any([packet, adoption, promotion])
    proposal_rank = int(packet.get("proposal_rank") or 0)
    promotion_class = str(promotion.get("promotion_class") or "")
    if surface_recommended and decision_recommended:
        bridge_status = "recommended_queue_row_waiting_decision"
        bridge_rank = 3
        taskboard_read = (
            f"Decision `{decision.get('id')}` is still open, but its current default is `{decision.get('recommended_option')}` and the promotion board says "
            f"`{symbol}` is `{promotion_class}`. Unblock this row first if the room keeps the default."
        )
    elif surface_recommended:
        bridge_status = "surface_recommended_queue_row_decision_stale"
        bridge_rank = 3
        taskboard_read = (
            f"Current passive queue truth now points to `{symbol}`, but decision `{decision.get('id')}` still defaults to "
            f"`{decision.get('recommended_option')}` for `{decision_symbol}`. Sync the decision/taskboard layer before the room keeps following stale queue order."
        )
    elif not has_surface_contract and decision_recommended:
        bridge_status = "already_adopted_queue_row_decision_stale"
        bridge_rank = 6
        taskboard_read = (
            f"Decision `{decision.get('id')}` still defaults to `{decision.get('recommended_option')}`, but `{symbol}` is no longer in the current missing queue packet. "
            "Treat this row as taskboard-sync debt because the adaptive queue already carries the symbol contract."
        )
    elif not has_surface_contract:
        bridge_status = "queue_row_not_in_current_missing_packet"
        bridge_rank = 9
        taskboard_read = (
            f"`{symbol}` is not present in the current missing queue packet/adoption/promotion surfaces, so this taskboard row is no longer part of the active missing-contract queue."
        )
    else:
        bridge_status = "lower_priority_queue_row_waiting_decision"
        bridge_rank = 5 + proposal_rank
        taskboard_read = (
            f"Current passive queue truth ranks `{surface_recommended_symbol}` ahead of `{symbol}`, so this row stays behind the current top promotion seam even though its packet and promotion contract are already defined."
        )
    seat_execution_gate_status = str(
        next_action.get("seat_execution_gate_status")
        or task.get("evidence", {}).get("seat_execution_gate_status")
        or ""
    )
    queue_task_id = str(packet.get("task_id") or next_action.get("queue_task_id") or "")
    next_action_class = str(packet.get("next_action_class") or next_action.get("next_action_class") or "")
    max_profit_posture = str(next_action.get("max_profit_posture") or "queue_contract_missing")
    return {
        "bridge_rank": bridge_rank,
        "task_id": int(task.get("id") or 0),
        "symbol": symbol,
        "task_group": "queue_contract",
        "title": str(task.get("title") or ""),
        "task_status": str(task.get("status") or ""),
        "task_owner": str(task.get("owner") or ""),
        "blocking_decision_id": str(task.get("blocking_decision_id") or ""),
        "decision_status": str(decision.get("status") or ""),
        "decision_recommended_option": str(decision.get("recommended_option") or ""),
        "bridge_status": bridge_status,
        "seat_execution_gate_status": seat_execution_gate_status,
        "queue_task_id": queue_task_id,
        "next_action_class": next_action_class,
        "max_profit_posture": max_profit_posture,
        "proposal_status": str(packet.get("proposal_status") or ""),
        "queue_adoption_status": str(adoption.get("queue_adoption_status") or ""),
        "promotion_class": promotion_class,
        "taskboard_read": taskboard_read,
        "source_read": " ".join(
            piece
            for piece in [
                str(packet.get("proposal_read") or ""),
                str(adoption.get("adoption_read") or ""),
                str(promotion.get("promotion_read") or ""),
                str(next_action.get("launch_read") or ""),
            ]
            if piece
        ),
    }


def build_btc_row(task: dict[str, Any], btc_control: dict[str, Any]) -> dict[str, Any]:
    summary = dict(btc_control.get("summary") or {})
    control_branch = dict(btc_control.get("control_branch") or {})
    return {
        "bridge_rank": 1,
        "task_id": int(task.get("id") or 0),
        "symbol": str(summary.get("symbol") or "BTCUSD"),
        "task_group": "preparatory_control_contract",
        "title": str(task.get("title") or ""),
        "task_status": str(task.get("status") or ""),
        "task_owner": str(task.get("owner") or ""),
        "blocking_decision_id": str(task.get("blocking_decision_id") or ""),
        "decision_status": "",
        "decision_recommended_option": "",
        "bridge_status": "active_preparatory_in_progress",
        "seat_execution_gate_status": str(summary.get("seat_execution_gate_status") or ""),
        "queue_task_id": str(summary.get("queue_task_id") or ""),
        "next_action_class": str(control_branch.get("next_action_class") or ""),
        "max_profit_posture": str(summary.get("max_profit_posture") or ""),
        "taskboard_read": (
            f"Task `{task.get('id')}` is already the active BTC preparatory seam. Keep it in progress, but do not confuse that work with the "
            "execution-ready `launch_now` seat seams."
        ),
        "source_read": str(summary.get("contract_read") or btc_control.get("inference_read") or ""),
    }


def build_payload(
    *,
    task_store: dict[str, Any],
    next_action_board: dict[str, Any],
    queue_packet_board: dict[str, Any],
    queue_adoption_board: dict[str, Any],
    queue_promotion_board: dict[str, Any],
    btc_control_board: dict[str, Any],
) -> dict[str, Any]:
    execution_decision = decision_by_id(task_store, EXECUTION_READY_DECISION_ID)
    queue_decision = decision_by_id(task_store, QUEUE_ADOPTION_DECISION_ID)
    next_action_rows = index_rows(next_action_board)
    packet_rows = index_rows(queue_packet_board)
    adoption_rows = index_rows(queue_adoption_board)
    promotion_rows = index_rows(queue_promotion_board)
    tracked_tasks = {task_id: task_by_id(task_store, task_id) for task_id in TRACKED_TASK_IDS}
    surface_recommended_queue = preferred_queue_symbol(
        queue_packet_board=queue_packet_board,
        queue_adoption_board=queue_adoption_board,
        queue_promotion_board=queue_promotion_board,
    )

    rows = [
        build_btc_row(tracked_tasks[89], btc_control_board),
        build_execution_ready_row(tracked_tasks[82], execution_decision, next_action_rows.get("GBPUSD", {})),
        build_execution_ready_row(tracked_tasks[83], execution_decision, next_action_rows.get("USDJPY", {})),
        build_umbrella_row(
            tracked_tasks[84],
            queue_decision,
            surface_symbols=list(queue_packet_board.get("summary", {}).get("proposal_symbols") or []),
            surface_recommended_symbol=surface_recommended_queue,
            promotion_statuses={
                symbol: str(row.get("promotion_class") or "")
                for symbol, row in promotion_rows.items()
                if str(row.get("promotion_class") or "")
            },
        ),
    ]
    for task_id in [85, 86, 87, 88]:
        queue_task = tracked_tasks[task_id]
        queue_symbol = str(queue_task.get("evidence", {}).get("symbol") or "")
        rows.append(
            build_queue_contract_row(
                queue_task,
                queue_decision,
                surface_recommended_queue,
                packet_rows.get(queue_symbol, {}),
                adoption_rows.get(queue_symbol, {}),
                promotion_rows.get(queue_symbol, {}),
                next_action_rows.get(queue_symbol, {}),
            )
        )
    rows.sort(key=lambda row: (int(row.get("bridge_rank") or 0), int(row.get("task_id") or 0)))

    recommended_execution = recommended_symbol(execution_decision)
    decision_queue_symbol = recommended_symbol(queue_decision)
    recommended_queue = surface_recommended_queue or decision_queue_symbol
    recommended_queue_task_id = next(
        (
            int(task_id)
            for task_id in [85, 86, 87, 88]
            if str(tracked_tasks[task_id].get("evidence", {}).get("symbol") or "") == recommended_queue
        ),
        85,
    )
    queue_alignment_status = (
        "aligned_with_surface" if decision_queue_symbol == recommended_queue else "decision_stale_vs_surface"
    )
    if queue_alignment_status == "aligned_with_surface":
        bridge_read = (
            f"Keep BTC preparatory task `89` active, but if the room accepts current decision defaults, unblock `82` for `{recommended_execution}` "
            f"and `{recommended_queue_task_id}` for `{recommended_queue}` before their alternates."
        )
        queue_leadership_read = (
            f"Decision `{QUEUE_ADOPTION_DECISION_ID}` is still open, yet its current default is `{queue_decision.get('recommended_option')}`; "
            f"that keeps task `{recommended_queue_task_id}` ahead of the remaining queue-contract rows and keeps task `84` as umbrella tracking rather than the first direct action."
        )
    else:
        bridge_read = (
            f"Keep BTC preparatory task `89` active, unblock `82` for `{recommended_execution}`, and sync decision `{QUEUE_ADOPTION_DECISION_ID}`: "
            f"current passive queue truth now points to task `{recommended_queue_task_id}` / `{recommended_queue}` while the taskboard default still says `{decision_queue_symbol}`."
        )
        queue_leadership_read = (
            f"Decision `{QUEUE_ADOPTION_DECISION_ID}` is still open and still defaults to `{queue_decision.get('recommended_option')}`, but the current packet/adoption/promotion stack has already moved to `{recommended_queue}`. "
            f"Treat task `{recommended_queue_task_id}` as the real next missing queue row and task `85` as stale taskboard debt until the decision store is refreshed."
        )
    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(TASK_STORE_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
            str(QUEUE_PACKET_PATH.relative_to(ROOT)),
            str(QUEUE_ADOPTION_PATH.relative_to(ROOT)),
            str(QUEUE_PROMOTION_PATH.relative_to(ROOT)),
            str(BTC_CONTROL_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "tracked_task_count": len(rows),
            "decision_blocked_task_count": sum(1 for row in rows if row["blocking_decision_id"]),
            "active_preparatory_task_id": 89,
            "recommended_execution_ready_task_id": 82,
            "recommended_execution_ready_symbol": recommended_execution,
            "recommended_queue_task_id": recommended_queue_task_id,
            "recommended_queue_symbol": recommended_queue,
            "queue_decision_alignment_status": queue_alignment_status,
            "decision_queue_symbol": decision_queue_symbol,
            "bridge_read": bridge_read,
        },
        "leadership_read": [
            (
                f"Task `89` remains the active BTC preparatory control seam, but it should not hide the fact that decision `{EXECUTION_READY_DECISION_ID}` "
                f"currently points the first execution-ready seat move to `{recommended_execution}`."
            ),
            (
                f"Decision `{EXECUTION_READY_DECISION_ID}` is still open, yet its current default is `{execution_decision.get('recommended_option')}`; "
                f"that keeps task `82` ahead of task `83` unless the room explicitly chooses parallel execution or flips the decision."
            ),
            queue_leadership_read,
            "This bridge is taskboard-facing only: it turns current passive max-profit authority into one room-readable execution order without mutating queue or runtime truth.",
        ],
        "decisions": [
            {
                "decision_id": int(execution_decision.get("id") or 0),
                "status": str(execution_decision.get("status") or ""),
                "recommended_option": str(execution_decision.get("recommended_option") or ""),
                "recommended_symbol": recommended_execution,
                "related_task_ids": list(execution_decision.get("related_task_ids") or []),
            },
            {
                "decision_id": int(queue_decision.get("id") or 0),
                "status": str(queue_decision.get("status") or ""),
                "recommended_option": str(queue_decision.get("recommended_option") or ""),
                "recommended_symbol": decision_queue_symbol,
                "surface_recommended_symbol": recommended_queue,
                "surface_alignment_status": queue_alignment_status,
                "related_task_ids": list(queue_decision.get("related_task_ids") or []),
            },
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It reads the checked-in max-profit surfaces plus `war_room_tasks.json` and turns them into one taskboard execution order.",
            "Use it when the room needs to reconcile passive seat and queue truth with actual switchboard ownership, blocking decisions, and current working defaults.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Max Profit Taskboard Bridge",
        "",
        "This board fuses passive max-profit authority with the current switchboard taskboard state for tasks `82-89`.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- tracked_task_count: `{summary.get('tracked_task_count', 0)}`",
        f"- decision_blocked_task_count: `{summary.get('decision_blocked_task_count', 0)}`",
        f"- active_preparatory_task_id: `{summary.get('active_preparatory_task_id', '')}`",
        f"- recommended_execution_ready_task_id: `{summary.get('recommended_execution_ready_task_id', '')}`",
        f"- recommended_queue_task_id: `{summary.get('recommended_queue_task_id', '')}`",
        f"- queue_decision_alignment_status: `{summary.get('queue_decision_alignment_status', '')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Decision Defaults", ""])
    for decision in list(payload.get("decisions") or []):
        lines.append(
            f"- decision `{decision['decision_id']}`: status=`{decision['status']}`, recommended_option=`{decision['recommended_option']}`, "
            f"recommended_symbol=`{decision['recommended_symbol']}`, "
            f"surface_recommended_symbol=`{decision.get('surface_recommended_symbol', decision['recommended_symbol'])}`, "
            f"surface_alignment_status=`{decision.get('surface_alignment_status', '')}`, related_task_ids=`{decision['related_task_ids']}`"
        )

    lines.extend(
        [
            "",
            "## Ordered Taskboard View",
            "",
            "| Rank | Task | Symbol | Group | Bridge Status | Task Status | Owner | Blocking Decision |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['bridge_rank']}` | `{row['task_id']}` | `{row['symbol']}` | `{row['task_group']}` | "
            f"`{row['bridge_status']}` | `{row['task_status']}` | `{row['task_owner']}` | `{row['blocking_decision_id']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### Task {row['task_id']} - {row['title']}",
                "",
                f"- symbol: `{row['symbol']}`",
                f"- task_group: `{row['task_group']}`",
                f"- task_status: `{row['task_status']}`",
                f"- task_owner: `{row['task_owner']}`",
                f"- blocking_decision_id: `{row['blocking_decision_id']}`",
                f"- decision_status: `{row['decision_status']}`",
                f"- decision_recommended_option: `{row['decision_recommended_option']}`",
                f"- bridge_status: `{row['bridge_status']}`",
                f"- seat_execution_gate_status: `{row['seat_execution_gate_status']}`",
                f"- queue_task_id: `{row['queue_task_id']}`",
                f"- next_action_class: `{row['next_action_class']}`",
                f"- max_profit_posture: `{row['max_profit_posture']}`",
            ]
        )
        if "proposal_status" in row:
            lines.append(f"- proposal_status: `{row['proposal_status']}`")
        if "queue_adoption_status" in row:
            lines.append(f"- queue_adoption_status: `{row['queue_adoption_status']}`")
        if "promotion_class" in row:
            lines.append(f"- promotion_class: `{row['promotion_class']}`")
        lines.extend(
            [
                f"- taskboard_read: {row['taskboard_read']}",
                f"- source_read: {row['source_read']}",
                "",
            ]
        )

    lines.extend(["## Notes", ""])
    for item in list(payload.get("notes") or []):
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        task_store=load_json(TASK_STORE_PATH),
        next_action_board=load_json(NEXT_ACTION_PATH),
        queue_packet_board=load_json(QUEUE_PACKET_PATH),
        queue_adoption_board=load_json(QUEUE_ADOPTION_PATH),
        queue_promotion_board=load_json(QUEUE_PROMOTION_PATH),
        btc_control_board=load_json(BTC_CONTROL_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
