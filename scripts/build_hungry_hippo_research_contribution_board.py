#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

CONTROLLER_PRIORS_PATH = CONFIGS / "adaptive_controller_priors.json"
PROFIT_BOARD_PATH = REPORTS / "profit_theory_graduation_board.json"
READINESS_BOARD_PATH = REPORTS / "shadow_graduation_readiness_board.json"
PROMOTION_GATE_PATH = REPORTS / "shadow_to_live_promotion_gate_board.json"
RUBRIC_BOARD_PATH = REPORTS / "graduation_rubric_board.json"
GUARDRAIL_AUDIT_PATH = REPORTS / "hungry_hippo_shapeshifter_guardrail_audit.json"
NEXT_ACTION_BOARD_PATH = REPORTS / "hungry_hippo_next_action_board.json"
PORTABILITY_BOARD_PATH = REPORTS / "hungry_hippo_symbol_portability_board.json"
POLICY_GAP_BOARD_PATH = REPORTS / "hungry_hippo_policy_gap_board.json"

OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_research_contribution_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_research_contribution_board.md"

GBP_HARVEST_CANDIDATE_ALIASES = (
    "GBPUSD alpha=0.5 FX harvest path",
)
ETH_REBUILD_CANDIDATE_ALIASES = (
    "ETHUSD M5 step14 normalized control",
    "ETHUSD M5 step5 Hungry Hippo rebuild",
)
BTC_DOWNTREND_CANDIDATE_ALIASES = (
    "BTCUSD M15 sell-tight downtrend shape",
)
NAS100_ASYM_CANDIDATE_ALIASES = (
    "NAS100 asym breakout family lane",
)
US30_ASYM_CANDIDATE_ALIASES = (
    "US30 asym breakout family lane",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def first_row(rows: list[dict[str, Any]], key: str, value: str) -> dict[str, Any]:
    for row in rows:
        if isinstance(row, dict) and str(row.get(key) or "") == value:
            return row
    raise KeyError(f"row not found for {key}={value}")


def first_row_by_aliases(
    rows: list[dict[str, Any]],
    key: str,
    aliases: tuple[str, ...],
    *,
    fallback_tokens: tuple[str, ...] = (),
) -> dict[str, Any]:
    alias_set = {alias.strip().lower() for alias in aliases if alias.strip()}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get(key) or "").strip().lower()
        if label in alias_set:
            return row
    if fallback_tokens:
        lowered_tokens = tuple(token.strip().lower() for token in fallback_tokens if token.strip())
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = str(row.get(key) or "").strip().lower()
            if all(token in label for token in lowered_tokens):
                return row
    raise KeyError(f"row not found for {key} aliases={aliases}")


def summarize_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get(key) or "")
        counts[label] = counts.get(label, 0) + 1
    return counts


def format_symbol_list(symbols: list[str]) -> str:
    return str(symbols or ["none"])


def first_action(rows: list[dict[str, Any]], action: str, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    for row in rows:
        if isinstance(row, dict) and str(row.get("action") or "") == action:
            return row
    if default is not None:
        return default
    raise KeyError(f"row not found for action={action}")


def build_payload(
    controller_priors: dict[str, Any],
    profit_board: dict[str, Any],
    readiness_board: dict[str, Any],
    promotion_gate: dict[str, Any],
    rubric_board: dict[str, Any],
    guardrail_audit: dict[str, Any],
    next_action_board: dict[str, Any],
    portability_board: dict[str, Any],
    policy_gap_board: dict[str, Any],
) -> dict[str, Any]:
    symbol_priors = dict(controller_priors.get("symbol_priors") or {})

    profit_rows = list(profit_board.get("rows") or [])
    readiness_rows = list(readiness_board.get("rows") or [])
    gate_rows = list(promotion_gate.get("rows") or [])
    rubric_rows = list(rubric_board.get("rows") or [])
    guardrail_rows = list(guardrail_audit.get("rows") or [])
    next_action_rows = list(next_action_board.get("rows") or [])
    portability_rows = list(portability_board.get("rows") or [])
    portability_summary = dict(portability_board.get("summary") or {})
    policy_gap_summary = dict(policy_gap_board.get("summary") or {})
    promotable_now_symbols = [
        str(row.get("symbol") or "")
        for row in guardrail_rows
        if str(row.get("status") or "") == "promotable_now" and str(row.get("symbol") or "")
    ]
    missing_policy_symbols = [str(symbol) for symbol in list(portability_summary.get("missing_policy_symbols") or []) if str(symbol)]
    missing_launch_contract_symbols = [
        str(symbol) for symbol in list(portability_summary.get("missing_launch_contract_symbols") or []) if str(symbol)
    ]
    policy_seed_now_symbols = [
        str(symbol) for symbol in list(policy_gap_summary.get("policy_seed_now_symbols") or []) if str(symbol)
    ]
    policy_seed_next_symbols = [
        str(symbol) for symbol in list(policy_gap_summary.get("policy_seed_next_symbols") or []) if str(symbol)
    ]
    waiting_forward_proof_symbols = [str(symbol) for symbol in list(portability_summary.get("waiting_forward_proof_symbols") or []) if str(symbol)]
    promotable_launch_contract_gap_symbols = [
        str(row.get("symbol") or "")
        for row in portability_rows
        if str(row.get("generalization_status") or "") == "portable_missing_launch_contract"
        and str(row.get("guardrail_status") or "") == "promotable_now"
        and str(row.get("deployment_verdict") or "") == "cleared_for_shadow_discussion"
        and str(row.get("symbol") or "")
    ]
    manual_review_launch_contract_gap_symbols = [
        str(row.get("symbol") or "")
        for row in portability_rows
        if str(row.get("generalization_status") or "") == "portable_missing_launch_contract"
        and str(row.get("deployment_verdict") or "") == "manual_review"
        and str(row.get("symbol") or "")
    ]
    eth_reconcile_action = first_action(
        next_action_rows,
        "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane",
    )
    nas100_watch_action = first_action(
        next_action_rows,
        "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves",
        default={},
    )

    gbp_ready = first_row_by_aliases(readiness_rows, "candidate", GBP_HARVEST_CANDIDATE_ALIASES, fallback_tokens=("gbpusd", "fx", "harvest"))
    gbp_gate = first_row_by_aliases(gate_rows, "candidate", GBP_HARVEST_CANDIDATE_ALIASES, fallback_tokens=("gbpusd", "fx", "harvest"))
    eth_ready = first_row_by_aliases(readiness_rows, "candidate", ETH_REBUILD_CANDIDATE_ALIASES, fallback_tokens=("ethusd", "m5"))
    eth_gate = first_row_by_aliases(gate_rows, "candidate", ETH_REBUILD_CANDIDATE_ALIASES, fallback_tokens=("ethusd", "m5"))
    btc_ready = first_row_by_aliases(readiness_rows, "candidate", BTC_DOWNTREND_CANDIDATE_ALIASES, fallback_tokens=("btcusd", "sell-tight"))
    btc_gate = first_row_by_aliases(gate_rows, "candidate", BTC_DOWNTREND_CANDIDATE_ALIASES, fallback_tokens=("btcusd", "sell-tight"))
    nas100_gate = first_row_by_aliases(gate_rows, "candidate", NAS100_ASYM_CANDIDATE_ALIASES, fallback_tokens=("nas100", "asym"))
    us30_gate = first_row_by_aliases(gate_rows, "candidate", US30_ASYM_CANDIDATE_ALIASES, fallback_tokens=("us30", "asym"))

    gbp_rubric = first_row_by_aliases(rubric_rows, "candidate", GBP_HARVEST_CANDIDATE_ALIASES, fallback_tokens=("gbpusd", "fx", "harvest"))
    eth_rubric = first_row_by_aliases(rubric_rows, "candidate", ETH_REBUILD_CANDIDATE_ALIASES, fallback_tokens=("ethusd", "m5"))
    btc_rubric = first_row_by_aliases(rubric_rows, "candidate", BTC_DOWNTREND_CANDIDATE_ALIASES, fallback_tokens=("btcusd", "sell-tight"))

    offensive_theory = first_row(profit_rows, "theory", "offensive_extreme_closure")
    dual_lattice_theory = first_row(profit_rows, "theory", "dual_lattice_hedge_wave_cancellation")

    any_symbol_contribution_implication = (
        "If the goal is honest broad symbol coverage, finish checked-in launch-contract follow-through for the symbols already cleared for shadow discussion, then keep shrinking the remaining policy queue."
        if promotable_launch_contract_gap_symbols
        else "If the goal is broader symbol coverage, the immediate cleared-symbol launch-contract seam is no longer the blocker; keep shrinking the remaining canonical policy queue before inventing new runtime behavior."
    )

    any_symbol_gap_area = {
        "priority": 1,
        "area": "any_symbol_portability_followthrough_gap",
        "maturity": "governance_blocker",
        "why_it_matters": "The family already parses nearly every discovered symbol. The honest next any-symbol work is now split between immediate full-stack follow-through for symbols that already cleared policy and guardrails, and the remaining canonical policy debt behind them.",
        "machine_truth": {
            "family_portable_count": int(portability_summary.get("family_portable_count") or 0),
            "surface_coverage_complete_count": int(portability_summary.get("surface_coverage_complete_count") or 0),
            "promotable_missing_launch_contract_symbols": promotable_launch_contract_gap_symbols,
            "manual_review_missing_launch_contract_symbols": manual_review_launch_contract_gap_symbols,
            "missing_launch_contract_symbol_count": len(missing_launch_contract_symbols),
            "missing_policy_symbol_count": len(missing_policy_symbols),
            "policy_seed_now_symbols": policy_seed_now_symbols,
            "policy_seed_next_symbols": policy_seed_next_symbols,
            "waiting_forward_proof_symbols": waiting_forward_proof_symbols,
        },
        "contribution_implication": any_symbol_contribution_implication,
    }

    research_areas = [
        any_symbol_gap_area,
        {
            "priority": 2,
            "area": "fx_alpha_half_controller_prior",
            "maturity": "durable_truth",
            "why_it_matters": "This is the strongest already-proven profit prior in the Hippo stack and should stop being re-litigated as a fresh theory.",
            "machine_truth": {
                "gbpusd_avg_per_close": float((symbol_priors.get("GBPUSD") or {}).get("evidence", {}).get("gbp_rearm_avg_per_close") or 0.0),
                "eurusd_avg_per_close": float((symbol_priors.get("EURUSD") or {}).get("evidence", {}).get("eur_rearm_avg_per_close") or 0.0),
                "gbpusd_forward_closes": int((gbp_ready.get("evidence") or {}).get("closes") or 0),
                "gbpusd_blocker": str(gbp_gate.get("blocking_issue") or ""),
            },
            "contribution_implication": "Do not spend time inventing a new FX default. The leverage is in reconciling the GBPUSD selector-vs-live contradiction so the proven alpha=0.5 prior can travel cleanly into runtime.",
        },
        {
            "priority": 3,
            "area": "eth_m5_control_rebuild_validation",
            "maturity": "shadow_ready",
            "why_it_matters": "ETH M5 is still the clearest aligned control-restoration lane, with a normalized registered control and an explicit forward-proof path.",
            "machine_truth": {
                "shadow_avg_per_close": float((eth_gate.get("machine_truth") or {}).get("shadow_avg_per_close") or (eth_ready.get("evidence") or {}).get("avg_per_close") or 0.0),
                "shadow_closes": int((eth_gate.get("machine_truth") or {}).get("shadow_realized_closes") or (eth_ready.get("evidence") or {}).get("realized_closes") or (eth_ready.get("evidence") or {}).get("closes") or 0),
                "failed_live_reference_avg_per_close": float((eth_gate.get("machine_truth") or {}).get("live_reference_avg_per_close") or 0.0),
                "promotion_blocker": str(eth_gate.get("blocking_issue") or ""),
                "top_fix_now_action": str(eth_reconcile_action.get("action") or ""),
            },
            "contribution_implication": "This lane does not need more ideation. It needs fresh forward proof under the aligned normalized-control stack.",
        },
        {
            "priority": 4,
            "area": "btc_downtrend_loss_control",
            "maturity": "shadow_config_exists_needs_reconcile",
            "why_it_matters": "BTC already has live capital and current SELL/bounce-reversal context, so better downtrend control is a direct less-losses seam.",
            "machine_truth": {
                "action_bias": str((btc_gate.get("machine_truth") or {}).get("action_bias") or ""),
                "control_mode": str((btc_gate.get("machine_truth") or {}).get("control_mode") or ""),
                "proposed_sell_step": float((btc_gate.get("machine_truth") or {}).get("proposed_sell_step") or 0.0),
                "promotion_verdict": str(btc_gate.get("promotion_verdict") or ""),
            },
            "contribution_implication": "Do not treat BTC downtrend control as a blank-sheet theory. The config exists; the job is reconciliation plus proof.",
        },
        {
            "priority": 5,
            "area": "index_asymmetry_family",
            "maturity": "forward_validating",
            "why_it_matters": "The index asymmetry idea has real upside, but the repo truth is explicit that it is family-scoped and window-sensitive, not universal.",
            "machine_truth": {
                "nas100_verdict": str(nas100_gate.get("promotion_verdict") or ""),
                "nas100_next_action": str(nas100_watch_action.get("action") or ""),
                "us30_verdict": str(us30_gate.get("promotion_verdict") or ""),
                "us30_guardrail_status": str((us30_gate.get("machine_truth") or {}).get("guardrail_status") or (guardrail_rows and first_row(guardrail_rows, "symbol", "US30").get("status") or "")),
            },
            "contribution_implication": "Avoid cloning breakout geometry across the book. Promote or reject it per symbol and per window.",
        },
        {
            "priority": 6,
            "area": "loss_reduction_theory_queue",
            "maturity": "research_candidate",
            "why_it_matters": "This is where genuine open research still exists for reducing bleed without pretending it is already production truth.",
            "machine_truth": {
                "offensive_extreme_closure_status": str((offensive_theory.get("machine_truth") or {}).get("policy_status") or ""),
                "dual_lattice_hedge_status": str((dual_lattice_theory.get("machine_truth") or {}).get("policy_status") or ""),
                "graduation_gate": str((offensive_theory.get("machine_truth") or {}).get("graduation_gate") or ""),
            },
            "contribution_implication": "If the goal is less losses rather than more storytelling, this is the honest research seam to work on next.",
        },
        {
            "priority": 7,
            "area": "guardrail_and_governance_surface",
            "maturity": "durable_truth",
            "why_it_matters": "A large share of Hippo work is blocked by contradictions and guardrails, not by lack of ideas.",
            "machine_truth": {
                "guardrail_status_counts": summarize_counts(guardrail_rows, "status"),
                "promotable_now_symbols": promotable_now_symbols,
                "top_readiness_rows": list(readiness_board.get("summary", {}).get("top_candidates") or []),
            },
            "contribution_implication": "The fastest contribution is often governance cleanup or proof generation, not another controller theory.",
        },
    ]

    any_symbol_contribution_lane = {
        "priority": 1,
        "lane": "expand_canonical_policy_coverage_for_portable_missing_policy_symbols",
        "fit": "best_any_symbol_generalization_move",
        "why_this_is_best": "The family defaults already cover the symbols, but the remaining missing-policy set still blocks broader cross-symbol portability, so shrinking that queue is still the honest breadth move.",
        "machine_truth": {
            "missing_policy_symbol_count": len(missing_policy_symbols),
            "policy_seed_now_symbols": policy_seed_now_symbols,
            "policy_seed_next_symbols": policy_seed_next_symbols,
            "surface_coverage_complete_count": int(portability_summary.get("surface_coverage_complete_count") or 0),
            "family_portable_count": int(portability_summary.get("family_portable_count") or 0),
            "waiting_forward_proof_symbols": waiting_forward_proof_symbols,
        },
        "first_artifact": "A canonical regime/rearm policy expansion pass that moves symbols out of `portable_missing_policy` without changing runtime code.",
        "reason_to_pick_this_over_new_theory": "It keeps attacking real breadth debt instead of adding another symbol-specific idea on top of missing governance.",
    }
    if promotable_launch_contract_gap_symbols:
        any_symbol_contribution_lane = {
            "priority": 1,
            "lane": "add_checked_in_launch_contracts_for_promotable_portable_symbols",
            "fit": "best_any_symbol_generalization_move",
            "why_this_is_best": "The nearest symbols already cleared policy and guardrail review. The immediate any-symbol follow-through is checked-in runnable launch contracts, not another round of abstract portability talk.",
            "machine_truth": {
                "promotable_missing_launch_contract_symbols": promotable_launch_contract_gap_symbols,
                "manual_review_missing_launch_contract_symbols": manual_review_launch_contract_gap_symbols,
                "missing_launch_contract_symbol_count": len(missing_launch_contract_symbols),
                "missing_policy_symbol_count": len(missing_policy_symbols),
                "policy_seed_now_symbols": policy_seed_now_symbols,
            },
            "first_artifact": "A checked-in shadow contract pair for the cleared symbols that passes the current Hungry Hippo launch-safety validator.",
            "reason_to_pick_this_over_new_theory": "It moves already-seeded symbols toward full-stack portability faster than reopening broad policy debates for symbols that are still farther away.",
        }

    contribution_lanes = [
        any_symbol_contribution_lane,
        {
            "priority": 2,
            "lane": "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane",
            "fit": "best_room_move",
            "why_this_is_best": "It is the current top fix-now action in the Hippo planning layer and removes the biggest contradiction around the family's main ETH control proof path.",
            "machine_truth": {
                "top_priority_action": str(eth_reconcile_action.get("action") or ""),
                "eth_gate_verdict": str((eth_reconcile_action.get("machine_truth") or {}).get("eth_gate_verdict") or ""),
                "runtime_stale": bool((eth_reconcile_action.get("machine_truth") or {}).get("runtime_stale") or False),
                "enabled_alignment_ok": bool((eth_reconcile_action.get("machine_truth") or {}).get("enabled_alignment_ok") or False),
            },
            "first_artifact": "A surface-reconciliation note or diff that makes config, registry, and proof boards point at one ETH control lineage.",
            "reason_to_pick_this_over_new_theory": "This removes a current proof-truth contradiction instead of inventing new behavior before the control path is even judged honestly.",
        },
        {
            "priority": 3,
            "lane": "reconcile_gbpusd_alpha_half_live_path",
            "fit": "best_fx_specific_move",
            "why_this_is_best": "It is the closest current live candidate, already clears most promotion thresholds, and converts proven FX prior truth into near-term real profit faster than any new theory branch.",
            "machine_truth": {
                "forward_closes": int((gbp_gate.get("machine_truth") or {}).get("proof_closes") or (gbp_ready.get("evidence") or {}).get("closes") or 0),
                "per_close": float((gbp_ready.get("evidence") or {}).get("per_close") or 0.0),
                "guardrail_status": str((gbp_gate.get("machine_truth") or {}).get("guardrail_status") or (gbp_ready.get("evidence") or {}).get("guardrail_status") or ""),
                "blocking_issue": str(gbp_gate.get("blocking_issue") or ""),
                "required_contradictions": str((gbp_rubric.get("shadow_to_live_rubric") or {}).get("required_contradictions") or (gbp_rubric.get("candidate_rubric") or {}).get("required_contradictions") or ""),
            },
            "first_artifact": "A selector-vs-deploy reconciliation note or diff that preserves alpha=0.5 geometry in the live path without resetting proof standards.",
            "reason_to_pick_this_over_new_theory": "This moves already-proven edge closer to capital instead of adding another unvalidated branch.",
        },
        {
            "priority": 4,
            "lane": "build_nas100_forward_shadow_proof_after_window_cleanup",
            "fit": "best_near_term_new_symbol_move",
            "why_this_is_best": "NAS100 is the only current symbol already out of the policy-gap bucket and waiting mainly on forward proof, so it is the cleanest non-FX expansion seam after the family-wide policy debt.",
            "machine_truth": {
                "generalization_status": "portable_waiting_forward_proof" if "NAS100" in waiting_forward_proof_symbols else "",
                "guardrail_status": str((nas100_gate.get("machine_truth") or {}).get("guardrail_status") or ""),
                "promotion_verdict": str(nas100_gate.get("promotion_verdict") or ""),
                "next_action": str(nas100_watch_action.get("action") or ""),
            },
            "first_artifact": "A fresh-window forward proof read that shows NAS100 is no longer closure-dominated under the current family-specific breakout control.",
            "reason_to_pick_this_over_new_theory": "It is the one current non-FX symbol closest to honest expansion, so proof is higher leverage than another new family branch.",
        },
        {
            "priority": 5,
            "lane": "build_eth_m5_forward_shadow_proof",
            "fit": "best_shadow_move",
            "why_this_is_best": "ETH M5 now has an aligned normalized control plus a failed live reference, which makes fresh forward proof more valuable than more tuning chatter.",
            "machine_truth": {
                "shadow_closes": int((eth_gate.get("machine_truth") or {}).get("shadow_realized_closes") or (eth_ready.get("evidence") or {}).get("realized_closes") or (eth_ready.get("evidence") or {}).get("closes") or 0),
                "shadow_avg_per_close": float((eth_gate.get("machine_truth") or {}).get("shadow_avg_per_close") or (eth_ready.get("evidence") or {}).get("avg_per_close") or (eth_ready.get("evidence") or {}).get("per_close") or 0.0),
                "required_forward_closes": int((eth_rubric.get("shadow_to_live_rubric") or {}).get("required_forward_closes") or (eth_rubric.get("candidate_rubric") or {}).get("required_forward_closes") or 0),
                "promotion_action": str((eth_gate.get("machine_truth") or {}).get("promotion_action") or ""),
            },
            "first_artifact": "Fresh forward proof that extends beyond the shelf sample and survives the aligned offensive/escape stack.",
            "reason_to_pick_this_over_new_theory": "The repo already knows ETH can work in shadow; it just does not know whether the aligned normalized-control path survives live-adjacent forward conditions.",
        },
        {
            "priority": 6,
            "lane": "offensive_extreme_closure_same_symbol_experiment",
            "fit": "best_codex_nonruntime_move",
            "why_this_is_best": "It directly targets the user's less-losses mandate, is still an honest research candidate, and does not collide with ongoing runtime ownership the way GBP/ETH path work likely would.",
            "machine_truth": {
                "policy_status": str((offensive_theory.get("machine_truth") or {}).get("policy_status") or ""),
                "theory_stage": str(offensive_theory.get("stage") or ""),
                "graduation_gate": str((offensive_theory.get("machine_truth") or {}).get("graduation_gate") or ""),
            },
            "first_artifact": "A shadow experiment spec or board that only closes extreme orders when realized inner-lattice profit can subsidize the cut cheaply.",
            "reason_to_pick_this_over_new_theory": "It fills the cleanest open loss-reduction gap with minimal overlap and a falsifiable test path.",
        },
        {
            "priority": 7,
            "lane": "reconcile_btc_m15_sell_tight_shadow_config",
            "fit": "best_btc_loss_control_move",
            "why_this_is_best": "BTC already has live baseline capital, current SELL bias, and an existing downtrend config; that makes proof-quality loss control more valuable than speculative BTC expansion.",
            "machine_truth": {
                "action_bias": str((btc_gate.get("machine_truth") or {}).get("action_bias") or ""),
                "control_mode": str((btc_gate.get("machine_truth") or {}).get("control_mode") or ""),
                "required_forward_closes": int((btc_rubric.get("shadow_to_live_rubric") or {}).get("required_forward_closes") or (btc_rubric.get("candidate_rubric") or {}).get("required_forward_closes") or 0),
                "blocking_issue": str(btc_gate.get("blocking_issue") or ""),
            },
            "first_artifact": "A reconciled config diff plus proof plan that compares downtrend loss-control against the existing bullish-hold baseline without touching live BTC M15.",
            "reason_to_pick_this_over_new_theory": "This addresses actual BTC bleed risk with existing assets instead of inventing another BTC side branch.",
        },
    ]

    avoid_now = [
        {
            "idea": "dual_lattice_hedge_wave_cancellation",
            "why_not_now": "Still explicitly simulation-required and not yet machine-proven against spread drag or trend persistence.",
        },
        {
            "idea": "universalize_index_breakout_geometry",
            "why_not_now": "Repo truth says the asymmetry family is index-scoped and window-sensitive, not a repo-wide default.",
        },
        {
            "idea": "promote_btc_m5_step200_off_two_closes",
            "why_not_now": "The upside is real, but the sample is still too small and the BTC hold gate remains active.",
        },
    ]

    any_symbol_leadership_line = (
        "Immediate full-stack follow-through is launch-contract coverage for the policy-cleared symbols, while the broader family-width blocker is the remaining portable-missing-policy pack and the nearest proof seam is NAS100."
        if promotable_launch_contract_gap_symbols
        else f"The immediate cleared-symbol launch-contract seam is closed; the broader family-width blocker is now the remaining portable-missing-policy pack `{format_symbol_list(policy_seed_now_symbols)}` / `{format_symbol_list(policy_seed_next_symbols)}`, while the nearest proof seam is NAS100."
    )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(CONTROLLER_PRIORS_PATH.relative_to(ROOT)),
            str(PROFIT_BOARD_PATH.relative_to(ROOT)),
            str(READINESS_BOARD_PATH.relative_to(ROOT)),
            str(PROMOTION_GATE_PATH.relative_to(ROOT)),
            str(RUBRIC_BOARD_PATH.relative_to(ROOT)),
            str(GUARDRAIL_AUDIT_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_BOARD_PATH.relative_to(ROOT)),
            str(PORTABILITY_BOARD_PATH.relative_to(ROOT)),
            str(POLICY_GAP_BOARD_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The Hippo repo already has enough theory to act; the current any-symbol bottleneck is governance follow-through, not symbol parsing or another controller idea.",
            any_symbol_leadership_line,
            "Use fresh Hippo authority surfaces for contribution calls: generic graduation boards still matter, but portability and next-action truth now decide what actually moves the family forward.",
        ],
        "best_overall_contribution": contribution_lanes[1]["lane"],
        "best_any_symbol_contribution": contribution_lanes[0]["lane"],
        "best_nonruntime_contribution": contribution_lanes[5]["lane"],
        "research_areas": research_areas,
        "contribution_lanes": contribution_lanes,
        "avoid_now": avoid_now,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Hungry Hippo Research Contribution Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: map what Hungry Hippo research is already done, what is still only half-proven, and where the next contribution buys the most profit or loss reduction.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Best Contribution Calls",
            "",
            f"- Best overall contribution: `{payload.get('best_overall_contribution', '')}`",
            f"- Best any-symbol contribution: `{payload.get('best_any_symbol_contribution', '')}`",
            f"- Best non-runtime contribution: `{payload.get('best_nonruntime_contribution', '')}`",
            "",
            "## Research Already Done",
            "",
        ]
    )

    for row in list(payload.get("research_areas") or []):
        lines.append(f"### P{int(row['priority'])} - {row['area']}")
        lines.append("")
        lines.append(f"- Maturity: `{row['maturity']}`")
        lines.append(f"- Why it matters: `{row['why_it_matters']}`")
        lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in dict(row.get('machine_truth') or {}).items())}`")
        lines.append(f"- Contribution implication: `{row['contribution_implication']}`")
        lines.append("")

    lines.extend(["## Where To Contribute Next", ""])
    for row in list(payload.get("contribution_lanes") or []):
        lines.append(f"### P{int(row['priority'])} - {row['lane']}")
        lines.append("")
        lines.append(f"- Fit: `{row['fit']}`")
        lines.append(f"- Why this is best: `{row['why_this_is_best']}`")
        lines.append(f"- Machine truth: `{'; '.join(f'{k}={v}' for k, v in dict(row.get('machine_truth') or {}).items())}`")
        lines.append(f"- First artifact: `{row['first_artifact']}`")
        lines.append(f"- Reason to pick this over new theory: `{row['reason_to_pick_this_over_new_theory']}`")
        lines.append("")

    lines.extend(["## Avoid For Now", ""])
    for row in list(payload.get("avoid_now") or []):
        lines.append(f"- `{row['idea']}`: {row['why_not_now']}")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(CONTROLLER_PRIORS_PATH),
        load_json(PROFIT_BOARD_PATH),
        load_json(READINESS_BOARD_PATH),
        load_json(PROMOTION_GATE_PATH),
        load_json(RUBRIC_BOARD_PATH),
        load_json(GUARDRAIL_AUDIT_PATH),
        load_json(NEXT_ACTION_BOARD_PATH),
        load_json(PORTABILITY_BOARD_PATH),
        load_json(POLICY_GAP_BOARD_PATH),
    )
    write_outputs(payload)
    print(f"Wrote {OUTPUT_JSON_PATH}")
    print(f"Wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
