#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
TASK_STORE_PATH = ROOT / "war_room_tasks.json"

ETH_BOARD_PATH = REPORTS / "eth_atr_runtime_status_board.json"
SHAPESHIFTER_BOARD_PATH = REPORTS / "structure_shapeshifter_proof_board.json"
EXPERIMENTAL_BOARD_PATH = REPORTS / "experimental_proof_watch_board.json"
LATTICE_GAP_BOARD_PATH = REPORTS / "lattice_telemetry_gap_board.json"
LATTICE_PHASE1_COVERAGE_PATH = REPORTS / "lattice_phase1_event_coverage_board.json"
FX_PHASE1_VISIBILITY_PATH = REPORTS / "fx_phase1_telemetry_visibility_board.json"
FX_SHADOW_RECYCLE_PATH = REPORTS / "fx_shadow_telemetry_recycle_board.json"
FX_SHADOW_CONTRACT_DEBT_PATH = REPORTS / "fx_shadow_telemetry_contract_debt_board.json"

ETH_BOARD_BUILDER = ROOT / "scripts" / "build_eth_atr_runtime_status_board.py"
SHAPESHIFTER_BOARD_BUILDER = ROOT / "scripts" / "build_structure_shapeshifter_proof_board.py"
EXPERIMENTAL_BOARD_BUILDER = ROOT / "scripts" / "build_experimental_proof_watch_board.py"
LATTICE_GAP_BOARD_BUILDER = ROOT / "scripts" / "build_lattice_telemetry_gap_board.py"
LATTICE_PHASE1_COVERAGE_BUILDER = ROOT / "scripts" / "build_lattice_phase1_event_coverage_board.py"
FX_PHASE1_VISIBILITY_BUILDER = ROOT / "scripts" / "build_fx_phase1_telemetry_visibility_board.py"
FX_SHADOW_RECYCLE_BUILDER = ROOT / "scripts" / "build_fx_shadow_telemetry_recycle_board.py"
FX_SHADOW_CONTRACT_DEBT_BUILDER = ROOT / "scripts" / "build_fx_shadow_telemetry_contract_debt_board.py"

OUTPUT_JSON_PATH = REPORTS / "team_leverage_execution_docket.json"
OUTPUT_MD_PATH = REPORTS / "team_leverage_execution_docket.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_builder(script_path: Path) -> None:
    subprocess.run([sys.executable, str(script_path)], check=True, cwd=ROOT)


def refresh_inputs() -> None:
    run_builder(ETH_BOARD_BUILDER)
    run_builder(SHAPESHIFTER_BOARD_BUILDER)
    run_builder(LATTICE_GAP_BOARD_BUILDER)
    run_builder(LATTICE_PHASE1_COVERAGE_BUILDER)
    run_builder(FX_PHASE1_VISIBILITY_BUILDER)
    run_builder(FX_SHADOW_RECYCLE_BUILDER)
    run_builder(FX_SHADOW_CONTRACT_DEBT_BUILDER)
    run_builder(EXPERIMENTAL_BOARD_BUILDER)


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


def maybe_task_by_id(task_store: dict[str, Any], task_id: int) -> dict[str, Any] | None:
    try:
        return task_by_id(task_store, task_id)
    except KeyError:
        return None


def maybe_decision_by_id(task_store: dict[str, Any], decision_id: int) -> dict[str, Any] | None:
    try:
        return decision_by_id(task_store, decision_id)
    except KeyError:
        return None


def eth_current_blocker(experimental_board: dict[str, Any]) -> str:
    eth = dict(experimental_board.get("eth_atr") or {})
    closes = int(eth.get("total_realized_closes") or 0)
    opens = int(eth.get("total_open_positions") or 0)
    if closes <= 0 and opens <= 0:
        return "market has not produced the first ETH ATR open or close yet"
    if closes <= 0:
        return "first ETH ATR close still missing"
    return "fresh ETH ATR proof exists but is still too early to judge"


def shapeshifter_current_blocker(shapeshifter_board: dict[str, Any]) -> str:
    events = dict(shapeshifter_board.get("events") or {})
    flips = int(events.get("structure_flip_count_since_runner_start") or 0)
    box_adjusts = int(events.get("box_geometry_adjust_count_since_runner_start") or 0)
    if flips <= 0 and box_adjusts <= 0:
        return "no post-repair structure_flip or post-start box_geometry_adjust yet"
    if flips <= 0:
        return "fresh box mutation exists, but no post-repair structure_flip yet"
    return "fresh shapeshifter proof exists but still needs repeatability"


def build_payload(
    experimental_board: dict[str, Any],
    eth_board: dict[str, Any],
    shapeshifter_board: dict[str, Any],
    lattice_gap_board: dict[str, Any],
    lattice_phase1_coverage_board: dict[str, Any],
    task_store: dict[str, Any],
    fx_shadow_recycle_board: dict[str, Any] | None = None,
    fx_shadow_contract_debt_board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eth_task = task_by_id(task_store, 13)
    shapeshifter_task = task_by_id(task_store, 23)
    hedge_task = task_by_id(task_store, 24)
    telemetry_task = maybe_task_by_id(task_store, 28)
    gbp_decision = decision_by_id(task_store, 6)
    telemetry_scope_decision = maybe_decision_by_id(task_store, 7)

    eth_summary = dict(experimental_board.get("eth_atr") or {})
    eth_task_evidence = dict(eth_task.get("evidence") or {})
    eth_rows = list(eth_board.get("active_rows") or [])
    shapeshifter_runner = dict(shapeshifter_board.get("runner") or {})
    shapeshifter_events = dict(shapeshifter_board.get("events") or {})
    shapeshifter_economics = dict(shapeshifter_board.get("economics") or {})
    hedge_evidence = dict(hedge_task.get("evidence") or {})
    gbp_evidence = dict(gbp_decision.get("evidence") or {})
    telemetry_summary = dict(lattice_gap_board.get("summary") or {})
    coverage_summary = dict(lattice_phase1_coverage_board.get("summary") or {})
    deployment_context = dict(lattice_phase1_coverage_board.get("deployment_context") or {})
    fx_shadow_recycle_board = fx_shadow_recycle_board or {}
    fx_shadow_summary = dict(fx_shadow_recycle_board.get("summary") or {})
    fx_shadow_contract_debt_board = fx_shadow_contract_debt_board or {}
    fx_shadow_contract_summary = dict(fx_shadow_contract_debt_board.get("summary") or {})
    telemetry_surface_present = str(lattice_gap_board.get("readiness") or "") == "telemetry_surface_present"
    coverage_readiness = str(lattice_phase1_coverage_board.get("readiness") or "")
    fx_shadow_recycle_readiness = str(fx_shadow_recycle_board.get("readiness") or "")
    fx_shadow_contract_debt_readiness = str(fx_shadow_contract_debt_board.get("readiness") or "")
    fx_shadow_top_candidate = str(fx_shadow_summary.get("top_recycle_candidate") or "")
    fx_shadow_first_wave_count = int(fx_shadow_summary.get("recycle_first_wave_count") or 0)
    fx_shadow_unlockable_first_wave_count = int(fx_shadow_contract_summary.get("unlockable_first_wave_count") or 0)
    fx_shadow_projected_safe_first_wave_count = int(fx_shadow_contract_summary.get("projected_safe_first_wave_count") or 0)
    fx_shadow_top_unlock_candidate = str(fx_shadow_contract_summary.get("top_unlock_candidate") or "")
    fx_shadow_acceleration_available = bool(
        fx_shadow_recycle_readiness in {"shadow_recycle_queue_ready", "shadow_recycle_second_wave_only"}
        and int(fx_shadow_summary.get("recycle_candidate_count") or 0) > 0
    )
    fx_shadow_contract_debt_actionable = bool(
        fx_shadow_contract_debt_readiness == "contract_debt_actionable"
        and fx_shadow_unlockable_first_wave_count > 0
    )
    coverage_field_count = int(coverage_summary.get("field_count") or 0)
    coverage_covered_field_count = int(coverage_summary.get("covered_field_count") or 0)
    coverage_zero_ratio_text = f"{coverage_covered_field_count}/{coverage_field_count}" if coverage_field_count > 0 else "0/0"
    stale_pre_enrichment_log = coverage_readiness == "stale_or_pre_enrichment_log"
    event_log_is_newer_than_reference_code = deployment_context.get("event_log_is_newer_than_reference_code")
    post_restart_waiting_window = str(experimental_board.get("overall_status") or "") == "waiting_post_restart_event"
    telemetry_task_done = bool(telemetry_task) and str(telemetry_task.get("status") or "") in {
        "completed",
        "done",
        "resolved",
    }
    telemetry_decision_done = bool(telemetry_scope_decision) and str(telemetry_scope_decision.get("status") or "") in {
        "completed",
        "done",
        "resolved",
    }
    telemetry_runtime_wait_only = bool(
        telemetry_surface_present and stale_pre_enrichment_log and post_restart_waiting_window and (telemetry_task_done or telemetry_decision_done)
    )
    deployment_lag_text = (
        "the reviewed event log predates the telemetry-bearing core code"
        if event_log_is_newer_than_reference_code is False
        else "the reviewed runtime log is still pre-enrichment"
    )

    rows: list[dict[str, Any]] = [
        {
            "priority": 1,
            "status": "passive_monitor",
            "workstream": "ETH ATR first-close accumulation",
            "lane": "shadow_ethusd_m5_atr_optimized + shadow_ethusd_m15_atr_optimized + shadow_ethusd_m15_asymmetric",
            "why_high_leverage": "The retuned ETH pack is already launched and healthy, so the next real information comes from first market proof rather than another parameter change.",
            "depends_on": [],
            "current_blocker": eth_current_blocker(experimental_board),
            "required_evidence": [
                "at least one realized close on the current ETH ATR pack",
                "lane health stays 3/3 while the sample starts",
                "the first open/close pattern is readable enough to decide whether more runtime work is justified",
            ],
            "first_honest_outcome": "The room learns whether the launched ETH ATR pack has started producing real sample data or is still only waiting on the market.",
            "unlocks": [
                "task 13 can move from passive accumulation to proof judgment",
                "the combined passive-proof board can leave pure waiting once ETH contributes evidence",
            ],
            "machine_truth": {
                "task_id": 13,
                "task_status": eth_task.get("status"),
                "overall_status": experimental_board.get("overall_status"),
                "healthy_lane_count": eth_summary.get("healthy_lane_count"),
                "lane_count": eth_summary.get("lane_count"),
                "total_realized_closes": eth_summary.get("total_realized_closes"),
                "total_open_positions": eth_summary.get("total_open_positions"),
                "latest_heartbeat_age_seconds": eth_summary.get("latest_heartbeat_age_seconds"),
                "authoritative_surface": eth_task_evidence.get("authoritative_surface"),
                "monitoring_surface_repaired": eth_task_evidence.get("monitoring_surface_repaired"),
                "runner_pids": [row.get("runner_pid") for row in eth_rows],
            },
            "do_not_do_yet": "Do not retune or kill the ETH ATR pack while it is healthy and still sample-free.",
        },
        {
            "priority": 2,
            "status": "passive_monitor",
            "workstream": "Structure-shapeshifter fresh proof",
            "lane": "shadow_ethusd_m5_structure_shapeshifter",
            "why_high_leverage": "The repair work is already done; the next real question is whether the current runner emits fresh adaptive-geometry proof instead of only historical evidence.",
            "depends_on": [],
            "current_blocker": shapeshifter_current_blocker(shapeshifter_board),
            "required_evidence": [
                "the first post-runner-start structure_flip event",
                "or repeated post-start box_geometry_adjust evidence with stable economics",
                "runner freshness and shared-cache health stay intact while the proof window grows",
            ],
            "first_honest_outcome": "The room learns whether the repaired shapeshifter path is only historically wired or actually alive in the current runner window.",
            "unlocks": [
                "task 23 can move from repair-complete to evidence-backed evaluation",
                "adaptive-geometry budget decisions can use current-runner proof instead of wiring-readiness alone",
            ],
            "machine_truth": {
                "task_id": 23,
                "task_status": shapeshifter_task.get("status"),
                "proof_status": shapeshifter_board.get("proof_status"),
                "readiness_verdict": shapeshifter_board.get("readiness_verdict"),
                "runner_fresh": shapeshifter_runner.get("fresh"),
                "runner_pid": shapeshifter_runner.get("pid"),
                "heartbeat_age_seconds": shapeshifter_runner.get("heartbeat_age_seconds"),
                "structure_flip_count_since_runner_start": shapeshifter_events.get("structure_flip_count_since_runner_start"),
                "box_geometry_adjust_count_since_runner_start": shapeshifter_events.get("box_geometry_adjust_count_since_runner_start"),
                "realized_closes": shapeshifter_economics.get("realized_closes"),
                "realized_net_usd": shapeshifter_economics.get("realized_net_usd"),
            },
            "do_not_do_yet": "Do not reopen bridge or scheduler surgery unless a new runtime failure appears.",
        },
    ]

    if telemetry_task:
        rows.append(
            {
                "priority": 3,
                "status": "passive_monitor" if telemetry_runtime_wait_only else "start_now",
                "workstream": (
                    "Post-patch lattice telemetry runtime evidence"
                    if telemetry_runtime_wait_only
                    else "Minimum lattice telemetry port"
                ),
                "lane": "tick-native lattice runtime telemetry",
                "why_high_leverage": (
                    "The bounded telemetry patch is already landed, so the remaining leverage is the first fresh post-patch event window that makes the new fields reviewable instead of stale-log only."
                    if telemetry_runtime_wait_only
                    else "While the top proof blockers wait on the market, this is the clearest bounded engineering slice that will make the next adaptive-geometry failure legible instead of repeating net/closes/resets-only storytelling."
                ),
                "depends_on": [],
                "current_blocker": (
                    (
                        "the telemetry-bearing runners are already live, but the current runner window has not emitted a fresh enriched event yet so coverage is still stale-log 0/18"
                        if telemetry_runtime_wait_only
                        else
                        (
                        (
                            f"the minimum telemetry surface is in code, but {deployment_lag_text} so runtime event coverage is still {coverage_zero_ratio_text}"
                            if telemetry_surface_present and stale_pre_enrichment_log
                            else (
                                f"the minimum telemetry surface is in code, but runtime event coverage is still {coverage_zero_ratio_text} so post-patch diagnostic visibility is not proven yet"
                                if telemetry_surface_present
                                else f"scope decision is already bounded, but runtime event coverage is still {coverage_zero_ratio_text} so the patch has not reached diagnostic legibility yet"
                            )
                        )
                        )
                    )
                    if coverage_covered_field_count <= 0
                    else (
                        "scope decision still open, and the runtime event coverage is only partial so the patch is not fully legible yet"
                        if telemetry_scope_decision and str(telemetry_scope_decision.get("status") or "") == "open"
                        else str(lattice_phase1_coverage_board.get("next_action") or lattice_gap_board.get("next_action") or "bounded telemetry port ready")
                    )
                ),
                "required_evidence": [
                    (
                        "the first fresh post-patch open_ticket or close/escape-like event lands in the current runner window"
                        if telemetry_runtime_wait_only
                        else "phase-1 event enrichment lands on open_ticket and close/escape events"
                    ),
                    (
                        "the current post-patch runners stay healthy while the first enriched event window is captured"
                        if telemetry_runtime_wait_only
                        else "inventory-pressure and burst metrics are included in v1 rather than deferred"
                    ),
                    (
                        f"a fresh post-enrichment event log moves the phase1 coverage board off {coverage_zero_ratio_text} so the new fields are reviewable instead of stale-log only"
                        if stale_pre_enrichment_log
                        else f"the phase1 coverage board moves off {coverage_zero_ratio_text} so the new fields are reviewable instead of raw-log only"
                    ),
                ],
                "first_honest_outcome": (
                    (
                        (
                            f"The room learns whether the landed telemetry patch is visible in fresh runtime events without any recycle by default; if faster evidence is worth the continuity cost, the shadow-only FX recycle queue defines the cheapest current sacrifice, and the contract-debt board shows {fx_shadow_unlockable_first_wave_count} more first-wave candidates suppressed by `--fresh-start`."
                            if fx_shadow_contract_debt_actionable
                            else "The room learns whether the landed telemetry patch is visible in fresh runtime events without any recycle by default; if faster evidence is worth the continuity cost, the shadow-only FX recycle queue defines the cheapest sacrifice."
                        )
                        if fx_shadow_acceleration_available
                        else "The room learns whether the landed telemetry patch is visible in fresh runtime events without ordering another recycle."
                    )
                    if telemetry_runtime_wait_only
                    else "The room gets causal telemetry for the next lattice failure instead of guessing from closes, net, and resets alone."
                ),
                "unlocks": [
                    (
                        "task 28 can move from landed code patch to reviewable runtime evidence"
                        if telemetry_surface_present
                        else "task 28 can move from checklist to implementation"
                    ),
                    "adaptive-geometry and rearm claims can be judged from path quality rather than only ledger totals",
                ],
                "machine_truth": {
                    "task_id": telemetry_task.get("id"),
                    "task_status": telemetry_task.get("status"),
                    "decision_7_status": telemetry_scope_decision.get("status") if telemetry_scope_decision else "",
                    "decision_7_recommended_option": telemetry_scope_decision.get("recommended_option") if telemetry_scope_decision else "",
                    "gap_board_readiness": lattice_gap_board.get("readiness"),
                    "gap_board_missing_count": telemetry_summary.get("missing_count"),
                    "gap_board_partial_count": telemetry_summary.get("partial_count"),
                    "gap_board_present_count": telemetry_summary.get("present_count"),
                    "coverage_board_readiness": lattice_phase1_coverage_board.get("readiness"),
                    "coverage_board_covered_field_count": coverage_covered_field_count,
                    "coverage_board_expected_field_count": coverage_field_count,
                    "coverage_board_zero_coverage_field_count": coverage_summary.get("zero_coverage_field_count"),
                    "coverage_board_event_log_is_newer_than_reference_code": event_log_is_newer_than_reference_code,
                    "coverage_board_event_log_mtime": deployment_context.get("event_log_mtime"),
                    "coverage_board_reference_code_mtime": deployment_context.get("reference_code_mtime"),
                    "experimental_board_status": experimental_board.get("overall_status"),
                    "fx_shadow_recycle_readiness": fx_shadow_recycle_readiness,
                    "fx_shadow_recycle_first_wave_count": fx_shadow_first_wave_count,
                    "fx_shadow_top_recycle_candidate": fx_shadow_top_candidate,
                    "fx_shadow_contract_debt_readiness": fx_shadow_contract_debt_readiness,
                    "fx_shadow_unlockable_first_wave_count": fx_shadow_unlockable_first_wave_count,
                    "fx_shadow_projected_safe_first_wave_count": fx_shadow_projected_safe_first_wave_count,
                    "fx_shadow_top_unlock_candidate": fx_shadow_top_unlock_candidate,
                },
                "do_not_do_yet": (
                    (
                        (
                            "Do not restart live lanes or broad-recycle the FX pack; wait by default, use only the current safe first-wave shadow recycle queue for deliberate acceleration, and treat the extra suppressed first-wave rows as contract debt rather than implicit permission to recycle them."
                            if fx_shadow_contract_debt_actionable
                            else "Do not restart live lanes or broad-recycle the FX pack; wait by default, and if the room deliberately wants faster telemetry evidence use only the first-wave shadow recycle queue."
                        )
                        if fx_shadow_acceleration_available
                        else "Do not order another recycle or broaden this into a larger observability project while the current post-patch runners are already live and waiting on first evidence."
                    )
                    if telemetry_runtime_wait_only
                    else "Do not expand this into a broad observability project or change trading logic in the same slice."
                ),
            }
        )

    rows.extend(
        [
            {
            "priority": 4 if telemetry_task else 3,
            "status": "blocked_on_dependency",
            "workstream": "Cross-symbol hedging prototype",
            "lane": "inverse-correlated FX pair expansion",
            "why_high_leverage": "It is the next new architecture seam only if the repo first has an inverse-correlated FX lane that the hedge logic can actually pair against.",
            "depends_on": [],
            "current_blocker": str(hedge_evidence.get("blocking_dependency") or "missing inverse-correlated FX lane"),
            "required_evidence": [
                "USDCHF or another inverse-correlated FX lane is running",
                "the hedge candidate pair exists in current registry/runtime truth rather than only in design docs",
                "implementation starts only after that inverse-leg dependency is real",
            ],
            "first_honest_outcome": "The room can decide whether cross-symbol hedging is ready to leave design-only status.",
            "unlocks": [
                "task 24 can move from design to implementation",
            ],
            "machine_truth": {
                "task_id": 24,
                "task_status": hedge_task.get("status"),
                "implementation_status": hedge_evidence.get("implementation_status"),
                "fx_verdict": hedge_evidence.get("fx_verdict"),
                "current_fx_lanes": hedge_evidence.get("current_fx_lanes"),
                "blocking_dependency": hedge_evidence.get("blocking_dependency"),
                "priority_downgraded": hedge_evidence.get("priority_downgraded"),
                "decision_6_executed": hedge_evidence.get("decision_6_executed"),
            },
            "do_not_do_yet": "Do not spend implementation time on cross-symbol hedging while the inverse FX leg does not exist.",
        },
        {
            "priority": 5 if telemetry_task else 4,
            "status": "do_not_start",
            "workstream": "GBP tick-forward promotion work",
            "lane": "shadow_gbpusd_tick_forward",
            "why_high_leverage": "Keeping the demotion decision explicit prevents the room from reopening a closed promotion story and stealing attention from the current proof pack.",
            "depends_on": [],
            "current_blocker": "decision_6_done_demoted_to_closure_diagnosis_only",
            "required_evidence": [
                "a new explicit decision reopens the GBP promotion queue",
                "fresh economics outperform the current negative proof if that queue is ever reopened",
            ],
            "first_honest_outcome": "The room keeps current spend on the real watch lead instead of drifting back into stale GBP promotion language.",
            "unlocks": [
                "cleaner queue discipline",
            ],
            "machine_truth": {
                "decision_id": 6,
                "decision_status": gbp_decision.get("status"),
                "recommended_option": gbp_decision.get("recommended_option"),
                "proof_status": gbp_evidence.get("proof_status"),
                "current_fx_watch_lead": gbp_evidence.get("current_fx_watch_lead"),
            },
            "do_not_do_yet": "Do not rank GBP tick-forward as an active promotion candidate again without a new decision.",
        },
    ])

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(EXPERIMENTAL_BOARD_PATH.relative_to(ROOT)),
            str(ETH_BOARD_PATH.relative_to(ROOT)),
            str(SHAPESHIFTER_BOARD_PATH.relative_to(ROOT)),
            str(LATTICE_GAP_BOARD_PATH.relative_to(ROOT)),
            str(LATTICE_PHASE1_COVERAGE_PATH.relative_to(ROOT)),
            str(FX_SHADOW_RECYCLE_PATH.relative_to(ROOT)),
            str(FX_SHADOW_CONTRACT_DEBT_PATH.relative_to(ROOT)),
            str(TASK_STORE_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The queue has shifted from repair work to evidence collection: the ETH ATR pack and shapeshifter lane are both healthy and currently waiting on the market.",
            (
                (
                    (
                        f"Task 28's bounded telemetry port is already landed, and the remaining leverage is runtime evidence capture: the current post-patch runners are live, but the Phase 1 coverage board is still stale-log {coverage_zero_ratio_text} until the first fresh post-enrichment event lands. The safe FX shadow queue is still 3 names, with {fx_shadow_unlockable_first_wave_count} more first-wave candidates currently suppressed by restart-contract debt."
                        if fx_shadow_contract_debt_actionable
                        else f"Task 28's bounded telemetry port is already landed, and the remaining leverage is runtime evidence capture: the current post-patch runners are live, but the Phase 1 coverage board is still stale-log {coverage_zero_ratio_text} until the first fresh post-enrichment event lands."
                    )
                    if telemetry_runtime_wait_only
                    else
                    (
                        f"While those two market blockers wait passively, the first active engineering slice worth doing is the bounded lattice telemetry port from Task 28, but the planning layer should still treat it as deployment-lagged until a fresh post-enrichment log moves the Phase 1 coverage board off {coverage_zero_ratio_text}."
                        if stale_pre_enrichment_log
                        else f"While those two market blockers wait passively, the first active engineering slice worth doing is the bounded lattice telemetry port from Task 28, but the planning layer should still treat it as diagnostically incomplete until the Phase 1 coverage board moves off {coverage_zero_ratio_text}."
                    )
                )
                if telemetry_task
                else "Keep cross-symbol hedging blocked behind an inverse-correlated FX lane, and keep GBP tick-forward out of the promotion queue because decision #6 is already done."
            ),
            (
                (
                    "If the passive-proof board leaves `waiting_market_proof` or `waiting_post_restart_event`, or if a runner fails, re-rank immediately; until then wait by default, treat any FX telemetry acceleration as a deliberate first-wave shadow recycle only, not a live restart, and read the contract-debt board before assuming the current 3-name safe queue is the full opportunity set."
                    if fx_shadow_contract_debt_actionable
                    else "If the passive-proof board leaves `waiting_market_proof` or `waiting_post_restart_event`, or if a runner fails, re-rank immediately; until then wait by default, and treat any FX telemetry acceleration as a deliberate first-wave shadow recycle only, not a live restart."
                )
                if fx_shadow_acceleration_available
                else "If the passive-proof board leaves `waiting_market_proof` or `waiting_post_restart_event`, or if a runner fails, re-rank immediately; until then more runtime surgery is lower leverage than disciplined monitoring."
            ),
        ],
        "status_counts": {
            "start_now": sum(1 for row in rows if row["status"] == "start_now"),
            "passive_monitor": sum(1 for row in rows if row["status"] == "passive_monitor"),
            "blocked_on_dependency": sum(1 for row in rows if row["status"] == "blocked_on_dependency"),
            "do_not_start": sum(1 for row in rows if row["status"] == "do_not_start"),
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Team Leverage Execution Docket",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: convert the current passive-proof and taskboard truth into one honest execution order for the team.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    counts = dict(payload.get("status_counts") or {})
    lines.extend(["", "## Status Counts", ""])
    for key in ("start_now", "passive_monitor", "blocked_on_dependency", "do_not_start"):
        lines.append(f"- {key}: `{counts.get(key, 0)}`")

    lines.extend(["", "## Ordered Docket", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### P{int(row['priority'])} - {row['workstream']}")
        lines.append("")
        lines.append(f"- Status: `{row['status']}`")
        lines.append(f"- Lane: `{row['lane']}`")
        lines.append(f"- Why high leverage: `{row['why_high_leverage']}`")
        depends_on = list(row.get("depends_on") or [])
        lines.append(f"- Depends on: `{'; '.join(depends_on) if depends_on else 'none'}`")
        lines.append(f"- Current blocker: `{row['current_blocker']}`")
        lines.append(f"- Required evidence: `{'; '.join(list(row.get('required_evidence') or []))}`")
        lines.append(f"- First honest outcome: `{row['first_honest_outcome']}`")
        lines.append(f"- Unlocks: `{'; '.join(list(row.get('unlocks') or []))}`")
        machine_truth = dict(row.get("machine_truth") or {})
        lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in machine_truth.items())}`")
        lines.append(f"- Do not do yet: `{row['do_not_do_yet']}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the team leverage execution docket.")
    parser.add_argument(
        "--skip-refresh-inputs",
        action="store_true",
        help="Assume upstream proof boards are already current and skip rebuilding them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.skip_refresh_inputs:
        refresh_inputs()
    payload = build_payload(
        load_json(EXPERIMENTAL_BOARD_PATH),
        load_json(ETH_BOARD_PATH),
        load_json(SHAPESHIFTER_BOARD_PATH),
        load_json(LATTICE_GAP_BOARD_PATH),
        load_json(LATTICE_PHASE1_COVERAGE_PATH),
        load_json(TASK_STORE_PATH),
        load_json(FX_SHADOW_RECYCLE_PATH),
        load_json(FX_SHADOW_CONTRACT_DEBT_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
