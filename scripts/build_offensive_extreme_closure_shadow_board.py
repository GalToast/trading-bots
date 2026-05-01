#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

PROFIT_BOARD_PATH = REPORTS / "profit_theory_graduation_board.json"
NEXT_ACTION_PATH = REPORTS / "hungry_hippo_next_action_board.json"
GATE_MATRIX_PATH = REPORTS / "theory_shadow_live_gate_matrix.json"
RUBRIC_PATH = REPORTS / "graduation_rubric_board.json"
ETH_CONTROL_GATE_PATH = REPORTS / "eth_m5_control_proof_gate_board.json"

OUTPUT_JSON_PATH = REPORTS / "offensive_extreme_closure_shadow_board.json"
OUTPUT_MD_PATH = REPORTS / "offensive_extreme_closure_shadow_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def row_by_candidate(payload: dict[str, Any], candidate: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("candidate") or "") == candidate:
            return row
    raise KeyError(f"candidate not found: {candidate}")


def row_by_theory(payload: dict[str, Any], theory: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("theory") or "") == theory:
            return row
    raise KeyError(f"theory not found: {theory}")


def row_by_action(payload: dict[str, Any], action: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get("action") or "") == action:
            return row
    raise KeyError(f"action not found: {action}")


def build_payload(
    profit_board: dict[str, Any],
    next_action_board: dict[str, Any],
    gate_matrix: dict[str, Any],
    rubric_board: dict[str, Any],
    eth_control_gate: dict[str, Any],
) -> dict[str, Any]:
    offensive_theory = row_by_theory(profit_board, "offensive_extreme_closure")
    eth_gate = row_by_candidate(gate_matrix, "ETHUSD M5 step14 normalized control")
    btc_probe_gate = row_by_candidate(gate_matrix, "BTCUSD M5 step200 salvage probe")
    btc_downtrend_gate = row_by_candidate(gate_matrix, "BTCUSD M15 sell-tight downtrend shape")
    gbp_gate = row_by_candidate(gate_matrix, "GBPUSD alpha=0.5 FX harvest path")
    eth_rubric = row_by_candidate(rubric_board, "ETHUSD M5 step14 normalized control")
    btc_probe_rubric = row_by_candidate(rubric_board, "BTCUSD M5 step200 salvage probe")
    btc_downtrend_rubric = row_by_candidate(rubric_board, "BTCUSD M15 sell-tight downtrend shape")
    gbp_rubric = row_by_candidate(rubric_board, "GBPUSD alpha=0.5 FX harvest path")
    eth_action = row_by_action(
        next_action_board,
        "prepare_eth_m5_offensive_closure_ab_only_after_control_normalization",
    )
    nas_action = row_by_action(
        next_action_board,
        "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves",
    )
    eth_gate_summary = dict(eth_control_gate.get("summary") or {})
    eth_gate_proof = dict(eth_control_gate.get("proof_progress") or {})
    eth_control_verdict = str(
        eth_gate_summary.get("verdict")
        or (eth_rubric.get("current_metrics") or {}).get("control_verdict")
        or ""
    )
    eth_control_realized_closes = int(
        eth_gate_proof.get("realized_closes")
        or eth_gate_summary.get("realized_closes")
        or (eth_rubric.get("current_metrics") or {}).get("control_realized_closes")
        or 0
    )
    eth_control_realized_net_usd = float(
        eth_gate_proof.get("realized_net_usd")
        or eth_gate_summary.get("realized_net_usd")
        or 0.0
    )
    eth_control_avg_per_close = float(
        eth_gate_proof.get("avg_per_close")
        or eth_gate_summary.get("avg_per_close")
        or 0.0
    )
    eth_required_forward_closes = int(
        eth_gate_summary.get("target_closes")
        or (eth_rubric.get("candidate_rubric") or {}).get("required_forward_closes")
        or 0
    )
    eth_closes_remaining = int(
        eth_gate_proof.get("closes_remaining")
        or eth_gate_summary.get("closes_remaining")
        or max(0, eth_required_forward_closes - eth_control_realized_closes)
    )
    btc_metrics = dict(btc_downtrend_rubric.get("current_metrics") or {})
    btc_blocking_issue = str(btc_downtrend_gate.get("blocking_issue") or "")
    btc_waiting_for_first_proof = btc_blocking_issue in {"forward_proof_not_collected_yet", "needs forward proof"}
    btc_negative_or_unstable_issues = {
        "forward_sample_negative_and_reset_rate_above_hourly_guardrail",
        "forward_sample_negative_and_reset_heavy",
        "forward_sample_started_but_still_negative",
        "forward_sample_runtime_went_stale",
    }
    btc_close_mix_status = (
        "zero_harvest_all_escape_so_far"
        if btc_blocking_issue == "forward_sample_all_escape_zero_harvest_so_far"
        else str(btc_metrics.get("btc_close_mix_status") or btc_metrics.get("close_mix_status") or "")
    )
    eth_status = "first_honest_pilot_after_control_restore"
    eth_why = "Current authority surfaces agree that the first honest offensive-closure pilot is ETH step14 once the control runtime is fresh, shape-clean, and comparison hygiene is real."
    eth_safety = "do not launch until the control runtime is fresh and the ladder matches the declared step14 shape"
    eth_graduation_gate = "restore control first, then require 25 clean closes and an OFF vs budgeted-ON comparison on the same shape before any live talk"
    if eth_control_verdict == "blocked_by_control_normalization":
        eth_why = "Current authority, next-action, and rubric surfaces agree that the first honest offensive-closure pilot is still ETH step14, but only after the aligned runtime behaves like a real fixed-step control and the proof sample stops being negative."
        eth_safety = "do not launch until the registered control keeps a fresh heartbeat and the ladder stays close to the declared step14 shape"
        eth_graduation_gate = "keep the aligned step14 control clean, then require 25 normalized positive closes and an OFF vs budgeted-ON comparison on the same shape before any live talk"
    elif eth_control_verdict == "blocked_by_negative_expectancy":
        eth_status = "first_honest_pilot_after_positive_control_proof"
        eth_why = "Current authority surfaces agree that the first honest offensive-closure pilot is still ETH step14, but the remaining blocker is positive control proof, not infra repair or shelf-history cleanup."
        eth_safety = "do not launch until the aligned control stops printing non-positive proof and can survive as the honest OFF baseline"
        eth_graduation_gate = "keep the aligned step14 control running until it has enough positive proof, then require an OFF vs budgeted-ON comparison on that same shape before any live talk"
    elif eth_control_verdict in {"continue_observation", "ready_for_proof_but_not_clean_ab"}:
        eth_status = "first_honest_pilot_after_positive_control_proof"
        eth_why = "ETH step14 remains the first honest offensive-closure pilot, and the current blocker is finishing the positive proof contract cleanly rather than fixing lane plumbing."
        eth_safety = "do not launch until the control clears its positive proof contract and remains the honest OFF baseline"
        eth_graduation_gate = "finish the positive proof contract first, then run OFF vs budgeted-ON on the same shape before any live talk"

    btc_status = "pilot_only_after_filesystem_confirmed_forward_proof"
    btc_why = "BTC sell-tight is now beyond config reconciliation, but it still needs visible post-launch proof before offensive closure can honestly be layered on top."
    btc_graduation_gate = "wait for refreshed state/event files and initial positive shadow proof before judging offensive closure on top"
    if btc_waiting_for_first_proof:
        btc_status = "pilot_only_after_filesystem_confirmed_forward_proof"
        btc_why = "BTC sell-tight is now beyond config reconciliation, but it still needs visible post-launch proof before offensive closure can honestly be layered on top."
        btc_graduation_gate = "wait for refreshed state/event files and initial positive shadow proof before judging offensive closure on top"
    elif btc_blocking_issue == "forward_sample_all_escape_zero_harvest_so_far":
        btc_status = "later_after_btc_harvest_appears"
        btc_why = "BTC sell-tight already has a fresh v2 sample, but every realized close is still escape-only and the sample remains negative, so offensive closure would be stacked on top of unproven baseline behavior."
        btc_graduation_gate = "first prove the retuned sell-tight baseline can print close_ticket harvests and move away from all-escape negative proof before layering offensive closure on top"
    elif btc_blocking_issue in btc_negative_or_unstable_issues:
        btc_status = "later_after_btc_forward_sample_stabilizes"
        btc_why = "BTC sell-tight already has a live v2 sample, but that sample is still negative or operationally unstable enough that layering offensive closure on top now would contaminate the read."
        btc_graduation_gate = "first prove the retuned sell-tight baseline itself can stay inside reset guardrails and recover positive net over a meaningful fresh sample before layering offensive closure on top"
    else:
        btc_status = "pilot_only_after_btc_forward_sample_grows"
        btc_why = "BTC sell-tight has fresh forward proof starting to accumulate, but the sample is still too small to justify adding an offensive-closure variant on top yet."
        btc_graduation_gate = "materially expand the fresh sample before judging offensive closure on top of BTC sell-tight"

    btc_leadership_read = "BTC step200 remains the high-upside second pilot after contract cleanliness and sample growth, BTC sell-tight still needs filesystem-confirmed forward proof, and GBP moves later behind bucket repair rather than ahead as a transfer story."
    if btc_blocking_issue == "forward_sample_all_escape_zero_harvest_so_far":
        btc_leadership_read = "BTC step200 remains the high-upside second pilot after contract cleanliness and sample growth, BTC sell-tight comes only after it prints real harvests and leaves the current all-escape negative proof state, and GBP moves later behind bucket repair rather than ahead as a transfer story."
    elif btc_blocking_issue in btc_negative_or_unstable_issues:
        btc_leadership_read = "BTC step200 remains the high-upside second pilot after contract cleanliness and sample growth, BTC sell-tight comes only after its fresh v2 baseline stabilizes, and GBP moves later behind bucket repair rather than ahead as a transfer story."
    elif not btc_waiting_for_first_proof:
        btc_leadership_read = "BTC step200 remains the high-upside second pilot after contract cleanliness and sample growth, BTC sell-tight only becomes interesting after the fresh sample materially outgrows early proof noise, and GBP moves later behind bucket repair rather than ahead as a transfer story."

    rows = [
        {
            "priority": 1,
            "pilot": "ETHUSD M5 step14 normalized control",
            "status": eth_status,
            "why": eth_why,
            "machine_truth": {
                "current_stage": str(eth_gate.get("current_stage") or ""),
                "control_verdict": eth_control_verdict,
                "control_realized_closes": eth_control_realized_closes,
                "control_realized_net_usd": eth_control_realized_net_usd,
                "control_avg_per_close": eth_control_avg_per_close,
                "closes_remaining": eth_closes_remaining,
                "required_forward_closes": eth_required_forward_closes,
                "comparison_status": str((eth_action.get("machine_truth") or {}).get("comparison_status") or ""),
                "recommended_control_step": float((eth_action.get("machine_truth") or {}).get("recommended_control_step") or 0.0),
            },
            "proposed_shadow_spec": {
                "control_arm": "ETH step14 normalized control with offensive closure OFF",
                "variant_arm": "same ETH step14 shape with budgeted offensive closure ON",
                "funding_rule": "allow subsidized cuts only when realized harvest buffer and cumulative budget both permit them",
                "safety": eth_safety,
            },
            "graduation_gate": eth_graduation_gate,
        },
        {
            "priority": 2,
            "pilot": "BTCUSD M5 step200 salvage probe",
            "status": "second_pilot_after_contract_clean_sample_growth",
            "why": "BTC step200 still has the high-upside upside profile, but it remains a probe until the launch surface is clean and the sample materially outgrows 2 closes.",
            "machine_truth": {
                "current_stage": str(btc_probe_gate.get("current_stage") or ""),
                "launch_verdict": str((btc_probe_rubric.get("current_metrics") or {}).get("launch_verdict") or ""),
                "shadow_realized_closes": int((btc_probe_rubric.get("current_metrics") or {}).get("shadow_realized_closes") or 0),
                "shadow_avg_per_close": float((btc_probe_rubric.get("current_metrics") or {}).get("shadow_avg_per_close") or 0.0),
                "required_forward_closes": int((btc_probe_rubric.get("candidate_rubric") or {}).get("required_forward_closes") or 0),
            },
            "proposed_shadow_spec": {
                "close_scope": "outermost_positions_only",
                "close_window": "micro_loss_or_micro_profit_only",
                "funding_rule": "only cut when inner realized buffer exceeds projected cut cost by a wide margin",
                "safety": "remain shadow-only until the launch surface is contract-clean and the sample is statistically meaningful",
            },
            "graduation_gate": "must clear contract cleanliness and materially expand the sample before the offensive-closure read has live relevance",
        },
        {
            "priority": 3,
            "pilot": "BTCUSD M15 sell-tight downtrend shape",
            "status": btc_status,
            "why": btc_why,
            "machine_truth": {
                "current_stage": str(btc_downtrend_rubric.get("current_stage") or ""),
                "validation_status": str(btc_metrics.get("validation_status") or ""),
                "realized_closes": int(btc_metrics.get("realized_closes") or 0),
                "realized_net_usd": float(btc_metrics.get("realized_net_usd") or 0.0),
                "anchor_resets": int(btc_metrics.get("anchor_resets") or 0),
                "resets_per_close": btc_metrics.get("resets_per_close"),
                "reset_rate_per_hour": btc_metrics.get("reset_rate_per_hour"),
                "required_initial_shadow_positive_closes": int((btc_downtrend_rubric.get("candidate_rubric") or {}).get("required_initial_shadow_positive_closes") or 0),
                "required_validated_shadow_closes": int((btc_downtrend_rubric.get("candidate_rubric") or {}).get("required_validated_shadow_closes") or 0),
                "close_mix_status": btc_close_mix_status,
                "blocking_issue": btc_blocking_issue,
            },
            "proposed_shadow_spec": {
                "close_scope": "sell_side_extremes_first",
                "close_window": "small_loss_before_reversal_widens",
                "funding_rule": "subsidize cuts from realized inner harvest only after the sell-tight lattice prints fresh forward harvest under the current launch",
                "safety": "do not touch the existing BTC live M15 baseline",
            },
            "graduation_gate": btc_graduation_gate,
        },
        {
            "priority": 4,
            "pilot": "GBPUSD alpha=0.5 FX harvest path",
            "status": "later_after_bucket_repair_and_contradiction_cleanup",
            "why": "GBP is no longer a clean transfer candidate because closure tax dominates the current lane; it only becomes relevant for offensive closure after bucket repair and contradiction cleanup.",
            "machine_truth": {
                "current_stage": str(gbp_gate.get("current_stage") or ""),
                "harvest_close_ticket_usd": float((gbp_gate.get("current_truth") or {}).get("harvest_close_ticket_usd") or 0.0),
                "escape_tier0_offensive_usd": float((gbp_gate.get("current_truth") or {}).get("escape_tier0_offensive_usd") or 0.0),
                "forced_unwind_usd": float((gbp_gate.get("current_truth") or {}).get("forced_unwind_usd") or 0.0),
                "required_bucket_split": str((gbp_rubric.get("candidate_rubric") or {}).get("required_bucket_split") or ""),
            },
            "proposed_shadow_spec": {
                "close_scope": "fx_edge_positions_only",
                "close_window": "very_small_loss_or_breakeven_only",
                "funding_rule": "only after closure tax is bounded and fresh bucket repair proves the lane is no longer dominated by offensive/forced exits",
                "safety": "do not let the offensive layer override the validated alpha=0.5 entry prior itself",
            },
            "graduation_gate": "repair the closure leak and contradiction first, then reconsider GBP as a transfer candidate",
        },
    ]

    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        status_counts[status] = status_counts.get(status, 0) + 1

    experiment_protocol = {
        "comparison_mode": "shadow_only_variant_vs_baseline_same_symbol",
        "primary_success": [
            "carry drag falls without flipping avg_per_close negative",
            "offensive cuts happen inside a tiny-loss to breakeven band rather than as hidden stop-loss imitation",
            "reset behavior does not degrade into a new reset storm",
            "realized inner-lattice profit subsidizes cuts instead of cuts dominating realized PnL",
        ],
        "failure_triggers": [
            "avg_per_close turns negative or degrades materially versus the same lane baseline",
            "offensive cuts consume more realized buffer than they save in floating drag",
            "cuts start firing deep in loss instead of near-flat extremes",
            "the variant only looks better because sample is too small or market regime became easier",
        ],
        "anti_goals": [
            "do not reinvent a generic stop-loss system and call it offensive closure",
            "do not override proven geometry/controller priors just to make the experiment look active",
            "do not use pilot profitability alone as proof that the closure mechanic itself worked",
            "do not discuss live promotion until the shadow comparison is clean and repeatable",
        ],
    }

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(PROFIT_BOARD_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
            str(GATE_MATRIX_PATH.relative_to(ROOT)),
            str(RUBRIC_PATH.relative_to(ROOT)),
            str(ETH_CONTROL_GATE_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "Offensive extreme closure is ready to become a real shadow experiment, but not every profitable lane is a good first pilot.",
            "The first honest pilot is no longer ETH step5 shelf language; it is ETH step14 on the current same-shape control, and the remaining gate should be stated as proof quality instead of stale infra language whenever the lane is already aligned.",
            btc_leadership_read,
        ],
        "policy_status": str((offensive_theory.get("machine_truth") or {}).get("policy_status") or ""),
        "experiment_protocol": experiment_protocol,
        "summary": {
            "pilot_count": len(rows),
            "status_counts": status_counts,
            "first_pilot": rows[0]["pilot"],
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Offensive Extreme Closure Shadow Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: choose the first real shadow pilots for offensive extreme closure so the less-losses thesis moves from idea to measured experiment.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Policy status: `{payload.get('policy_status', '')}`")
    lines.append(f"- Pilot count: `{summary.get('pilot_count', 0)}`")
    counts = dict(summary.get("status_counts") or {})
    if counts:
        lines.append("- Status counts: `" + ", ".join(f"{k}={v}" for k, v in counts.items()) + "`")
    lines.append(f"- First pilot: `{summary.get('first_pilot', '')}`")

    protocol = dict(payload.get("experiment_protocol") or {})
    lines.extend(["", "## Experiment Protocol", ""])
    lines.append(f"- Comparison mode: `{protocol.get('comparison_mode', '')}`")
    primary_success = list(protocol.get("primary_success") or [])
    if primary_success:
        lines.append(f"- Primary success: `{'; '.join(primary_success)}`")
    failure_triggers = list(protocol.get("failure_triggers") or [])
    if failure_triggers:
        lines.append(f"- Failure triggers: `{'; '.join(failure_triggers)}`")
    anti_goals = list(protocol.get("anti_goals") or [])
    if anti_goals:
        lines.append(f"- Anti-goals: `{'; '.join(anti_goals)}`")

    lines.extend(["", "## Queue", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### P{int(row['priority'])} - {row['pilot']}")
        lines.append("")
        lines.append(f"- Status: `{row['status']}`")
        lines.append(f"- Why: `{row['why']}`")
        lines.append(f"- Machine truth: `{', '.join(f'{k}={v}' for k, v in dict(row.get('machine_truth') or {}).items())}`")
        lines.append(f"- Proposed shadow spec: `{', '.join(f'{k}={v}' for k, v in dict(row.get('proposed_shadow_spec') or {}).items())}`")
        lines.append(f"- Graduation gate: `{row['graduation_gate']}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict[str, Any]) -> None:
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


def main() -> int:
    payload = build_payload(
        load_json(PROFIT_BOARD_PATH),
        load_json(NEXT_ACTION_PATH),
        load_json(GATE_MATRIX_PATH),
        load_json(RUBRIC_PATH),
        load_json(ETH_CONTROL_GATE_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
