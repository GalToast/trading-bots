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
ETH_CONTROL_GATE_PATH = REPORTS / "eth_m5_control_proof_gate_board.json"
NEXT_ACTION_BOARD_PATH = REPORTS / "hungry_hippo_next_action_board.json"
LAUNCH_SAFETY_PATH = REPORTS / "hungry_hippo_launch_safety_validation.json"
BTC_RECONCILIATION_REPORT_PATH = REPORTS / "btc_m15_sell_tight_reconciliation.md"
BTC_SELL_TIGHT_COMPARISON_PATH = REPORTS / "btc_sell_tight_comparison_latest.json"
BUCKET_SPLIT_MD_PATH = REPORTS / "bucket_split_analysis.md"
BTC_DOWNTREND_CONFIG_PATH = CONFIGS / "hungry_hippo_btcusd_m15_sell_tight_shadow.json"
BTC_DOWNTREND_V2_CONFIG_PATH = CONFIGS / "hungry_hippo_btcusd_m15_sell_tight_v2_retuned.json"

OUTPUT_JSON_PATH = REPORTS / "shadow_graduation_readiness_board.json"
OUTPUT_MD_PATH = REPORTS / "shadow_graduation_readiness_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return load_json(path)


def load_optional_repo_json(path_str: str | None) -> Any | None:
    if not path_str:
        return None
    path = (ROOT / path_str).resolve()
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


def active_state_path_from_config(payload: dict[str, Any] | None) -> str:
    config = payload or {}
    restart_args = list(config.get("restart_args") or [])
    for idx, item in enumerate(restart_args):
        if str(item) == "--state-path" and idx + 1 < len(restart_args):
            return str(restart_args[idx + 1])
    return str(config.get("state_path") or "")


def parse_iso_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def summarize_btc_forward_proof(btc_config: dict[str, Any] | None, btc_state: dict[str, Any] | None) -> dict[str, Any]:
    metadata = dict(((btc_config or {}).get("hungry_hippo_metadata") or {}))
    guardrails = dict(metadata.get("guardrails") or {})
    state_payload = btc_state or {}
    runner = dict(state_payload.get("runner") or {})
    symbol_state = dict(((state_payload.get("symbols") or {}).get("BTCUSD") or {}))

    realized_closes = int(symbol_state.get("realized_closes") or 0)
    realized_net_usd = round(float(symbol_state.get("realized_net_usd") or 0.0), 2)
    anchor_resets = int(symbol_state.get("anchor_resets") or 0)
    resets_per_close = round(anchor_resets / realized_closes, 4) if realized_closes > 0 else None

    started_at = parse_iso_timestamp(str(runner.get("started_at") or ""))
    updated_at = parse_iso_timestamp(str(state_payload.get("updated_at") or runner.get("heartbeat_at") or ""))
    reset_rate_per_hour = None
    if started_at and updated_at and updated_at > started_at:
        elapsed_hours = (updated_at - started_at).total_seconds() / 3600.0
        if elapsed_hours > 0:
            reset_rate_per_hour = round(anchor_resets / elapsed_hours, 4)

    stale_after_seconds = int((btc_config or {}).get("stale_after_seconds") or 0)
    runtime_stale = False
    if stale_after_seconds > 0 and updated_at is not None:
        runtime_stale = (datetime.now(timezone.utc) - updated_at).total_seconds() > stale_after_seconds

    return {
        "proof_started": realized_closes > 0 or anchor_resets > 0 or abs(realized_net_usd) > 0,
        "runtime_stale": runtime_stale,
        "heartbeat_at": str(runner.get("heartbeat_at") or ""),
        "realized_closes": realized_closes,
        "realized_net_usd": realized_net_usd,
        "anchor_resets": anchor_resets,
        "resets_per_close": resets_per_close,
        "reset_rate_per_hour": reset_rate_per_hour,
        "action_bias": str(metadata.get("action_bias") or ""),
        "control_mode": str(metadata.get("control_mode") or ""),
        "max_resets_per_close": float(guardrails.get("max_resets_per_close")) if guardrails.get("max_resets_per_close") is not None else None,
        "max_resets_per_hour": float(guardrails.get("max_resets_per_hour")) if guardrails.get("max_resets_per_hour") is not None else None,
    }


def summarize_btc_close_mix(comparison_payload: dict[str, Any] | None) -> dict[str, Any]:
    close_mix = dict(((comparison_payload or {}).get("v2_close_mix") or {}))
    harvest_share = close_mix.get("harvest_share")
    return {
        "total_close_events": int(close_mix.get("total_close_events") or 0),
        "harvest_closes": int(close_mix.get("harvest_closes") or 0),
        "escape_tier2_surgical_closes": int(close_mix.get("escape_tier2_surgical_closes") or 0),
        "harvest_share": float(harvest_share) if harvest_share is not None else None,
        "close_mix_status": str(close_mix.get("close_mix_status") or ""),
        "all_closes_escape_dominated": bool(close_mix.get("all_closes_escape_dominated")),
    }


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
    success_match = re.search(r"\*\*Success criteria for forward proof:\*\*([\s\S]+?)\n---", markdown_text)
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


def build_payload(
    profit_board: dict[str, Any],
    eth_control_gate: dict[str, Any],
    next_action_board: dict[str, Any],
    launch_safety: dict[str, Any],
    bucket_split_summary: dict[str, float],
    btc_config: dict[str, Any] | None = None,
    btc_state: dict[str, Any] | None = None,
    btc_reconciliation_report: dict[str, Any] | None = None,
    btc_sell_tight_comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eth_theory = theory_row(profit_board, "eth_m5_no_session_gate_harvest_rebuild")
    btc_theory = theory_row(profit_board, "btc_m15_downtrend_sell_tight_shape")
    gbp_theory = theory_row(profit_board, "fx_alpha_half_universal_prior")
    btc_step200_theory = theory_row(profit_board, "btc_m5_step200_salvage_probe")
    index_theory = theory_row(profit_board, "index_asymmetry_family_prior")

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
    nas_action = action_row_any(
        next_action_board,
        [
            "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves",
            "treat_nas100_m15_breakout_buy_as_the_only_clean_research_only_shadow_candidate",
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

    btc_launch = config_row(launch_safety, "hungry_hippo_btcusd_m15_sell_tight_shadow.json")
    btc_step200_launch = config_row(launch_safety, "hungry_hippo_btcusd_m5_step200_shadow.json")
    nas_launch = config_row(launch_safety, "hungry_hippo_nas100_m15_breakout_buy_shadow.json")
    us30_launch = config_row(launch_safety, "hungry_hippo_us30_m15_breakdown_sell_shadow.json")

    btc_reconciliation_report = btc_reconciliation_report or {"status": "", "success_criteria": []}
    btc_forward = summarize_btc_forward_proof(btc_config, btc_state)
    btc_close_mix = summarize_btc_close_mix(btc_sell_tight_comparison)
    btc_initial_close_target = 10
    btc_readiness = "shadow_reconciled_waiting_forward_proof"
    btc_blocker = "forward_proof_not_collected_yet"
    btc_next_move = "Keep the config shadow-only and wait for enough forward closes to decide whether the downtrend controller actually improves loss control."
    if btc_forward["proof_started"]:
        btc_readiness = "shadow_forward_sample_running"
        btc_blocker = "forward_sample_started_but_still_negative"
        btc_next_move = "Keep the v2 sell-tight lane shadow-only and let the fresh sample grow until net recovers positive over enough closes to judge whether the retune actually reduces losses."
        if btc_forward["runtime_stale"]:
            btc_blocker = "forward_sample_runtime_went_stale"
            btc_next_move = "Restore a fresh BTC v2 heartbeat before judging the new sample; stale forward proof is not honest enough to cite."
        elif btc_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far":
            btc_blocker = "forward_sample_all_escape_zero_harvest_so_far"
            btc_next_move = "Keep the fresh BTC v2 sample running until the close mix stops being all-escape and the first close_ticket harvest appears; cleaner losses alone are not enough to treat the retune as real loss control."
        elif (
            btc_forward["realized_net_usd"] <= 0
            and btc_forward["max_resets_per_hour"] is not None
            and btc_forward["reset_rate_per_hour"] is not None
            and btc_forward["reset_rate_per_hour"] > btc_forward["max_resets_per_hour"]
        ):
            btc_blocker = "forward_sample_negative_and_reset_rate_above_hourly_guardrail"
            btc_next_move = "Keep the fresh BTC v2 sample running long enough to separate startup churn from a real failure, but do not treat it as improved loss control until the reset pace falls back under the hourly guardrail and net recovers."
        elif (
            btc_forward["realized_net_usd"] <= 0
            and btc_forward["max_resets_per_close"] is not None
            and btc_forward["resets_per_close"] is not None
            and btc_forward["resets_per_close"] > btc_forward["max_resets_per_close"]
        ):
            btc_blocker = "forward_sample_negative_and_reset_heavy"
            btc_next_move = "Keep the fresh BTC v2 sample shadow-only until the reset-per-close ratio drops back inside guardrails and the retuned sell-tight lane recovers positive net."
        elif btc_forward["realized_net_usd"] > 0 and btc_forward["realized_closes"] < btc_initial_close_target:
            btc_blocker = "initial_positive_sample_not_large_enough_yet"
            btc_next_move = "Fresh BTC v2 proof has started, but the sample is still too small; keep it shadow-only until the first 10 positive closes arrive without guardrail breaks."

    eth_readiness = "control_restore_required"
    if eth_gate_verdict == "blocked_by_negative_expectancy":
        eth_readiness = "control_positive_proof_required"
    elif eth_gate_verdict == "blocked_by_control_normalization":
        eth_readiness = "control_normalization_required"

    rows = [
        {
            "priority": 1,
            "candidate": "ETHUSD M5 step14 normalized control",
            "source_theory": eth_theory["theory"],
            "readiness": eth_readiness,
            "evidence": {
                "gate_verdict": str((eth_control_gate.get("summary") or {}).get("verdict") or ""),
                "realized_closes": int((eth_control_gate.get("summary") or {}).get("realized_closes") or 0),
                "realized_net_usd": round(float((eth_control_gate.get("summary") or {}).get("realized_net_usd") or 0.0), 2),
                "avg_per_close": round(float((eth_control_gate.get("summary") or {}).get("avg_per_close") or 0.0), 4),
                "runtime_stale": bool((eth_control_gate.get("control_runtime") or {}).get("runtime_stale")),
                "geometry_normalized": bool((eth_control_gate.get("control_runtime") or {}).get("geometry_normalized")),
            },
            "blocker": eth_gate_verdict,
            "next_move": "Restore a fresh heartbeat on the registered step14 lane before any offensive-closure A/B or validated-shadow discussion."
            if eth_gate_verdict == "blocked_by_stale_runtime"
            else "Keep the aligned registered step14 control running until it prints enough positive forward proof to be trusted as the baseline."
            if eth_gate_verdict == "blocked_by_negative_expectancy"
            else "Normalize the registered step14 runtime geometry and accumulate honest control proof before any offensive-closure A/B or validated-shadow discussion."
            if eth_gate_verdict == "blocked_by_control_normalization"
            else "Unify the checked-in step14 control into one canonical launch/proof surface, then refresh the runtime heartbeat and normalized ladder before any offensive-closure A/B or validated-shadow discussion.",
        },
        {
            "priority": 2,
            "candidate": "BTCUSD M15 sell-tight downtrend shape",
            "source_theory": btc_theory["theory"],
            "readiness": btc_readiness,
            "evidence": {
                "launch_verdict": str(btc_launch.get("verdict") or ""),
                "config_enabled": bool((btc_config or {}).get("enabled")),
                "validation_status": str(((btc_config or {}).get("hungry_hippo_metadata") or {}).get("validation_status") or ""),
                "reconciliation_status": str(btc_reconciliation_report.get("status") or ""),
                "runtime_stale": bool(btc_forward["runtime_stale"]),
                "heartbeat_at": btc_forward["heartbeat_at"],
                "realized_closes": int(btc_forward["realized_closes"]),
                "realized_net_usd": float(btc_forward["realized_net_usd"]),
                "anchor_resets": int(btc_forward["anchor_resets"]),
                "resets_per_close": btc_forward["resets_per_close"],
                "reset_rate_per_hour": btc_forward["reset_rate_per_hour"],
                "max_resets_per_close": btc_forward["max_resets_per_close"],
                "max_resets_per_hour": btc_forward["max_resets_per_hour"],
                "btc_total_close_events": btc_close_mix["total_close_events"],
                "btc_harvest_closes": btc_close_mix["harvest_closes"],
                "btc_escape_tier2_surgical_closes": btc_close_mix["escape_tier2_surgical_closes"],
                "btc_harvest_share": btc_close_mix["harvest_share"],
                "btc_close_mix_status": btc_close_mix["close_mix_status"],
                "btc_all_closes_escape_dominated": btc_close_mix["all_closes_escape_dominated"],
                "proof_started": bool(btc_forward["proof_started"]),
            },
            "blocker": btc_blocker,
            "next_move": btc_next_move,
        },
        {
            "priority": 3,
            "candidate": "GBPUSD alpha=0.5 FX harvest path",
            "source_theory": gbp_theory["theory"],
            "readiness": "closure_policy_diagnosis_before_live",
            "evidence": {
                "proof_closes": int((gbp_action.get("machine_truth") or {}).get("gbpusd_proof_closes") or 0),
                "guardrail_status": str((gbp_action.get("machine_truth") or {}).get("gbpusd_guardrail_status") or ""),
                "harvest_close_ticket_usd": float(bucket_split_summary.get("close_ticket") or 0.0),
                "escape_tier0_offensive_usd": float(bucket_split_summary.get("escape_tier0_offensive") or 0.0),
                "forced_unwind_usd": float(bucket_split_summary.get("forced_unwind") or 0.0),
                "closure_pair_live": bool(gbp_machine_truth.get("gbp_closure_pair_live")),
                "no_escape_present": bool(gbp_machine_truth.get("gbp_no_escape_present")),
            },
            "blocker": "no_escape_companion_lane_not_live_yet"
            if gbp_action_name == "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair"
            else "paired_forward_sample_not_ready_yet"
            if gbp_action_name == "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape"
            else "closure_buckets_dominate_harvest_and_live_path_contradiction_remains",
            "next_move": "Launch or restore the no-escape GBP companion lane and wait for fresh paired state before judging closure repair."
            if gbp_action_name == "launch_or_restore_shadow_gbpusd_tick_forward_no_escape_before_judging_closure_repair"
            else "Keep both GBP lanes live and collect enough paired forward closes to compare baseline vs no-escape honestly before restoring live-candidate language."
            if gbp_action_name == "accumulate_paired_forward_closes_and_compare_baseline_vs_no_escape"
            else "Split fresh forward evidence by harvest vs closure buckets and repair the leak before restoring live-candidate language.",
        },
        {
            "priority": 4,
            "candidate": "NAS100 asym breakout family lane",
            "source_theory": index_theory["theory"],
            "readiness": "research_only_shadow_candidate",
            "evidence": {
                "launch_verdict": str(nas_launch.get("verdict") or ""),
                "config_enabled": bool(nas_launch.get("enabled")),
                "proof_closes": int((nas_action.get("machine_truth") or {}).get("proof_closes") or 0),
                "guardrail_status": str((nas_action.get("machine_truth") or {}).get("guardrail_status") or ""),
                "deployment_gate_verdict": str((nas_action.get("machine_truth") or {}).get("deployment_gate_verdict") or ""),
            },
            "blocker": "manual_review_and_window_regime_continuity",
            "next_move": "Use NAS100 as the cleanest current HH shadow-expansion candidate, but keep it research-only until manual-review and window continuity are satisfied.",
        },
        {
            "priority": 5,
            "candidate": "BTCUSD M5 step200 salvage probe",
            "source_theory": btc_step200_theory["theory"],
            "readiness": "shadow_probe_ready_low_sample",
            "evidence": {
                "shadow_realized_closes": int((btc_step200_theory.get("machine_truth") or {}).get("shadow_realized_closes") or 0),
                "shadow_avg_per_close": round(float((btc_step200_theory.get("machine_truth") or {}).get("shadow_avg_per_close") or 0.0), 4),
                "launch_verdict": str(btc_step200_launch.get("verdict") or ""),
            },
            "blocker": "sample_too_small_and_launch_surface_still_failing",
            "next_move": "Keep it probe-only until the sample is materially larger and the current contract failures are no longer part of the story.",
        },
        {
            "priority": 6,
            "candidate": "US30 asym breakout family lane",
            "source_theory": index_theory["theory"],
            "readiness": "blocked_before_live_discussion",
            "evidence": {
                "launch_verdict": str(us30_launch.get("verdict") or ""),
                "gate_verdict": str(us30_launch.get("gate_verdict") or ""),
                "hard_fail_count": len(list(us30_launch.get("hard_fail_reasons") or [])),
            },
            "blocker": "guardrail_blockade_and_contract_failures",
            "next_move": "Keep US30 out of live discussion until the guardrail blockade is resolved and the current shadow contract no longer fails.",
        },
    ]

    readiness_counts: dict[str, int] = {}
    for row in rows:
        readiness = str(row["readiness"])
        readiness_counts[readiness] = readiness_counts.get(readiness, 0) + 1

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(PROFIT_BOARD_PATH.relative_to(ROOT)),
            str(ETH_CONTROL_GATE_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_BOARD_PATH.relative_to(ROOT)),
            str(LAUNCH_SAFETY_PATH.relative_to(ROOT)),
            str(BUCKET_SPLIT_MD_PATH.relative_to(ROOT)),
            str(BTC_DOWNTREND_CONFIG_PATH.relative_to(ROOT)),
            str(BTC_RECONCILIATION_REPORT_PATH.relative_to(ROOT)),
            str(BTC_SELL_TIGHT_COMPARISON_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "Current readiness should come from active control, safety, and queue surfaces rather than old profitability shelves.",
            "ETH is now blocked on positive proof quality from the aligned registered step14 lane, BTC sell-tight has started fresh proof but every realized close is still escape-only with zero harvest, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate."
            if btc_forward["proof_started"] and btc_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far" and eth_gate_verdict == "blocked_by_negative_expectancy"
            else
            "ETH is now blocked on runtime geometry normalization and fresh positive proof from the registered step14 lane, BTC sell-tight has started fresh proof but every realized close is still escape-only with zero harvest, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate."
            if btc_forward["proof_started"] and btc_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far" and eth_gate_verdict == "blocked_by_control_normalization"
            else
            "ETH is now blocked on positive proof quality from the aligned registered step14 lane, BTC sell-tight has started fresh proof but every realized close is still escape-only with zero harvest, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate."
            if btc_forward["proof_started"] and btc_close_mix["close_mix_status"] == "zero_harvest_all_escape_so_far"
            else
            "ETH is now blocked on positive proof quality from the aligned registered step14 lane, BTC sell-tight has started fresh proof but the early sample is still negative, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate."
            if btc_forward["proof_started"] and eth_gate_verdict == "blocked_by_negative_expectancy"
            else
            "ETH is now blocked on runtime geometry normalization and fresh positive proof from the registered step14 lane, BTC sell-tight has started fresh proof but the early sample is still negative, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate."
            if btc_forward["proof_started"] and eth_gate_verdict == "blocked_by_control_normalization"
            else
            "ETH is now blocked on positive proof quality from the aligned registered step14 lane, BTC sell-tight has started fresh proof but the early sample is still negative, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate."
            if btc_forward["proof_started"]
            else
            "ETH is now blocked on positive proof quality from the aligned registered step14 lane, BTC sell-tight is reconciled but still waiting on proof, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate."
            if eth_gate_verdict == "blocked_by_negative_expectancy"
            else
            "ETH is now blocked on runtime geometry normalization and fresh positive proof from the registered step14 lane, BTC sell-tight is reconciled but still waiting on proof, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate."
            if eth_gate_verdict == "blocked_by_control_normalization"
            else "ETH is blocked on control restoration or surface unification, BTC sell-tight is reconciled but still waiting on proof, GBP is a closure-policy diagnosis lane, and NAS100 is the cleanest checked-in HH shadow candidate.",
            "This board separates what can be tested now from what merely sounds promising.",
        ],
        "summary": {
            "candidate_count": len(rows),
            "readiness_counts": readiness_counts,
            "top_candidates": [row["candidate"] for row in rows[:4]],
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Shadow Graduation Readiness Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: convert current theory state into explicit shadow-launch and validated-shadow readiness so ideas move through the pipeline honestly.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Candidate count: `{summary.get('candidate_count', 0)}`")
    readiness_counts = dict(summary.get("readiness_counts") or {})
    if readiness_counts:
        lines.append("- Readiness counts: `" + ", ".join(f"{key}={value}" for key, value in readiness_counts.items()) + "`")
    top_candidates = list(summary.get("top_candidates") or [])
    if top_candidates:
        lines.append("- Top candidates: `" + ", ".join(top_candidates) + "`")

    lines.extend(["", "## Queue", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### P{int(row['priority'])} - {row['candidate']}")
        lines.append("")
        lines.append(f"- Readiness: `{row['readiness']}`")
        lines.append(f"- Source theory: `{row['source_theory']}`")
        lines.append(f"- Evidence: `{', '.join(f'{k}={v}' for k, v in dict(row.get('evidence') or {}).items())}`")
        lines.append(f"- Blocker: `{row['blocker']}`")
        lines.append(f"- Next move: `{row['next_move']}`")
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
        load_json(ETH_CONTROL_GATE_PATH),
        load_json(NEXT_ACTION_BOARD_PATH),
        load_json(LAUNCH_SAFETY_PATH),
        parse_bucket_split_summary(bucket_text),
        btc_config,
        load_optional_repo_json(active_state_path_from_config(btc_config)),
        btc_reconciliation_report,
        load_optional_json(BTC_SELL_TIGHT_COMPARISON_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
