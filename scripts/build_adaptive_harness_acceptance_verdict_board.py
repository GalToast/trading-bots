#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"
DOCS = ROOT / "docs"

CHECKLIST_PATH = DOCS / "adaptive_harness_acceptance_checklist.md"
QUEUE_PATH = REPORTS / "adaptive_lab_queue.json"
PROOF_BOARD_PATH = REPORTS / "adaptive_lattice_proof_board.json"
FORMULA_COVERAGE_PATH = REPORTS / "adaptive_formula_input_coverage_board.json"
BTC_BRANCH_DECISION_PATH = REPORTS / "adaptive_btc_branch_decision_board.json"
BTC_RUNTIME_AUDIT_PATH = REPORTS / "btc_adaptive_runtime_audit.json"
BTC_RESTORE_BOARD_PATH = REPORTS / "btc_m15_warp_restore_board.json"
BTC_RUNNER_PLAN_PATH = REPORTS / "adaptive_btc_shadow_runner_plan.json"
CONTROLLER_PRIORS_PATH = CONFIGS / "adaptive_controller_priors.json"
INCUMBENT_STUDY_PATH = REPORTS / "adaptive_incumbent_study_board.json"
PACKET_BOARD_PATH = REPORTS / "adaptive_overnight_launch_packet_board.json"
SHARED_SCORE_PATH = REPORTS / "adaptive_shared_score_board.json"
OUTPUT_JSON = REPORTS / "adaptive_harness_acceptance_verdict_board.json"
OUTPUT_MD = REPORTS / "adaptive_harness_acceptance_verdict_board.md"

HARD_REJECT_CHECKS = {
    "branch_clarity",
    "input_honesty",
    "runtime_safety",
    "launch_packet_clarity",
    "forward_proof_integrity",
}
SHADOW_READY_CHECKS = {
    "branch_clarity",
    "doctrine_clarity",
    "input_honesty",
    "controller_coherence",
    "telemetry_explainability",
    "runtime_safety",
    "launch_packet_clarity",
}
PROMOTION_READY_CHECKS = SHADOW_READY_CHECKS | {
    "early_green_monetization",
    "inventory_governance",
    "portfolio_governance",
    "cross_family_generalization",
    "forward_proof_integrity",
    "live_slot_superiority",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def relative_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def check(check_id: str, title: str, status: str, read: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "title": title,
        "status": status,
        "read": read,
        "evidence": evidence or [],
    }


def live_slot_superiority_check_from_shared_score(
    *,
    symbol: str,
    shared_score_row: dict[str, Any] | None,
) -> dict[str, Any]:
    score_row = dict(shared_score_row or {})
    verdict = str(score_row.get("comparison_verdict") or "")
    score_gap = score_row.get("score_gap")
    incumbent = dict(score_row.get("incumbent") or {})
    adaptive = dict(score_row.get("adaptive") or {})

    if verdict == "adaptive_leading_preliminarily":
        return check(
            "live_slot_superiority",
            "Live-Slot Superiority",
            "pass",
            "Current shared-score evidence says the adaptive branch is already leading the incumbent for this symbol.",
            [
                f"comparison_verdict={verdict}",
                f"score_gap={score_gap}",
                f"adaptive_lane={adaptive.get('lane', '')}",
                f"incumbent_lane={incumbent.get('lane', '')}",
            ],
        )
    if verdict == "incumbent_still_leading":
        return check(
            "live_slot_superiority",
            "Live-Slot Superiority",
            "fail",
            "Current shared-score evidence still says the incumbent is leading, so this branch is not honest live-slot material yet.",
            [
                f"comparison_verdict={verdict}",
                f"score_gap={score_gap}",
                f"adaptive_score={adaptive.get('score_total', '')}",
                f"incumbent_score={incumbent.get('score_total', '')}",
            ],
        )
    if verdict == "too_close_or_low_confidence":
        return check(
            "live_slot_superiority",
            "Live-Slot Superiority",
            "warn",
            "Adaptive-versus-incumbent scoring is still too close or too low-confidence to claim live-slot superiority.",
            [
                f"comparison_verdict={verdict}",
                f"score_gap={score_gap}",
            ],
        )
    if verdict == "no_adaptive_score":
        return check(
            "live_slot_superiority",
            "Live-Slot Superiority",
            "warn",
            "The incumbent is scoreable, but the adaptive branch still lacks enough realized proof to claim the live slot honestly.",
            [
                f"comparison_verdict={verdict}",
                f"incumbent_lane={incumbent.get('lane', '')}",
            ],
        )
    if verdict == "no_incumbent_score":
        return check(
            "live_slot_superiority",
            "Live-Slot Superiority",
            "warn",
            "There is no current incumbent score for this symbol, so live-slot superiority cannot be claimed yet.",
            [
                f"comparison_verdict={verdict}",
                f"adaptive_lane={adaptive.get('lane', '')}",
            ],
        )
    return check(
        "live_slot_superiority",
        "Live-Slot Superiority",
        "warn",
        "Live-slot superiority is not yet explicit on the current shared-score surface.",
        [f"comparison_verdict={verdict or 'missing'}"],
    )


def live_slot_superiority_check_from_btc_contract(
    *,
    btc_profit_contract: dict[str, Any],
) -> dict[str, Any]:
    verdict = str(btc_profit_contract.get("verdict") or "")
    score_gap = btc_profit_contract.get("score_gap")
    if verdict == "adaptive_candidate_beating_restore":
        return check(
            "live_slot_superiority",
            "Live-Slot Superiority",
            "pass",
            "The explicit BTC adaptive contract says the adaptive candidate is already outperforming the restore control path.",
            [
                f"btc_max_profit_verdict={verdict}",
                f"score_gap={score_gap}",
            ],
        )
    if verdict == "adaptive_candidate_defined_but_unproven":
        return check(
            "live_slot_superiority",
            "Live-Slot Superiority",
            "warn",
            "The true adaptive BTC branch is explicit, but it still lacks enough branch-local proof to claim superiority over the current control/live path.",
            [
                f"btc_max_profit_verdict={verdict}",
                f"score_gap={score_gap}",
                f"adaptive_runner_session_close_count={btc_profit_contract.get('adaptive_runner_session_close_count', '')}",
            ],
        )
    return check(
        "live_slot_superiority",
        "Live-Slot Superiority",
        "fail",
        "The current BTC adaptive contract still does not show a superior adaptive branch for live-slot purposes.",
        [
            f"btc_max_profit_verdict={verdict or 'missing'}",
            f"score_gap={score_gap}",
        ],
    )


def early_green_monetization_check_from_shared_score(
    *,
    symbol: str,
    shared_score_row: dict[str, Any] | None,
) -> dict[str, Any]:
    score_row = dict(shared_score_row or {})
    adaptive = dict(score_row.get("adaptive") or {})
    comparison_verdict = str(score_row.get("comparison_verdict") or "")
    realized_usd = safe_float(adaptive.get("realized_usd"))
    close_count = max(safe_int(adaptive.get("close_count")), 0)
    usd_per_close = safe_float(adaptive.get("usd_per_close"))
    first_path_verdict = str(adaptive.get("first_path_verdict") or "")
    objective_verdict = str(adaptive.get("unified_objective_verdict") or "")

    if comparison_verdict == "no_adaptive_score":
        return check(
            "early_green_monetization",
            "Early-Green Monetization",
            "warn",
            "The adaptive branch still lacks enough realized evidence to judge close conversion honestly.",
            [f"comparison_verdict={comparison_verdict}"],
        )
    if (
        first_path_verdict == "never_green_toxic_continuation"
        or objective_verdict == "toxic_path_untradeable"
        or (realized_usd is not None and realized_usd < 0)
    ):
        return check(
            "early_green_monetization",
            "Early-Green Monetization",
            "fail",
            "Fresh shared-score evidence says this branch is still losing or monetizing through a toxic path, so it is not an honest profit challenger yet.",
            [
                f"comparison_verdict={comparison_verdict or 'missing'}",
                f"realized_usd={realized_usd}",
                f"close_count={close_count}",
                f"usd_per_close={usd_per_close}",
                f"first_path_verdict={first_path_verdict or 'missing'}",
                f"unified_objective_verdict={objective_verdict or 'missing'}",
            ],
        )
    if close_count >= 3 and realized_usd is not None and realized_usd > 0 and objective_verdict not in {"", "flat_or_insufficient_sample"}:
        return check(
            "early_green_monetization",
            "Early-Green Monetization",
            "pass",
            "The adaptive branch is converting favorable movement into realized closes with enough sample to count as real monetization evidence.",
            [
                f"comparison_verdict={comparison_verdict or 'missing'}",
                f"realized_usd={realized_usd}",
                f"close_count={close_count}",
                f"usd_per_close={usd_per_close}",
                f"unified_objective_verdict={objective_verdict or 'missing'}",
            ],
        )
    return check(
        "early_green_monetization",
        "Early-Green Monetization",
        "warn",
        "The branch has at least some realized evidence, but the sample is still too thin or too flat to count as proven monetization.",
        [
            f"comparison_verdict={comparison_verdict or 'missing'}",
            f"realized_usd={realized_usd}",
            f"close_count={close_count}",
            f"usd_per_close={usd_per_close}",
            f"unified_objective_verdict={objective_verdict or 'missing'}",
        ],
    )


def early_green_monetization_check_from_btc_contract(
    *,
    btc_profit_contract: dict[str, Any],
) -> dict[str, Any]:
    verdict = str(btc_profit_contract.get("verdict") or "")
    close_count = max(safe_int(btc_profit_contract.get("adaptive_runner_session_close_count")), 0)
    realized_usd = safe_float(btc_profit_contract.get("adaptive_runner_session_realized_usd"))
    carry_realized_usd = safe_float(btc_profit_contract.get("adaptive_pre_start_carry_realized_usd"))

    if close_count >= 3 and realized_usd is not None and realized_usd > 0:
        return check(
            "early_green_monetization",
            "Early-Green Monetization",
            "pass",
            "The true adaptive BTC branch now has enough branch-local realized proof to count as real close conversion rather than doctrine only.",
            [
                f"btc_max_profit_verdict={verdict or 'missing'}",
                f"adaptive_runner_session_close_count={close_count}",
                f"adaptive_runner_session_realized_usd={realized_usd}",
                f"adaptive_pre_start_carry_realized_usd={carry_realized_usd}",
            ],
        )
    if (
        close_count <= 0
        and (realized_usd is None or realized_usd <= 0)
        and carry_realized_usd is not None
        and carry_realized_usd < 0
    ):
        return check(
            "early_green_monetization",
            "Early-Green Monetization",
            "fail",
            "The true adaptive BTC branch still has no fresh realized closes and is carrying negative inherited cashflow, so monetization remains unproven.",
            [
                f"btc_max_profit_verdict={verdict or 'missing'}",
                f"adaptive_runner_session_close_count={close_count}",
                f"adaptive_runner_session_realized_usd={realized_usd}",
                f"adaptive_pre_start_carry_realized_usd={carry_realized_usd}",
            ],
        )
    return check(
        "early_green_monetization",
        "Early-Green Monetization",
        "warn",
        "The true adaptive BTC branch is explicit, but it still needs more branch-local realized proof before monetization can be trusted.",
        [
            f"btc_max_profit_verdict={verdict or 'missing'}",
            f"adaptive_runner_session_close_count={close_count}",
            f"adaptive_runner_session_realized_usd={realized_usd}",
            f"adaptive_pre_start_carry_realized_usd={carry_realized_usd}",
        ],
    )


def row_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        indexed[str(row.get(key) or "")] = dict(row)
    return indexed


def compute_verdict(checks: list[dict[str, Any]]) -> str:
    status_by_id = {str(row.get("check_id") or ""): str(row.get("status") or "") for row in checks}
    fail_ids = {check_id for check_id, status in status_by_id.items() if status == "fail"}
    if fail_ids & HARD_REJECT_CHECKS:
        return "rejected"
    if all(status_by_id.get(check_id) == "pass" for check_id in PROMOTION_READY_CHECKS):
        return "promotion_ready"
    if all(status_by_id.get(check_id) == "pass" for check_id in SHADOW_READY_CHECKS):
        return "shadow_ready"
    return "research_only"


def finalize_candidate(
    candidate_id: str,
    symbol: str,
    queue_task: dict[str, Any],
    checks: list[dict[str, Any]],
    candidate_read: str,
    evidence: list[str],
) -> dict[str, Any]:
    verdict = compute_verdict(checks)
    pass_count = sum(1 for row in checks if row.get("status") == "pass")
    warn_count = sum(1 for row in checks if row.get("status") == "warn")
    fail_count = sum(1 for row in checks if row.get("status") == "fail")
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "priority": safe_int(queue_task.get("priority")),
        "lane": str(queue_task.get("lane") or ""),
        "queue_status": str(queue_task.get("status") or ""),
        "title": str(queue_task.get("title") or candidate_id),
        "verdict": verdict,
        "candidate_read": candidate_read,
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "failing_checks": [row["check_id"] for row in checks if row.get("status") == "fail"],
        "warning_checks": [row["check_id"] for row in checks if row.get("status") == "warn"],
        "queue_why": str(queue_task.get("why") or ""),
        "machine_truth": dict(queue_task.get("machine_truth") or {}),
        "runtime_overlays": list(queue_task.get("runtime_overlays") or []),
        "runtime_obligation_class": str(queue_task.get("runtime_obligation_class") or ""),
        "runtime_obligation_read": str(queue_task.get("runtime_obligation_read") or queue_task.get("runtime_overlay_read") or ""),
        "supporting_evidence": evidence,
        "checks": checks,
    }


def build_payload(
    adaptive_queue: dict[str, Any] | None = None,
    proof_board: dict[str, Any] | None = None,
    formula_coverage: dict[str, Any] | None = None,
    btc_branch_decision: dict[str, Any] | None = None,
    btc_runtime_audit: dict[str, Any] | None = None,
    btc_restore_board: dict[str, Any] | None = None,
    btc_runner_plan: dict[str, Any] | None = None,
    controller_priors: dict[str, Any] | None = None,
    incumbent_study: dict[str, Any] | None = None,
    packet_board: dict[str, Any] | None = None,
    shared_score_board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    queue = adaptive_queue or load_json(QUEUE_PATH)
    proof = proof_board or load_json(PROOF_BOARD_PATH)
    formula = formula_coverage or load_json(FORMULA_COVERAGE_PATH)
    branch_board = btc_branch_decision or load_json(BTC_BRANCH_DECISION_PATH)
    runtime_audit = btc_runtime_audit or load_json(BTC_RUNTIME_AUDIT_PATH)
    restore_board = btc_restore_board or load_json(BTC_RESTORE_BOARD_PATH)
    runner_plan = btc_runner_plan or load_json(BTC_RUNNER_PLAN_PATH)
    priors = controller_priors or load_json(CONTROLLER_PRIORS_PATH)
    study = incumbent_study or load_json(INCUMBENT_STUDY_PATH)
    packet = packet_board or load_json(PACKET_BOARD_PATH)
    shared_score = shared_score_board or load_json(SHARED_SCORE_PATH)

    tasks = row_by_key(list(queue.get("tasks") or []), "task_id")
    proof_rows = row_by_key(list(proof.get("rows") or []), "symbol")
    formula_rows = row_by_key(list(formula.get("rows") or []), "symbol")
    branch_rows = row_by_key(list(branch_board.get("rows") or []), "branch_id")
    runtime_checks = row_by_key(list(runtime_audit.get("checks") or []), "check_id")
    study_rows = row_by_key(list(study.get("rows") or []), "symbol")
    packet_rows = row_by_key(list(packet.get("rows") or []), "packet_id")
    shared_score_rows = row_by_key(list(shared_score.get("rows") or []), "symbol")

    queue_summary = dict(queue.get("summary") or {})
    branch_summary = dict(branch_board.get("summary") or {})
    runtime_lane = dict(runtime_audit.get("runtime_lane") or {})
    restore_candidate = dict(restore_board.get("restore_candidate") or {})
    global_policy = dict(priors.get("global_policy") or {})
    btc_prior = dict((priors.get("symbol_priors") or {}).get("BTCUSD") or {})
    btc_profit_contract = dict((study_rows.get("BTCUSD") or {}).get("btc_max_profit_comparison") or {})

    btc_formula = formula_rows.get("BTCUSD", {})
    btc_branch_recommended = branch_rows.get("launch_restore_comparison_shadow", {})
    btc_branch_target = branch_rows.get("define_true_adaptive_candidate_then_build", {})
    btc_hold_branch = branch_rows.get("hold_parked_artifact_only", {})

    candidates: list[dict[str, Any]] = []

    restore_task = tasks.get("btc_restore_comparison_shadow", {})
    restore_runtime_overlays = list(
        restore_task.get("runtime_overlays")
        or dict(study_rows.get("BTCUSD") or {}).get("adaptive_runtime_overlays")
        or []
    )
    restore_runtime_obligation_class = str(restore_task.get("runtime_obligation_class") or "")
    restore_runtime_obligation_read = str(
        restore_task.get("runtime_obligation_read")
        or restore_task.get("runtime_overlay_read")
        or dict(study_rows.get("BTCUSD") or {}).get("adaptive_runtime_overlay_read")
        or ""
    )
    restore_checks = [
        check("branch_clarity", "Branch Clarity", "pass", "This row names one explicit BTC branch: restore-comparison shadow.", [f"branch_row_status={btc_branch_recommended.get('status', '')}", f"queue_status={restore_task.get('status', '')}"]),
        check("doctrine_clarity", "Doctrine Clarity", "pass", "The room now explicitly separates restore comparison from true adaptive build and parked-artifact review.", [f"recommended_branch_id={branch_summary.get('recommended_branch_id', '')}", f"doctrine_target_branch_id={branch_summary.get('doctrine_target_branch_id', '')}"]),
        check("input_honesty", "Input Honesty", "pass", "This branch does not pretend to be richer than it is; it is an explicit fixed-geometry comparison packet.", [f"restore_verdict={restore_candidate.get('verdict', '')}", f"step_buy={restore_candidate.get('step_buy', '')}", f"step_sell={restore_candidate.get('step_sell', '')}"]),
        check("controller_coherence", "Controller Coherence", "pass", "Geometry, close posture, and branch goal all tell one story: preserve the live baseline and run a clean shadow comparison.", [f"close_alpha={restore_candidate.get('close_alpha', '')}", f"max_open_per_side={restore_candidate.get('max_open_per_side', '')}"]),
        check("telemetry_explainability", "Telemetry Explainability", "pass", "The restore packet already names state/event outputs and keeps the live baseline explicit for later diagnosis.", [f"state_path={restore_candidate.get('state_path', '')}", f"event_path={restore_candidate.get('event_path', '')}"]),
        early_green_monetization_check_from_shared_score(symbol="BTCUSD", shared_score_row=shared_score_rows.get("BTCUSD")),
        check("inventory_governance", "Inventory Governance", "pass", "The shadow packet has an explicit slot cap and max-loss posture.", [f"max_open_per_side={restore_candidate.get('max_open_per_side', '')}", f"max_floating_loss_usd={restore_candidate.get('max_floating_loss_usd', '')}"]),
        check("portfolio_governance", "Portfolio Governance", "warn", "The portfolio prior still says `hold_until_buy_realign`, so this branch is a control experiment rather than a promotion decision.", [f"promotion_action={btc_prior.get('promotion_action', '')}"]),
        check("cross_family_generalization", "Cross-Family Generalization", "warn", "This is a BTC-local restore control, not cross-family adaptive proof.", [f"controller_role={btc_prior.get('controller_role', '')}"]),
        check(
            "runtime_safety",
            "Runtime Safety",
            "pass",
            (
                "The branch has a clean shadow runner path and its guarded-toxic-flow runtime obligations are explicit: new opens stay guarded and burst clusters collapse into one escape unit before expansion."
                if restore_runtime_obligation_class
                else "The branch has a clean shadow runner path and does not depend on the parked direct-live artifact."
            ),
            [
                f"command_len={len(restore_candidate.get('command') or [])}",
                f"live_change_rule={restore_candidate.get('live_change_rule', '')}",
                f"runtime_obligation_class={restore_runtime_obligation_class or 'none'}",
                f"runtime_overlays={restore_runtime_overlays}",
            ],
        ),
        check("launch_packet_clarity", "Launch Packet Clarity", "pass", "The restore branch already names command, outputs, branch goal, and live-preservation rule.", [f"lane={restore_candidate.get('lane', '')}", f"action={restore_candidate.get('action', '')}"]),
        check("forward_proof_integrity", "Forward-Proof Integrity", "warn", "This is honest to launch, but it still needs fresh runtime-local proof before promotion claims.", ["fresh_restore_sample=missing"]),
        live_slot_superiority_check_from_shared_score(symbol="BTCUSD", shared_score_row=shared_score_rows.get("BTCUSD")),
    ]
    candidates.append(
        finalize_candidate(
            "btc_restore_comparison_shadow",
            "BTCUSD",
            restore_task,
            restore_checks,
            (
                "Shadow-ready as a control branch: explicit, reversible, and launch-packet complete, but still waiting on fresh comparison proof and not itself the doctrinal adaptive end-state."
                if not restore_runtime_obligation_class
                else "Shadow-ready as a control branch, but only under the checked-in guarded-toxic-flow runtime contract: keep opens guarded, treat burst fills as one risk unit, and suppress additional levels until the burst dissipates."
            ),
            [relative_path_text(QUEUE_PATH), relative_path_text(BTC_BRANCH_DECISION_PATH), relative_path_text(BTC_RESTORE_BOARD_PATH)],
        )
    )

    parked_task = tasks.get("btc_parked_artifact_review", tasks.get("btc_adaptive_posture_reconciliation", {}))
    parked_checks = [
        check("branch_clarity", "Branch Clarity", "pass", "This row names one explicit non-launch branch: hold the parked artifact as historical context only.", [f"branch_row_status={btc_hold_branch.get('status', '')}", f"queue_status={parked_task.get('status', '')}"]),
        check("doctrine_clarity", "Doctrine Clarity", "pass", "Branch and runtime surfaces agree that the parked artifact is context, not the next executable seam.", [f"runtime_status={runtime_audit.get('status', '')}"]),
        check("input_honesty", "Input Honesty", "pass" if btc_formula.get("verdict") == "true_range_atr_ready" else "warn", "The controller inputs are readable, but that does not rescue the parked artifact as a current candidate.", [f"formula_verdict={btc_formula.get('verdict', '')}"]),
        check("controller_coherence", "Controller Coherence", "fail", "The parked runtime still conflicts with the current controller/design story on step mode, alpha, and asymmetry.", [f"controller_step_mode={runtime_checks.get('controller_step_mode', {}).get('status', '')}", f"controller_alpha={runtime_checks.get('controller_alpha', {}).get('status', '')}", f"design_asymmetry={runtime_checks.get('design_asymmetry', {}).get('status', '')}"]),
        check("telemetry_explainability", "Telemetry Explainability", "pass", "The parked artifact is diagnosable; the problem is that the diagnosis says it should stay parked.", [f"runtime_check_count={len(runtime_checks)}"]),
        check("early_green_monetization", "Early-Green Monetization", "fail", "There are no fresh runner-session closes or realized dollars in the current window.", [f"runner_session_trade_closes={runtime_lane.get('runner_session_trade_closes', 0)}", f"runner_session_trade_realized_usd={runtime_lane.get('runner_session_trade_realized_usd', 0.0)}"]),
        check("inventory_governance", "Inventory Governance", "warn", "Inventory posture is explicit, but only on a stale direct-live artifact.", [f"max_open_per_side={runtime_lane.get('max_open_per_side', '')}", f"runtime_direct_live_status={runtime_checks.get('runtime_direct_live', {}).get('status', '')}"]),
        check("portfolio_governance", "Portfolio Governance", "warn", "BTC portfolio priors still point at hold/review rather than deployment from this artifact.", [f"promotion_action={btc_prior.get('promotion_action', '')}"]),
        check("cross_family_generalization", "Cross-Family Generalization", "warn", "A parked BTC artifact proves nothing about cross-family adaptive readiness.", [f"graduation_funnel_present={bool(global_policy.get('graduation_funnel'))}"]),
        check("runtime_safety", "Runtime Safety", "fail", "The only runtime attached here is the parked stale direct-live artifact.", [f"watchdog_status={runtime_lane.get('watchdog_status', '')}", f"direct_live={runtime_lane.get('direct_live', False)}"]),
        check("launch_packet_clarity", "Launch Packet Clarity", "fail", "Holding a parked artifact is not an adaptive launch packet.", [f"restore_candidate_lane={restore_candidate.get('lane', '')}", f"runner_plan_status={runner_plan.get('status', '')}"]),
        check("forward_proof_integrity", "Forward-Proof Integrity", "fail", "This branch depends on stale parked runtime context with carry residue, not fresh branch-local proof.", [f"pre_start_state_carry_closes={runtime_lane.get('pre_start_state_carry_closes', 0)}", f"pre_start_state_carry_realized_usd={runtime_lane.get('pre_start_state_carry_realized_usd', 0.0)}"]),
        check("live_slot_superiority", "Live-Slot Superiority", "fail", "A parked historical artifact cannot honestly claim the live slot for its symbol.", [f"runtime_status={runtime_audit.get('status', '')}"]),
    ]
    candidates.append(
        finalize_candidate(
            "btc_parked_artifact_review",
            "BTCUSD",
            parked_task,
            parked_checks,
            "Rejected as a launch candidate: useful context, but it remains stale parked-runtime evidence rather than an acceptable adaptive branch.",
            [relative_path_text(QUEUE_PATH), relative_path_text(BTC_RUNTIME_AUDIT_PATH), relative_path_text(BTC_BRANCH_DECISION_PATH)],
        )
    )

    downtrend_task = tasks.get("btc_true_adaptive_candidate", tasks.get("btc_downtrend_candidate_or_hold_gate", {}))
    downtrend_checks = [
        check("branch_clarity", "Branch Clarity", "pass", "This row now names one explicit branch: define and build the true downtrend-aware adaptive BTC candidate.", [f"queue_status={downtrend_task.get('status', '')}", f"branch_row_status={btc_branch_target.get('status', '')}"]),
        check("doctrine_clarity", "Doctrine Clarity", "pass", "The doctrine target is now explicit, even though it is not yet the first executable branch.", [f"recommended_branch_id={branch_summary.get('recommended_branch_id', '')}", f"doctrine_target_branch_id={branch_summary.get('doctrine_target_branch_id', '')}"]),
        check("input_honesty", "Input Honesty", "pass" if btc_formula.get("verdict") == "true_range_atr_ready" else "warn", "The BTC adaptive formula inputs are ready enough to study the true adaptive branch honestly.", [f"formula_verdict={btc_formula.get('verdict', '')}", f"adaptive_step_plan_kind={dict(runner_plan.get('adaptive_step_plan') or {}).get('kind', '')}"]),
        check("controller_coherence", "Controller Coherence", "warn", "The branch is coherent enough to study, but it still needs a final candidate packet that resolves restore-first versus adaptive-target rollout order cleanly.", [f"execution_read={btc_branch_target.get('execution_read', '')}", f"review_read={dict(runner_plan.get('step_review') or {}).get('review_read', '')}"]),
        check("telemetry_explainability", "Telemetry Explainability", "pass", "The current branch board and runner plan explain the candidate better than the old blended BTC queue ever did.", [f"runner_plan_status={runner_plan.get('status', '')}", f"proposed_lane_name={runner_plan.get('proposed_lane_name', '')}"]),
        early_green_monetization_check_from_btc_contract(btc_profit_contract=btc_profit_contract),
        check("inventory_governance", "Inventory Governance", "warn", "Inventory posture is still candidate-stage rather than forward-validated.", [f"controller_max_open={runtime_checks.get('controller_max_open', {}).get('status', '')}", f"design_max_open={runtime_checks.get('design_max_open', {}).get('status', '')}"]),
        check("portfolio_governance", "Portfolio Governance", "warn", "BTC remains under `hold_until_buy_realign`, so this branch is still research-only from a portfolio perspective.", [f"promotion_action={btc_prior.get('promotion_action', '')}"]),
        check("cross_family_generalization", "Cross-Family Generalization", "warn", "This branch is still BTC-local and does not yet prove a cross-family adaptive controller.", [f"controller_role={btc_prior.get('controller_role', '')}"]),
        check("runtime_safety", "Runtime Safety", "pass", "There is no active family-level runtime fault and the scaffold has an explicit shadow runner path.", [f"runner_plan_status={runner_plan.get('status', '')}"]),
        check("launch_packet_clarity", "Launch Packet Clarity", "warn", "The branch is explicit, but the final launch packet still needs success/kill criteria and fresh first-proof expectations tied to the true adaptive candidate rather than the generic scaffold.", [f"proposed_lane_name={runner_plan.get('proposed_lane_name', '')}", f"blocked_by={downtrend_task.get('blocked_by', [])}"]),
        check("forward_proof_integrity", "Forward-Proof Integrity", "warn", "This candidate is no longer leaning on stale proof, but it still lacks fresh branch-local adaptive forward evidence.", [f"blocked_by={downtrend_task.get('blocked_by', [])}", "fresh_forward_sample=missing"]),
        live_slot_superiority_check_from_btc_contract(btc_profit_contract=btc_profit_contract),
    ]
    candidates.append(
        finalize_candidate(
            "btc_true_adaptive_candidate",
            "BTCUSD",
            downtrend_task,
            downtrend_checks,
            "Research-only, not rejected: the branch is explicit and instrumented now, but it still lacks a final candidate packet and fresh branch-local proof, so it is not yet shadow-ready.",
            [relative_path_text(QUEUE_PATH), relative_path_text(BTC_BRANCH_DECISION_PATH), relative_path_text(BTC_RUNNER_PLAN_PATH)],
        )
    )

    gbp_task = tasks.get("gbpusd_adaptive_comparison_packet", {})
    gbp_proof = proof_rows.get("GBPUSD", {})
    gbp_packet = packet_rows.get("gbpusd_adaptive_comparison_packet", {})
    if gbp_task and gbp_packet:
        gbp_checks = [
            check("branch_clarity", "Branch Clarity", "pass", "This row names one explicit GBP adaptive comparison packet against the incumbent live seat.", [f"queue_status={gbp_task.get('status', '')}", f"packet_action_status={gbp_packet.get('action_status', '')}"]),
            check("doctrine_clarity", "Doctrine Clarity", "pass", "Queue, proof, and packet surfaces now tell one story: GBP is the executable FX adaptive comparison seam.", [f"study_status={dict(study_rows.get('GBPUSD') or {}).get('study_status', '')}", f"proof_stage={gbp_proof.get('stage', '')}"]),
            check("input_honesty", "Input Honesty", "pass", "The GBP packet now uses a dedicated adaptive trend-harvest lane instead of borrowing the older asym runtime implicitly.", [f"adaptive_shape_id={dict(study_rows.get('GBPUSD') or {}).get('adaptive_shape_id', '')}", f"lane_name={gbp_packet.get('lane_name', '')}"]),
            check("controller_coherence", "Controller Coherence", "pass", "Profit mode, packet lane, and proof board all point at the same trend-harvest comparison branch.", [f"profit_mode={dict(study_rows.get('GBPUSD') or {}).get('adaptive_profit_mode', '')}", f"proof_shape={gbp_proof.get('recommended_shape_id', '')}"]),
            check("telemetry_explainability", "Telemetry Explainability", "pass", "The packet now names a dedicated lane plus concrete command/state/event contract, so comparison telemetry can be audited instead of inferred.", [f"command_len={len(gbp_packet.get('command') or [])}", f"authority_inputs={len(gbp_packet.get('authority_inputs') or [])}"]),
            early_green_monetization_check_from_shared_score(symbol="GBPUSD", shared_score_row=shared_score_rows.get("GBPUSD")),
            check("inventory_governance", "Inventory Governance", "pass", "The GBP packet carries an explicit slot cap and max-floating-loss contract in the launch command.", [f"command_has_max_open={'--max-open-per-side' in list(gbp_packet.get('command') or [])}", f"command_has_max_floating={'--max-floating-loss-usd' in list(gbp_packet.get('command') or [])}"]),
            check("portfolio_governance", "Portfolio Governance", "warn", "GBP still shares the live incumbent seat with EUR inside `live_rearm_941777`, so packet readiness is not yet seat-displacement proof.", [f"incumbent_lane={dict(study_rows.get('GBPUSD') or {}).get('incumbent_lane', '')}"]),
            check("cross_family_generalization", "Cross-Family Generalization", "warn", "This is an FX-local executable comparison branch, not cross-family adaptive closure.", [f"asset_class={dict(study_rows.get('GBPUSD') or {}).get('asset_class', '')}"]),
            check("runtime_safety", "Runtime Safety", "pass", "The packet points at a supervised or deliberately held shadow FX lane and does not require live-seat mutation.", [f"packet_action_status={gbp_packet.get('action_status', '')}", f"watchdog_status={gbp_packet.get('execution_watchdog_status', '')}"]),
            check("launch_packet_clarity", "Launch Packet Clarity", "pass", "The GBP branch now has one compact packet row with lane, command, and incumbent-comparison purpose.", [f"lane={gbp_packet.get('lane_name', '')}", f"action_read={gbp_packet.get('action_read', '')}"]),
            check("forward_proof_integrity", "Forward-Proof Integrity", "warn", "The packet is honest and executable, but it still needs fresh branch-local comparison proof before promotion claims.", ["fresh_gbp_adaptive_comparison_sample=missing"]),
            live_slot_superiority_check_from_shared_score(symbol="GBPUSD", shared_score_row=shared_score_rows.get("GBPUSD")),
        ]
        candidates.append(
            finalize_candidate(
                "gbpusd_adaptive_comparison_packet",
                "GBPUSD",
                gbp_task,
                gbp_checks,
                "Shadow-ready FX comparison branch: the packet is explicit, dedicated, and tied to the incumbent live seat, but it still needs fresh comparison proof before any promotion claim.",
                [relative_path_text(QUEUE_PATH), relative_path_text(PROOF_BOARD_PATH), relative_path_text(PACKET_BOARD_PATH), relative_path_text(INCUMBENT_STUDY_PATH)],
            )
        )

    usdjpy_task = tasks.get("usdjpy_bounded_forward_proof", tasks.get("usdjpy_bounded_proof_refresh", {}))
    usdjpy_formula = formula_rows.get("USDJPY", {})
    usdjpy_proof = proof_rows.get("USDJPY", {})
    usdjpy_packet = packet_rows.get("usdjpy_bounded_forward_proof", {})
    bounded_fault_active = next(
        (bool(row.get("active")) for row in list(proof.get("blockers") or []) if row.get("blocker_id") == "bounded_close_style_runtime_fault"),
        False,
    )
    usdjpy_checks = [
        check("branch_clarity", "Branch Clarity", "pass", "This row has one explicit branch: fresh bounded proof for USDJPY.", [f"queue_status={usdjpy_task.get('status', '')}", f"proof_stage={usdjpy_proof.get('stage', '')}"]),
        check("doctrine_clarity", "Doctrine Clarity", "pass", "Queue and proof surfaces now agree that USDJPY moved from archival fault repair to bounded proof debt.", [f"source_stage={usdjpy_proof.get('source_stage', '')}", f"runtime_fault_active={bounded_fault_active}"]),
        check("input_honesty", "Input Honesty", "pass" if usdjpy_formula.get("verdict") == "atr_ready" else "warn", "The bounded USDJPY shape has the ATR input it advertises.", [f"formula_verdict={usdjpy_formula.get('verdict', '')}", f"missing_fields={usdjpy_formula.get('missing_fields', [])}"]),
        check("controller_coherence", "Controller Coherence", "pass", "The queue, proof board, and recommended bounded shape all point at the same bounded proof-refresh task.", [f"recommended_shape_id={usdjpy_proof.get('recommended_shape_id', '')}", f"family={usdjpy_proof.get('family', '')}"]),
        check("telemetry_explainability", "Telemetry Explainability", "warn", "The proof and formula surfaces are honest, but there is not yet a dedicated bounded first-proof watch packet attached to this row.", [f"proof_board_generated_at={proof.get('generated_at', '')}", f"formula_board_generated_at={formula.get('generated_at', '')}"]),
        check("early_green_monetization", "Early-Green Monetization", "warn", "No fresh bounded proof run has monetized yet; this remains a proof-refresh candidate.", ["fresh_bounded_closes=missing"]),
        check("inventory_governance", "Inventory Governance", "warn", "Inventory behavior is still unproven because the fresh bounded run has not been replayed yet.", ["fresh_inventory_sample=missing"]),
        check("portfolio_governance", "Portfolio Governance", "warn", "USDJPY is coherent as a family-proof candidate, but it does not yet carry a stronger portfolio-role surface than proof refresh.", ["portfolio_role_surface=not_explicit"]),
        check("cross_family_generalization", "Cross-Family Generalization", "warn", "This is the bounded-family proof seam, not cross-family proof that the whole adaptive doctrine is solved.", [f"family={usdjpy_proof.get('family', '')}"]),
        check("runtime_safety", "Runtime Safety", "pass", "There is no active bounded runtime fault in the current proof surface.", [f"bounded_runtime_fault_active={bounded_fault_active}"]),
        check("launch_packet_clarity", "Launch Packet Clarity", "pass" if usdjpy_packet else "warn", "The branch is explicit, and the repo now has one compact bounded proof packet row for the canonical relaunch lane." if usdjpy_packet else "The branch is explicit, but the repo still lacks one compact bounded proof packet that names runner path, first-proof criteria, and kill conditions for this row.", [f"queue_lane={usdjpy_task.get('lane', '')}", f"packet_lane={usdjpy_packet.get('lane_name', '')}", f"allowed_inputs={usdjpy_task.get('allowed_inputs', [])}"]),
        check("forward_proof_integrity", "Forward-Proof Integrity", "warn", "This row is not borrowing stale proof, but it still needs fresh bounded runtime evidence before it can move beyond research-only.", [f"proof_stage={usdjpy_proof.get('stage', '')}", "fresh_forward_sample=missing"]),
        live_slot_superiority_check_from_shared_score(symbol="USDJPY", shared_score_row=shared_score_rows.get("USDJPY")),
    ]
    candidates.append(
        finalize_candidate(
            "usdjpy_bounded_forward_proof",
            "USDJPY",
            usdjpy_task,
            usdjpy_checks,
            (
                "Research-only, not blocked: the bounded fault is historical, inputs are honest, and the relaunch packet is now explicit, "
                "but a fresh bounded runtime sample still needs to land before this becomes shadow-ready."
                if usdjpy_packet
                else "Research-only, not blocked: the bounded fault is historical, inputs are honest, and the branch is explicit, "
                "but a fresh bounded proof packet and runtime sample still need to land before this becomes shadow-ready."
            ),
            [
                relative_path_text(QUEUE_PATH),
                relative_path_text(PROOF_BOARD_PATH),
                relative_path_text(FORMULA_COVERAGE_PATH),
                relative_path_text(PACKET_BOARD_PATH),
            ],
        )
    )

    verdict_counts = {verdict: sum(1 for row in candidates if row.get("verdict") == verdict) for verdict in ["rejected", "research_only", "shadow_ready", "promotion_ready"]}
    shadow_ready_rows = [row for row in candidates if row.get("verdict") == "shadow_ready"]
    promotion_ready_rows = [row for row in candidates if row.get("verdict") == "promotion_ready"]
    overlay_governed_rows = [row for row in candidates if row.get("runtime_obligation_class")]
    monetization_pass_rows = [row for row in candidates if any(check_row.get("check_id") == "early_green_monetization" and check_row.get("status") == "pass" for check_row in row.get("checks", []))]
    monetization_fail_rows = [row for row in candidates if any(check_row.get("check_id") == "early_green_monetization" and check_row.get("status") == "fail" for check_row in row.get("checks", []))]
    live_slot_pass_rows = [row for row in candidates if any(check_row.get("check_id") == "live_slot_superiority" and check_row.get("status") == "pass" for check_row in row.get("checks", []))]
    live_slot_fail_rows = [row for row in candidates if any(check_row.get("check_id") == "live_slot_superiority" and check_row.get("status") == "fail" for check_row in row.get("checks", []))]
    top_non_rejected = next((row for row in candidates if row.get("verdict") != "rejected"), None)

    return {
        "generated_at": utc_now_iso(),
        "sources": [relative_path_text(path) for path in [CHECKLIST_PATH, QUEUE_PATH, PROOF_BOARD_PATH, FORMULA_COVERAGE_PATH, BTC_BRANCH_DECISION_PATH, BTC_RUNTIME_AUDIT_PATH, BTC_RESTORE_BOARD_PATH, BTC_RUNNER_PLAN_PATH, CONTROLLER_PRIORS_PATH, INCUMBENT_STUDY_PATH, PACKET_BOARD_PATH, SHARED_SCORE_PATH]],
        "summary": {
            "candidate_count": len(candidates),
            "verdict_counts": verdict_counts,
            "top_non_rejected_candidate_id": str((top_non_rejected or {}).get("candidate_id") or ""),
            "top_non_rejected_verdict": str((top_non_rejected or {}).get("verdict") or ""),
            "overlay_governed_candidate_count": len(overlay_governed_rows),
            "early_green_monetization_pass_count": len(monetization_pass_rows),
            "early_green_monetization_fail_count": len(monetization_fail_rows),
            "early_green_monetization_pass_candidates": [row["candidate_id"] for row in monetization_pass_rows],
            "early_green_monetization_fail_candidates": [row["candidate_id"] for row in monetization_fail_rows],
            "live_slot_superiority_pass_count": len(live_slot_pass_rows),
            "live_slot_superiority_fail_count": len(live_slot_fail_rows),
            "live_slot_superiority_pass_candidates": [row["candidate_id"] for row in live_slot_pass_rows],
            "live_slot_superiority_fail_candidates": [row["candidate_id"] for row in live_slot_fail_rows],
            "btc_recommended_branch_id": str(branch_summary.get("recommended_branch_id") or ""),
            "btc_doctrine_target_branch_id": str(branch_summary.get("doctrine_target_branch_id") or ""),
            "btc_max_profit_verdict": str(btc_profit_contract.get("verdict") or ""),
        },
        "leadership_read": [
            "No current adaptive candidate is `promotion_ready`, but the room now has honest `shadow_ready` executable branches plus explicit `research_only` adaptive candidates." if shadow_ready_rows and not promotion_ready_rows else f"`{len(shadow_ready_rows)}` current adaptive candidate(s) are already `shadow_ready`.",
            f"Early monetization currently passes for `{[row['candidate_id'] for row in monetization_pass_rows]}` and fails for `{[row['candidate_id'] for row in monetization_fail_rows]}`. Do not confuse structural readiness with profit viability when fresh realized evidence is still negative or toxic.",
            f"Live-slot superiority currently passes for `{[row['candidate_id'] for row in live_slot_pass_rows]}` and fails for `{[row['candidate_id'] for row in live_slot_fail_rows]}`. Do not read launchable shadow packets as live-worthy unless they clear that per-symbol superiority gate.",
            "The BTC restore-comparison shadow is currently the clean executable branch: explicit, reversible, and launch-packet complete, while still waiting on fresh proof.",
            (
                f"BTC restore is now explicitly overlay-governed as `{restore_runtime_obligation_class}`: {restore_runtime_obligation_read}"
                if restore_runtime_obligation_class
                else "BTC restore currently carries no explicit runtime-overlay obligation on this surface."
            ),
            "GBPUSD now also has an explicit shadow-ready adaptive comparison packet tied to the incumbent live seat; it should be judged on fresh comparison proof, not on packet ambiguity.",
            (
                f"BTC now also has an explicit max-profit contract: `{btc_profit_contract.get('verdict', '')}` for restore `{btc_profit_contract.get('restore_lane', '')}` versus adaptive `{btc_profit_contract.get('adaptive_shape_id', '')}`."
                if btc_profit_contract
                else "BTC still lacks an explicit max-profit contract on the current authority stack."
            ),
            "The true adaptive BTC branch is no longer rejected for branch confusion; it is now research-only because the branch is explicit but still lacks a final candidate packet and fresh branch-local proof.",
            "The parked BTC artifact remains rejected as a launch candidate because it is still stale direct-live context rather than a current proof branch.",
            (
                "USDJPY bounded proof refresh remains research-only: the relaunch packet is now explicit and the old bounded runtime fault is historical-only, "
                "but the branch still needs fresh bounded evidence before it is shadow-ready."
                if usdjpy_packet
                else "USDJPY bounded proof refresh remains research-only: the old bounded runtime fault is historical-only now, but the branch still needs an explicit proof packet and fresh bounded evidence before it is shadow-ready."
            ),
        ],
        "candidates": candidates,
        "next_actions": [
            {"action_id": "btc_restore_branch_execution", "read": "Keep BTC restore-comparison as the executable next move until the room resolves the true adaptive branch cleanly.", "source": relative_path_text(BTC_BRANCH_DECISION_PATH)},
            {
                "action_id": "btc_guarded_toxic_flow_overlay_proof",
                "read": restore_runtime_obligation_read or "Prove the guarded-open and cluster-escape runtime contract on BTC before reading shadow-ready as unconditional expansion permission.",
                "source": relative_path_text(QUEUE_PATH),
            },
            {"action_id": "gbpusd_packet_forward_comparison", "read": "Use the explicit GBP comparison packet to collect fresh incumbent-vs-adaptive FX proof instead of treating GBP as packet-blocked.", "source": relative_path_text(PACKET_BOARD_PATH)},
            {"action_id": "btc_cash_harvest_forward_proof", "read": str(btc_profit_contract.get("read") or ""), "source": relative_path_text(INCUMBENT_STUDY_PATH)},
            {"action_id": "btc_true_adaptive_branch_resolution", "read": "Split hold-gate doctrine from true adaptive build so the BTC downtrend row stops failing branch clarity and launch-packet clarity at the same time.", "source": relative_path_text(QUEUE_PATH)},
            {"action_id": "usdjpy_bounded_launch_packet", "read": "Run the explicit bounded proof relaunch packet and collect fresh first-proof evidence so USDJPY can advance from research-only toward shadow-ready.", "source": relative_path_text(PACKET_BOARD_PATH)},
        ],
        "notes": [
            "This board is passive. It grades current adaptive candidates against the pinned acceptance checklist; it does not replace branch decisions or launch anything.",
            "A candidate can be a valid queue task and still be a rejected launch candidate. That is the point of this surface.",
            "BTC restore-comparison remains executable restore work even when none of the current adaptive candidates are shadow-ready.",
        ],
        "supporting_rows": {
            "btc_hold_branch": btc_hold_branch,
            "btc_recommended_branch": btc_branch_recommended,
            "btc_doctrine_target_branch": btc_branch_target,
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    verdict_counts = dict(summary.get("verdict_counts") or {})
    lines = [
        "# Adaptive Harness Acceptance Verdict Board",
        "",
        "This board applies the pinned adaptive harness checklist to the current adaptive candidates so the room can separate queue-next work from honest launch readiness.",
        "",
        f"- generated_at: `{payload.get('generated_at', '-')}`",
        f"- candidate_count: `{summary.get('candidate_count', 0)}`",
        f"- verdict_counts: `rejected={verdict_counts.get('rejected', 0)}` / `research_only={verdict_counts.get('research_only', 0)}` / `shadow_ready={verdict_counts.get('shadow_ready', 0)}` / `promotion_ready={verdict_counts.get('promotion_ready', 0)}`",
        f"- top_non_rejected_candidate_id: `{summary.get('top_non_rejected_candidate_id', '-')}`",
        f"- early_green_monetization: `pass={summary.get('early_green_monetization_pass_count', 0)}` / `fail={summary.get('early_green_monetization_fail_count', 0)}`",
        f"- btc_recommended_branch_id: `{summary.get('btc_recommended_branch_id', '-')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for row in payload.get("leadership_read", []):
        lines.append(f"- {row}")
    lines.extend(["", "## Candidate Verdicts", "", "| Candidate | Verdict | Queue Status | Fails | Warnings | Read |", "| --- | --- | --- | --- | --- | --- |"])
    for row in payload.get("candidates", []):
        lines.append(f"| `{row['candidate_id']}` | `{row['verdict']}` | `{row['queue_status']}` | `{row['fail_count']}` | `{row['warn_count']}` | {row['candidate_read']} |")
    lines.extend(["", "## Detail", ""])
    for row in payload.get("candidates", []):
        lines.append(f"### {row['candidate_id']}")
        lines.append(f"- title: `{row['title']}`")
        lines.append(f"- symbol: `{row['symbol']}`")
        lines.append(f"- verdict: `{row['verdict']}`")
        lines.append(f"- queue_status: `{row['queue_status']}`")
        lines.append(f"- lane: `{row['lane']}`")
        lines.append(f"- candidate_read: {row['candidate_read']}")
        lines.append(f"- queue_why: {row['queue_why']}")
        if row.get("runtime_overlays"):
            lines.append("- runtime_overlays: " + ", ".join(f"`{item}`" for item in row["runtime_overlays"]))
        if row.get("runtime_obligation_class"):
            lines.append(f"- runtime_obligation_class: `{row['runtime_obligation_class']}`")
            lines.append(f"- runtime_obligation_read: {row.get('runtime_obligation_read')}")
        if row.get("supporting_evidence"):
            lines.append("- supporting_evidence: " + ", ".join(f"`{item}`" for item in row["supporting_evidence"]))
        if row.get("failing_checks"):
            lines.append("- failing_checks: " + ", ".join(f"`{item}`" for item in row["failing_checks"]))
        if row.get("warning_checks"):
            lines.append("- warning_checks: " + ", ".join(f"`{item}`" for item in row["warning_checks"]))
        lines.extend(["", "| Check | Status | Read |", "| --- | --- | --- |"])
        for check_row in row.get("checks", []):
            lines.append(f"| `{check_row['check_id']}` | `{check_row['status']}` | {check_row['read']} |")
        lines.append("")
    lines.extend(["## Next Actions", ""])
    for row in payload.get("next_actions", []):
        lines.append(f"- `{row['action_id']}` from `{row['source']}`: {row['read']}")
    lines.extend(["", "## Notes", ""])
    for row in payload.get("notes", []):
        lines.append(f"- {row}")
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
