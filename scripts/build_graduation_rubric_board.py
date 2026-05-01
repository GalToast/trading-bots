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

OUTPUT_JSON_PATH = REPORTS / "graduation_rubric_board.json"
OUTPUT_MD_PATH = REPORTS / "graduation_rubric_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def row_by_key(payload: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    for row in list(payload.get("rows") or []):
        if isinstance(row, dict) and str(row.get(key) or "") == value:
            return row
    raise KeyError(f"row not found: {key}={value}")


def build_payload(
    controller_priors: dict[str, Any],
    profit_board: dict[str, Any],
    readiness_board: dict[str, Any],
    promotion_gate: dict[str, Any],
) -> dict[str, Any]:
    symbol_priors = dict(controller_priors.get("symbol_priors") or {})

    eth_theory = row_by_key(profit_board, "theory", "eth_m5_no_session_gate_harvest_rebuild")
    btc_theory = row_by_key(profit_board, "theory", "btc_m15_downtrend_sell_tight_shape")
    gbp_theory = row_by_key(profit_board, "theory", "fx_alpha_half_universal_prior")
    nas_theory = row_by_key(profit_board, "theory", "index_asymmetry_family_prior")
    btc_step200_theory = row_by_key(profit_board, "theory", "btc_m5_step200_salvage_probe")

    eth_ready = row_by_key(readiness_board, "candidate", "ETHUSD M5 step14 normalized control")
    btc_ready = row_by_key(readiness_board, "candidate", "BTCUSD M15 sell-tight downtrend shape")
    gbp_ready = row_by_key(readiness_board, "candidate", "GBPUSD alpha=0.5 FX harvest path")
    nas_ready = row_by_key(readiness_board, "candidate", "NAS100 asym breakout family lane")
    btc_step200_ready = row_by_key(readiness_board, "candidate", "BTCUSD M5 step200 salvage probe")

    eth_gate = row_by_key(promotion_gate, "candidate", "ETHUSD M5 step14 normalized control")
    btc_gate = row_by_key(promotion_gate, "candidate", "BTCUSD M15 sell-tight downtrend shape")
    gbp_gate = row_by_key(promotion_gate, "candidate", "GBPUSD alpha=0.5 FX harvest path")
    nas_gate = row_by_key(promotion_gate, "candidate", "NAS100 asym breakout family lane")
    btc_step200_gate = row_by_key(promotion_gate, "candidate", "BTCUSD M5 step200 salvage probe")

    eth_ready_evidence = dict(eth_ready.get("evidence") or {})
    eth_current_control_verdict = str(eth_gate.get("blocking_issue") or eth_ready_evidence.get("gate_verdict") or (eth_theory.get("machine_truth") or {}).get("control_verdict") or "")
    eth_current_control_closes = int(eth_ready_evidence["realized_closes"]) if "realized_closes" in eth_ready_evidence else int((eth_theory.get("machine_truth") or {}).get("control_realized_closes") or 0)
    eth_current_control_avg = float(eth_ready_evidence["avg_per_close"]) if "avg_per_close" in eth_ready_evidence else float((eth_theory.get("machine_truth") or {}).get("control_avg_per_close") or 0.0)
    btc_ready_evidence = dict(btc_ready.get("evidence") or {})
    btc_required_reset_parts: list[str] = []
    if btc_ready_evidence.get("max_resets_per_hour") is not None:
        btc_required_reset_parts.append(f"<={btc_ready_evidence.get('max_resets_per_hour')}/hour")
    if btc_ready_evidence.get("max_resets_per_close") is not None:
        btc_required_reset_parts.append(f"<={btc_ready_evidence.get('max_resets_per_close')} resets/close")
    btc_required_reset_behavior = " and ".join(btc_required_reset_parts) if btc_required_reset_parts else "no_reset_storm"

    stage_thresholds = {
        "theory_to_shadow": {
            "required": [
                "coherent mechanism",
                "family-local benchmark to beat",
                "guardrails and failure policy defined",
                "a concrete shadow config or experiment spec path",
            ]
        },
        "shadow_to_validated_shadow": {
            "required": [
                "fresh forward evidence under the current runtime path",
                "acceptable reset and floating-loss behavior",
                "no stale runtime or mixed-control contamination",
                "candidate-specific proof threshold cleared",
            ]
        },
        "validated_shadow_to_live": {
            "required": [
                "family-local validated shadow survives its intended window/regime",
                "no active contradiction with controller priors or guardrail blockers",
                "runtime path matches proof path",
                "candidate-specific live gate cleared",
            ]
        },
        "live_to_scale": {
            "required": [
                "sustained forward-positive live evidence",
                "clean survival under real execution",
                "no hidden contradiction between governance and runtime path",
            ]
        },
    }

    rows = [
        {
            "candidate": "ETHUSD M5 step14 normalized control",
            "family": "crypto_m5_rebuild",
            "current_stage": str(eth_gate.get("current_stage") or eth_theory.get("stage") or ""),
            "next_gate": "shadow_to_validated_shadow",
            "candidate_rubric": {
                "required_runtime_freshness": "heartbeat_fresh",
                "required_geometry": "normalized_step14_control",
                "required_forward_closes": 25,
                "required_avg_per_close": ">0",
                "required_reset_behavior": "<=6_per_hour",
                "required_comparison_cleanliness": "off_vs_budgeted_on_same_shape",
            },
            "current_metrics": {
                "control_verdict": eth_current_control_verdict,
                "control_realized_closes": eth_current_control_closes,
                "control_avg_per_close": eth_current_control_avg,
                "readiness": str(eth_ready.get("readiness") or ""),
                "promotion_verdict": str(eth_gate.get("promotion_verdict") or ""),
            },
            "gap_to_next_gate": "The registered step14 lane is aligned and geometry is honest enough to judge, but the control sample still needs fresh positive proof before it can produce honest validated-shadow evidence."
            if eth_current_control_verdict == "blocked_by_negative_expectancy"
            else "The registered step14 lane is now the judged lane, but the runtime ladder still needs normalization and the control sample still needs fresh positive proof before it can produce honest validated-shadow evidence."
            if eth_current_control_verdict == "blocked_by_control_normalization"
            else "Control is still stale/non-normalized, so the lane cannot yet produce honest validated-shadow evidence.",
        },
        {
            "candidate": "BTCUSD M15 sell-tight downtrend shape",
            "family": "btc_downtrend_control",
            "current_stage": str(btc_ready.get("readiness") or btc_gate.get("current_stage") or btc_theory.get("stage") or ""),
            "next_gate": "shadow_to_validated_shadow",
            "candidate_rubric": {
                "required_initial_shadow_positive_closes": 10,
                "required_validated_shadow_closes": 20,
                "required_avg_per_close": ">0",
                "required_regime_fit": "SELL_or_bounce_reversal_slice",
                "required_reset_behavior": btc_required_reset_behavior,
                "required_comparison": "better_loss_control_than_bullish_hold_alternative",
            },
            "current_metrics": {
                "reconciliation_status": str((btc_ready.get("evidence") or {}).get("reconciliation_status") or ""),
                "validation_status": str((btc_ready.get("evidence") or {}).get("validation_status") or ""),
                "launch_verdict": str((btc_ready.get("evidence") or {}).get("launch_verdict") or ""),
                "runtime_stale": bool((btc_ready.get("evidence") or {}).get("runtime_stale")),
                "realized_closes": int((btc_ready.get("evidence") or {}).get("realized_closes") or 0),
                "realized_net_usd": float((btc_ready.get("evidence") or {}).get("realized_net_usd") or 0.0),
                "anchor_resets": int((btc_ready.get("evidence") or {}).get("anchor_resets") or 0),
                "resets_per_close": (btc_ready.get("evidence") or {}).get("resets_per_close"),
                "reset_rate_per_hour": (btc_ready.get("evidence") or {}).get("reset_rate_per_hour"),
                "readiness": str(btc_ready.get("readiness") or ""),
                "promotion_verdict": str(btc_gate.get("promotion_verdict") or ""),
            },
            "gap_to_next_gate": "The config is reconciled, but the fresh sample is still negative and needs to settle inside reset guardrails before the room can claim the controller actually reduces losses."
            if str(btc_ready.get("blocker") or "") in {"forward_sample_negative_and_reset_rate_above_hourly_guardrail", "forward_sample_negative_and_reset_heavy", "forward_sample_started_but_still_negative"}
            else "Fresh BTC v2 proof has started, but the sample is still too small to support any graduation claim."
            if str(btc_ready.get("blocker") or "") == "initial_positive_sample_not_large_enough_yet"
            else "The config is reconciled, but it still needs forward proof before the room can claim the controller actually reduces losses.",
        },
        {
            "candidate": "GBPUSD alpha=0.5 FX harvest path",
            "family": "fx_harvest",
            "current_stage": str(gbp_ready.get("readiness") or gbp_gate.get("current_stage") or ""),
            "next_gate": "validated_shadow_to_live",
            "candidate_rubric": {
                "required_bucket_split": "harvest_vs_offensive_vs_forced_unwind",
                "required_harvest_status": "positive_over_fresh_sample",
                "required_closure_repair": "closure_tax_bounded",
                "required_contradictions": "zero",
                "required_geometry": "preserve_alpha_0_5_live_path",
            },
            "current_metrics": {
                "close_alpha_prior": float((symbol_priors.get("GBPUSD") or {}).get("close_alpha_prior") or 0.0),
                "proof_closes": int((gbp_ready.get("evidence") or {}).get("proof_closes") or 0),
                "harvest_close_ticket_usd": float((gbp_ready.get("evidence") or {}).get("harvest_close_ticket_usd") or 0.0),
                "escape_tier0_offensive_usd": float((gbp_ready.get("evidence") or {}).get("escape_tier0_offensive_usd") or 0.0),
                "forced_unwind_usd": float((gbp_ready.get("evidence") or {}).get("forced_unwind_usd") or 0.0),
            },
            "gap_to_next_gate": "The controller prior is validated, but the current GBP lane is not live-worthy until the closure-policy leak is isolated and repaired.",
        },
        {
            "candidate": "NAS100 asym breakout family lane",
            "family": "index_asymmetry",
            "current_stage": str(nas_ready.get("readiness") or nas_gate.get("current_stage") or ""),
            "next_gate": "shadow_to_validated_shadow",
            "candidate_rubric": {
                "required_forward_closes": 20,
                "required_window": "inside_intended_session_window",
                "required_regime_continuity": "no_reversal_degradation_during_proof",
                "required_guardrail_status": "promotable_now_or_better",
                "required_manual_review": "accepted",
            },
            "current_metrics": {
                "launch_verdict": str((nas_ready.get("evidence") or {}).get("launch_verdict") or ""),
                "proof_closes": int((nas_ready.get("evidence") or {}).get("proof_closes") or 0),
                "guardrail_status": str((nas_ready.get("evidence") or {}).get("guardrail_status") or ""),
                "deployment_gate_verdict": str((nas_ready.get("evidence") or {}).get("deployment_gate_verdict") or ""),
            },
            "gap_to_next_gate": "NAS100 is the cleanest research-only shadow candidate, but it still needs manual-review acceptance and intended-window continuity to earn validated-shadow status.",
        },
        {
            "candidate": "BTCUSD M5 step200 salvage probe",
            "family": "crypto_salvage_probe",
            "current_stage": str(btc_step200_ready.get("readiness") or btc_step200_gate.get("current_stage") or ""),
            "next_gate": "shadow_to_validated_shadow",
            "candidate_rubric": {
                "required_forward_closes": 20,
                "required_sample_quality": "repeatable_and_still_positive_after_expansion",
                "required_hold_gate": "buy_realign_cleared",
                "required_launch_surface": "contract_clean",
                "required_posture": "shadow_only_until_statistically_meaningful",
            },
            "current_metrics": {
                "shadow_realized_closes": int((btc_step200_ready.get("evidence") or {}).get("shadow_realized_closes") or 0),
                "shadow_avg_per_close": float((btc_step200_ready.get("evidence") or {}).get("shadow_avg_per_close") or 0.0),
                "launch_verdict": str((btc_step200_ready.get("evidence") or {}).get("launch_verdict") or ""),
                "promotion_verdict": str(btc_step200_gate.get("promotion_verdict") or ""),
            },
            "gap_to_next_gate": "The upside is still sample-starved and the current launch surface is failing, so this remains a probe rather than a promotable shadow.",
        },
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(CONTROLLER_PRIORS_PATH.relative_to(ROOT)),
            str(PROFIT_BOARD_PATH.relative_to(ROOT)),
            str(READINESS_BOARD_PATH.relative_to(ROOT)),
            str(PROMOTION_GATE_PATH.relative_to(ROOT)),
        ],
        "leadership_read": [
            "Graduation should be rubric-driven, but the rubric must obey the current authority stack rather than older optimistic shelves.",
            "ETH now needs fresh positive proof on the aligned registered step14 lane before validated-shadow proof, BTC sell-tight has moved into proof-collection, GBP needs closure-policy diagnosis before live language, and NAS100 is the cleanest current shadow candidate."
            if eth_current_control_verdict == "blocked_by_negative_expectancy"
            else
            "ETH now needs runtime geometry normalization and fresh positive proof on the aligned registered step14 lane before validated-shadow proof, BTC sell-tight has moved into proof-collection, GBP needs closure-policy diagnosis before live language, and NAS100 is the cleanest current shadow candidate."
            if eth_current_control_verdict == "blocked_by_control_normalization"
            else "ETH now needs control restoration before validated-shadow proof, BTC sell-tight has moved into proof-collection, GBP needs closure-policy diagnosis before live language, and NAS100 is the cleanest current shadow candidate.",
            "This rubric is the canonical threshold surface for theory -> shadow -> validated_shadow -> live across the current top lanes.",
        ],
        "stage_thresholds": stage_thresholds,
        "rows": rows,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Graduation Rubric Board",
        "",
        f"- Generated at: `{payload.get('generated_at', '')}`",
        "- Purpose: define one canonical threshold set for theory -> shadow -> validated_shadow -> live decisions across the current top candidates.",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(["", "## Stage Thresholds", ""])
    for stage, value in dict(payload.get("stage_thresholds") or {}).items():
        lines.append(f"### {stage}")
        lines.append("")
        lines.append(f"- Required: `{'; '.join(list((value or {}).get('required') or []))}`")
        lines.append("")

    lines.extend(["## Candidate Rubric", ""])
    for row in list(payload.get("rows") or []):
        lines.append(f"### {row['candidate']}")
        lines.append("")
        lines.append(f"- Family: `{row['family']}`")
        lines.append(f"- Current stage: `{row['current_stage']}`")
        lines.append(f"- Next gate: `{row['next_gate']}`")
        lines.append(f"- Candidate rubric: `{'; '.join(f'{k}={v}' for k, v in dict(row.get('candidate_rubric') or {}).items())}`")
        lines.append(f"- Current metrics: `{'; '.join(f'{k}={v}' for k, v in dict(row.get('current_metrics') or {}).items())}`")
        lines.append(f"- Gap to next gate: `{row['gap_to_next_gate']}`")
        lines.append("")

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
    )
    write_outputs(payload)
    print(f"wrote {OUTPUT_JSON_PATH}")
    print(f"wrote {OUTPUT_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
