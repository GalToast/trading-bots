#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RESTORE_BOARD_PATH = ROOT / "reports" / "btc_m15_warp_restore_board.json"
RUNTIME_AUDIT_PATH = ROOT / "reports" / "btc_adaptive_runtime_audit.json"
DOWNTREND_HANDOFF_PATH = ROOT / "reports" / "btc_downtrend_handoff.json"
ADAPTIVE_PLAN_PATH = ROOT / "reports" / "adaptive_btc_shadow_runner_plan.json"
OVERNIGHT_PACKET_PATH = ROOT / "reports" / "adaptive_overnight_launch_packet_board.json"
ACCEPTANCE_VERDICT_PATH = ROOT / "reports" / "adaptive_harness_acceptance_verdict_board.json"
OUTPUT_JSON = ROOT / "reports" / "adaptive_btc_branch_decision_board.json"
OUTPUT_MD = ROOT / "reports" / "adaptive_btc_branch_decision_board.md"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_payload(
    restore_board: dict[str, Any] | None = None,
    runtime_audit: dict[str, Any] | None = None,
    downtrend_handoff: dict[str, Any] | None = None,
    adaptive_plan: dict[str, Any] | None = None,
    overnight_packet: dict[str, Any] | None = None,
    acceptance_verdict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    restore = restore_board or load_json(RESTORE_BOARD_PATH)
    audit = runtime_audit or load_json(RUNTIME_AUDIT_PATH)
    handoff = downtrend_handoff or load_json(DOWNTREND_HANDOFF_PATH)
    plan = adaptive_plan or load_json(ADAPTIVE_PLAN_PATH)
    overnight = overnight_packet or load_json(OVERNIGHT_PACKET_PATH)
    acceptance = acceptance_verdict or load_json(ACCEPTANCE_VERDICT_PATH)

    restore_candidate = dict(restore.get("restore_candidate") or {})
    audit_summary = dict(audit.get("summary") or {})
    runtime_lane = dict(audit.get("runtime_lane") or {})
    runtime_objective_context = dict(audit.get("runtime_objective_context") or {})
    plan_recommendation = dict(plan.get("controller_recommendation") or {})
    proposed_downtrend_shape = dict(handoff.get("proposed_downtrend_shape") or {})
    plan_warnings = list((plan.get("warnings") or []))
    step_review = dict(plan.get("step_review") or {})
    step_review_notes = list(step_review.get("notes") or [])
    overnight_rows = {str(row.get("packet_id") or ""): dict(row) for row in list(overnight.get("rows") or [])}
    acceptance_rows = {str(row.get("candidate_id") or ""): dict(row) for row in list(acceptance.get("candidates") or [])}
    restore_packet = overnight_rows.get("btc_restore_comparison_shadow", {})
    parked_packet = overnight_rows.get("btc_parked_adaptive_artifact", {})
    restore_acceptance = acceptance_rows.get("btc_restore_comparison_shadow", {})
    parked_acceptance = acceptance_rows.get("btc_parked_artifact_review", {})
    target_acceptance = acceptance_rows.get("btc_true_adaptive_candidate", {})

    rows = [
        {
            "branch_id": "hold_parked_artifact_only",
            "priority": 1,
            "status": "not_next_action",
            "title": "Keep the parked BTC adaptive artifact in hold/manual-review only",
            "doctrine_alignment": "low",
            "execution_read": "parked_runtime_artifact_only",
            "launch_status": str(parked_packet.get("action_status") or ""),
            "launch_read": str(parked_packet.get("action_read") or ""),
            "acceptance_verdict": str(parked_acceptance.get("verdict") or ""),
            "acceptance_read": str(parked_acceptance.get("candidate_read") or ""),
            "why": str(audit_summary.get("completion_read") or "The parked BTC adaptive lane is historical runtime evidence only."),
            "blockers": [
                "parked_direct_live_artifact",
                "stale_runtime_evidence",
                "not_controller_aligned",
            ],
            "allowed_inputs": [
                str(audit.get("lane_name") or runtime_lane.get("lane_name") or "shadow_btcusd_m15_adaptive_regime"),
            ],
        },
        {
            "branch_id": "launch_restore_comparison_shadow",
            "priority": 2,
            "status": "recommended_next_action",
            "title": "Launch the BTC M15 warp restore comparison shadow",
            "doctrine_alignment": "medium",
            "execution_read": "explicit_shadow_packet_ready" if not restore_packet else str(restore_packet.get("action_status") or "explicit_shadow_packet_ready"),
            "launch_status": str(restore_packet.get("action_status") or ""),
            "launch_read": str(restore_packet.get("action_read") or ""),
            "acceptance_verdict": str(restore_acceptance.get("verdict") or ""),
            "acceptance_read": str(restore_acceptance.get("candidate_read") or ""),
            "why": (
                f"{restore_candidate.get('action') or 'Launch the explicit restore-comparison shadow packet.'} "
                "This is the clean executable branch that preserves the live baseline while gathering fresh BTC evidence."
            ).strip(),
            "blockers": [],
            "allowed_inputs": [
                str(restore_candidate.get("lane") or "shadow_btcusd_m15_warp_restore_v1"),
                "reports/btc_m15_warp_restore_board.json",
            ],
        },
        {
            "branch_id": "define_true_adaptive_candidate_then_build",
            "priority": 3,
            "status": "doctrine_target_not_first_build",
            "title": "Define and build the true downtrend-aware adaptive BTC candidate",
            "doctrine_alignment": "high",
            "execution_read": (
                "monetization_aware_shadow_candidate"
                if plan_recommendation.get("recommended_shape_id")
                else "manual_review_shadow_candidate"
            ),
            "launch_status": "",
            "launch_read": "",
            "acceptance_verdict": str(target_acceptance.get("verdict") or ""),
            "acceptance_read": str(target_acceptance.get("candidate_read") or ""),
            "why": str(
                (handoff.get("summary") or {}).get("completion_read")
                or "The true adaptive branch is the doctrinal target, but it is not yet the first honest executable build."
            )
            + (
                f" Current plan recommendation is `{plan_recommendation.get('recommended_shape_id')}`"
                f" with objective read: {runtime_objective_context.get('objective_read')}"
                if plan_recommendation.get("recommended_shape_id") and runtime_objective_context.get("objective_read")
                else ""
            ),
            "review_read": str(step_review.get("review_read") or ""),
            "review_notes": step_review_notes,
            "blockers": plan_warnings
            + [
                "branch_not_yet_forward_proven",
                "restore_comparison_shadow_should_land_first",
            ],
            "allowed_inputs": [
                str(proposed_downtrend_shape.get("shape_id") or "btcusd_m15_bounce_down_v1"),
                "reports/btc_downtrend_handoff.json",
                str(plan_recommendation.get("recommended_shape_id") or ""),
            ],
        },
    ]

    recommended = next(row for row in rows if row["status"] == "recommended_next_action")
    doctrine_target = next(row for row in rows if row["status"] == "doctrine_target_not_first_build")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "branch_count": len(rows),
            "recommended_branch_id": recommended["branch_id"],
            "recommended_title": recommended["title"],
            "doctrine_target_branch_id": doctrine_target["branch_id"],
            "doctrine_target_title": doctrine_target["title"],
            "parked_runtime_status": str(audit.get("status") or ""),
            "restore_candidate_verdict": str(restore_candidate.get("verdict") or ""),
            "adaptive_plan_status": str(plan.get("status") or ""),
            "adaptive_plan_shape_id": str(plan_recommendation.get("recommended_shape_id") or ""),
            "adaptive_plan_close_conversion_pressure": bool(runtime_objective_context.get("close_conversion_pressure")),
            "recommended_branch_launch_status": str(recommended.get("launch_status") or ""),
            "recommended_branch_acceptance_verdict": str(recommended.get("acceptance_verdict") or ""),
            "doctrine_target_acceptance_verdict": str(doctrine_target.get("acceptance_verdict") or ""),
        },
        "leadership_read": [
            "BTC adaptive work currently splits into three different branches: parked artifact review, restore-comparison shadow, and true adaptive build.",
            (
                f"The currently chosen executable branch is `{recommended['branch_id']}`, and it is already in `{recommended.get('launch_status', 'ready')}` posture rather than waiting for a first launch."
                if recommended.get("launch_status")
                else f"The next honest executable branch is `{recommended['branch_id']}` because it already has a clean shadow packet and preserves the live baseline."
            ),
            (
                f"Checklist truth agrees: `{recommended['branch_id']}` is `{recommended.get('acceptance_verdict', '')}`, while `{doctrine_target['branch_id']}` remains `{doctrine_target.get('acceptance_verdict', '')}`."
                if recommended.get("acceptance_verdict") or doctrine_target.get("acceptance_verdict")
                else f"The pinned perfection doctrine still points toward `{doctrine_target['branch_id']}` as the higher-order target, but that branch remains branch-order-gated rather than the first clean launch."
            ),
            (
                f"The current BTC selector is already monetization-aware and now prefers `{plan_recommendation.get('recommended_shape_id')}` under `close_conversion_pressure={str(bool(runtime_objective_context.get('close_conversion_pressure'))).lower()}`."
                if plan_recommendation.get("recommended_shape_id")
                else "The current BTC selector has not produced a usable adaptive-shape recommendation yet."
            ),
            "Treat `hold_parked_artifact_only` as historical runtime context, not as the adaptive next move.",
        ],
        "sources": {
            "restore_board": str(RESTORE_BOARD_PATH.relative_to(ROOT)),
            "runtime_audit": str(RUNTIME_AUDIT_PATH.relative_to(ROOT)),
            "downtrend_handoff": str(DOWNTREND_HANDOFF_PATH.relative_to(ROOT)),
            "adaptive_plan": str(ADAPTIVE_PLAN_PATH.relative_to(ROOT)),
            "overnight_packet": str(OVERNIGHT_PACKET_PATH.relative_to(ROOT)),
            "acceptance_verdict": str(ACCEPTANCE_VERDICT_PATH.relative_to(ROOT)),
        },
        "rows": rows,
        "notes": [
            "This board is passive. It chooses between existing BTC adaptive branches; it does not launch or edit any lane.",
            "The recommended branch can differ from the doctrinal target branch. In the current repo state, restore comparison is the honest executable control branch while true adaptive build remains the strategic target.",
            "When the overnight packet already marks the recommended branch as running, read this board as branch-choice authority plus execution-state sync, not as a duplicate launch instruction.",
            "Use this board to keep `adaptive_lab_queue` and room planning from collapsing restore work, parked artifacts, and true adaptive-controller work into one label.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Adaptive BTC Branch Decision Board",
        "",
        "This board separates the three current BTC adaptive branches so the room can choose the next honest action explicitly.",
        "",
        f"- generated_at: `{payload.get('generated_at', '-')}`",
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
            f"- branch_count: `{summary.get('branch_count')}`",
            f"- recommended_branch_id: `{summary.get('recommended_branch_id')}`",
            f"- doctrine_target_branch_id: `{summary.get('doctrine_target_branch_id')}`",
            f"- parked_runtime_status: `{summary.get('parked_runtime_status')}`",
            f"- restore_candidate_verdict: `{summary.get('restore_candidate_verdict')}`",
            f"- adaptive_plan_status: `{summary.get('adaptive_plan_status')}`",
            f"- adaptive_plan_shape_id: `{summary.get('adaptive_plan_shape_id')}`",
            f"- adaptive_plan_close_conversion_pressure: `{summary.get('adaptive_plan_close_conversion_pressure')}`",
            f"- recommended_branch_launch_status: `{summary.get('recommended_branch_launch_status')}`",
            f"- recommended_branch_acceptance_verdict: `{summary.get('recommended_branch_acceptance_verdict')}`",
            f"- doctrine_target_acceptance_verdict: `{summary.get('doctrine_target_acceptance_verdict')}`",
            "",
            "## Rows",
            "",
            "| Branch | Status | Acceptance | Launch Status | Doctrine Alignment | Execution Read | Why |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload.get("rows", []):
        lines.append(
            f"| `{row['branch_id']}` | `{row['status']}` | `{row.get('acceptance_verdict', '')}` | `{row.get('launch_status', '')}` | `{row['doctrine_alignment']}` | "
            f"`{row['execution_read']}` | {row['why']} |"
        )

    lines.extend(["", "## Detail", ""])
    for row in payload.get("rows", []):
        lines.append(f"### {row['branch_id']}")
        lines.append(f"- title: `{row['title']}`")
        lines.append(f"- status: `{row['status']}`")
        lines.append(f"- doctrine_alignment: `{row['doctrine_alignment']}`")
        lines.append(f"- execution_read: `{row['execution_read']}`")
        if row.get("launch_status"):
            lines.append(f"- launch_status: `{row['launch_status']}`")
        if row.get("launch_read"):
            lines.append(f"- launch_read: {row['launch_read']}")
        if row.get("acceptance_verdict"):
            lines.append(f"- acceptance_verdict: `{row['acceptance_verdict']}`")
        if row.get("acceptance_read"):
            lines.append(f"- acceptance_read: {row['acceptance_read']}")
        lines.append(f"- why: {row['why']}")
        if row.get("allowed_inputs"):
            lines.append("- allowed inputs: " + ", ".join(f"`{item}`" for item in row["allowed_inputs"]))
        if row.get("review_read"):
            lines.append(f"- review read: {row['review_read']}")
        if row.get("review_notes"):
            lines.append("- review notes: " + ", ".join(f"`{item}`" for item in row["review_notes"]))
        if row.get("blockers"):
            lines.append("- blockers: " + ", ".join(f"`{item}`" for item in row["blockers"]))
        lines.append("")

    lines.extend(["## Notes", ""])
    for note in payload.get("notes", []):
        lines.append(f"- {note}")

    return "\n".join(lines) + "\n"


def main() -> int:
    payload = build_payload()
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
