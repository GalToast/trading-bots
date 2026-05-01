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
BRANCH_PATH = REPORTS / "adaptive_btc_branch_decision_board.json"
GUARDED_PATH = REPORTS / "guarded_toxic_flow_contract_board.json"
NEXT_ACTION_PATH = REPORTS / "max_profit_next_action_board.json"
RUNNER_PLAN_PATH = REPORTS / "adaptive_btc_shadow_runner_plan.json"

OUTPUT_JSON_PATH = REPORTS / "btc_execution_ready_control_contract_board.json"
OUTPUT_MD_PATH = REPORTS / "btc_execution_ready_control_contract_board.md"

SYMBOL = "BTCUSD"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_symbol_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("symbol") or "") == symbol:
            return dict(row)
    return {}


def find_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    for row in tasks:
        if str(row.get("task_id") or "") == task_id:
            return dict(row)
    return {}


def find_branch(rows: list[dict[str, Any]], branch_id: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("branch_id") or "") == branch_id:
            return dict(row)
    return {}


def build_graduation_blockers(
    *,
    seat_row: dict[str, Any],
    next_action_row: dict[str, Any],
    guarded_row: dict[str, Any],
    runner_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []

    seat_gate = str(seat_row.get("seat_execution_gate_status") or "")
    if seat_gate and seat_gate != "ready_for_seat_execution":
        blockers.append(
            {
                "blocker_id": "seat_execution_gate",
                "status": seat_gate,
                "read": str(seat_row.get("seat_execution_gate_read") or ""),
            }
        )

    queue_alignment = str(seat_row.get("seat_queue_alignment_status") or "")
    if queue_alignment == "queue_ready_precedes_seat_call":
        blockers.append(
            {
                "blocker_id": "queue_alignment",
                "status": queue_alignment,
                "read": "Queue coverage exists, but the current seat call still trails the queue contract rather than matching an execution-ready seat move.",
            }
        )

    overlay_contract = str(seat_row.get("seat_overlay_contract_status") or "")
    if overlay_contract == "preparatory_overlay_contract":
        blockers.append(
            {
                "blocker_id": "overlay_contract",
                "status": overlay_contract,
                "read": str(seat_row.get("seat_overlay_contract_read") or ""),
            }
        )

    overlay_launch = str(seat_row.get("seat_overlay_launch_bridge_status") or "")
    if overlay_launch and overlay_launch != "no_overlay_launch_bridge_needed":
        requested = list(runner_contract.get("requested_overlays") or [])
        executable = list(runner_contract.get("executable_overlays") or [])
        blockers.append(
            {
                "blocker_id": "overlay_launch_alignment",
                "status": overlay_launch,
                "read": (
                    f"{seat_row.get('seat_overlay_launch_bridge_read') or ''} "
                    f"Runner plan currently requests `{requested}` and executes `{executable}`."
                ).strip(),
            }
        )

    runtime_evidence = dict(guarded_row.get("runtime_evidence") or {})
    runtime_visibility = str(runtime_evidence.get("verdict") or "")
    if runtime_visibility and runtime_visibility != "guard_runtime_observed":
        blockers.append(
            {
                "blocker_id": "guarded_runtime_visibility",
                "status": runtime_visibility,
                "read": str(runtime_evidence.get("read") or ""),
            }
        )

    posture = str(next_action_row.get("max_profit_posture") or "")
    if posture == "preparatory_only":
        blockers.append(
            {
                "blocker_id": "max_profit_posture",
                "status": posture,
                "read": str(next_action_row.get("launch_read") or next_action_row.get("max_profit_posture_read") or ""),
            }
        )

    return blockers


def build_payload(
    *,
    seat_board: dict[str, Any],
    adaptive_queue: dict[str, Any],
    branch_board: dict[str, Any],
    guarded_contract: dict[str, Any],
    next_action_board: dict[str, Any],
    runner_plan: dict[str, Any],
) -> dict[str, Any]:
    seat_row = find_symbol_row(list(seat_board.get("rows") or []), SYMBOL)
    next_action_row = find_symbol_row(list(next_action_board.get("rows") or []), SYMBOL)
    guarded_row = find_symbol_row(list(guarded_contract.get("rows") or []), SYMBOL)

    queue_task_id = str(
        seat_row.get("seat_unblocker_queue_task_id")
        or next_action_row.get("queue_task_id")
        or ""
    )
    queue_task = find_task(list(adaptive_queue.get("tasks") or []), queue_task_id)

    branch_summary = dict(branch_board.get("summary") or {})
    recommended_branch_id = str(branch_summary.get("recommended_branch_id") or "")
    doctrine_target_branch_id = str(branch_summary.get("doctrine_target_branch_id") or "")
    recommended_branch = find_branch(list(branch_board.get("rows") or []), recommended_branch_id)
    doctrine_target_branch = find_branch(list(branch_board.get("rows") or []), doctrine_target_branch_id)

    guarded_contract_truth = dict(guarded_row.get("contract") or {})
    guarded_runtime_truth = dict(guarded_row.get("runtime_evidence") or {})
    runner_contract = dict(runner_plan.get("runtime_overlay_contract") or {})

    graduation_blockers = build_graduation_blockers(
        seat_row=seat_row,
        next_action_row=next_action_row,
        guarded_row=guarded_row,
        runner_contract=runner_contract,
    )
    blocker_ids = [str(row.get("blocker_id") or "") for row in graduation_blockers]

    inference_read = (
        "Inference from the checked-in seat, queue, branch, guarded-contract, and runner-plan statuses: "
        f"`{SYMBOL}` remains a preparatory control contract until the seat gate stops reading "
        f"`{seat_row.get('seat_execution_gate_status') or ''}`, queue alignment no longer says "
        f"`{seat_row.get('seat_queue_alignment_status') or ''}`, the overlay contract clears "
        f"`{seat_row.get('seat_overlay_contract_status') or ''}`, the launch bridge moves past "
        f"`{seat_row.get('seat_overlay_launch_bridge_status') or ''}`, and guarded-open runtime evidence "
        f"stops reading `{guarded_runtime_truth.get('verdict') or ''}`."
    )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(SEAT_PATH.relative_to(ROOT)),
            str(QUEUE_PATH.relative_to(ROOT)),
            str(BRANCH_PATH.relative_to(ROOT)),
            str(GUARDED_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
            str(RUNNER_PLAN_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "symbol": SYMBOL,
            "contract_status": "preparatory_control_contract",
            "seat_execution_gate_status": str(seat_row.get("seat_execution_gate_status") or ""),
            "seat_queue_alignment_status": str(seat_row.get("seat_queue_alignment_status") or ""),
            "max_profit_posture": str(next_action_row.get("max_profit_posture") or ""),
            "queue_task_id": queue_task_id,
            "queue_task_status": str(queue_task.get("status") or next_action_row.get("queue_task_status") or ""),
            "recommended_branch_id": recommended_branch_id,
            "recommended_branch_acceptance_verdict": str(recommended_branch.get("acceptance_verdict") or ""),
            "recommended_branch_launch_status": str(recommended_branch.get("launch_status") or ""),
            "doctrine_target_branch_id": doctrine_target_branch_id,
            "doctrine_target_acceptance_verdict": str(doctrine_target_branch.get("acceptance_verdict") or ""),
            "runtime_obligation_class": str(queue_task.get("runtime_obligation_class") or ""),
            "guarded_contract_verdict": str(guarded_contract_truth.get("verdict") or ""),
            "runtime_visibility_verdict": str(guarded_runtime_truth.get("verdict") or ""),
            "seat_overlay_launch_bridge_status": str(seat_row.get("seat_overlay_launch_bridge_status") or ""),
            "required_overlay_count": len(list(queue_task.get("runtime_overlays") or [])),
            "requested_overlay_count": len(list(runner_contract.get("requested_overlays") or [])),
            "executable_overlay_count": len(list(runner_contract.get("executable_overlays") or [])),
            "unsupported_overlay_count": len(list(runner_contract.get("unsupported_overlays") or [])),
            "graduation_blocker_count": len(graduation_blockers),
            "graduation_blocker_ids": blocker_ids,
            "contract_read": inference_read,
        },
        "leadership_read": [
            f"`{SYMBOL}` remains the top preparatory seam, not an execution-ready seat move: the live seat still reads `{seat_row.get('seat_execution_gate_status') or ''}` and the max-profit board still reads `{next_action_row.get('max_profit_posture') or ''}`.",
            f"The active BTC control branch is still `{recommended_branch_id}` / `{queue_task_id}`, and it is `shadow_ready` plus `{recommended_branch.get('launch_status') or ''}` rather than a new launch-now seat claim.",
            f"Guarded doctrine is still constraining the seam: `{guarded_contract_truth.get('verdict') or ''}` with runtime evidence `{guarded_runtime_truth.get('verdict') or ''}`.",
            f"Overlay bridge capability now exists, but alignment is still incomplete: seat truth reads `{seat_row.get('seat_overlay_launch_bridge_status') or ''}` while the runner plan supports `{runner_contract.get('supported_overlays') or []}` and requests `{runner_contract.get('requested_overlays') or []}`.",
            inference_read,
        ],
        "control_branch": {
            "queue_task_id": queue_task_id,
            "queue_task_title": str(queue_task.get("title") or next_action_row.get("queue_task_title") or ""),
            "queue_task_status": str(queue_task.get("status") or next_action_row.get("queue_task_status") or ""),
            "queue_task_lane": str(queue_task.get("lane") or next_action_row.get("queue_lane") or ""),
            "queue_task_priority": queue_task.get("priority"),
            "profit_mode": str(queue_task.get("profit_mode") or next_action_row.get("profit_mode") or ""),
            "next_action_class": str(queue_task.get("next_action_class") or next_action_row.get("next_action_class") or ""),
            "runtime_obligation_class": str(queue_task.get("runtime_obligation_class") or ""),
            "runtime_overlay_read": str(queue_task.get("runtime_overlay_read") or ""),
            "runtime_overlays": list(queue_task.get("runtime_overlays") or []),
            "recommended_branch_id": recommended_branch_id,
            "recommended_branch_title": str(recommended_branch.get("title") or ""),
            "recommended_branch_acceptance_verdict": str(recommended_branch.get("acceptance_verdict") or ""),
            "recommended_branch_launch_status": str(recommended_branch.get("launch_status") or ""),
            "recommended_branch_read": str(recommended_branch.get("why") or ""),
        },
        "doctrine_boundary": {
            "recommended_branch_id": recommended_branch_id,
            "recommended_branch_title": str(recommended_branch.get("title") or ""),
            "recommended_branch_acceptance_verdict": str(recommended_branch.get("acceptance_verdict") or ""),
            "recommended_branch_launch_status": str(recommended_branch.get("launch_status") or ""),
            "doctrine_target_branch_id": doctrine_target_branch_id,
            "doctrine_target_branch_title": str(doctrine_target_branch.get("title") or ""),
            "doctrine_target_acceptance_verdict": str(doctrine_target_branch.get("acceptance_verdict") or ""),
            "read": (
                f"BTC branch authority still separates the executable control branch `{recommended_branch_id}` from the doctrine target "
                f"`{doctrine_target_branch_id}`. Treat `{recommended_branch_id}` as the current control path and "
                f"`{doctrine_target_branch_id}` as the later strategic build, not as interchangeable launch instructions."
            ),
        },
        "overlay_truth": {
            "seat_overlay_contract_status": str(seat_row.get("seat_overlay_contract_status") or ""),
            "seat_overlay_contract_read": str(seat_row.get("seat_overlay_contract_read") or ""),
            "seat_overlay_launch_bridge_status": str(seat_row.get("seat_overlay_launch_bridge_status") or ""),
            "seat_overlay_launch_bridge_read": str(seat_row.get("seat_overlay_launch_bridge_read") or ""),
            "supported_overlays": list(runner_contract.get("supported_overlays") or []),
            "requested_overlays": list(runner_contract.get("requested_overlays") or []),
            "executable_overlays": list(runner_contract.get("executable_overlays") or []),
            "unsupported_overlays": list(runner_contract.get("unsupported_overlays") or []),
            "runner_plan_read": str(runner_contract.get("read") or ""),
        },
        "guarded_contract_truth": {
            "contract_verdict": str(guarded_contract_truth.get("verdict") or ""),
            "contract_read": str(guarded_contract_truth.get("read") or ""),
            "primary_entry_guard": str(guarded_contract_truth.get("primary_entry_guard") or ""),
            "escape_role": str(guarded_contract_truth.get("escape_role") or ""),
            "step_widening_role": str(guarded_contract_truth.get("step_widening_role") or ""),
            "runtime_visibility_verdict": str(guarded_runtime_truth.get("verdict") or ""),
            "runtime_visibility_read": str(guarded_runtime_truth.get("read") or ""),
        },
        "graduation_blockers": graduation_blockers,
        "inference_read": inference_read,
        "notes": [
            "This board is passive and compresses existing BTC preparatory truth; it does not launch, edit, or approve any runtime.",
            "The graduation read is an inference from checked-in statuses, not a new runtime rule.",
            "Use this board when the room starts collapsing BTC queue priority, branch readiness, overlay capability, and seat execution-readiness into one claim.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    control_branch = dict(payload.get("control_branch") or {})
    doctrine_boundary = dict(payload.get("doctrine_boundary") or {})
    overlay_truth = dict(payload.get("overlay_truth") or {})
    guarded_truth = dict(payload.get("guarded_contract_truth") or {})
    graduation_blockers = list(payload.get("graduation_blockers") or [])

    lines = [
        "# BTC Execution-Ready Control Contract Board",
        "",
        f"Generated at: `{payload.get('generated_at', '')}`",
        "",
        "## Summary",
        f"- symbol: `{summary.get('symbol', '')}`",
        f"- contract_status: `{summary.get('contract_status', '')}`",
        f"- seat_execution_gate_status: `{summary.get('seat_execution_gate_status', '')}`",
        f"- seat_queue_alignment_status: `{summary.get('seat_queue_alignment_status', '')}`",
        f"- max_profit_posture: `{summary.get('max_profit_posture', '')}`",
        f"- queue_task_id: `{summary.get('queue_task_id', '')}` / `{summary.get('queue_task_status', '')}`",
        f"- recommended_branch_id: `{summary.get('recommended_branch_id', '')}` / `{summary.get('recommended_branch_acceptance_verdict', '')}` / `{summary.get('recommended_branch_launch_status', '')}`",
        f"- doctrine_target_branch_id: `{summary.get('doctrine_target_branch_id', '')}` / `{summary.get('doctrine_target_acceptance_verdict', '')}`",
        f"- runtime_obligation_class: `{summary.get('runtime_obligation_class', '')}`",
        f"- guarded_contract_verdict: `{summary.get('guarded_contract_verdict', '')}`",
        f"- runtime_visibility_verdict: `{summary.get('runtime_visibility_verdict', '')}`",
        f"- seat_overlay_launch_bridge_status: `{summary.get('seat_overlay_launch_bridge_status', '')}`",
        f"- graduation_blocker_ids: `{summary.get('graduation_blocker_ids', [])}`",
        f"- contract_read: {summary.get('contract_read', '')}",
        "",
        "## Leadership Read",
    ]

    for entry in list(payload.get("leadership_read") or []):
        lines.append(f"- {entry}")

    lines.extend(
        [
            "",
            "## Control Branch",
            f"- queue_task: `{control_branch.get('queue_task_id', '')}` | `{control_branch.get('queue_task_status', '')}` | `{control_branch.get('queue_task_lane', '')}`",
            f"- queue_task_title: {control_branch.get('queue_task_title', '')}",
            f"- profit_mode: `{control_branch.get('profit_mode', '')}`",
            f"- next_action_class: `{control_branch.get('next_action_class', '')}`",
            f"- runtime_obligation_class: `{control_branch.get('runtime_obligation_class', '')}`",
            f"- runtime_overlays: `{control_branch.get('runtime_overlays', [])}`",
            f"- runtime_overlay_read: {control_branch.get('runtime_overlay_read', '')}",
            f"- recommended_branch: `{control_branch.get('recommended_branch_id', '')}` | `{control_branch.get('recommended_branch_acceptance_verdict', '')}` | `{control_branch.get('recommended_branch_launch_status', '')}`",
            f"- recommended_branch_read: {control_branch.get('recommended_branch_read', '')}",
            "",
            "## Doctrine Boundary",
            f"- recommended_branch_id: `{doctrine_boundary.get('recommended_branch_id', '')}`",
            f"- doctrine_target_branch_id: `{doctrine_boundary.get('doctrine_target_branch_id', '')}`",
            f"- recommended_branch_acceptance_verdict: `{doctrine_boundary.get('recommended_branch_acceptance_verdict', '')}`",
            f"- doctrine_target_acceptance_verdict: `{doctrine_boundary.get('doctrine_target_acceptance_verdict', '')}`",
            f"- read: {doctrine_boundary.get('read', '')}",
            "",
            "## Overlay Truth",
            f"- seat_overlay_contract_status: `{overlay_truth.get('seat_overlay_contract_status', '')}`",
            f"- seat_overlay_contract_read: {overlay_truth.get('seat_overlay_contract_read', '')}",
            f"- seat_overlay_launch_bridge_status: `{overlay_truth.get('seat_overlay_launch_bridge_status', '')}`",
            f"- seat_overlay_launch_bridge_read: {overlay_truth.get('seat_overlay_launch_bridge_read', '')}",
            f"- supported_overlays: `{overlay_truth.get('supported_overlays', [])}`",
            f"- requested_overlays: `{overlay_truth.get('requested_overlays', [])}`",
            f"- executable_overlays: `{overlay_truth.get('executable_overlays', [])}`",
            f"- unsupported_overlays: `{overlay_truth.get('unsupported_overlays', [])}`",
            f"- runner_plan_read: {overlay_truth.get('runner_plan_read', '')}",
            "",
            "## Guarded Contract Truth",
            f"- contract_verdict: `{guarded_truth.get('contract_verdict', '')}`",
            f"- contract_read: {guarded_truth.get('contract_read', '')}",
            f"- primary_entry_guard: `{guarded_truth.get('primary_entry_guard', '')}`",
            f"- escape_role: `{guarded_truth.get('escape_role', '')}`",
            f"- step_widening_role: `{guarded_truth.get('step_widening_role', '')}`",
            f"- runtime_visibility_verdict: `{guarded_truth.get('runtime_visibility_verdict', '')}`",
            f"- runtime_visibility_read: {guarded_truth.get('runtime_visibility_read', '')}",
            "",
            "## Inferred Graduation Blockers",
            "_Inference from current checked-in statuses, not a new runtime rule._",
        ]
    )

    for row in graduation_blockers:
        lines.append(f"- `{row.get('blocker_id', '')}` -> `{row.get('status', '')}`: {row.get('read', '')}")

    lines.extend(
        [
            "",
            "## Notes",
        ]
    )
    for note in list(payload.get("notes") or []):
        lines.append(f"- {note}")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = build_payload(
        seat_board=load_json(SEAT_PATH),
        adaptive_queue=load_json(QUEUE_PATH),
        branch_board=load_json(BRANCH_PATH),
        guarded_contract=load_json(GUARDED_PATH),
        next_action_board=load_json(NEXT_ACTION_PATH),
        runner_plan=load_json(RUNNER_PLAN_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
