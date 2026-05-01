#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
CONFIGS = ROOT / "configs"

PROFIT_BOARD_PATH = REPORTS / "profit_theory_graduation_board.json"
READINESS_BOARD_PATH = REPORTS / "shadow_graduation_readiness_board.json"
CONTROLLER_PRIORS_PATH = CONFIGS / "adaptive_controller_priors.json"
ETH_CONTROL_GATE_PATH = REPORTS / "eth_m5_control_proof_gate_board.json"
NEXT_ACTION_BOARD_PATH = REPORTS / "hungry_hippo_next_action_board.json"
LAUNCH_SAFETY_PATH = REPORTS / "hungry_hippo_launch_safety_validation.json"
BUCKET_SPLIT_MD_PATH = REPORTS / "bucket_split_analysis.md"
BTC_RECONCILIATION_REPORT_PATH = REPORTS / "btc_m15_sell_tight_reconciliation.md"

OUTPUT_JSON_PATH = REPORTS / "shadow_to_live_promotion_gate_board.json"
OUTPUT_MD_PATH = REPORTS / "shadow_to_live_promotion_gate_board.md"
BTC_DOWNTREND_CONFIG_PATH = CONFIGS / "hungry_hippo_btcusd_m15_sell_tight_shadow.json"
BTC_DOWNTREND_V2_CONFIG_PATH = CONFIGS / "hungry_hippo_btcusd_m15_sell_tight_v2_retuned.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return load_json(path)


def merge_btc_config(primary: dict[str, Any] | None, fallback: dict[str, Any] | None) -> dict[str, Any] | None:
    if primary is None and fallback is None:
        return None
    merged = dict(fallback or {})
    merged.update(primary or {})
    merged_meta = dict(((fallback or {}).get("hungry_hippo_metadata") or {}))
    merged_meta.update(((primary or {}).get("hungry_hippo_metadata") or {}))
    if "guardrails" not in merged_meta and ((fallback or {}).get("hungry_hippo_metadata") or {}).get("guardrails") is not None:
        merged_meta["guardrails"] = dict((((fallback or {}).get("hungry_hippo_metadata") or {}).get("guardrails") or {}))
    merged["hungry_hippo_metadata"] = merged_meta
    return merged


def readiness_row(payload: dict[str, Any], candidate: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("candidate") or "") == candidate:
            return row
    raise KeyError(f"candidate not found: {candidate}")


def theory_row(payload: dict[str, Any], theory: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("theory") or "") == theory:
            return row
    raise KeyError(f"theory not found: {theory}")


def action_row(payload: dict[str, Any], action: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("action") or "") == action:
            return row
    raise KeyError(f"action not found: {action}")


def action_row_any(payload: dict[str, Any], actions: list[str]) -> dict[str, Any]:
    for action in actions:
        try:
            return action_row(payload, action)
        except KeyError:
            continue
    raise KeyError(f"actions not found: {actions}")


def config_row(payload: dict[str, Any], config_name: str) -> dict[str, Any]:
    needle = str(config_name).lower()
    for row in list(payload.get("rows") or []):
        path = str(row.get("config_path") or "").lower()
        name = str(row.get("name") or "").lower()
        if needle in path or needle == name:
            return row
    raise KeyError(f"config not found: {config_name}")


def parse_bucket_split_summary(markdown_text: str) -> dict[str, float]:
    match = re.search(
        r"close_ticket\).*?\(\+\$([0-9,\.]+)\).*?escape_tier0_offensive\s*\(-\$([0-9,\.]+)\)\s*and\s*forced_unwind\s*\(-\$([0-9,\.]+)\)",
        markdown_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {
            "close_ticket": 0.0,
            "escape_tier0_offensive": 0.0,
            "forced_unwind": 0.0,
        }
    return {
        "close_ticket": float(match.group(1).replace(",", "")),
        "escape_tier0_offensive": -float(match.group(2).replace(",", "")),
        "forced_unwind": -float(match.group(3).replace(",", "")),
    }


def parse_btc_reconciliation_markdown(markdown_text: str) -> dict[str, Any]:
    status_match = re.search(r"\*\*Status:\*\*\s*`([^`]+)`", markdown_text)
    success_match = re.search(
        r"\*\*Success criteria for forward proof:\*\*([\s\S]+?)\n---",
        markdown_text,
    )
    criteria: list[str] = []
    if success_match:
        for line in success_match.group(1).splitlines():
            line = line.strip()
            if line.startswith("- "):
                criteria.append(line[2:])
    return {
        "status": status_match.group(1) if status_match else "",
        "success_criteria": criteria,
    }


def build_btc_promotion_gate(
    btc_reconciliation_report: dict[str, Any],
    btc_downtrend_config: dict[str, Any] | None,
    btc_evidence: dict[str, Any],
) -> list[str]:
    criteria: list[str] = []
    report_criteria = list(btc_reconciliation_report.get("success_criteria") or [])
    for line in report_criteria:
        lower = line.lower()
        if "reset" in lower or "floating loss" in lower:
            continue
        criteria.append(line)

    if not any("closes" in line.lower() for line in criteria):
        criteria.append("10+ closes under SELL bias conditions")
    if not any("avg_per_close" in line.lower() for line in criteria):
        criteria.append("avg_per_close positive (any value — this is loss-reduction, not profit-maximization)")

    metadata = dict(((btc_downtrend_config or {}).get("hungry_hippo_metadata") or {}))
    guardrails = dict(metadata.get("guardrails") or {})
    max_resets_per_hour = btc_evidence.get("max_resets_per_hour")
    if max_resets_per_hour is None:
        max_resets_per_hour = guardrails.get("max_resets_per_hour")
    max_resets_per_close = btc_evidence.get("max_resets_per_close")
    if max_resets_per_close is None:
        max_resets_per_close = guardrails.get("max_resets_per_close")
    floating_loss_limit = (
        guardrails.get("floating_loss_limit_usd")
        if guardrails.get("floating_loss_limit_usd") is not None
        else (btc_downtrend_config or {}).get("max_floating_loss_usd")
    )

    if max_resets_per_hour is not None:
        criteria.append(f"Reset rate stays <= {max_resets_per_hour}/hour")
    if max_resets_per_close is not None:
        criteria.append(f"Resets per close stay <= {max_resets_per_close}")
    if floating_loss_limit is not None:
        criteria.append(f"Floating loss stays within {floating_loss_limit} USD guardrail")

    return criteria


def build_payload(
    profit_board: dict[str, Any],
    readiness_board: dict[str, Any],
    controller_priors: dict[str, Any],
    eth_control_gate: dict[str, Any],
    next_action_board: dict[str, Any],
    launch_safety: dict[str, Any],
    bucket_split_summary: dict[str, float],
    btc_downtrend_config: dict[str, Any] | None = None,
    btc_reconciliation_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol_priors = dict(controller_priors.get("symbol_priors") or {})

    btc_downtrend = readiness_row(readiness_board, "BTCUSD M15 sell-tight downtrend shape")
    gbp_ready = readiness_row(readiness_board, "GBPUSD alpha=0.5 FX harvest path")
    btc_m5_ready = readiness_row(readiness_board, "BTCUSD M5 step200 salvage probe")
    nas100_ready = readiness_row(readiness_board, "NAS100 asym breakout family lane")
    us30_ready = readiness_row(readiness_board, "US30 asym breakout family lane")

    btc_m5_theory = theory_row(profit_board, "btc_m5_step200_salvage_probe")
    eth_action = action_row_any(
        next_action_board,
        [
            "register_eth_m5_step14_control_and_repoint_the_proof_board_to_the_same_lane",
            "retire_orphan_eth_m5_proof_artifact_and_restore_registered_step14_control_runtime",
            "normalize_eth_m5_step14_runtime_geometry_and_accumulate_honest_control_proof",
            "reconcile_eth_m5_control_to_one_canonical_surface_before_using_it_as_the_proof_lane",
            "verify_or_restore_eth_m5_step14_control_runtime_before_treating_it_as_the_proof_lane",
            "keep_eth_m5_step14_control_running_as_the_single_proof_lane",
        ],
    )
    gbp_action = action_row_any(
        next_action_board,
        [
            "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair",
            "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape",
            "treat_gbpusd_alpha_half_as_bucket_diagnosis_before_any_promotion_or_default_story",
            "reconcile_gbpusd_alpha_half_path_before_any_new_fx_default_story",
        ],
    )
    eth_gate_verdict = str((eth_control_gate.get("summary") or {}).get("verdict") or "") or str((eth_action.get("machine_truth") or {}).get("eth_gate_verdict") or "")
    gbp_action_name = str(gbp_action.get("action") or "")
    gbp_machine_truth = dict(gbp_action.get("machine_truth") or {})
    nas_action = action_row_any(
        next_action_board,
        [
            "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves",
            "treat_nas100_m15_breakout_buy_as_the_only_clean_research_only_shadow_candidate",
        ],
    )

    btc_launch = config_row(launch_safety, "hungry_hippo_btcusd_m15_sell_tight_shadow.json")
    btc_step200_launch = config_row(launch_safety, "hungry_hippo_btcusd_m5_step200_shadow.json")
    nas_launch = config_row(launch_safety, "hungry_hippo_nas100_m15_breakout_buy_shadow.json")

    btc_reconciliation_report = btc_reconciliation_report or {"status": "", "success_criteria": []}
    btc_metadata = dict((btc_downtrend_config or {}).get("hungry_hippo_metadata") or {})
    btc_evidence = dict(btc_downtrend.get("evidence") or {})
    btc_blocker = str(btc_downtrend.get("blocker") or "")
    btc_close_mix_status = str(btc_evidence.get("btc_close_mix_status") or "")
    btc_promotion_gate = build_btc_promotion_gate(btc_reconciliation_report, btc_downtrend_config, btc_evidence)

    eth_current_stage = "tested_theory_waiting_for_clean_control"
    eth_promotion_verdict = "restore_control_then_validate_shadow"
    if eth_gate_verdict == "blocked_by_negative_expectancy":
        eth_current_stage = "tested_theory_waiting_for_positive_control_proof"
        eth_promotion_verdict = "collect_positive_control_proof_before_validated_shadow"
    elif eth_gate_verdict == "blocked_by_control_normalization":
        eth_promotion_verdict = "normalize_control_then_validate_shadow"

    rows = [
        {
            "priority": 1,
            "candidate": "ETHUSD M5 step14 normalized control",
            "current_stage": eth_current_stage,
            "promotion_verdict": eth_promotion_verdict,
            "machine_truth": {
                "gate_verdict": str((eth_control_gate.get("summary") or {}).get("verdict") or ""),
                "realized_closes": int((eth_control_gate.get("summary") or {}).get("realized_closes") or 0),
                "avg_per_close": float((eth_control_gate.get("summary") or {}).get("avg_per_close") or 0.0),
                "runtime_stale": bool((eth_control_gate.get("control_runtime") or {}).get("runtime_stale")),
                "geometry_normalized": bool((eth_control_gate.get("control_runtime") or {}).get("geometry_normalized")),
                "comparison_status": str((eth_control_gate.get("summary") or {}).get("comparison_status") or ""),
            },
            "blocking_issue": eth_gate_verdict,
            "promotion_gate": list(eth_control_gate.get("advance_when") or []),
            "live_read": "This is not a live candidate yet. The launch/proof path is aligned, comparison hygiene is ready, and geometry is clean enough to judge, but the control sample still needs positive validated-shadow proof before any live discussion."
            if eth_gate_verdict == "blocked_by_negative_expectancy"
            else "This is not a live candidate yet. The launch/proof path is aligned and the heartbeat is fresh, but the runtime ladder still needs to behave like an honest fixed step14 control and the sample still needs positive validated-shadow proof before any live discussion."
            if eth_gate_verdict == "blocked_by_control_normalization"
            else "This is not a live candidate yet. First make the checked-in step14 control and the judged proof surface one canonical lane, then refresh the runtime heartbeat and normalized ladder and let it clear validated-shadow proof before any live discussion.",
        },
        {
            "priority": 2,
            "candidate": "GBPUSD alpha=0.5 FX harvest path",
            "current_stage": "closure_policy_diagnosis_before_live",
            "promotion_verdict": "bucket_diagnosis_before_live",
            "machine_truth": {
                "close_alpha_prior": float((symbol_priors.get("GBPUSD") or {}).get("close_alpha_prior") or 0.0),
                "proof_closes": int((gbp_action.get("machine_truth") or {}).get("gbpusd_proof_closes") or 0),
                "guardrail_status": str((gbp_action.get("machine_truth") or {}).get("gbpusd_guardrail_status") or ""),
                "harvest_close_ticket_usd": float(bucket_split_summary.get("close_ticket") or 0.0),
                "escape_tier0_offensive_usd": float(bucket_split_summary.get("escape_tier0_offensive") or 0.0),
                "forced_unwind_usd": float(bucket_split_summary.get("forced_unwind") or 0.0),
                "closure_pair_live": bool(gbp_machine_truth.get("gbp_closure_pair_live")),
                "no_escape_present": bool(gbp_machine_truth.get("gbp_no_escape_present")),
            },
            "blocking_issue": "no-escape companion lane is not live yet"
            if gbp_action_name == "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair"
            else "paired forward sample not mature enough yet"
            if gbp_action_name == "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape"
            else "mixed net remains negative because closure buckets dominate while selector/live-path contradiction is still unresolved",
            "promotion_gate": [
                "launch or restore the GBP no-escape companion lane as the paired closure-diagnosis control",
                "wait for the companion lane to write fresh state",
                "confirm offensive_closure_enabled=false in the no-escape lane",
                "only then judge the paired closure-repair read",
            ]
            if gbp_action_name == "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair"
            else [
                "keep both GBP lanes alive under forward conditions",
                "preserve offensive_closure_enabled=false in the no-escape lane",
                "accumulate enough paired closes to compare baseline vs no-escape honestly",
                "only then judge whether closure repair rescues the lane economics",
            ]
            if gbp_action_name == "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape"
            else [
                "split fresh forward results into harvest, offensive-close, and forced-unwind buckets",
                "confirm the harvest bucket stays positive over fresh sample",
                "reduce or disable the losing closure buckets without weakening alpha=0.5 geometry",
                "reconcile the selector-vs-live-path contradiction before restoring live-candidate language",
            ],
            "live_read": "The signal may still be real, but this is not an honest closest-live row until the no-escape companion exists as a real paired control and the closure-repair read is judged from that pair."
            if gbp_action_name == "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair"
            else "The signal may still be real, but this is not an honest closest-live row until the paired baseline-vs-no-escape forward sample is large enough to judge whether closure repair actually improves the lane."
            if gbp_action_name == "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape"
            else "The signal may still be real, but this is no longer an honest closest-live row until the closure-policy leak is isolated and fixed.",
        },
        {
            "priority": 3,
            "candidate": "NAS100 asym breakout family lane",
            "current_stage": "research_only_shadow_candidate",
            "promotion_verdict": "cleanest_shadow_candidate_after_control_work",
            "machine_truth": {
                "launch_verdict": str(nas_launch.get("verdict") or ""),
                "config_enabled": bool(nas_launch.get("enabled")),
                "proof_closes": int((nas_action.get("machine_truth") or {}).get("proof_closes") or 0),
                "guardrail_status": str((nas_action.get("machine_truth") or {}).get("guardrail_status") or ""),
                "deployment_gate_verdict": str((nas_action.get("machine_truth") or {}).get("deployment_gate_verdict") or ""),
            },
            "blocking_issue": "manual review, micro-step sensitivity, and window/regime continuity still matter",
            "promotion_gate": [
                "treat it as family-specific shadow proof, not a universal controller argument",
                "stay positive in the intended session window under forward conditions",
                "keep spread/reset behavior clean under the current escape contract",
                "require explicit manual-review acceptance before any promotion step",
            ],
            "live_read": "This is the cleanest current HH shadow-expansion candidate, but it is still a research-only lane rather than a live queue member.",
        },
        {
            "priority": 4,
            "candidate": "BTCUSD M15 sell-tight downtrend shape",
            "current_stage": str(btc_downtrend.get("readiness") or ""),
            "promotion_verdict": "collect_forward_proof_then_judge",
            "machine_truth": {
                "action_bias": str(btc_evidence.get("current_action_bias") or btc_metadata.get("action_bias") or ""),
                "control_mode": str(btc_evidence.get("current_control_mode") or btc_metadata.get("control_mode") or ""),
                "proposed_sell_step": float(btc_evidence.get("proposed_sell_step") or btc_metadata.get("computed_sell_step") or 0.0),
                "config_enabled": bool(btc_downtrend_config.get("enabled")) if btc_downtrend_config else False,
                "launch_verdict": str(btc_launch.get("verdict") or ""),
                "reconciliation_status": str(btc_reconciliation_report.get("status") or ""),
                "validation_status": str(btc_metadata.get("validation_status") or ""),
                "runtime_stale": bool(btc_evidence.get("runtime_stale")),
                "realized_closes": int(btc_evidence.get("realized_closes") or 0),
                "realized_net_usd": float(btc_evidence.get("realized_net_usd") or 0.0),
                "anchor_resets": int(btc_evidence.get("anchor_resets") or 0),
                "resets_per_close": btc_evidence.get("resets_per_close"),
                "reset_rate_per_hour": btc_evidence.get("reset_rate_per_hour"),
                "max_resets_per_close": btc_evidence.get("max_resets_per_close"),
                "max_resets_per_hour": btc_evidence.get("max_resets_per_hour"),
                "btc_total_close_events": btc_evidence.get("btc_total_close_events"),
                "btc_harvest_closes": btc_evidence.get("btc_harvest_closes"),
                "btc_escape_tier2_surgical_closes": btc_evidence.get("btc_escape_tier2_surgical_closes"),
                "btc_harvest_share": btc_evidence.get("btc_harvest_share"),
                "btc_close_mix_status": btc_close_mix_status,
                "btc_all_closes_escape_dominated": btc_evidence.get("btc_all_closes_escape_dominated"),
                "floating_loss_limit_usd": (
                    dict((btc_downtrend_config or {}).get("hungry_hippo_metadata") or {}).get("guardrails", {}).get("floating_loss_limit_usd")
                    if dict((btc_downtrend_config or {}).get("hungry_hippo_metadata") or {}).get("guardrails") is not None
                    else None
                ) or (btc_downtrend_config or {}).get("max_floating_loss_usd"),
            },
            "blocking_issue": btc_blocker or "forward proof not collected yet under the reconciled shadow config",
            "promotion_gate": btc_promotion_gate
            + [
                "keep reset behavior inside the declared guardrails while the fresh sample grows",
                "require the close mix to stop being all-escape and show at least some close_ticket harvest before any live language",
                "show better loss-control than the bullish-hold alternative during SELL bias",
                "do not disturb the existing BTC live M15 baseline while proof is incomplete",
            ],
            "live_read": "The config is reconciled, but this is still a proof-collection lane until fresh SELL-bias forward evidence shows cleaner loss control than the current BTC live baseline."
            if not btc_blocker
            else "Fresh BTC v2 proof has started, but every realized close so far is still escape_tier2_surgical with zero harvest, so this remains a watch lane rather than an honest live candidate."
            if btc_blocker == "forward_sample_all_escape_zero_harvest_so_far" or btc_close_mix_status == "zero_harvest_all_escape_so_far"
            else "Fresh BTC v2 proof has started, but the early sample is still negative and reset-heavy enough that this remains a watch lane rather than an honest live candidate."
            if btc_blocker == "forward_sample_negative_and_reset_rate_above_hourly_guardrail"
            else "Fresh BTC v2 proof has started, but the early sample is still negative and reset-heavy enough that this remains a watch lane rather than an honest live candidate."
            if btc_blocker == "forward_sample_negative_and_reset_heavy"
            else "Fresh BTC v2 proof has started, but the sample is still negative, so this remains a proof-collection lane rather than an honest live candidate."
            if btc_blocker == "forward_sample_started_but_still_negative"
            else "Fresh BTC v2 proof is positive so far, but the sample is still too small to support live language."
            if btc_blocker == "initial_positive_sample_not_large_enough_yet"
            else "The config is reconciled, but the runtime went stale before the fresh sample was mature enough to judge, so this remains a proof-collection lane."
            if btc_blocker == "forward_sample_runtime_went_stale"
            else "The config is reconciled, but this is still a proof-collection lane until fresh SELL-bias forward evidence shows cleaner loss control than the current BTC live baseline.",
        },
        {
            "priority": 5,
            "candidate": "BTCUSD M5 step200 salvage probe",
            "current_stage": str(btc_m5_ready.get("readiness") or ""),
            "promotion_verdict": "too_early_for_live",
            "machine_truth": {
                "shadow_avg_per_close": float((btc_m5_theory.get("machine_truth") or {}).get("shadow_avg_per_close") or 0.0),
                "shadow_realized_closes": int((btc_m5_theory.get("machine_truth") or {}).get("shadow_realized_closes") or 0),
                "hold_gate": str((btc_m5_theory.get("machine_truth") or {}).get("hold_gate") or ""),
                "live_m15_baseline_avg_per_close": float((btc_m5_theory.get("machine_truth") or {}).get("live_m15_baseline_avg_per_close") or 0.0),
                "launch_verdict": str(btc_step200_launch.get("verdict") or ""),
            },
            "blocking_issue": str(btc_m5_ready.get("blocker") or ""),
            "promotion_gate": [
                "outgrow the 2-close sample by a large margin",
                "keep repeatability under active shadow monitoring",
                "clear the BTC hold gate / buy realignment issue",
                "only compare against live promotion after the sample is statistically meaningful",
            ],
            "live_read": "High upside remains theoretical until the sample grows and the current launch-contract failures are no longer in the way.",
        },
        {
            "priority": 6,
            "candidate": "US30 asym breakout family lane",
            "current_stage": str(us30_ready.get("readiness") or ""),
            "promotion_verdict": "blocked_before_live_discussion",
            "machine_truth": {
                "per_close": float((us30_ready.get("evidence") or {}).get("per_close") or 0.0),
                "closes": int((us30_ready.get("evidence") or {}).get("closes") or 0),
                "guardrail_status": str((us30_ready.get("evidence") or {}).get("guardrail_status") or ""),
                "next_action": str((us30_ready.get("evidence") or {}).get("next_action") or ""),
            },
            "blocking_issue": str(us30_ready.get("blocker") or ""),
            "promotion_gate": [
                "resolve the guardrail blockade first",
                "produce direct forward asym-family proof on US30 itself",
                "then re-evaluate whether it should join any promotion queue",
            ],
            "live_read": "Profitable shelf row, but governance still blocks it before live discussion starts.",
        },
    ]

    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict = str(row["promotion_verdict"])
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(PROFIT_BOARD_PATH.relative_to(ROOT)),
            str(READINESS_BOARD_PATH.relative_to(ROOT)),
            str(CONTROLLER_PRIORS_PATH.relative_to(ROOT)),
            str(ETH_CONTROL_GATE_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_BOARD_PATH.relative_to(ROOT)),
            str(LAUNCH_SAFETY_PATH.relative_to(ROOT)),
            str(BUCKET_SPLIT_MD_PATH.relative_to(ROOT)),
            str(BTC_RECONCILIATION_REPORT_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "Validated shadow should mean family-local proof under the current runtime path, not shelf optimism or mixed-net storytelling.",
            "ETH M5 step14 is the first promotion gate to clean up because the launch/proof path is finally aligned and geometry is honest enough to judge, but the sample still has not turned into positive proof, BTC sell-tight has moved from reconciliation talk into forward-proof collection, GBPUSD has moved backward into closure-policy diagnosis, and NAS100 is the cleanest checked-in Hungry Hippo shadow candidate after launch cleanup."
            if eth_gate_verdict == "blocked_by_negative_expectancy"
            else
            "ETH M5 step14 is the first promotion gate to clean up because launch/proof alignment is finally in place but runtime geometry normalization and fresh positive proof still are not, BTC sell-tight has moved from reconciliation talk into forward-proof collection, GBPUSD has moved backward into closure-policy diagnosis, and NAS100 is the cleanest checked-in Hungry Hippo shadow candidate after launch cleanup."
            if eth_gate_verdict == "blocked_by_control_normalization"
            else "ETH M5 step14 is the first promotion gate to clean up, BTC sell-tight has moved from reconciliation talk into forward-proof collection, GBPUSD has moved backward into closure-policy diagnosis, and NAS100 is the cleanest checked-in Hungry Hippo shadow candidate after launch cleanup.",
            "This board keeps graduation disciplined by forcing each row to clear its current blocker before it gets live language.",
        ],
        "summary": {
            "candidate_count": len(rows),
            "promotion_verdict_counts": verdict_counts,
            "closest_current_live_candidate": "none_honest_yet",
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Shadow To Live Promotion Gate Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: make shadow-to-live promotion explicit and thresholded for the current top candidates.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Candidate count: `{summary.get('candidate_count', 0)}`")
    counts = dict(summary.get("promotion_verdict_counts") or {})
    if counts:
        lines.append("- Verdict counts: `" + ", ".join(f"{k}={v}" for k, v in counts.items()) + "`")
    lines.append(f"- Closest current live candidate: `{summary.get('closest_current_live_candidate', '')}`")

    lines.extend(["", "## Queue", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### P{int(row['priority'])} - {row['candidate']}")
        lines.append("")
        lines.append(f"- Current stage: `{row['current_stage']}`")
        lines.append(f"- Promotion verdict: `{row['promotion_verdict']}`")
        lines.append(f"- Machine truth: `{', '.join(f'{k}={v}' for k, v in dict(row.get('machine_truth') or {}).items())}`")
        lines.append(f"- Blocking issue: `{row['blocking_issue']}`")
        lines.append(f"- Promotion gate: `{'; '.join(list(row.get('promotion_gate') or []))}`")
        lines.append(f"- Live read: `{row['live_read']}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    try:
        bucket_text = BUCKET_SPLIT_MD_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        bucket_text = BUCKET_SPLIT_MD_PATH.read_text(encoding="cp1252")

    btc_reconciliation_report = {"status": "", "success_criteria": []}
    if BTC_RECONCILIATION_REPORT_PATH.exists():
        try:
            report_text = BTC_RECONCILIATION_REPORT_PATH.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            report_text = BTC_RECONCILIATION_REPORT_PATH.read_text(encoding="cp1252")
        btc_reconciliation_report = parse_btc_reconciliation_markdown(report_text)

    btc_config = merge_btc_config(
        load_optional_json(BTC_DOWNTREND_CONFIG_PATH),
        load_optional_json(BTC_DOWNTREND_V2_CONFIG_PATH),
    )

    payload = build_payload(
        load_json(PROFIT_BOARD_PATH),
        load_json(READINESS_BOARD_PATH),
        load_json(CONTROLLER_PRIORS_PATH),
        load_json(ETH_CONTROL_GATE_PATH),
        load_json(NEXT_ACTION_BOARD_PATH),
        load_json(LAUNCH_SAFETY_PATH),
        parse_bucket_split_summary(bucket_text),
        btc_config,
        btc_reconciliation_report,
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
