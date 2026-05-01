#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

SEAT_PATH = REPORTS / "per_symbol_live_seat_board.json"
NEXT_ACTION_PATH = REPORTS / "max_profit_next_action_board.json"
GBP_FIRST_PATH_PATH = REPORTS / "gbpusd_adaptive_first_path_board.json"
STUDY_PATH = REPORTS / "adaptive_incumbent_study_board.json"
SHARED_SCORE_PATH = REPORTS / "adaptive_shared_score_board.json"
ACCEPTANCE_PATH = REPORTS / "adaptive_harness_acceptance_verdict_board.json"
OVERNIGHT_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"

OUTPUT_JSON_PATH = REPORTS / "execution_ready_blind_spot_board.json"
OUTPUT_MD_PATH = REPORTS / "execution_ready_blind_spot_board.md"

DECISION_ID = 12
DECISION_TITLE = "Which queue-backed execution-ready seat seam should the room advance first?"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_symbol_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("symbol") or "") == symbol:
            return dict(row)
    return {}


def find_candidate(rows: list[dict[str, Any]], candidate_id: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("candidate_id") or "") == candidate_id:
            return dict(row)
    return {}


def find_packet(rows: list[dict[str, Any]], packet_id: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("packet_id") or "") == packet_id:
            return dict(row)
    return {}


def compact_blind_spot(
    *,
    blind_spot_id: str,
    severity: str,
    read: str,
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "blind_spot_id": blind_spot_id,
        "severity": severity,
        "read": read,
        "evidence": evidence,
    }


def build_gbp_launch_blind_spot(
    *,
    proof_gate_status: str,
    action_status: str,
    first_path_verdict: str,
) -> dict[str, Any]:
    if action_status == "already_running_monitor_only" or first_path_verdict not in {"", "awaiting_first_trade_path_event"}:
        return compact_blind_spot(
            blind_spot_id="first_path_still_open",
            severity="high",
            read=(
                "GBP is execution-ready and the dedicated adaptive lane is now running, but the first path still has not "
                "closed, so the room does not yet have outcome-quality proof."
            ),
            evidence=[
                f"proof_gate_status={proof_gate_status}",
                f"action_status={action_status}",
                f"first_path_verdict={first_path_verdict}",
            ],
        )
    return compact_blind_spot(
        blind_spot_id="launch_not_started",
        severity="high",
        read=(
            "GBP is execution-ready on the seat surface, but the dedicated adaptive lane still has no first launch, "
            "no first path, and no current-run proof."
        ),
        evidence=[
            f"proof_gate_status={proof_gate_status}",
            f"action_status={action_status}",
            f"first_path_verdict={first_path_verdict}",
        ],
    )


def build_gbp_row(
    *,
    seat_row: dict[str, Any],
    next_action_row: dict[str, Any],
    gbp_first_path_board: dict[str, Any],
    study_row: dict[str, Any],
    shared_score_row: dict[str, Any],
    acceptance_row: dict[str, Any],
    overnight_row: dict[str, Any],
) -> dict[str, Any]:
    summary = dict(gbp_first_path_board.get("summary") or {})
    overnight_runtime = dict(gbp_first_path_board.get("overnight_runtime") or {})
    acceptance = dict(gbp_first_path_board.get("acceptance") or {})
    shared_score = dict(gbp_first_path_board.get("shared_score") or {})
    runtime_action_status = str(overnight_runtime.get("action_status") or overnight_row.get("action_status") or "")
    first_path_verdict = str(
        overnight_runtime.get("first_path_verdict")
        or summary.get("first_path_verdict")
        or shared_score.get("adaptive_first_path_verdict")
        or ""
    )
    proof_gate_status = str(summary.get("proof_gate_status") or "")
    launch_blind_spot = build_gbp_launch_blind_spot(
        proof_gate_status=proof_gate_status,
        action_status=runtime_action_status,
        first_path_verdict=first_path_verdict,
    )
    running_first_path = launch_blind_spot["blind_spot_id"] == "first_path_still_open"

    warning_checks = list(acceptance_row.get("warning_checks") or acceptance.get("warning_checks") or [])
    blind_spots = [
        launch_blind_spot,
        compact_blind_spot(
            blind_spot_id="no_adaptive_score",
            severity="high",
            read="GBP still cannot be scored against the incumbent because the dedicated adaptive lane has not produced any profit basis.",
            evidence=[
                f"comparison_verdict={shared_score.get('comparison_verdict') or shared_score_row.get('comparison_verdict') or ''}",
                f"adaptive_basis={shared_score.get('adaptive_basis') or ''}",
                f"study_status={study_row.get('study_status') or ''}",
            ],
        ),
        compact_blind_spot(
            blind_spot_id="contested_incumbent_seat",
            severity="medium",
            read="GBP does map to a real incumbent seat, but that seat is still contested/shared rather than an isolated one-symbol benchmark.",
            evidence=[
                f"seat_verdict={seat_row.get('seat_verdict') or ''}",
                f"incumbent_lane={study_row.get('incumbent_lane') or ''}",
                f"incumbent_seat_verdict={study_row.get('incumbent_seat_verdict') or ''}",
            ],
        ),
        compact_blind_spot(
            blind_spot_id="acceptance_warning_debt",
            severity="medium",
            read="GBP is shadow-ready, but the acceptance surface still carries explicit monetization, portfolio, and proof-integrity warnings.",
            evidence=[
                f"acceptance_verdict={acceptance_row.get('verdict') or acceptance.get('verdict') or ''}",
                f"warning_checks={warning_checks}",
            ],
        ),
    ]

    return {
        "symbol": "GBPUSD",
        "decision_option": "advance_gbpusd_first",
        "recommended": True,
        "recommendation_rank": 1,
        "adversarial_verdict": (
            "cleaner_first_move_but_first_close_pending"
            if running_first_path
            else "cleaner_first_move_but_launch_debt"
        ),
        "seat_execution_gate_status": str(seat_row.get("seat_execution_gate_status") or ""),
        "seat_execution_gate_read": str(seat_row.get("seat_execution_gate_read") or ""),
        "seat_verdict": str(seat_row.get("seat_verdict") or ""),
        "queue_task_id": str(next_action_row.get("queue_task_id") or ""),
        "queue_task_status": str(next_action_row.get("queue_task_status") or ""),
        "queue_task_priority": next_action_row.get("queue_task_priority"),
        "profit_mode": str(next_action_row.get("profit_mode") or ""),
        "next_action_class": str(next_action_row.get("next_action_class") or ""),
        "acceptance_verdict": str(acceptance_row.get("verdict") or acceptance.get("verdict") or ""),
        "study_status": str(study_row.get("study_status") or ""),
        "comparison_verdict": str(shared_score_row.get("comparison_verdict") or shared_score.get("comparison_verdict") or ""),
        "runtime_action_status": runtime_action_status,
        "adaptive_runtime_status": str(runtime_action_status or study_row.get("adaptive_runtime_status") or ""),
        "lane_name": str(overnight_row.get("lane_name") or study_row.get("adaptive_lane") or ""),
        "incumbent_lane": str(study_row.get("incumbent_lane") or ""),
        "blind_spot_count": len(blind_spots),
        "blind_spot_ids": [row["blind_spot_id"] for row in blind_spots],
        "proof_debt": warning_checks,
        "blind_spots": blind_spots,
        "why_this_option_could_fail": [
            (
                "The dedicated GBP lane can still fail to convert this running first path into usable proof if the first close lands weak, toxic, or too ambiguous to score."
                if running_first_path
                else "The dedicated GBP lane may stay packet-complete but unlaunched, leaving the room with no new proof despite choosing it first."
            ),
            "The first real GBP path could land weak or toxic, collapsing the current shadow-ready advantage back into ordinary research debt.",
            "Shared-score truth may remain unavailable even after launch if the lane still does not expose a defensible adaptive profit basis.",
        ],
        "reversal_triggers": [
            (
                "GBP prints a poor or ambiguous first close while another execution-ready seam lands cleaner proof first."
                if running_first_path
                else "GBP prints a poor first-path result or stays `hold_launch_packet_defined_not_started` while another execution-ready seam lands fresh proof first."
            ),
            "GBP remains `no_adaptive_score` after deliberate launch/proof collection, meaning seat comparison still cannot advance honestly.",
            "The current shared incumbent seat stops being a usable benchmark for this comparison path.",
        ],
        "why_considered": (
            "GBP is already the highest execution-ready and highest launch-now symbol on the checked-in passive stack, and it is the only option here that is already `shadow_ready` against a real incumbent seat while also collecting first-path runtime evidence."
            if running_first_path
            else "GBP is already the highest execution-ready and highest launch-now symbol on the checked-in passive stack, and it is the only option here that is already `shadow_ready` against a real incumbent seat."
        ),
    }


def build_usdjpy_row(
    *,
    seat_row: dict[str, Any],
    next_action_row: dict[str, Any],
    study_row: dict[str, Any],
    shared_score_row: dict[str, Any],
    acceptance_row: dict[str, Any],
    overnight_row: dict[str, Any],
) -> dict[str, Any]:
    shared_adaptive = dict(shared_score_row.get("adaptive") or {})
    blind_spots = [
        compact_blind_spot(
            blind_spot_id="no_incumbent_score",
            severity="high",
            read="USDJPY still lacks an incumbent live-seat comparison surface, so even a clean adaptive relaunch would not yet resolve the seat question honestly.",
            evidence=[
                f"comparison_verdict={shared_score_row.get('comparison_verdict') or ''}",
                f"study_status={shared_score_row.get('study_status') or study_row.get('study_status') or ''}",
                f"incumbent_present={study_row.get('incumbent_present')}",
            ],
        ),
        compact_blind_spot(
            blind_spot_id="research_only_acceptance",
            severity="high",
            read="USDJPY has an explicit packet, but the candidate still fails the adversarial readiness bar because it is research-only rather than shadow-ready.",
            evidence=[
                f"acceptance_verdict={acceptance_row.get('verdict') or ''}",
                f"adaptive_runtime_status={study_row.get('adaptive_runtime_status') or ''}",
            ],
        ),
        compact_blind_spot(
            blind_spot_id="lane_identity_split",
            severity="high",
            read="The canonical overnight relaunch lane and the current study/shared-score lane are not the same artifact, so fresh proof would still leave comparability ambiguity.",
            evidence=[
                f"overnight_lane={overnight_row.get('lane_name') or ''}",
                f"study_lane={study_row.get('adaptive_lane') or ''}",
                f"shared_score_lane={shared_adaptive.get('lane') or ''}",
            ],
        ),
        compact_blind_spot(
            blind_spot_id="fresh_bounded_proof_missing",
            severity="medium",
            read="USDJPY's old bounded runtime fault is historical, but the room still does not have the fresh bounded proof needed to promote the branch out of theory and relaunch narration.",
            evidence=[
                f"action_status={overnight_row.get('action_status') or ''}",
                f"candidate_read={acceptance_row.get('candidate_read') or ''}",
                f"proof_mode={next_action_row.get('next_action_class') or ''}",
            ],
        ),
    ]

    return {
        "symbol": "USDJPY",
        "decision_option": "advance_usdjpy_first",
        "recommended": False,
        "recommendation_rank": 2,
        "adversarial_verdict": "comparability_debt_heavier_than_launch_debt",
        "seat_execution_gate_status": str(seat_row.get("seat_execution_gate_status") or ""),
        "seat_execution_gate_read": str(seat_row.get("seat_execution_gate_read") or ""),
        "seat_verdict": str(seat_row.get("seat_verdict") or ""),
        "queue_task_id": str(next_action_row.get("queue_task_id") or ""),
        "queue_task_status": str(next_action_row.get("queue_task_status") or ""),
        "queue_task_priority": next_action_row.get("queue_task_priority"),
        "profit_mode": str(next_action_row.get("profit_mode") or ""),
        "next_action_class": str(next_action_row.get("next_action_class") or ""),
        "acceptance_verdict": str(acceptance_row.get("verdict") or ""),
        "study_status": str(study_row.get("study_status") or ""),
        "comparison_verdict": str(shared_score_row.get("comparison_verdict") or ""),
        "runtime_action_status": str(overnight_row.get("action_status") or ""),
        "adaptive_runtime_status": str(study_row.get("adaptive_runtime_status") or ""),
        "lane_name": str(overnight_row.get("lane_name") or ""),
        "study_lane_name": str(study_row.get("adaptive_lane") or ""),
        "shared_score_lane_name": str(shared_adaptive.get("lane") or ""),
        "blind_spot_count": len(blind_spots),
        "blind_spot_ids": [row["blind_spot_id"] for row in blind_spots],
        "proof_debt": list(acceptance_row.get("warning_checks") or []),
        "blind_spots": blind_spots,
        "why_this_option_could_fail": [
            "Fresh USDJPY proof could arrive on a relaunch packet that still does not line up with the current study/shared-score lane, leaving the room unable to compare apples to apples.",
            "Because there is no incumbent live-seat score today, the room could collect adaptive proof without actually resolving seat displacement order.",
            "The candidate can remain `research_only` even after more packet work if bounded executability or survival proof still does not clear the checklist.",
        ],
        "reversal_triggers": [
            "USDJPY gets a unified lane identity across overnight packet, study board, and shared-score board and moves from `research_only` to `shadow_ready`.",
            "A real incumbent-seat comparison surface lands for USDJPY, closing the current `no_incumbent_score` gap.",
            "Fresh bounded proof lands cleanly before GBP collects any first-path evidence, making the current ordering less defensible.",
        ],
        "why_considered": "USDJPY is a real execution-ready seam on the seat and max-profit surfaces, but it still carries deeper comparability debt than GBP because the current stack does not yet line up one clean incumbent-vs-adaptive path.",
    }


def build_payload(
    *,
    seat_board: dict[str, Any],
    next_action_board: dict[str, Any],
    gbp_first_path_board: dict[str, Any],
    study_board: dict[str, Any],
    shared_score_board: dict[str, Any],
    acceptance_board: dict[str, Any],
    overnight_board: dict[str, Any],
) -> dict[str, Any]:
    seat_rows = list(seat_board.get("rows") or [])
    next_action_rows = list(next_action_board.get("rows") or [])
    study_rows = list(study_board.get("rows") or [])
    shared_score_rows = list(shared_score_board.get("rows") or [])
    acceptance_rows = list(acceptance_board.get("candidates") or [])
    overnight_rows = list(overnight_board.get("rows") or [])

    gbp_row = build_gbp_row(
        seat_row=find_symbol_row(seat_rows, "GBPUSD"),
        next_action_row=find_symbol_row(next_action_rows, "GBPUSD"),
        gbp_first_path_board=gbp_first_path_board,
        study_row=find_symbol_row(study_rows, "GBPUSD"),
        shared_score_row=find_symbol_row(shared_score_rows, "GBPUSD"),
        acceptance_row=find_candidate(acceptance_rows, "gbpusd_adaptive_comparison_packet"),
        overnight_row=find_packet(overnight_rows, "gbpusd_adaptive_comparison_packet"),
    )
    usdjpy_row = build_usdjpy_row(
        seat_row=find_symbol_row(seat_rows, "USDJPY"),
        next_action_row=find_symbol_row(next_action_rows, "USDJPY"),
        study_row=find_symbol_row(study_rows, "USDJPY"),
        shared_score_row=find_symbol_row(shared_score_rows, "USDJPY"),
        acceptance_row=find_candidate(acceptance_rows, "usdjpy_bounded_forward_proof"),
        overnight_row=find_packet(overnight_rows, "usdjpy_bounded_forward_proof"),
    )
    rows = [gbp_row, usdjpy_row]

    seat_summary = dict(seat_board.get("summary") or {})
    next_action_summary = dict(next_action_board.get("summary") or {})
    recommended_row = gbp_row

    return {
        "generated_at": utc_now_iso(),
        "decision_id": DECISION_ID,
        "title": DECISION_TITLE,
        "sources": [
            str(SEAT_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
            str(GBP_FIRST_PATH_PATH.relative_to(ROOT)),
            str(STUDY_PATH.relative_to(ROOT)),
            str(SHARED_SCORE_PATH.relative_to(ROOT)),
            str(ACCEPTANCE_PATH.relative_to(ROOT)),
            str(OVERNIGHT_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "recommended_option": recommended_row["decision_option"],
            "recommended_symbol": recommended_row["symbol"],
            "recommended_verdict": recommended_row["adversarial_verdict"],
            "highest_execution_ready_symbol": str(seat_summary.get("highest_execution_ready_symbol") or ""),
            "highest_launch_now_symbol": str(next_action_summary.get("highest_launch_now_symbol") or ""),
            "parallel_option_status": "not_recommended_yet",
            "parallel_option_read": "Do not collapse GBPUSD and USDJPY into one go-order yet: both are execution-ready on the seat surface, but GBP is launch/proof-debt heavy while USDJPY is comparability-debt heavy.",
            "option_count": len(rows),
            "recommended_blind_spot_count": recommended_row["blind_spot_count"],
        },
        "leadership_read": [
            "Decision 12 is real because both GBPUSD and USDJPY still read `ready_for_seat_execution`; the question is which seam has the cleaner adversarial failure profile, not whether only one seam exists.",
            "Inference from the checked-in passive stack: GBPUSD should still go first because it is both the highest execution-ready symbol and the highest launch-now symbol, and unlike USDJPY it is already `shadow_ready` against a real incumbent seat.",
            (
                "The adversarial catch on GBP is no longer launch-not-started debt; it is first-close and score-conversion debt: the lane is already running, but the room still lacks a closed first path and a defensible adaptive basis."
                if gbp_row["runtime_action_status"] == "already_running_monitor_only"
                else "The adversarial catch on GBP is launch/proof debt, not packet ambiguity: it is still `hold_launch_packet_defined_not_started`, `awaiting_first_trade_path_event`, and `no_adaptive_score`."
            ),
            "The adversarial catch on USDJPY is deeper comparability debt: it is still `research_only`, still `no_incumbent_score`, and the canonical overnight relaunch lane does not yet match the study/shared-score lane.",
            "Parallel execution is not the current recommended read because it would mix two different unresolved debts and make it harder to tell whether the room solved launch debt, comparability debt, or neither.",
        ],
        "rows": rows,
        "notes": [
            "This board is passive. It does not launch either seam or resolve decision 12 by itself.",
            "Read it as the adversarial companion to a priority board: it is meant to surface the blind spots, proof debt, and reversal triggers behind the current recommendation.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Execution-Ready Blind-Spot Board",
        "",
        "This board is the adversarial companion to the current execution-ready seat decision. It explains why the current recommendation could still fail and what would reverse it.",
        "",
        f"- generated_at: `{payload.get('generated_at', '-')}`",
        f"- decision_id: `{payload.get('decision_id', '-')}`",
        f"- title: {payload.get('title', '-')}",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload.get("leadership_read", []):
        lines.append(f"- {line}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- recommended_option: `{summary.get('recommended_option')}`",
            f"- recommended_symbol: `{summary.get('recommended_symbol')}`",
            f"- recommended_verdict: `{summary.get('recommended_verdict')}`",
            f"- highest_execution_ready_symbol: `{summary.get('highest_execution_ready_symbol')}`",
            f"- highest_launch_now_symbol: `{summary.get('highest_launch_now_symbol')}`",
            f"- parallel_option_status: `{summary.get('parallel_option_status')}`",
            f"- parallel_option_read: {summary.get('parallel_option_read')}",
            "",
            "## Options",
            "",
            "| Option | Symbol | Verdict | Acceptance | Study | Shared Score | Runtime | Blind Spots |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload.get("rows", []):
        lines.append(
            f"| `{row['decision_option']}` | `{row['symbol']}` | `{row['adversarial_verdict']}` | `{row.get('acceptance_verdict', '')}` | "
            f"`{row.get('study_status', '')}` | `{row.get('comparison_verdict', '')}` | `{row.get('runtime_action_status', '')}` | "
            f"`{row.get('blind_spot_ids', [])}` |"
        )

    lines.extend(["", "## Detail", ""])
    for row in payload.get("rows", []):
        lines.append(f"### {row['decision_option']}")
        lines.append(f"- symbol: `{row['symbol']}`")
        lines.append(f"- recommended: `{row['recommended']}`")
        lines.append(f"- adversarial_verdict: `{row['adversarial_verdict']}`")
        lines.append(f"- why_considered: {row['why_considered']}")
        lines.append(f"- seat_execution_gate_status: `{row['seat_execution_gate_status']}`")
        lines.append(f"- seat_verdict: `{row['seat_verdict']}`")
        lines.append(f"- queue_task_id: `{row['queue_task_id']}`")
        lines.append(f"- queue_task_status: `{row['queue_task_status']}`")
        lines.append(f"- profit_mode: `{row['profit_mode']}`")
        lines.append(f"- next_action_class: `{row['next_action_class']}`")
        lines.append(f"- acceptance_verdict: `{row['acceptance_verdict']}`")
        lines.append(f"- study_status: `{row['study_status']}`")
        lines.append(f"- comparison_verdict: `{row['comparison_verdict']}`")
        lines.append(f"- runtime_action_status: `{row['runtime_action_status']}`")
        lines.append(f"- adaptive_runtime_status: `{row['adaptive_runtime_status']}`")
        if row.get("lane_name"):
            lines.append(f"- lane_name: `{row['lane_name']}`")
        if row.get("study_lane_name"):
            lines.append(f"- study_lane_name: `{row['study_lane_name']}`")
        if row.get("shared_score_lane_name"):
            lines.append(f"- shared_score_lane_name: `{row['shared_score_lane_name']}`")
        lines.append("- blind_spots:")
        for blind_spot in row.get("blind_spots", []):
            lines.append(
                f"  - `{blind_spot['blind_spot_id']}` (`{blind_spot['severity']}`): {blind_spot['read']} "
                f"Evidence: `{blind_spot['evidence']}`"
            )
        if row.get("proof_debt"):
            lines.append("- proof_debt: " + ", ".join(f"`{item}`" for item in row["proof_debt"]))
        lines.append("- why_this_option_could_fail:")
        for item in row.get("why_this_option_could_fail", []):
            lines.append(f"  - {item}")
        lines.append("- reversal_triggers:")
        for item in row.get("reversal_triggers", []):
            lines.append(f"  - {item}")
        lines.append("")

    lines.extend(["## Notes", ""])
    for line in payload.get("notes", []):
        lines.append(f"- {line}")
    lines.append("")
    return "\n".join(lines)


def build_from_disk() -> dict[str, Any]:
    return build_payload(
        seat_board=load_json(SEAT_PATH),
        next_action_board=load_json(NEXT_ACTION_PATH),
        gbp_first_path_board=load_json(GBP_FIRST_PATH_PATH),
        study_board=load_json(STUDY_PATH),
        shared_score_board=load_json(SHARED_SCORE_PATH),
        acceptance_board=load_json(ACCEPTANCE_PATH),
        overnight_board=load_json(OVERNIGHT_PATH),
    )


def main() -> int:
    payload = build_from_disk()
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"json": str(OUTPUT_JSON_PATH), "md": str(OUTPUT_MD_PATH)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
