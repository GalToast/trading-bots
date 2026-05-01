#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

SEAT_PATH = REPORTS / "per_symbol_live_seat_board.json"
QUEUE_PATH = REPORTS / "adaptive_lab_queue.json"
GBP_FIRST_PATH_PATH = REPORTS / "gbpusd_adaptive_first_path_board.json"
OVERNIGHT_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"
SHARED_SCORE_PATH = REPORTS / "adaptive_shared_score_board.json"
ACCEPTANCE_PATH = REPORTS / "adaptive_harness_acceptance_verdict_board.json"

OUTPUT_JSON_PATH = REPORTS / "execution_ready_seat_priority_board.json"
OUTPUT_MD_PATH = REPORTS / "execution_ready_seat_priority_board.md"

DECISION_ID = 12
SYMBOLS = ("GBPUSD", "USDJPY")
CANDIDATE_ID_BY_SYMBOL = {
    "GBPUSD": "gbpusd_adaptive_comparison_packet",
    "USDJPY": "usdjpy_bounded_forward_proof",
}
RECOMMENDED_OPTION_BY_SYMBOL = {
    "GBPUSD": "advance_gbpusd_first",
    "USDJPY": "advance_usdjpy_first",
}
ACCEPTANCE_RANK = {
    "promotion_ready": 0,
    "shadow_ready": 1,
    "ready_for_shadow_discussion": 2,
    "shadow_collecting": 3,
    "research_only": 4,
    "rejected": 5,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def relative_path_text(path: Path) -> str:
    return str(path.relative_to(ROOT))


def find_row(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    needle = str(value or "").upper()
    for row in rows:
        if str(row.get(key) or "").upper() == needle:
            return dict(row)
    return {}


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def summarize_acceptance(candidate: dict[str, Any]) -> dict[str, Any]:
    warning_checks: list[str] = []
    warning_reads: list[str] = []
    for check in list(candidate.get("checks") or []):
        if str(check.get("status") or "") == "warn":
            warning_checks.append(str(check.get("check_id") or ""))
            warning_reads.append(str(check.get("read") or ""))
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "verdict": str(candidate.get("verdict") or ""),
        "candidate_read": str(candidate.get("candidate_read") or ""),
        "queue_status": str(candidate.get("queue_status") or ""),
        "warning_checks": warning_checks,
        "warning_reads": warning_reads,
    }


def seat_case_type(seat_row: dict[str, Any]) -> tuple[str, str]:
    incumbent_lane = str(seat_row.get("current_live_holder_lane") or "")
    if incumbent_lane:
        return (
            "incumbent_comparison_seam",
            "A live incumbent already exists on this symbol, so advancing it buys an honest incumbent-versus-adaptive comparison instead of first-seat construction.",
        )
    return (
        "first_seat_construction_seam",
        "No live incumbent exists yet, so advancing this symbol is still first-seat construction and forward proof rather than displacement comparison.",
    )


def runtime_contract_from_gbp(board_payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(board_payload.get("summary") or {})
    overnight = dict(board_payload.get("overnight_runtime") or {})
    return {
        "source": relative_path_text(GBP_FIRST_PATH_PATH),
        "source_status": str(summary.get("runtime_truth_source_status") or overnight.get("source_status") or ""),
        "lane_name": str(summary.get("adaptive_lane") or ""),
        "proof_contract_status": str(summary.get("proof_gate_status") or ""),
        "action_status": str(summary.get("overnight_action_status") or overnight.get("action_status") or ""),
        "first_path_verdict": str(summary.get("first_path_verdict") or overnight.get("first_path_verdict") or ""),
        "proof_read": "GBP is execution-ready, but its dedicated comparison packet is still waiting for first lane-local proof.",
    }


def runtime_contract_from_overnight(row: dict[str, Any]) -> dict[str, Any]:
    action_status = str(row.get("action_status") or "")
    first_path_verdict = str(row.get("first_path_verdict") or "")
    proof_contract_status = action_status
    proof_read = str(row.get("action_read") or row.get("why") or "")
    if action_status == "launch_now_manual_packet" and not first_path_verdict:
        proof_contract_status = "launch_packet_ready_waiting_runtime"
        proof_read = (
            "USDJPY has a concrete relaunch packet and is execution-ready on paper, but it still needs fresh bounded runtime proof before it can graduate beyond first-seat construction."
        )
    return {
        "source": relative_path_text(OVERNIGHT_PATH),
        "source_status": "overnight_packet_row",
        "lane_name": str(row.get("lane_name") or ""),
        "proof_contract_status": proof_contract_status,
        "action_status": action_status,
        "first_path_verdict": first_path_verdict,
        "proof_read": proof_read,
    }


def recommendation_sort_key(row: dict[str, Any]) -> list[Any]:
    case_type = str(row.get("seat_case_type") or "")
    case_rank = 0 if case_type == "incumbent_comparison_seam" else 1
    acceptance_verdict = str(row.get("acceptance_verdict") or "")
    acceptance_rank = ACCEPTANCE_RANK.get(acceptance_verdict, 9)
    queue_priority = row.get("queue_priority")
    queue_priority_rank = parse_int(queue_priority, default=10**6) if queue_priority is not None else 10**6
    return [
        0 if str(row.get("seat_execution_gate_status") or "") == "ready_for_seat_execution" else 1,
        case_rank,
        acceptance_rank,
        queue_priority_rank,
        str(row.get("symbol") or ""),
    ]


def build_row(
    *,
    symbol: str,
    seat_board: dict[str, Any],
    adaptive_queue: dict[str, Any],
    gbp_first_path: dict[str, Any],
    overnight_board: dict[str, Any],
    shared_score: dict[str, Any],
    acceptance_board: dict[str, Any],
) -> dict[str, Any]:
    seat_row = find_row(list(seat_board.get("rows") or []), "symbol", symbol)
    queue_task_id = str(
        seat_row.get("seat_unblocker_queue_task_id")
        or CANDIDATE_ID_BY_SYMBOL.get(symbol, "")
    )
    queue_row = find_row(list(adaptive_queue.get("tasks") or []), "task_id", queue_task_id)
    shared_row = find_row(list(shared_score.get("rows") or []), "symbol", symbol)
    acceptance_row = find_row(list(acceptance_board.get("candidates") or []), "candidate_id", queue_task_id)
    acceptance = summarize_acceptance(acceptance_row)
    seat_case, seat_case_read = seat_case_type(seat_row)

    if symbol == "GBPUSD":
        runtime_contract = runtime_contract_from_gbp(gbp_first_path)
    else:
        overnight_row = find_row(list(overnight_board.get("rows") or []), "packet_id", queue_task_id)
        runtime_contract = runtime_contract_from_overnight(overnight_row)

    if symbol == "GBPUSD":
        recommendation_read = (
            "Recommend GBPUSD first because it is already an execution-ready incumbent-comparison seam on a defended live seat, the adaptive branch is `shadow_ready`, and the queue already ranks it ahead of USDJPY."
        )
    else:
        recommendation_read = (
            "Keep USDJPY second unless the room explicitly chooses parallel execution: it is execution-ready for bounded forward proof, but it is still a `research_only` first-seat construction seam with no live incumbent to compare against."
        )

    shared_adaptive = dict(shared_row.get("adaptive") or {})
    return {
        "symbol": symbol,
        "seat_verdict": str(seat_row.get("seat_verdict") or ""),
        "seat_case_type": seat_case,
        "seat_case_read": seat_case_read,
        "current_live_holder_lane": str(seat_row.get("current_live_holder_lane") or ""),
        "seat_conflict": bool(seat_row.get("seat_conflict")),
        "seat_execution_gate_status": str(seat_row.get("seat_execution_gate_status") or ""),
        "seat_execution_gate_read": str(seat_row.get("seat_execution_gate_read") or ""),
        "seat_unblocker_action": str(seat_row.get("seat_unblocker_action") or ""),
        "seat_unblocker_read": str(seat_row.get("seat_unblocker_read") or ""),
        "queue_task_id": queue_task_id,
        "queue_task_title": str(queue_row.get("title") or seat_row.get("seat_unblocker_queue_task_title") or ""),
        "queue_status": str(queue_row.get("status") or seat_row.get("seat_unblocker_queue_task_status") or ""),
        "queue_priority": queue_row.get("priority"),
        "queue_lane": str(queue_row.get("lane") or seat_row.get("seat_unblocker_queue_task_lane") or ""),
        "next_action_class": str(queue_row.get("next_action_class") or seat_row.get("seat_unblocker_queue_task_next_action_class") or ""),
        "profit_mode": str(queue_row.get("profit_mode") or ""),
        "acceptance_verdict": acceptance["verdict"],
        "acceptance_read": acceptance["candidate_read"],
        "acceptance_warning_checks": acceptance["warning_checks"],
        "runtime_source": runtime_contract["source"],
        "runtime_source_status": runtime_contract["source_status"],
        "runtime_lane_name": runtime_contract["lane_name"],
        "proof_contract_status": runtime_contract["proof_contract_status"],
        "runtime_action_status": runtime_contract["action_status"],
        "first_path_verdict": runtime_contract["first_path_verdict"],
        "proof_read": runtime_contract["proof_read"],
        "shared_score_verdict": str(shared_row.get("comparison_verdict") or ""),
        "shared_score_ready": bool(shared_row.get("shared_score_ready")),
        "shared_adaptive_basis": str(shared_adaptive.get("basis") or ""),
        "best_challenger_candidate_class": str(seat_row.get("best_challenger_candidate_class") or ""),
        "best_challenger_runtime_status": str(seat_row.get("best_challenger_runtime_status") or ""),
        "best_challenger_objective_status": str(seat_row.get("best_challenger_objective_status") or ""),
        "max_profit_objective_status": str(seat_row.get("max_profit_objective_status") or ""),
        "recommendation_option": RECOMMENDED_OPTION_BY_SYMBOL.get(symbol, ""),
        "recommendation_read": recommendation_read,
    }


def build_payload(
    *,
    seat_board: dict[str, Any],
    adaptive_queue: dict[str, Any],
    gbp_first_path: dict[str, Any],
    overnight_board: dict[str, Any],
    shared_score: dict[str, Any],
    acceptance_board: dict[str, Any],
) -> dict[str, Any]:
    rows = [
        build_row(
            symbol=symbol,
            seat_board=seat_board,
            adaptive_queue=adaptive_queue,
            gbp_first_path=gbp_first_path,
            overnight_board=overnight_board,
            shared_score=shared_score,
            acceptance_board=acceptance_board,
        )
        for symbol in SYMBOLS
    ]
    rows.sort(key=recommendation_sort_key)
    recommended = rows[0] if rows else {}
    deferred = rows[1] if len(rows) > 1 else {}
    recommended_symbol = str(recommended.get("symbol") or "")
    deferred_symbol = str(deferred.get("symbol") or "")
    recommended_option = str(recommended.get("recommendation_option") or "")
    execution_ready_symbols = [
        row["symbol"]
        for row in rows
        if row.get("seat_execution_gate_status") == "ready_for_seat_execution"
    ]

    decision_read = (
        f"Recommend `{recommended_option}`: `{recommended_symbol}` currently reads "
        f"`{recommended.get('seat_execution_gate_status', '')}` on a `{recommended.get('seat_case_type', '')}` with acceptance "
        f"`{recommended.get('acceptance_verdict', '')}` and queue priority `{recommended.get('queue_priority')}`. "
        f"`{deferred_symbol}` currently reads `{deferred.get('seat_execution_gate_status', '')}` on a "
        f"`{deferred.get('seat_case_type', '')}` with acceptance `{deferred.get('acceptance_verdict', '')}` and queue priority "
        f"`{deferred.get('queue_priority')}`."
    )

    ready_read = (
        f"Execution-ready rows on the live-seat surface are `{execution_ready_symbols}`."
        if execution_ready_symbols
        else "No compared row currently reads `ready_for_seat_execution` on the live-seat surface."
    )
    deferred_read = (
        f"`{deferred_symbol}` currently reads `{deferred.get('seat_execution_gate_status', '')}` as a "
        f"`{deferred.get('seat_case_type', '')}` and should stay second unless the room explicitly chooses parallel execution."
        if deferred_symbol
        else "No deferred comparison row remains after the current recommendation."
    )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            relative_path_text(SEAT_PATH),
            relative_path_text(QUEUE_PATH),
            relative_path_text(GBP_FIRST_PATH_PATH),
            relative_path_text(OVERNIGHT_PATH),
            relative_path_text(SHARED_SCORE_PATH),
            relative_path_text(ACCEPTANCE_PATH),
        ],
        "summary": {
            "decision_id": DECISION_ID,
            "compared_symbols": list(SYMBOLS),
            "execution_ready_symbols": execution_ready_symbols,
            "recommended_symbol": recommended_symbol,
            "recommended_option": recommended_option,
            "deferred_symbol": deferred_symbol,
            "parallel_feasible": True,
            "decision_read": decision_read,
        },
        "leadership_read": [
            ready_read,
            str(recommended.get("recommendation_read") or decision_read),
            deferred_read,
            "This board is a passive decision-support surface for taskboard decision `12`; it does not mutate queue order, launch runtime, or close the decision automatically.",
        ],
        "rows": rows,
        "notes": [
            "Use this board when the room starts treating all execution-ready rows as equivalent. It exists to keep incumbent-comparison leverage separate from first-seat proof leverage.",
            "A recommendation here is an inference from checked-in seat, queue, packet, shared-score, and acceptance truth; it is not a runtime command.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Execution-Ready Seat Priority Board",
        "",
        "This board compares the currently queue-backed `ready_for_seat_execution` adaptive seat seams so decision `12` can rest on one passive authority surface instead of task metadata alone.",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        f"- decision_id: `{summary.get('decision_id', '')}`",
        f"- compared_symbols: `{summary.get('compared_symbols', [])}`",
        f"- execution_ready_symbols: `{summary.get('execution_ready_symbols', [])}`",
        f"- recommended_symbol: `{summary.get('recommended_symbol', '')}`",
        f"- recommended_option: `{summary.get('recommended_option', '')}`",
        f"- deferred_symbol: `{summary.get('deferred_symbol', '')}`",
        f"- parallel_feasible: `{summary.get('parallel_feasible', False)}`",
        f"- decision_read: {summary.get('decision_read', '')}",
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
            "| Symbol | Recommendation | Seat Case | Execution Gate | Queue Task | Queue Priority | Acceptance | Proof Contract | Shared Score |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.append(
            f"| `{row['symbol']}` | `{row['recommendation_option']}` | `{row['seat_case_type']}` | `{row['seat_execution_gate_status']}` | "
            f"`{row['queue_task_id']}` | `{row['queue_priority']}` | `{row['acceptance_verdict']}` | "
            f"`{row['proof_contract_status']}` | `{row['shared_score_verdict']}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row['symbol']}",
                "",
                f"- recommendation_option: `{row['recommendation_option']}`",
                f"- recommendation_read: {row['recommendation_read']}",
                f"- seat_verdict: `{row['seat_verdict']}`",
                f"- seat_case_type: `{row['seat_case_type']}`",
                f"- seat_case_read: {row['seat_case_read']}",
                f"- current_live_holder_lane: `{row['current_live_holder_lane'] or '-'}`",
                f"- seat_conflict: `{row['seat_conflict']}`",
                f"- seat_execution_gate_status: `{row['seat_execution_gate_status']}`",
                f"- seat_execution_gate_read: {row['seat_execution_gate_read']}",
                f"- seat_unblocker_action: `{row['seat_unblocker_action']}`",
                f"- seat_unblocker_read: {row['seat_unblocker_read']}",
                f"- queue_task: `{row['queue_task_id']}` | `{row['queue_status']}` | priority `{row['queue_priority']}` | `{row['queue_lane']}`",
                f"- queue_task_title: {row['queue_task_title']}",
                f"- next_action_class: `{row['next_action_class']}`",
                f"- profit_mode: `{row['profit_mode']}`",
                f"- acceptance_verdict: `{row['acceptance_verdict']}`",
                f"- acceptance_read: {row['acceptance_read']}",
                f"- acceptance_warning_checks: `{row['acceptance_warning_checks']}`",
                f"- runtime_source: `{row['runtime_source']}` / `{row['runtime_source_status']}`",
                f"- runtime_lane_name: `{row['runtime_lane_name']}`",
                f"- proof_contract_status: `{row['proof_contract_status']}`",
                f"- runtime_action_status: `{row['runtime_action_status']}`",
                f"- first_path_verdict: `{row['first_path_verdict'] or '-'}`",
                f"- proof_read: {row['proof_read']}",
                f"- shared_score_verdict: `{row['shared_score_verdict']}`",
                f"- shared_score_ready: `{row['shared_score_ready']}`",
                f"- shared_adaptive_basis: `{row['shared_adaptive_basis'] or '-'}`",
                f"- best_challenger_candidate_class: `{row['best_challenger_candidate_class']}`",
                f"- best_challenger_runtime_status: `{row['best_challenger_runtime_status']}`",
                f"- best_challenger_objective_status: `{row['best_challenger_objective_status']}`",
                f"- max_profit_objective_status: `{row['max_profit_objective_status']}`",
                "",
            ]
        )

    lines.extend(["## Notes", ""])
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    payload = build_payload(
        seat_board=load_json(SEAT_PATH),
        adaptive_queue=load_json(QUEUE_PATH),
        gbp_first_path=load_json(GBP_FIRST_PATH_PATH),
        overnight_board=load_json(OVERNIGHT_PATH),
        shared_score=load_json(SHARED_SCORE_PATH),
        acceptance_board=load_json(ACCEPTANCE_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
