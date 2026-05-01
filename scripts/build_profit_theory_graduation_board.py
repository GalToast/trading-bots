#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

CONTROLLER_PRIORS_PATH = ROOT / "configs" / "adaptive_controller_priors.json"
SALVAGE_BOARD_PATH = REPORTS / "m5_warp_salvage_board.json"
PROMOTION_QUEUE_PATH = REPORTS / "hungry_hippo_promotion_queue.json"
BTC_HANDOFF_PATH = REPORTS / "btc_downtrend_handoff.json"
BTC_RECONCILIATION_PATH = REPORTS / "btc_downtrend_config_reconciliation_board.json"
ETH_CONTROL_GATE_PATH = REPORTS / "eth_m5_control_proof_gate_board.json"
NEXT_ACTION_BOARD_PATH = REPORTS / "hungry_hippo_next_action_board.json"
BUCKET_SPLIT_MD_PATH = REPORTS / "bucket_split_analysis.md"
BTC_CONFIG_PATH = ROOT / "configs" / "hungry_hippo_btcusd_m15_sell_tight_shadow.json"

OUTPUT_JSON_PATH = REPORTS / "profit_theory_graduation_board.json"
OUTPUT_MD_PATH = REPORTS / "profit_theory_graduation_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def first_row(rows: list[dict[str, Any]], **matches: str) -> dict[str, Any]:
    for row in rows:
        if not isinstance(row, dict):
            continue
        if all(str(row.get(key) or "") == value for key, value in matches.items()):
            return row
    raise KeyError(f"row not found for {matches}")


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


def build_payload(
    controller_priors: dict[str, Any],
    salvage_board: dict[str, Any],
    promotion_queue: dict[str, Any],
    btc_handoff: dict[str, Any],
    btc_reconciliation: dict[str, Any],
    eth_control_gate: dict[str, Any],
    next_action_board: dict[str, Any],
    bucket_split_summary: dict[str, float],
    btc_config: dict[str, Any],
) -> dict[str, Any]:
    symbol_priors = dict(controller_priors.get("symbol_priors") or {})
    global_policy = dict(controller_priors.get("global_policy") or {})
    salvage_lanes = list(salvage_board.get("lanes") or [])
    promotion_rows = list(promotion_queue.get("rows") or [])

    btc_shadow = first_row(salvage_lanes, lane="shadow_btcusd_m5_warp_step200")
    btc_live_m15 = first_row(salvage_lanes, lane="live_btcusd_m15_warp")
    nas100_queue = first_row(promotion_rows, symbol="NAS100")
    us30_queue = first_row(promotion_rows, symbol="US30")
    eth_control_summary = dict(eth_control_gate.get("summary") or {})
    eth_control_verdict = str(eth_control_summary.get("verdict") or "")

    nas_action = action_row_any(
        next_action_board,
        [
            "keep_nas100_breakout_research_only_and_treat_it_as_closure_diagnosis_until_fresh_window_improves",
            "treat_nas100_m15_breakout_buy_as_the_only_clean_research_only_shadow_candidate",
        ],
    )
    try:
        btc_action = action_row_any(
            next_action_board,
            [
                "continue_btc_m15_sell_tight_v2_forward_proof_and_watch_reset_behavior",
                "continue_btc_m15_sell_tight_forward_proof_and_watch_reset_behavior",
            ],
        )
    except KeyError:
        btc_action = {}
    btc_action_truth = dict(btc_action.get("machine_truth") or {})
    btc_close_mix_status = str(btc_action_truth.get("btc_close_mix_status") or "")

    eth_stage = "tested_theory_waiting_for_clean_control"
    eth_evidence = "ETH M5 step14 is positive enough to matter, but the current proof gate still says the checked-in control and the judged proof surface are not yet one canonical lane, so the lane is not yet a clean control."
    eth_next_move = "Unify the checked-in step14 control as one canonical launch/proof lane, then refresh the heartbeat and normalized ladder before the first honest OFF vs budgeted-ON offensive-closure A/B on that exact shape."
    eth_why_now = "This is still the cleanest less-losses experiment in the room, but only after the control truth is clean enough to compare against."
    eth_leadership_fragment = "ETH is currently a canonical-control unification problem"
    if eth_control_verdict == "blocked_by_negative_expectancy":
        eth_stage = "tested_theory_waiting_for_positive_control_proof"
        eth_evidence = "ETH M5 step14 is now the aligned control lane, but the current forward sample is still negative, so the blocker is positive proof rather than launch/proof surface mismatch."
        eth_next_move = "Keep the aligned step14 control running as the single proof lane until the sample turns positive over the required close count, then run the first honest OFF vs budgeted-ON offensive-closure A/B on that exact shape."
        eth_why_now = "This is still the cleanest less-losses experiment in the room, and the leverage is now in honest positive-proof accumulation rather than more surface unification work."
        eth_leadership_fragment = "ETH is currently blocked by negative control proof on the aligned step14 lane"
    elif eth_control_verdict == "blocked_by_control_normalization":
        eth_evidence = "ETH M5 step14 is the right lane, but the control still needs normalization before its sample can count as honest proof for a less-losses comparison."
        eth_next_move = "Keep the aligned step14 control as the single proof lane, finish normalization, and only then run the first honest OFF vs budgeted-ON offensive-closure A/B on that exact shape."
        eth_why_now = "This is still the cleanest less-losses experiment in the room, but the leverage is finishing control normalization instead of opening new branches."
        eth_leadership_fragment = "ETH is currently a control-normalization problem on the aligned step14 lane"

    btc_evidence = "The BTC sell-tight candidate has moved past reconciliation. The config metadata now says it is aligned to handoff and awaiting forward proof."
    btc_next_move = "Keep the live BTC M15 baseline untouched and collect fresh forward proof on the reconciled sell-tight config before making any loss-control claim."
    btc_why_now = "This remains the cleanest path to less BTC bleed under SELL bias, and the bottleneck has moved from reconciliation to evidence."
    btc_leadership_fragment = "BTC sell-tight has moved from reconciliation into forward-proof waiting"
    if btc_close_mix_status == "zero_harvest_all_escape_so_far":
        btc_evidence = "The BTC sell-tight candidate is already printing fresh forward data, but every realized close so far is still escape-only with zero harvest closes, so the current read is still proof-of-problem rather than proof of cleaner loss control."
        btc_next_move = "Keep the live BTC M15 baseline untouched and keep collecting fresh v2 forward proof until close_ticket harvest appears, the close mix stops being all-escape, and reset behavior stays inside guardrails."
        btc_why_now = "This remains the cleanest path to less BTC bleed under SELL bias, but the leverage is in honest proof-quality readout rather than more reconciliation language."
        btc_leadership_fragment = "BTC sell-tight is an active forward-proof watch with zero harvest closes and all closes still escape-only"

    rows = [
        {
            "priority": 1,
            "theory": "fx_alpha_half_universal_prior",
            "stage": "validated_live_prior",
            "goal": "larger_profits_less_losses",
            "evidence": "FX alpha=0.5 remains the strongest validated controller prior, but the current GBP path should not regain near-live language until the paired no-escape control has enough forward evidence to compare baseline vs no-escape honestly.",
            "machine_truth": {
                "gbpusd_avg_per_close": float(symbol_priors["GBPUSD"]["evidence"]["gbp_rearm_avg_per_close"]),
                "eurusd_avg_per_close": float(symbol_priors["EURUSD"]["evidence"]["eur_rearm_avg_per_close"]),
                "eurusd_guardrail_status": str(symbol_priors["EURUSD"].get("guardrail_status") or ""),
                "gbpusd_bucket_close_ticket": float(bucket_split_summary.get("close_ticket") or 0.0),
                "gbpusd_bucket_escape_tier0_offensive": float(bucket_split_summary.get("escape_tier0_offensive") or 0.0),
                "gbpusd_bucket_forced_unwind": float(bucket_split_summary.get("forced_unwind") or 0.0),
            },
            "next_move": "Keep alpha=0.5 as the FX controller prior for new harvest shadows, but require enough paired forward closes on baseline vs no-escape before using GBP as a near-live example again.",
            "why_now": "The prior itself is still production-backed; the current leverage is protecting that truth from being polluted by the GBP closure leak.",
        },
        {
            "priority": 2,
            "theory": "eth_m5_no_session_gate_harvest_rebuild",
            "stage": eth_stage,
            "goal": "restore_spread_safe_eth_control_then_test_budgeted_loss_reduction",
            "evidence": eth_evidence,
            "machine_truth": {
                "control_verdict": eth_control_verdict,
                "control_realized_closes": int(eth_control_summary.get("realized_closes") or 0),
                "control_realized_net_usd": round(float(eth_control_summary.get("realized_net_usd") or 0.0), 2),
                "control_avg_per_close": round(float(eth_control_summary.get("avg_per_close") or 0.0), 4),
                "comparison_status": str(eth_control_summary.get("comparison_status") or ""),
            },
            "next_move": eth_next_move,
            "why_now": eth_why_now,
        },
        {
            "priority": 3,
            "theory": "btc_m15_downtrend_sell_tight_shape",
            "stage": "shadow_config_reconciled_waiting_forward_proof",
            "goal": "reduce_loss_when_btc_bias_flips_sell",
            "evidence": btc_evidence,
            "machine_truth": {
                "current_action_bias": str(btc_handoff["current_truth"]["regime_signal"]["action_bias"]),
                "current_control_mode": str(btc_handoff["current_truth"]["regime_signal"]["control_mode"]),
                "launch_verdict": str(btc_action_truth.get("btc_launch_verdict") or ""),
                "validation_status": str(((btc_config.get("hungry_hippo_metadata") or {}).get("validation_status") or "")),
                "btc_forward_proof_started": bool(btc_action_truth.get("btc_forward_proof_started")),
                "btc_realized_closes": int(btc_action_truth.get("btc_realized_closes") or 0),
                "btc_realized_net_usd": round(float(btc_action_truth.get("btc_realized_net_usd") or 0.0), 2),
                "btc_anchor_resets": int(btc_action_truth.get("btc_anchor_resets") or 0),
                "btc_reset_rate_per_hour": btc_action_truth.get("btc_reset_rate_per_hour"),
                "btc_harvest_closes": int(btc_action_truth.get("btc_harvest_closes") or 0),
                "btc_escape_tier2_surgical_closes": int(btc_action_truth.get("btc_escape_tier2_surgical_closes") or 0),
                "btc_close_mix_status": btc_close_mix_status,
            },
            "next_move": btc_next_move,
            "why_now": btc_why_now,
        },
        {
            "priority": 4,
            "theory": "btc_m5_step200_salvage_probe",
            "stage": "shadow_probe_only",
            "goal": "recover_high_dollars_per_close_m5_btc",
            "evidence": "Wider BTC M5 spacing still shows the highest dollars per close on the shelf, but the sample is too small and the launch contract is still not clean enough for promotion talk.",
            "machine_truth": {
                "shadow_avg_per_close": round(float(btc_shadow.get("avg_per_close") or 0.0), 4),
                "shadow_realized_closes": int(btc_shadow.get("realized_closes") or 0),
                "shadow_realized_net_usd": round(float(btc_shadow.get("realized_net_usd") or 0.0), 2),
                "live_m15_baseline_avg_per_close": round(float(btc_live_m15.get("avg_per_close") or 0.0), 4),
                "hold_gate": str((symbol_priors.get("BTCUSD") or {}).get("promotion_action") or ""),
            },
            "next_move": "Keep it shadow-only and require materially more than two closes plus a clean launch surface before it influences capital allocation.",
            "why_now": "The upside per close is real, but this is still a probe, not a promotable lane.",
        },
        {
            "priority": 5,
            "theory": "index_asymmetry_family_prior",
            "stage": "forward_validating",
            "goal": "capture_breakout_profits_without_universalizing_wrong_geometry",
            "evidence": "NAS100 is now the cleanest checked-in Hungry Hippo research-only shadow candidate after launch cleanup, while US30 still needs direct proof and guardrail relief.",
            "machine_truth": {
                "nas100_launch_verdict": str((nas_action.get("machine_truth") or {}).get("launch_verdict") or ""),
                "nas100_guardrail_status": str((nas_action.get("machine_truth") or {}).get("guardrail_status") or ""),
                "nas100_next_action": str(nas100_queue.get("next_action") or ""),
                "us30_next_action": str(us30_queue.get("next_action") or ""),
                "us30_guardrail_status": str((symbol_priors.get("US30") or {}).get("guardrail_status") or ""),
            },
            "next_move": "Use NAS100 as the first honest index-family shadow seam after control work, but keep the family scoped per symbol and per session window.",
            "why_now": "The profit thesis is still alive, and NAS100 now survives the current launch cleanup better than the other index rows.",
        },
        {
            "priority": 6,
            "theory": "offensive_extreme_closure",
            "stage": "shadow_spec_ready",
            "goal": "bleed_less_on_stranded_extremes",
            "evidence": str((global_policy.get("offensive_extreme_closure") or {}).get("read") or ""),
            "machine_truth": {
                "policy_status": str((global_policy.get("offensive_extreme_closure") or {}).get("status") or ""),
                "graduation_gate": str((global_policy.get("graduation_funnel") or {}).get("theory_to_shadow") or ""),
                "gbpusd_closure_tax_multiple_vs_harvest": round(
                    abs(float(bucket_split_summary.get("escape_tier0_offensive") or 0.0) + float(bucket_split_summary.get("forced_unwind") or 0.0))
                    / float(bucket_split_summary.get("close_ticket") or 1.0),
                    2,
                ) if float(bucket_split_summary.get("close_ticket") or 0.0) else 0.0,
            },
            "next_move": "Build the first shadow A/B as budgeted offensive closure on ETH step14 only after the aligned control clears positive proof, with a cumulative closure-budget firewall tied to positive harvest.",
            "why_now": "This directly targets the less-losses thesis, and the new bucket evidence shows why per-cut affordability alone is not enough.",
        },
        {
            "priority": 7,
            "theory": "dual_lattice_hedge_wave_cancellation",
            "stage": "simulation_required",
            "goal": "cancel_floating_drag_while_adding_realized_harvest",
            "evidence": str((global_policy.get("dual_lattice_hedge") or {}).get("read") or ""),
            "machine_truth": {
                "policy_status": str((global_policy.get("dual_lattice_hedge") or {}).get("status") or ""),
                "graduation_gate": str((global_policy.get("graduation_funnel") or {}).get("theory_to_shadow") or ""),
            },
            "next_move": "Build a replay study that measures same-symbol dual-lattice floating cancellation, realized additive PnL, and spread drag before any shadow launch.",
            "why_now": "The profit thesis is coherent, but there is still no machine proof that the wave-cancellation idea survives spread and trend persistence.",
        },
    ]

    stage_counts: dict[str, int] = {}
    for row in rows:
        stage = str(row["stage"])
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(CONTROLLER_PRIORS_PATH.relative_to(ROOT)),
            str(SALVAGE_BOARD_PATH.relative_to(ROOT)),
            str(PROMOTION_QUEUE_PATH.relative_to(ROOT)),
            str(BTC_HANDOFF_PATH.relative_to(ROOT)),
            str(BTC_RECONCILIATION_PATH.relative_to(ROOT)),
            str(ETH_CONTROL_GATE_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_BOARD_PATH.relative_to(ROOT)),
            str(BUCKET_SPLIT_MD_PATH.relative_to(ROOT)),
            str(BTC_CONFIG_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "The highest-leverage profit work is no longer abstract brainstorming; it is keeping theory stages honest against the newest repo-backed blockers and launches.",
            f"{eth_leadership_fragment}, {btc_leadership_fragment}, GBP needs paired baseline-vs-no-escape forward evidence, and NAS100 is the cleanest checked-in Hungry Hippo expansion seam.",
            "This board is the canonical filter for which ideas belong in research, which ones deserve shadow proof now, and which ones are still nowhere near live language.",
        ],
        "summary": {
            "theory_count": len(rows),
            "stage_counts": stage_counts,
            "top_ready_rows": [
                row["theory"]
                for row in rows
                if row["stage"] in {
                    "validated_live_prior",
                    "tested_theory_waiting_for_clean_control",
                    "tested_theory_waiting_for_positive_control_proof",
                    "shadow_config_reconciled_waiting_forward_proof",
                    "shadow_probe_only",
                }
            ][:4],
        },
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Profit Theory Graduation Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: rank larger-profit / lower-loss theories by the correct graduation stage so the team knows what belongs in research, shadow, and live discussions.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    summary = dict(payload.get("summary") or {})
    lines.extend(["", "## Summary", ""])
    lines.append(f"- Theory count: `{summary.get('theory_count', 0)}`")
    stage_counts = dict(summary.get("stage_counts") or {})
    if stage_counts:
        rendered = ", ".join(f"{key}={value}" for key, value in stage_counts.items())
        lines.append(f"- Stage counts: `{rendered}`")
    top_ready_rows = list(summary.get("top_ready_rows") or [])
    if top_ready_rows:
        lines.append(f"- Top ready rows: `{', '.join(top_ready_rows)}`")

    lines.extend(["", "## Queue", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### P{int(row['priority'])} - {row['theory']}")
        lines.append("")
        lines.append(f"- Stage: `{row['stage']}`")
        lines.append(f"- Goal: `{row['goal']}`")
        lines.append(f"- Evidence: `{row['evidence']}`")
        machine_truth = ", ".join(f"{k}={v}" for k, v in dict(row.get("machine_truth") or {}).items())
        lines.append(f"- Machine truth: `{machine_truth}`")
        lines.append(f"- Next move: `{row['next_move']}`")
        lines.append(f"- Why now: `{row['why_now']}`")
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

    payload = build_payload(
        load_json(CONTROLLER_PRIORS_PATH),
        load_json(SALVAGE_BOARD_PATH),
        load_json(PROMOTION_QUEUE_PATH),
        load_json(BTC_HANDOFF_PATH),
        load_json(BTC_RECONCILIATION_PATH),
        load_json(ETH_CONTROL_GATE_PATH),
        load_json(NEXT_ACTION_BOARD_PATH),
        parse_bucket_split_summary(bucket_text),
        load_json(BTC_CONFIG_PATH),
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
