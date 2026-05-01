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

FORMULA_COVERAGE_PATH = REPORTS / "adaptive_formula_input_coverage_board.json"
PROOF_BOARD_PATH = REPORTS / "adaptive_lattice_proof_board.json"
QUEUE_PATH = REPORTS / "adaptive_lab_queue.json"
RUNTIME_AUDIT_PATH = REPORTS / "btc_adaptive_runtime_audit.json"
RESTORE_BOARD_PATH = REPORTS / "btc_m15_warp_restore_board.json"
CONTROLLER_PRIORS_PATH = CONFIGS / "adaptive_controller_priors.json"
INCUMBENT_STUDY_PATH = REPORTS / "adaptive_incumbent_study_board.json"
DOCTRINE_PATH = DOCS / "adaptive_lattice_research_spec.md"
OUTPUT_JSON = REPORTS / "adaptive_lattice_perfection_scorecard_board.json"
OUTPUT_MD = REPORTS / "adaptive_lattice_perfection_scorecard_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def relative_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def category(category_id: str, title: str, score: int, rationale: str, evidence: list[str]) -> dict[str, Any]:
    verdict = {2: "strong", 1: "mixed", 0: "weak"}.get(score, "weak")
    return {
        "category_id": category_id,
        "title": title,
        "score": score,
        "max_score": 2,
        "verdict": verdict,
        "rationale": rationale,
        "evidence": evidence,
    }


def build_payload(
    formula_coverage: dict[str, Any] | None = None,
    proof_board: dict[str, Any] | None = None,
    adaptive_queue: dict[str, Any] | None = None,
    runtime_audit: dict[str, Any] | None = None,
    restore_board: dict[str, Any] | None = None,
    controller_priors: dict[str, Any] | None = None,
    incumbent_study: dict[str, Any] | None = None,
) -> dict[str, Any]:
    formula = formula_coverage or load_json(FORMULA_COVERAGE_PATH)
    proof = proof_board or load_json(PROOF_BOARD_PATH)
    queue = adaptive_queue or load_json(QUEUE_PATH)
    audit = runtime_audit or load_json(RUNTIME_AUDIT_PATH)
    restore = restore_board or load_json(RESTORE_BOARD_PATH)
    priors = controller_priors or load_json(CONTROLLER_PRIORS_PATH)
    study = incumbent_study or load_json(INCUMBENT_STUDY_PATH)

    formula_summary = dict(formula.get("summary") or {})
    formula_counts = dict(formula_summary.get("verdict_counts") or {})
    debt_symbols = list(formula_summary.get("formula_input_debt_symbols") or [])

    queue_summary = dict(queue.get("summary") or {})
    queue_tasks = list(queue.get("tasks") or [])
    highest_runtime_obligation_task = next(
        (row for row in queue_tasks if str(row.get("runtime_obligation_class") or "")),
        None,
    )
    proof_rows = list(proof.get("rows") or [])
    proof_stages = {str(row.get("symbol") or ""): str(row.get("stage") or "") for row in proof_rows}
    proof_statuses = {str(row.get("symbol") or ""): str(row.get("status") or "") for row in proof_rows}
    active_blockers = [row for row in list(proof.get("blockers") or []) if bool(row.get("active"))]

    runtime_summary = dict(audit.get("summary") or {})
    runtime_lane = dict(audit.get("runtime_lane") or {})
    runtime_checks = {str(row.get("check_id") or ""): dict(row) for row in list(audit.get("checks") or [])}
    study_rows = {str(row.get("symbol") or ""): dict(row) for row in list(study.get("rows") or [])}
    btc_profit_contract = dict((study_rows.get("BTCUSD") or {}).get("btc_max_profit_comparison") or {})

    restore_candidate = dict(restore.get("restore_candidate") or {})
    btc_prior = dict((priors.get("symbol_priors") or {}).get("BTCUSD") or {})
    global_policy = dict(priors.get("global_policy") or {})

    categories: list[dict[str, Any]] = []

    state_score = 2 if not debt_symbols and safe_int(formula_counts.get("true_range_atr_ready")) >= 2 else 1 if not debt_symbols else 0
    categories.append(
        category(
            "state_reading_honesty",
            "State-Reading Honesty",
            state_score,
            (
                "Adaptive inputs are currently honest: the advertised range/ATR shapes have live `avg_range` and `range_atr_ratio` support."
                if state_score == 2
                else "Adaptive input honesty is partial: some rows still rely on fallback or debt."
                if state_score == 1
                else "Adaptive input honesty is weak: the live regime surface still cannot support the advertised formulas."
            ),
            [
                f"true_range_atr_ready={safe_int(formula_counts.get('true_range_atr_ready'))}",
                f"atr_ready={safe_int(formula_counts.get('atr_ready'))}",
                f"formula_input_debt_symbols={debt_symbols}",
            ],
        )
    )

    ready_count = safe_int(queue.get("ready_count"))
    blocked_count = safe_int(queue.get("blocked_count"))
    coherence_score = 2 if not active_blockers and safe_int(queue.get("decision_gated_count")) == 0 and ready_count > 0 else 1 if not active_blockers else 0
    categories.append(
        category(
            "geometry_close_rearm_coherence",
            "Geometry / Close / Rearm Coherence",
            coherence_score,
            (
                "The current adaptive family reads coherently: no active blocker is suppressing the current queue and the next move is execution-ready."
                if coherence_score == 2
                else "Current adaptive geometry is coherent enough to plan from, but the recommended BTC branch is temporarily held behind runtime repair rather than being execution-ready."
                if coherence_score == 1 and ready_count == 0 and blocked_count > 0
                else "Current adaptive geometry is coherent enough to plan from, but BTC still splits between posture reconciliation and a later adaptive branch."
                if coherence_score == 1
                else "Current adaptive geometry is still blocked by active runtime-family contradictions."
            ),
            [
                f"active_blockers={[row.get('blocker_id') for row in active_blockers]}",
                f"decision_gated_count={safe_int(queue.get('decision_gated_count'))}",
                f"ready_count={ready_count}",
                f"blocked_count={blocked_count}",
                f"highest_priority_ready_task_id={queue_summary.get('highest_priority_ready_task_id', '')}",
            ],
        )
    )

    fresh_closes = safe_int(runtime_lane.get("runner_session_trade_closes"))
    realized_fresh = safe_float(runtime_lane.get("runner_session_trade_realized_usd"))
    early_green_score = 2 if fresh_closes > 0 and realized_fresh > 0 else 1 if fresh_closes > 0 else 0
    categories.append(
        category(
            "early_green_monetization",
            "Early-Green Monetization",
            early_green_score,
            (
                "The adaptive runtime has already converted fresh path movement into realized profit."
                if early_green_score == 2
                else "The adaptive runtime is producing fresh closes, but monetization quality is still mixed."
                if early_green_score == 1
                else "Adaptive monetization is still unproven: BTC now has an explicit restore-versus-cash-harvest comparison contract, but the adaptive side still has no fresh closes in the current window."
            ),
            [
                f"runner_session_trade_closes={fresh_closes}",
                f"runner_session_trade_realized_usd={realized_fresh}",
                f"pre_start_state_carry_realized_usd={safe_float(runtime_lane.get('pre_start_state_carry_realized_usd'))}",
                f"btc_max_profit_verdict={btc_profit_contract.get('verdict', '')}",
            ],
        )
    )

    inventory_warn = str(runtime_checks.get("runtime_direct_live", {}).get("status") or "")
    cap_pass = str(runtime_checks.get("design_max_open", {}).get("status") or "") == "pass"
    inventory_score = 2 if inventory_warn != "warn" and cap_pass else 1 if cap_pass else 0
    categories.append(
        category(
            "inventory_governance",
            "Inventory Governance",
            inventory_score,
            (
                "Inventory governance is explicit and aligned with the current adaptive runtime."
                if inventory_score == 2
                else "Inventory governance is partial: lean caps are explicit, but the parked adaptive artifact is still direct-live and stale."
                if inventory_score == 1
                else "Inventory governance is weak: current adaptive runtime posture still conflicts with the intended guardrails."
            ),
            [
                f"max_open_per_side={safe_int(runtime_lane.get('max_open_per_side'))}",
                f"runtime_direct_live_status={inventory_warn or 'missing'}",
                f"parked_runtime_status={audit.get('status', '')}",
            ],
        )
    )

    has_portfolio_roles = bool(btc_prior) and bool(global_policy.get("graduation_funnel"))
    heavy_symbols = sorted(str(row.get("symbol") or "") for row in proof_rows if str(row.get("symbol") or "") in {"BTCUSD", "ETHUSD"})
    portfolio_score = 1 if has_portfolio_roles else 0
    categories.append(
        category(
            "portfolio_governance",
            "Portfolio Governance",
            portfolio_score,
            (
                "Portfolio governance is partial: symbol roles and graduation doctrine exist, but there is still no live adaptive portfolio governor surface."
                if portfolio_score == 1
                else "Portfolio governance is weak: adaptive work is still mostly symbol-local without explicit portfolio doctrine."
            ),
            [
                f"btc_controller_role={btc_prior.get('controller_role', '')}",
                f"graduation_funnel_present={bool(global_policy.get('graduation_funnel'))}",
                f"portfolio_heavy_symbols={heavy_symbols}",
            ],
        )
    )

    telemetry_score = 2 if not debt_symbols and bool(audit.get("checks")) else 1 if bool(audit.get("checks")) else 0
    categories.append(
        category(
            "telemetry_explainability",
            "Telemetry Explainability",
            telemetry_score,
            (
                "Telemetry explainability is strong: formula readiness, parked runtime audits, and proof surfaces now make the adaptive claims inspectable."
                if telemetry_score == 2
                else "Telemetry explainability is partial: some adaptive claims can be inspected, but the explanation layer is still incomplete."
                if telemetry_score == 1
                else "Telemetry explainability is weak: the adaptive surfaces still do not explain their own claims well enough."
            ),
            [
                f"runtime_check_count={len(runtime_checks)}",
                f"formula_input_debt_symbols={debt_symbols}",
                f"proof_board_generated_at={proof.get('generated_at', '')}",
                f"runtime_obligation_task_count={safe_int(queue_summary.get('runtime_obligation_task_count'))}",
            ],
        )
    )

    ready_count = safe_int(queue.get("ready_count"))
    btc_stage = proof_stages.get("BTCUSD", "")
    eth_stage = proof_stages.get("ETHUSD", "")
    forward_score = 2 if ready_count > 0 and btc_stage == "live_ready" else 1 if ready_count > 0 or eth_stage == "probation" else 0
    categories.append(
        category(
            "forward_proof_status",
            "Forward-Proof Status",
            forward_score,
            (
                "Adaptive forward proof is strong enough to promote decisions from current runtime evidence."
                if forward_score == 2
                else "Adaptive forward proof is still mixed: there are live planning surfaces, but BTC posture remains manual-review and ETH is still probationary."
                if forward_score == 1
                else "Adaptive forward proof is weak: current adaptive work is still mostly doctrine and instrumentation without fresh positive proof."
            ),
            [
                f"ready_count={ready_count}",
                f"btc_stage={btc_stage}",
                f"eth_stage={eth_stage}",
                f"btc_runtime_status={audit.get('status', '')}",
            ],
        )
    )

    total_score = sum(int(row["score"]) for row in categories)
    max_score = sum(int(row["max_score"]) for row in categories)
    if total_score >= 10:
        overall_verdict = "near_perfectible_with_forward_proof"
    elif total_score >= 6:
        overall_verdict = "instrumented_but_not_yet_perfect"
    else:
        overall_verdict = "far_from_perfect"

    leadership_read = [
        f"Adaptive perfection score is `{total_score}/{max_score}` -> `{overall_verdict}`.",
        (
            "Inputs and explainability are now strong enough to stop blaming missing telemetry for adaptive ambiguity."
            if state_score == 2 and telemetry_score == 2
            else "Adaptive perfection is still bottlenecked by missing or weak input/explainability surfaces."
        ),
        (
            "Current weakness is monetization and forward proof, not input readiness."
            if early_green_score == 0 and forward_score <= 1
            else "Current weakness is no longer just proof; governance/coherence still needs work too."
        ),
        (
            f"BTC max-profit contract now reads `{btc_profit_contract.get('verdict', '')}`: restore `{btc_profit_contract.get('restore_lane', '')}` remains the executable control while adaptive `{btc_profit_contract.get('adaptive_shape_id', '')}` is explicit but still unproven."
            if btc_profit_contract
            else "BTC still lacks an explicit max-profit comparison contract on current passive surfaces."
        ),
        (
            f"Current executable seam stays `{restore_candidate.get('lane', 'shadow_btcusd_m15_warp_restore_v1')}` from the restore board, but queue truth now has no ready adaptive task and instead blocks on `{queue_summary.get('highest_priority_blocked_title', '')}`."
            if ready_count == 0 and blocked_count > 0
            else f"Current executable seam stays `{restore_candidate.get('lane', 'shadow_btcusd_m15_warp_restore_v1')}` from the restore board, while queue truth still says `{queue_summary.get('highest_priority_ready_title', '')}`."
        ),
        (
            f"The highest-priority runtime obligation is `{highest_runtime_obligation_task.get('runtime_obligation_class')}` on `{highest_runtime_obligation_task.get('task_id')}`: {highest_runtime_obligation_task.get('runtime_obligation_read') or highest_runtime_obligation_task.get('runtime_overlay_read') or ''}"
            if highest_runtime_obligation_task
            else "No current queue task carries an explicit runtime-overlay obligation."
        ),
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            relative_path_text(DOCTRINE_PATH),
            relative_path_text(FORMULA_COVERAGE_PATH),
            relative_path_text(PROOF_BOARD_PATH),
            relative_path_text(QUEUE_PATH),
            relative_path_text(RUNTIME_AUDIT_PATH),
            relative_path_text(RESTORE_BOARD_PATH),
            relative_path_text(CONTROLLER_PRIORS_PATH),
            relative_path_text(INCUMBENT_STUDY_PATH),
        ],
        "summary": {
            "category_count": len(categories),
            "total_score": total_score,
            "max_score": max_score,
            "overall_verdict": overall_verdict,
            "highest_priority_ready_task_id": queue_summary.get("highest_priority_ready_task_id", ""),
            "highest_priority_ready_title": queue_summary.get("highest_priority_ready_title", ""),
            "highest_priority_runtime_obligation_task_id": queue_summary.get("highest_priority_runtime_obligation_task_id", ""),
            "highest_priority_runtime_obligation_class": queue_summary.get("highest_priority_runtime_obligation_class", ""),
            "restore_candidate_lane": restore_candidate.get("lane", ""),
            "restore_candidate_verdict": restore_candidate.get("verdict", ""),
            "btc_max_profit_verdict": btc_profit_contract.get("verdict", ""),
        },
        "leadership_read": leadership_read,
        "categories": categories,
        "next_actions": [
            {
                "action_id": "queue_ready_posture_reconciliation",
                "source": "adaptive_lab_queue",
                "read": str(queue_summary.get("highest_priority_ready_title") or ""),
            },
            {
                "action_id": "restore_comparison_shadow_packet",
                "source": "btc_m15_warp_restore_board",
                "read": str(restore_candidate.get("action") or ""),
            },
            {
                "action_id": "forward_proof_gap",
                "source": "btc_adaptive_runtime_audit",
                "read": str(runtime_summary.get("completion_read") or ""),
            },
            {
                "action_id": "runtime_overlay_obligation",
                "source": "adaptive_lab_queue",
                "read": str((highest_runtime_obligation_task or {}).get("runtime_obligation_read") or ""),
            },
            {
                "action_id": "btc_max_profit_contract",
                "source": "adaptive_incumbent_study_board",
                "read": str(btc_profit_contract.get("read") or ""),
            },
        ],
        "notes": [
            "This board is passive. It grades the current adaptive program against the pinned doctrine; it does not settle branch decisions or launch any lane.",
            "A high score here does not override the queue or restore packet. It only tells the room how close the current program is to the repo's own definition of adaptive-lattice perfection.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Adaptive Lattice Perfection Scorecard Board",
        "",
        "This board grades the current adaptive-lattice program against the pinned doctrine in `docs/adaptive_lattice_research_spec.md`.",
        "",
        f"- generated_at: `{payload.get('generated_at', '-')}`",
        f"- overall_verdict: `{summary.get('overall_verdict', '-')}`",
        f"- score: `{summary.get('total_score', 0)}/{summary.get('max_score', 0)}`",
        "",
        "## Leadership Read",
        "",
    ]
    for row in payload.get("leadership_read", []):
        lines.append(f"- {row}")
    lines.extend(
        [
            "",
            "## Category Scores",
            "",
            "| Category | Verdict | Score | Rationale |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in payload.get("categories", []):
        lines.append(
            f"| `{row['category_id']}` | `{row['verdict']}` | `{row['score']}/{row['max_score']}` | {row['rationale']} |"
        )

    lines.extend(["", "## Evidence", ""])
    for row in payload.get("categories", []):
        lines.append(f"### {row['title']}")
        lines.append(f"- verdict: `{row['verdict']}`")
        lines.append(f"- rationale: {row['rationale']}")
        for item in row.get("evidence", []):
            lines.append(f"- {item}")
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
