#!/usr/bin/env python3
"""Build a prioritized adaptive-lattice lab queue from current research surfaces."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_adaptive_btc_branch_decision_board as btc_branch_mod
import build_adaptive_controller_priors as controller_priors_mod
import build_adaptive_lattice_proof_board as proof_board_mod
import build_adaptive_optimizer_board as optimizer_board_mod
import build_adaptive_optimizer_decision_board as optimizer_decision_mod
import build_adaptive_optimizer_reconciliation_board as optimizer_recon_mod
import build_adaptive_transfer_board as transfer_board_mod


ROOT = Path(__file__).resolve().parent.parent
PROOF_PATH = ROOT / "reports" / "adaptive_lattice_proof_board.json"
TRANSFER_PATH = ROOT / "reports" / "adaptive_transfer_board.json"
OPTIMIZER_PATH = ROOT / "reports" / "adaptive_optimizer_board.json"
OPTIMIZER_RECON_PATH = ROOT / "reports" / "adaptive_optimizer_reconciliation_board.json"
OPTIMIZER_DECISION_PATH = ROOT / "reports" / "adaptive_optimizer_decision_board.json"
CONTROLLER_PRIORS_PATH = ROOT / "configs" / "adaptive_controller_priors.json"
NZDUSD_PROBE_PATH = ROOT / "reports" / "nzdusd_transfer_probe.json"
GBPUSD_PACKET_PATH = ROOT / "reports" / "gbpusd_adaptive_shadow_packet.json"
BTC_AUDIT_PATH = ROOT / "reports" / "btc_adaptive_runtime_audit.json"
BTC_SHADOW_PLAN_PATH = ROOT / "reports" / "adaptive_btc_shadow_runner_plan.json"
BTC_BRANCH_DECISION_PATH = ROOT / "reports" / "adaptive_btc_branch_decision_board.json"
INCUMBENT_STUDY_PATH = ROOT / "reports" / "adaptive_incumbent_study_board.json"
SEAT_BOARD_PATH = ROOT / "reports" / "per_symbol_live_seat_board.json"
OUTPUT_MD = ROOT / "reports" / "adaptive_lab_queue.md"
OUTPUT_JSON = ROOT / "reports" / "adaptive_lab_queue.json"
FRESHNESS_WARNING_HOURS = 6.0


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return load_json(path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def freshness_read(generated_at: str | None) -> dict[str, Any]:
    parsed = parse_iso_datetime(generated_at)
    if not parsed:
        return {
            "status": "missing_generated_at",
            "generated_at": str(generated_at or ""),
            "age_hours": None,
            "read": "missing generated_at metadata",
        }
    age_hours = round((utc_now() - parsed).total_seconds() / 3600.0, 2)
    status = "fresh" if age_hours <= FRESHNESS_WARNING_HOURS else "stale"
    return {
        "status": status,
        "generated_at": parsed.isoformat(),
        "age_hours": age_hours,
        "read": f"{status} ({age_hours}h old)",
    }


def freshness_read_for_path(path: Path, generated_at: str | None) -> dict[str, Any]:
    freshness = freshness_read(generated_at)
    if freshness["status"] != "missing_generated_at":
        return freshness
    if not path.exists():
        return {
            "status": "missing_file",
            "generated_at": "",
            "age_hours": None,
            "read": "declared surface file is not present yet",
        }
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_hours = round((utc_now() - mtime).total_seconds() / 3600.0, 2)
    status = "fresh" if age_hours <= FRESHNESS_WARNING_HOURS else "stale"
    return {
        "status": status,
        "generated_at": mtime.isoformat(),
        "age_hours": age_hours,
        "read": f"{status} by file mtime fallback ({age_hours}h old)",
    }


def refresh_upstream_reports() -> None:
    proof_payload = proof_board_mod.build_payload()
    proof_board_mod.write_reports(proof_payload)

    transfer_payload = transfer_board_mod.build_payload()
    transfer_board_mod.OUTPUT_JSON.write_text(json.dumps(transfer_payload, indent=2), encoding="utf-8")
    transfer_board_mod.write_markdown(transfer_payload)

    optimizer_payload = optimizer_board_mod.build_payload()
    optimizer_board_mod.OUTPUT_JSON.write_text(json.dumps(optimizer_payload, indent=2), encoding="utf-8")
    optimizer_board_mod.write_markdown(optimizer_payload)

    optimizer_recon_payload = optimizer_recon_mod.build_payload()
    optimizer_recon_mod.OUTPUT_JSON.write_text(json.dumps(optimizer_recon_payload, indent=2), encoding="utf-8")
    optimizer_recon_mod.write_markdown(optimizer_recon_payload)

    optimizer_decision_payload = optimizer_decision_mod.build_payload()
    optimizer_decision_mod.OUTPUT_JSON.write_text(json.dumps(optimizer_decision_payload, indent=2), encoding="utf-8")
    optimizer_decision_mod.write_markdown(optimizer_decision_payload)

    controller_priors_payload = controller_priors_mod.build_payload(
        controller_priors_mod.load_json(controller_priors_mod.CONFIG_TO_PERF_PATH),
        controller_priors_mod.load_json(controller_priors_mod.REAL_WORLD_PATH),
        controller_priors_mod.load_json(controller_priors_mod.SALVAGE_PATH),
        controller_priors_mod.load_json(controller_priors_mod.REARM_PARAMS_PATH),
        controller_priors_mod.load_json(controller_priors_mod.PROMOTION_QUEUE_PATH),
        controller_priors_mod.load_json(controller_priors_mod.REGIME_SIGNAL_PATH),
    )
    controller_priors_mod.write_outputs(controller_priors_payload)

    btc_branch_payload = btc_branch_mod.build_payload()
    btc_branch_mod.OUTPUT_JSON.write_text(json.dumps(btc_branch_payload, indent=2), encoding="utf-8")
    btc_branch_mod.OUTPUT_MD.write_text(btc_branch_mod.render_markdown(btc_branch_payload), encoding="utf-8")


def collect_input_surfaces(
    proof: dict[str, Any],
    transfer: dict[str, Any],
    optimizer: dict[str, Any],
    optimizer_recon: dict[str, Any],
    optimizer_decision: dict[str, Any],
    controller_priors: dict[str, Any],
    gbpusd_packet: dict[str, Any] | None,
    btc_audit: dict[str, Any] | None,
    btc_shadow_plan: dict[str, Any] | None,
    btc_branch_decision: dict[str, Any] | None,
    incumbent_study: dict[str, Any] | None,
    seat_board: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    surfaces = [
        ("adaptive_lattice_proof_board", PROOF_PATH, proof),
        ("adaptive_transfer_board", TRANSFER_PATH, transfer),
        ("adaptive_optimizer_board", OPTIMIZER_PATH, optimizer),
        ("adaptive_optimizer_reconciliation_board", OPTIMIZER_RECON_PATH, optimizer_recon),
        ("adaptive_optimizer_decision_board", OPTIMIZER_DECISION_PATH, optimizer_decision),
        ("adaptive_controller_priors", CONTROLLER_PRIORS_PATH, controller_priors),
        ("gbpusd_adaptive_shadow_packet", GBPUSD_PACKET_PATH, gbpusd_packet or {}),
        ("btc_adaptive_runtime_audit", BTC_AUDIT_PATH, btc_audit or {}),
        ("adaptive_btc_shadow_runner_plan", BTC_SHADOW_PLAN_PATH, btc_shadow_plan or {}),
        ("adaptive_btc_branch_decision_board", BTC_BRANCH_DECISION_PATH, btc_branch_decision or {}),
        ("adaptive_incumbent_study_board", INCUMBENT_STUDY_PATH, incumbent_study or {}),
        ("per_symbol_live_seat_board", SEAT_BOARD_PATH, seat_board or {}),
    ]
    rows: list[dict[str, Any]] = []
    for surface_id, path, payload in surfaces:
        freshness = freshness_read_for_path(path, str(payload.get("generated_at") or ""))
        rows.append(
            {
                "surface_id": surface_id,
                "path": str(path.relative_to(ROOT)),
                **freshness,
            }
        )
    return rows


def next_action_class(profit_mode: str, study_status: str) -> str:
    if profit_mode == "guarded_toxic_flow":
        return "control_shadow_and_collect_path_safety_evidence"
    if profit_mode == "micro_harvest":
        return "validate_microstructure_capture_under_real_friction"
    if profit_mode == "cash_repair_harvest":
        return "prove_close_conversion_before_extension"
    if profit_mode == "friction_survivor":
        return "prove_executability_and_survival_before_promotion"
    if study_status == "adaptive_shape_defined_packet_missing":
        return "build_executable_comparison_packet"
    if study_status == "research_only_adaptive_candidate":
        return "keep_in_research_until_forward_proof"
    return "shadow_compare_and_score"


def seat_compatible_next_action_class(action_class: str) -> str:
    return {
        "validate_microstructure_capture_under_real_friction": "shadow_compare_and_score",
        "prove_close_conversion_before_extension": "prove_executability_and_survival_before_promotion",
    }.get(str(action_class or ""), str(action_class or ""))


def next_action_class_pair(profit_mode: str, study_status: str) -> tuple[str, str]:
    detailed = next_action_class(profit_mode, study_status)
    return seat_compatible_next_action_class(detailed), detailed


def runtime_overlays_for_study_row(study_row: dict[str, Any]) -> list[str]:
    overlays = []
    for raw in list(study_row.get("adaptive_runtime_overlays") or []):
        text = str(raw or "").strip()
        if text:
            overlays.append(text)
    return overlays


def runtime_obligation(runtime_overlays: list[str], runtime_overlay_read: str) -> tuple[str, str]:
    overlay_set = set(runtime_overlays)
    if {
        "guard_open_admission",
        "cluster_aware_escape",
        "suppress_additional_levels_after_burst",
    }.issubset(overlay_set):
        return (
            "prove_guarded_open_admission_with_cluster_escape",
            runtime_overlay_read
            or "Keep new opens guarded, collapse burst fills into one escape unit, and suppress extra levels until the burst dissipates.",
        )
    if "guard_open_admission" in overlay_set:
        return (
            "prove_guarded_open_admission_before_expansion",
            runtime_overlay_read or "Keep new opens guarded until fresh path evidence stops reading as toxic.",
        )
    if "suppress_additional_levels_after_burst" in overlay_set:
        return (
            "prove_additional_level_suppression_until_burst_dissipates",
            runtime_overlay_read or "Suppress additional levels until the burst state dissipates.",
        )
    return "", ""


def find_symbol_row(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    needle = str(symbol or "").upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() == needle:
            return dict(row)
    return {}


def build_tasks() -> list[dict[str, Any]]:
    proof = load_json(PROOF_PATH)
    transfer = load_json(TRANSFER_PATH)
    optimizer = load_json(OPTIMIZER_PATH)
    optimizer_recon = load_json(OPTIMIZER_RECON_PATH)
    optimizer_decision = load_optional_json(OPTIMIZER_DECISION_PATH) or {}
    controller_priors = load_optional_json(CONTROLLER_PRIORS_PATH) or {}
    nzdusd_probe = load_optional_json(NZDUSD_PROBE_PATH)
    gbpusd_packet = load_optional_json(GBPUSD_PACKET_PATH) or {}
    btc_branch_decision = load_optional_json(BTC_BRANCH_DECISION_PATH) or {}
    incumbent_study = load_optional_json(INCUMBENT_STUDY_PATH) or {}
    seat_board = load_optional_json(SEAT_BOARD_PATH) or {}

    proof_rows = {row["symbol"]: row for row in proof.get("rows", [])}
    transfer_rows = {row["symbol"]: row for row in transfer.get("rows", [])}
    study_rows = {row["symbol"]: row for row in incumbent_study.get("rows", [])}
    optimizer_rows = {row["surface_id"]: row for row in optimizer.get("rows", [])}
    optimizer_recon_rows = {row["surface_id"]: row for row in optimizer_recon.get("rows", [])}
    optimizer_decision_rows = {row["surface_id"]: row for row in optimizer_decision.get("rows", [])}
    symbol_priors = dict(controller_priors.get("symbol_priors") or {})
    btc_prior = dict(symbol_priors.get("BTCUSD") or {})
    btc_branch_rows = {row["branch_id"]: row for row in btc_branch_decision.get("rows", [])}
    btc_restore_branch = dict(btc_branch_rows.get("launch_restore_comparison_shadow") or {})
    btc_true_adaptive_branch = dict(btc_branch_rows.get("define_true_adaptive_candidate_then_build") or {})
    btc_parked_branch = dict(btc_branch_rows.get("hold_parked_artifact_only") or {})

    nzdusd_completed = bool(nzdusd_probe and nzdusd_probe.get("summary", {}).get("runtime_present"))
    nzdusd_why = transfer_rows["NZDUSD"]["rationale"]
    if nzdusd_completed:
        nzdusd_why = str(nzdusd_probe.get("summary", {}).get("completion_read") or nzdusd_why)

    btc_prior_action = str(btc_prior.get("promotion_action") or "")
    optimizer_decision_ready_surfaces = int((optimizer_decision.get("summary") or {}).get("decision_ready_surfaces") or 0)
    optimizer_decision_read = str((optimizer_decision_rows.get("optimal_portfolio_optimizer") or {}).get("decision") or "")
    btc_restore_launch_status = str(btc_restore_branch.get("launch_status") or "")
    btc_restore_launch_read = str(btc_restore_branch.get("launch_read") or "")
    btc_restore_runtime_blocked = btc_restore_launch_status == "hold_runtime_repair_candidate"
    btc_study_row = dict(study_rows.get("BTCUSD") or {})
    gbp_study_row = dict(study_rows.get("GBPUSD") or {})
    eur_study_row = dict(study_rows.get("EURUSD") or {})
    usdjpy_study_row = dict(study_rows.get("USDJPY") or {})
    nzd_study_row = dict(study_rows.get("NZDUSD") or {})
    btc_runtime_overlays = runtime_overlays_for_study_row(btc_study_row)
    btc_runtime_overlay_read = str(btc_study_row.get("adaptive_runtime_overlay_read") or "")
    btc_runtime_obligation_class, btc_runtime_obligation_read = runtime_obligation(
        btc_runtime_overlays,
        btc_runtime_overlay_read,
    )
    gbp_runtime_overlays = runtime_overlays_for_study_row(gbp_study_row)
    gbp_runtime_overlay_read = str(gbp_study_row.get("adaptive_runtime_overlay_read") or "")
    gbp_runtime_obligation_class, gbp_runtime_obligation_read = runtime_obligation(
        gbp_runtime_overlays,
        gbp_runtime_overlay_read,
    )
    eur_runtime_overlays = runtime_overlays_for_study_row(eur_study_row)
    eur_runtime_overlay_read = str(eur_study_row.get("adaptive_runtime_overlay_read") or "")
    eur_runtime_obligation_class, eur_runtime_obligation_read = runtime_obligation(
        eur_runtime_overlays,
        eur_runtime_overlay_read,
    )
    usdjpy_runtime_overlays = runtime_overlays_for_study_row(usdjpy_study_row)
    usdjpy_runtime_overlay_read = str(usdjpy_study_row.get("adaptive_runtime_overlay_read") or "")
    usdjpy_runtime_obligation_class, usdjpy_runtime_obligation_read = runtime_obligation(
        usdjpy_runtime_overlays,
        usdjpy_runtime_overlay_read,
    )
    nzd_runtime_overlays = runtime_overlays_for_study_row(nzd_study_row)
    nzd_runtime_overlay_read = str(nzd_study_row.get("adaptive_runtime_overlay_read") or "")
    nzd_runtime_obligation_class, nzd_runtime_obligation_read = runtime_obligation(
        nzd_runtime_overlays,
        nzd_runtime_overlay_read,
    )

    gbp_profit_mode = str(gbp_study_row.get("adaptive_profit_mode") or "")
    gbp_next_action, gbp_next_action_detailed = next_action_class_pair(
        gbp_profit_mode,
        str(gbp_study_row.get("study_status") or ""),
    )
    gbp_study_status = str(gbp_study_row.get("study_status") or "")
    gbp_runtime_status = str(gbp_study_row.get("adaptive_runtime_status") or "")
    gbp_packet_ready = bool(dict(gbpusd_packet.get("summary") or {}).get("packet_defined"))
    gbp_packet_read = str(dict(gbpusd_packet.get("summary") or {}).get("completion_read") or "")
    usdjpy_profit_mode = str(usdjpy_study_row.get("adaptive_profit_mode") or "")
    usdjpy_next_action, usdjpy_next_action_detailed = next_action_class_pair(
        usdjpy_profit_mode,
        str(usdjpy_study_row.get("study_status") or ""),
    )
    eur_profit_mode = str(eur_study_row.get("adaptive_profit_mode") or "")
    nzd_profit_mode = str(nzd_study_row.get("adaptive_profit_mode") or "")
    btc_next_action, btc_next_action_detailed = next_action_class_pair(
        str(btc_study_row.get("adaptive_profit_mode") or ""),
        str(btc_study_row.get("study_status") or ""),
    )
    nzd_next_action, nzd_next_action_detailed = next_action_class_pair(
        nzd_profit_mode,
        str(nzd_study_row.get("study_status") or ""),
    )
    eur_next_action, eur_next_action_detailed = next_action_class_pair(
        eur_profit_mode,
        str(eur_study_row.get("study_status") or ""),
    )

    btc_restore_why = str(
        btc_restore_branch.get("why")
        or "Restore comparison is the first explicit executable BTC branch that preserves the live baseline."
    )
    if btc_prior_action:
        btc_restore_why = f"{btc_restore_why} Current controller-prior posture is `{btc_prior_action}`."
    if btc_restore_runtime_blocked:
        btc_restore_why = (
            f"{btc_restore_why} Current branch launch status is `{btc_restore_launch_status}`"
            + (f" (`{btc_restore_launch_read}`)." if btc_restore_launch_read else ".")
        )
    if btc_study_row:
        btc_restore_why = (
            f"{btc_restore_why} Current profit mode is `{btc_study_row.get('adaptive_profit_mode')}`"
            f" and next-action class is `{btc_next_action_detailed}`."
        )
    if btc_runtime_obligation_class:
        btc_restore_why = (
            f"{btc_restore_why} Current runtime obligation is `{btc_runtime_obligation_class}`: "
            f"{btc_runtime_obligation_read or btc_runtime_overlay_read}"
        )

    btc_true_adaptive_why = str(
        btc_true_adaptive_branch.get("why")
        or "The true adaptive BTC branch is the doctrinal target, but it is not yet the first clean executable move."
    )
    if optimizer_decision_read:
        btc_true_adaptive_why = f"{btc_true_adaptive_why} Optimizer rule: {optimizer_decision_read}"

    usdjpy_fault_row = proof_rows.get("USDJPY", {})
    usdjpy_fault = str(next(iter(usdjpy_fault_row.get("blockers") or []), "") or "bounded_close_style_runtime_fault")

    gbp_queue_why = (
        f"{gbp_packet_read or 'GBPUSD is currently packet-defined but not yet running as a dedicated adaptive challenger.'} "
        f"Profit mode remains `{gbp_profit_mode or 'unknown'}`, so the next honest max-profit step is deliberate shadow launch and first-proof collection on the same study surface as the incumbent."
    )
    if gbp_runtime_status == "already_running_monitor_only":
        if gbp_study_status == "first_path_opened_wait_shared_score_refresh":
            gbp_queue_why = (
                f"{gbp_packet_read or 'GBPUSD has an explicit adaptive trend-harvest packet.'} "
                f"Profit mode remains `{gbp_profit_mode or 'unknown'}`, and the dedicated comparison lane is already running with a real first open, "
                "so the next honest max-profit step is first-close collection and shared-score refresh on the same study surface as the incumbent."
            )
        elif gbp_study_status == "first_path_recorded_wait_shared_score_refresh":
            gbp_queue_why = (
                f"{gbp_packet_read or 'GBPUSD has an explicit adaptive trend-harvest packet.'} "
                f"Profit mode remains `{gbp_profit_mode or 'unknown'}`, and the dedicated comparison lane is already running with a real first-path verdict, "
                "so the next honest max-profit step is shared-score refresh and incumbent comparison rather than a fresh launch debate."
            )
        else:
            gbp_queue_why = (
                f"{gbp_packet_read or 'GBPUSD has an explicit adaptive trend-harvest packet.'} "
                f"Profit mode remains `{gbp_profit_mode or 'unknown'}`, and the dedicated comparison lane is already running, "
                "so the next honest max-profit step is lane-local proof collection rather than another launch pass."
            )

    usdcad_seat_row = find_symbol_row(list(seat_board.get("rows") or []), "USDCAD")
    usdcad_contract_task: dict[str, Any] | None = None
    if usdcad_seat_row and str(usdcad_seat_row.get("seat_unblocker_action") or "") == "prepare_first_live_seat_case":
        usdcad_runtime_status = str(usdcad_seat_row.get("best_challenger_runtime_status") or "")
        usdcad_actionability_status = str(usdcad_seat_row.get("seat_actionability_status") or "")
        usdcad_why = (
            f"{str(usdcad_seat_row.get('seat_unblocker_read') or '')} "
            f"Current challenger `{str(usdcad_seat_row.get('best_challenger_lane') or '')}` reads "
            f"`{str(usdcad_seat_row.get('best_challenger_candidate_class') or '')}` / `{usdcad_runtime_status}`."
        ).strip()
        queue_read = str(usdcad_seat_row.get("seat_actionability_read") or "")
        if queue_read:
            usdcad_why = f"{usdcad_why} {queue_read}".strip()
        usdcad_contract_task = {
            "task_id": "usdcad_first_live_seat_contract",
            "priority": 5,
            "status": "ready",
            "lane": "shadow HH",
            "title": "Formalize the USDCAD first live-seat decision contract",
            "why": usdcad_why,
            "depends_on": [],
            "allowed_inputs": [
                str(usdcad_seat_row.get("best_challenger_lane") or ""),
                "reports/per_symbol_live_seat_board.json",
            ],
            "blocked_by": [],
            "profit_mode": "",
            "next_action_class": "formalize_first_live_seat_contract",
            "next_action_class_detailed": "formalize_first_live_seat_contract",
            "runtime_overlays": [],
            "runtime_overlay_read": "",
            "runtime_obligation_class": "",
            "runtime_obligation_read": "",
            "machine_truth": {
                "seat_verdict": str(usdcad_seat_row.get("seat_verdict") or ""),
                "seat_actionability_status": usdcad_actionability_status,
                "best_challenger_candidate_class": str(usdcad_seat_row.get("best_challenger_candidate_class") or ""),
                "best_challenger_runtime_status": usdcad_runtime_status,
                "seat_execution_gate_status": str(usdcad_seat_row.get("seat_execution_gate_status") or ""),
            },
        }

    tasks = [
        {
            "task_id": "btc_restore_comparison_shadow",
            "priority": 1,
            "status": "blocked" if btc_restore_runtime_blocked else "ready",
            "lane": "shadow crypto",
            "title": str(btc_restore_branch.get("title") or "Launch the BTC M15 warp restore comparison shadow"),
            "why": btc_restore_why,
            "depends_on": [],
            "allowed_inputs": list(
                btc_restore_branch.get("allowed_inputs")
                or ["shadow_btcusd_m15_warp_restore_v1", "reports/btc_m15_warp_restore_board.json"]
            ),
            "blocked_by": ["runtime_repair_required_before_relaunch"] if btc_restore_runtime_blocked else [],
            "profit_mode": str(btc_study_row.get("adaptive_profit_mode") or ""),
            "next_action_class": btc_next_action,
            "next_action_class_detailed": btc_next_action_detailed,
            "runtime_overlays": btc_runtime_overlays,
            "runtime_overlay_read": btc_runtime_overlay_read,
            "runtime_obligation_class": btc_runtime_obligation_class,
            "runtime_obligation_read": btc_runtime_obligation_read,
            "machine_truth": {
                "recommended_branch_id": str((btc_branch_decision.get("summary") or {}).get("recommended_branch_id") or ""),
                "recommended_branch_launch_status": btc_restore_launch_status,
                "optimizer_decision_ready_surfaces": optimizer_decision_ready_surfaces,
                "btc_prior_promotion_action": btc_prior_action,
                "runtime_overlays": btc_runtime_overlays,
                "runtime_obligation_class": btc_runtime_obligation_class,
            },
        },
        {
            "task_id": "btc_true_adaptive_candidate",
            "priority": 2,
            "status": "blocked",
            "lane": "shadow crypto",
            "title": str(
                btc_true_adaptive_branch.get("title") or "Define and build the true downtrend-aware adaptive BTC candidate"
            ),
            "why": btc_true_adaptive_why,
            "depends_on": [],
            "allowed_inputs": list(
                btc_true_adaptive_branch.get("allowed_inputs")
                or [proof_rows["BTCUSD"]["recommended_shape_id"], optimizer_rows["atr_step_optimizer"]["surface_id"]]
            ),
            "blocked_by": list(
                btc_true_adaptive_branch.get("blockers")
                or ["restore_comparison_shadow_should_land_first"]
            ),
            "profit_mode": str(btc_study_row.get("adaptive_profit_mode") or ""),
            "next_action_class": btc_next_action,
            "next_action_class_detailed": btc_next_action_detailed,
            "runtime_overlays": btc_runtime_overlays,
            "runtime_overlay_read": btc_runtime_overlay_read,
            "runtime_obligation_class": btc_runtime_obligation_class,
            "runtime_obligation_read": btc_runtime_obligation_read,
            "machine_truth": {
                "doctrine_target_branch_id": str((btc_branch_decision.get("summary") or {}).get("doctrine_target_branch_id") or ""),
                "recommended_branch_launch_status": btc_restore_launch_status,
                "optimizer_decision_ready_surfaces": optimizer_decision_ready_surfaces,
                "runtime_overlays": btc_runtime_overlays,
                "runtime_obligation_class": btc_runtime_obligation_class,
            },
        },
        {
            "task_id": "btc_parked_artifact_review",
            "priority": 3,
            "status": "completed",
            "lane": "shadow crypto",
            "title": str(
                btc_parked_branch.get("title") or "Keep the parked BTC adaptive artifact in hold/manual-review only"
            ),
            "why": str(
                btc_parked_branch.get("why")
                or "Completed: the parked BTC adaptive artifact has an explicit audit and should not be treated as the next adaptive launch."
            ),
            "depends_on": [],
            "allowed_inputs": list(
                btc_parked_branch.get("allowed_inputs") or ["shadow_btcusd_m15_adaptive_regime"]
            ),
            "blocked_by": [],
            "profit_mode": str(btc_study_row.get("adaptive_profit_mode") or ""),
            "next_action_class": btc_next_action,
            "next_action_class_detailed": btc_next_action_detailed,
            "runtime_overlays": btc_runtime_overlays,
            "runtime_overlay_read": btc_runtime_overlay_read,
            "runtime_obligation_class": btc_runtime_obligation_class,
            "runtime_obligation_read": btc_runtime_obligation_read,
        },
        {
            "task_id": "gbpusd_adaptive_comparison_packet",
            "priority": 4,
            "status": "ready" if gbp_packet_ready else "completed",
            "lane": "shadow FX",
            "title": "Build the GBPUSD adaptive comparison packet against the incumbent live seat",
            "why": gbp_queue_why,
            "depends_on": [],
            "allowed_inputs": [
                str(gbp_study_row.get("adaptive_shape_id") or proof_rows["GBPUSD"]["recommended_shape_id"]),
                "reports/gbpusd_adaptive_shadow_packet.json",
                str(gbp_study_row.get("incumbent_lane") or ""),
            ],
            "blocked_by": [] if gbp_packet_ready else ["launch_packet_not_defined"],
            "profit_mode": gbp_profit_mode,
            "next_action_class": gbp_next_action,
            "next_action_class_detailed": gbp_next_action_detailed,
            "runtime_overlays": gbp_runtime_overlays,
            "runtime_overlay_read": gbp_runtime_overlay_read,
            "runtime_obligation_class": gbp_runtime_obligation_class,
            "runtime_obligation_read": gbp_runtime_obligation_read,
            "machine_truth": {
                "study_status": gbp_study_status,
                "adaptive_runtime_status": gbp_runtime_status,
                "incumbent_lane": str(gbp_study_row.get("incumbent_lane") or ""),
                "runtime_overlays": gbp_runtime_overlays,
                "runtime_obligation_class": gbp_runtime_obligation_class,
            },
        },
    ]
    if usdcad_contract_task:
        tasks.append(usdcad_contract_task)
    tasks.extend(
        [
        {
            "task_id": "nzdusd_transfer_probe",
            "priority": 6,
            "status": "completed" if nzdusd_completed else "ready",
            "lane": "shadow FX",
            "title": "Launch NZDUSD adapt-first transfer probe from the GBPUSD donor family",
            "why": f"{nzdusd_why} Current profit mode is `{nzd_profit_mode}`.",
            "depends_on": [],
            "allowed_inputs": [
                transfer_rows["NZDUSD"]["recommended_shape_id"],
                transfer_rows["GBPUSD"]["recommended_shape_id"],
            ],
            "blocked_by": [],
            "profit_mode": nzd_profit_mode,
            "next_action_class": nzd_next_action,
            "next_action_class_detailed": nzd_next_action_detailed,
            "runtime_overlays": nzd_runtime_overlays,
            "runtime_overlay_read": nzd_runtime_overlay_read,
            "runtime_obligation_class": nzd_runtime_obligation_class,
            "runtime_obligation_read": nzd_runtime_obligation_read,
        },
        {
            "task_id": "usdjpy_bounded_forward_proof",
            "priority": 7,
            "status": "ready",
            "lane": "shadow FX",
            "title": "Run fresh USDJPY bounded forward proof under the restored friction-survivor branch",
            "why": (
                "USDJPY is no longer honestly blocked by the archival bounded runtime fault. "
                "The remaining max-profit job is fresh bounded forward proof under real executability pressure."
            ),
            "depends_on": [],
            "allowed_inputs": [
                proof_rows["USDJPY"]["recommended_shape_id"],
                "reports/adaptive_lattice_proof_board.json",
                "reports/adaptive_incumbent_study_board.json",
            ],
            "blocked_by": [],
            "profit_mode": usdjpy_profit_mode,
            "next_action_class": usdjpy_next_action,
            "next_action_class_detailed": usdjpy_next_action_detailed,
            "runtime_overlays": usdjpy_runtime_overlays,
            "runtime_overlay_read": usdjpy_runtime_overlay_read,
            "runtime_obligation_class": usdjpy_runtime_obligation_class,
            "runtime_obligation_read": usdjpy_runtime_obligation_read,
            "machine_truth": {
                "study_status": str(usdjpy_study_row.get("study_status") or ""),
                "stage": str(usdjpy_fault_row.get("stage") or ""),
                "family": str(usdjpy_fault_row.get("family") or ""),
                "runtime_overlays": usdjpy_runtime_overlays,
                "runtime_obligation_class": usdjpy_runtime_obligation_class,
            },
        },
        {
            "task_id": "optimizer_reconciliation_harness",
            "priority": 8,
            "status": "completed",
            "lane": "research tooling",
            "title": "Build a harness reconciliation check for reconcile-first optimizers",
            "why": "Completed: both reconcile-first optimizer surfaces now emit native inline canonical replay truth and no longer rely on sidecar-only governance.",
            "depends_on": [],
            "allowed_inputs": [
                optimizer_rows["allocation_optimizer"]["surface_id"],
                optimizer_rows["optimal_portfolio_optimizer"]["surface_id"],
            ],
            "blocked_by": [],
        },
        {
            "task_id": "optimizer_decision_surface",
            "priority": 9,
            "status": "completed",
            "lane": "research tooling",
            "title": "Build one operator-facing decision surface for native vs canonical optimizer truth",
            "why": (
                "Completed: the optimizer decision board now compresses both dual-mode optimizer surfaces into one deployment-facing read, "
                "and adaptive planning should treat its canonical-only decisions as the trust boundary."
            ),
            "depends_on": [],
            "allowed_inputs": [
                optimizer_recon_rows["allocation_optimizer"]["surface_id"],
                optimizer_recon_rows["optimal_portfolio_optimizer"]["surface_id"],
            ],
            "blocked_by": [],
        },
        {
            "task_id": "usdjpy_bounded_fault_repair",
            "priority": 10,
            "status": "completed",
            "lane": "runtime repair",
            "title": "Clear the old bounded close_style runtime fault as the active USDJPY adaptive blocker",
            "why": "Completed: the adaptive proof board now treats the old bounded close_style constructor fault as historical-only, so USDJPY no longer sits behind runtime repair as the first adaptive blocker.",
            "depends_on": [],
            "allowed_inputs": [
                usdjpy_fault,
            ],
            "blocked_by": [],
            "profit_mode": usdjpy_profit_mode,
            "next_action_class": usdjpy_next_action,
            "next_action_class_detailed": usdjpy_next_action_detailed,
            "runtime_overlays": usdjpy_runtime_overlays,
            "runtime_overlay_read": usdjpy_runtime_overlay_read,
            "runtime_obligation_class": usdjpy_runtime_obligation_class,
            "runtime_obligation_read": usdjpy_runtime_obligation_read,
        },
        {
            "task_id": "eurusd_friction_survivor_research",
            "priority": 11,
            "status": "blocked",
            "lane": "shadow FX",
            "title": "Keep EURUSD on friction-survivor research until forward proof beats the incumbent",
            "why": (
                "EURUSD is not a packet gap problem. It is a friction-survivor research problem with an incumbent-led live seat, "
                "so the right next move is better executability/forward proof rather than forced challenger promotion."
            ),
            "depends_on": [],
            "allowed_inputs": [
                str(eur_study_row.get("adaptive_shape_id") or proof_rows["EURUSD"]["recommended_shape_id"]),
                str(eur_study_row.get("incumbent_lane") or ""),
                "reports/adaptive_incumbent_study_board.json",
            ],
            "blocked_by": ["forward_proof_not_yet_superior_to_incumbent"],
            "profit_mode": eur_profit_mode,
            "next_action_class": eur_next_action,
            "next_action_class_detailed": eur_next_action_detailed,
            "runtime_overlays": eur_runtime_overlays,
            "runtime_overlay_read": eur_runtime_overlay_read,
            "runtime_obligation_class": eur_runtime_obligation_class,
            "runtime_obligation_read": eur_runtime_obligation_read,
        },
    ]
    )
    return tasks


def build_payload(*, refresh_inputs: bool = False) -> dict[str, Any]:
    if refresh_inputs:
        refresh_upstream_reports()

    proof = load_json(PROOF_PATH)
    transfer = load_json(TRANSFER_PATH)
    optimizer = load_json(OPTIMIZER_PATH)
    optimizer_recon = load_json(OPTIMIZER_RECON_PATH)
    optimizer_decision = load_optional_json(OPTIMIZER_DECISION_PATH) or {}
    controller_priors = load_optional_json(CONTROLLER_PRIORS_PATH) or {}
    btc_audit = load_optional_json(BTC_AUDIT_PATH)
    btc_shadow_plan = load_optional_json(BTC_SHADOW_PLAN_PATH)
    btc_branch_decision = load_optional_json(BTC_BRANCH_DECISION_PATH) or {}
    incumbent_study = load_optional_json(INCUMBENT_STUDY_PATH) or {}
    seat_board = load_optional_json(SEAT_BOARD_PATH) or {}
    tasks = build_tasks()
    ready_tasks = [task for task in tasks if task["status"] == "ready"]
    completed_tasks = [task for task in tasks if task["status"] == "completed"]
    blocked_tasks = [task for task in tasks if task["status"] == "blocked"]
    highest_priority_ready = ready_tasks[0] if ready_tasks else None
    highest_priority_blocked = blocked_tasks[0] if blocked_tasks else None
    runtime_obligation_tasks = [task for task in tasks if str(task.get("runtime_obligation_class") or "")]
    highest_priority_runtime_obligation = next(
        (task for task in tasks if str(task.get("runtime_obligation_class") or "")),
        None,
    )
    input_surfaces = collect_input_surfaces(
        proof,
        transfer,
        optimizer,
        optimizer_recon,
        optimizer_decision,
        controller_priors,
        load_optional_json(GBPUSD_PACKET_PATH) or {},
        btc_audit,
        btc_shadow_plan,
        btc_branch_decision,
        incumbent_study,
        seat_board,
    )
    stale_input_count = sum(1 for row in input_surfaces if row["status"] != "fresh")
    lane_counts: dict[str, int] = {}
    for task in tasks:
        lane = str(task.get("lane") or "")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task_count": len(tasks),
        "ready_count": len(ready_tasks),
        "completed_count": len(completed_tasks),
        "blocked_count": len(blocked_tasks),
        "input_surfaces": input_surfaces,
        "summary": {
            "highest_priority_ready_task_id": str((highest_priority_ready or {}).get("task_id") or ""),
            "highest_priority_ready_title": str((highest_priority_ready or {}).get("title") or ""),
            "highest_priority_ready_lane": str((highest_priority_ready or {}).get("lane") or ""),
            "highest_priority_blocked_task_id": str((highest_priority_blocked or {}).get("task_id") or ""),
            "highest_priority_blocked_title": str((highest_priority_blocked or {}).get("title") or ""),
            "runtime_obligation_task_count": len(runtime_obligation_tasks),
            "highest_priority_runtime_obligation_task_id": str((highest_priority_runtime_obligation or {}).get("task_id") or ""),
            "highest_priority_runtime_obligation_class": str((highest_priority_runtime_obligation or {}).get("runtime_obligation_class") or ""),
            "btc_recommended_branch_id": str((btc_branch_decision.get("summary") or {}).get("recommended_branch_id") or ""),
            "btc_doctrine_target_branch_id": str((btc_branch_decision.get("summary") or {}).get("doctrine_target_branch_id") or ""),
            "lane_counts": lane_counts,
            "fresh_input_surface_count": len(input_surfaces) - stale_input_count,
            "stale_input_surface_count": stale_input_count,
        },
        "leadership_read": [
            (
                f"Current adaptive-lattice next move is `{highest_priority_ready['title']}` in `{highest_priority_ready['lane']}`."
                if highest_priority_ready
                else "No adaptive-lattice task is currently marked ready."
            ),
            (
                f"Profit-mode-aware ready work now includes GBPUSD packetization under `{next((task.get('profit_mode') for task in tasks if task.get('task_id') == 'gbpusd_adaptive_comparison_packet'), '')}` posture and USDJPY bounded forward proof under `{next((task.get('profit_mode') for task in tasks if task.get('task_id') == 'usdjpy_bounded_forward_proof'), '')}` posture."
                if any(task.get("status") == "ready" for task in tasks if task.get("task_id") in {"gbpusd_adaptive_comparison_packet", "usdjpy_bounded_forward_proof"})
                else "No profit-mode-driven cross-symbol follow-up is currently marked ready."
            ),
            (
                f"The first blocked seam is `{highest_priority_blocked['title']}`, and it is blocked on runtime repair rather than doctrine."
                if highest_priority_blocked
                and "runtime_repair_required_before_relaunch" in list(highest_priority_blocked.get("blocked_by") or [])
                else f"The first blocked seam is `{highest_priority_blocked['title']}`, which is doctrinal rather than runtime repair."
                if highest_priority_blocked
                else "There is no currently blocked adaptive-lattice seam."
            ),
            (
                f"The highest-priority runtime-overlay obligation is `{highest_priority_runtime_obligation.get('runtime_obligation_class')}` on `{highest_priority_runtime_obligation.get('task_id')}`: {highest_priority_runtime_obligation.get('runtime_obligation_read') or highest_priority_runtime_obligation.get('runtime_overlay_read') or ''}"
                if highest_priority_runtime_obligation
                else "No current adaptive queue task carries an explicit runtime-overlay obligation."
            ),
            (
                f"BTC branch doctrine is now explicit: next executable branch is `{(btc_branch_decision.get('summary') or {}).get('recommended_branch_id')}`, while the pinned perfection target remains `{(btc_branch_decision.get('summary') or {}).get('doctrine_target_branch_id')}`."
                if btc_branch_decision
                else "BTC adaptive branch doctrine is not currently loaded."
            ),
            "Trust order for adaptive launch decisions is the BTC branch decision board over older blended BTC queue language, and optimizer outputs stay canonical-first.",
            (
                f"Adaptive queue input freshness is `fresh={len(input_surfaces) - stale_input_count}` / `stale={stale_input_count}`, so stale upstream reports should be repaired before using this board as authority."
                if stale_input_count
                else f"All `{len(input_surfaces)}` upstream adaptive authority surfaces were freshly rebuilt for this queue."
            ),
        ],
        "notes": [
            "This queue now treats BTC restore comparison, parked artifact review, and true adaptive-candidate work as separate branches rather than one generic BTC row.",
            "Profit mode now affects the next-action class: `guarded_toxic_flow` favors control-shadow/path-safety evidence, `trend_harvest` favors executable comparison packets, and `friction_survivor` favors forward executability proof before promotion.",
            "Runtime overlays now stay separate from `next_action_class` as `runtime_obligation_class` so burst-governed guarded-toxic-flow rows do not get flattened into generic control-shadow language.",
            "Use canonical optimizer decisions, not native optimizer projections, when a task cites optimizer surfaces as planning inputs.",
        ],
        "tasks": tasks,
    }


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# Adaptive Lattice Lab Queue",
        "",
        "This queue merges the current proof board, transfer board, optimizer trust, BTC branch doctrine, and controller priors into the next safe adaptive steps.",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload.get("leadership_read", []):
        lines.append(f"- {line}")

    summary = payload.get("summary", {})
    lines.extend(
        [
            "",
            "## Current Read",
            "",
            f"- ready tasks: `{payload['ready_count']}`",
            f"- completed tasks: `{payload['completed_count']}`",
            f"- blocked tasks: `{payload['blocked_count']}`",
            f"- highest-priority ready: `{summary.get('highest_priority_ready_task_id')}`",
            f"- highest-priority blocked: `{summary.get('highest_priority_blocked_task_id')}`",
            f"- runtime-obligation tasks: `{summary.get('runtime_obligation_task_count')}`",
            f"- highest-priority runtime obligation: `{summary.get('highest_priority_runtime_obligation_task_id')}` / `{summary.get('highest_priority_runtime_obligation_class')}`",
            f"- btc recommended branch: `{summary.get('btc_recommended_branch_id')}`",
            f"- btc doctrine target: `{summary.get('btc_doctrine_target_branch_id')}`",
            f"- fresh inputs: `{summary.get('fresh_input_surface_count')}`",
            f"- stale inputs: `{summary.get('stale_input_surface_count')}`",
            "",
            "## Input Surfaces",
            "",
            "| Surface | Status | Generated | Age (h) |",
            "|---|---|---|---|",
        ]
    )

    for row in payload.get("input_surfaces", []):
        age_hours = "-" if row.get("age_hours") is None else row["age_hours"]
        lines.append(
            f"| `{row['surface_id']}` | `{row['status']}` | `{row['generated_at'] or '-'}` | {age_hours} |"
        )

    lines.extend(
        [
            "",
            "## Queue",
            "",
            "| Priority | Status | Lane | Profit Mode | Action Class | Runtime Obligation | Task | Why |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )

    for task in payload["tasks"]:
        lines.append(
            f"| {task['priority']} | `{task['status']}` | {task['lane']} | `{task.get('profit_mode') or '-'}` | `{task.get('next_action_class') or '-'}` | `{task.get('runtime_obligation_class') or '-'}` | {task['title']} | {task['why']} |"
        )

    lines.extend(["", "## Inputs And Blockers", ""])
    for task in payload["tasks"]:
        lines.append(f"### {task['task_id']}")
        if task["depends_on"]:
            lines.append("- depends on: " + ", ".join(f"`{item}`" for item in task["depends_on"]))
        if task["allowed_inputs"]:
            lines.append("- allowed inputs: " + ", ".join(f"`{item}`" for item in task["allowed_inputs"]))
        if task["blocked_by"]:
            lines.append("- blocked by: " + ", ".join(f"`{item}`" for item in task["blocked_by"]))
        if task.get("runtime_overlays"):
            lines.append("- runtime overlays: " + ", ".join(f"`{item}`" for item in task["runtime_overlays"]))
        if task.get("runtime_obligation_class"):
            lines.append(f"- runtime obligation: `{task['runtime_obligation_class']}`")
            lines.append(f"- runtime obligation read: {task.get('runtime_obligation_read') or task.get('runtime_overlay_read')}")
        machine_truth = dict(task.get("machine_truth") or {})
        if machine_truth:
            lines.append(f"- machine truth: `{machine_truth}`")
        lines.append("")

    notes = list(payload.get("notes") or [])
    if notes:
        lines.extend(["## Notes", ""])
        for note in notes:
            lines.append(f"- {note}")

    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-refresh-inputs",
        action="store_true",
        help="Use the existing adaptive upstream boards instead of rebuilding them first.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_payload(refresh_inputs=not args.skip_refresh_inputs)
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_markdown(payload)
    print(f"Wrote {OUTPUT_MD}")
    print(f"Wrote {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
