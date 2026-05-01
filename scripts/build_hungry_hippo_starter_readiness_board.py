#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

ACCOUNT_UNLOCK_PATH = REPORTS / "hungry_hippo_account_unlock_gate_board.json"
NEXT_ACTION_PATH = REPORTS / "hungry_hippo_next_action_board.json"
PORTABILITY_PATH = REPORTS / "hungry_hippo_symbol_portability_board.json"
POLICY_GAP_PATH = REPORTS / "hungry_hippo_policy_gap_board.json"
POLICY_SEED_PACKET_PATH = REPORTS / "hungry_hippo_policy_seed_packet_board.json"
RESEARCH_CONTRIBUTION_PATH = REPORTS / "hungry_hippo_research_contribution_board.json"

OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_starter_readiness_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_starter_readiness_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def find_symbol(rows: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    clean_symbol = str(symbol or "").upper()
    for row in rows:
        if str(row.get("symbol") or "").upper() == clean_symbol:
            return dict(row)
    return None


def find_action(rows: list[dict[str, Any]], action: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("action") or "") == action:
            return dict(row)
    return None


def find_research_area(rows: list[dict[str, Any]], area: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("area") or "") == area:
            return dict(row)
    return None


def has_active_policy_debt(portability_row: dict[str, Any] | None) -> bool:
    return str((portability_row or {}).get("generalization_status") or "") == "portable_missing_policy"


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def build_starter_candidate_row(
    starter_symbol: str,
    unlock_row: dict[str, Any] | None,
    portability_row: dict[str, Any] | None,
) -> dict[str, Any]:
    unlock_machine_truth = dict((unlock_row or {}).get("machine_truth") or {})
    port_row = dict(portability_row or {})
    return {
        "lane": "starter_candidate",
        "symbol": starter_symbol,
        "asset_class": str(port_row.get("asset_class") or (unlock_row or {}).get("asset_class") or ""),
        "current_status": str((unlock_row or {}).get("current_status") or "missing_starter_row"),
        "blocker_reason": str((unlock_row or {}).get("blocker_reason") or "Starter candidate row is missing from the unlock board."),
        "next_honest_move": str(
            unlock_machine_truth.get("highest_leverage_gap")
            or unlock_machine_truth.get("starter_suggested_seed_action")
            or port_row.get("highest_leverage_gap")
            or ""
        ),
        "why_this_lane": "This is the current tiny-account starter candidate from the unlock ladder. It may already be past policy and launch-contract debt even if another symbol still carries the top policy-seeding obligation.",
        "machine_truth": {
            "generalization_status": str(port_row.get("generalization_status") or ""),
            "deployment_verdict": str(port_row.get("deployment_verdict") or ""),
            "guardrail_status": str(port_row.get("guardrail_status") or ""),
            "estimated_min_lot_margin_usd": as_float(
                unlock_machine_truth.get("estimated_min_lot_margin_usd")
                or unlock_machine_truth.get("margin_estimated_min_lot_usd")
            ),
            "launch_contract_count": int(port_row.get("launch_contract_count") or 0),
            "highest_leverage_gap": str(port_row.get("highest_leverage_gap") or ""),
        },
    }


def build_proof_lead_row(
    proof_symbol: str,
    account_unlock_summary: dict[str, Any],
    portability_row: dict[str, Any] | None,
) -> dict[str, Any]:
    port_row = dict(portability_row or {})
    return {
        "lane": "proof_lead",
        "symbol": proof_symbol,
        "asset_class": str(port_row.get("asset_class") or ""),
        "current_status": str(port_row.get("generalization_status") or "missing_portability_row"),
        "blocker_reason": "This is the cleanest forward-proof seam, but it is not the tiny-account starter because current minimum-lot margin is materially heavier.",
        "next_honest_move": "fresh_forward_shadow_proof",
        "why_this_lane": "Proof lead answers which non-FX seam is closest to honest expansion, not which symbol should start a $50 account.",
        "machine_truth": {
            "deployment_verdict": str(port_row.get("deployment_verdict") or ""),
            "guardrail_status": str(port_row.get("guardrail_status") or ""),
            "highest_leverage_gap": str(port_row.get("highest_leverage_gap") or ""),
            "launch_contract_count": int(port_row.get("launch_contract_count") or 0),
            "estimated_min_lot_margin_usd": as_float(account_unlock_summary.get("proof_lead_estimated_min_lot_margin_usd")),
        },
    }


def build_starter_next_row(
    symbol: str,
    unlock_row: dict[str, Any] | None,
    portability_row: dict[str, Any] | None,
    seed_row: dict[str, Any] | None,
) -> dict[str, Any]:
    unlock_machine_truth = dict((unlock_row or {}).get("machine_truth") or {})
    port_row = dict(portability_row or {})
    packet_row = dict(seed_row or {})
    return {
        "lane": "starter_next_queue",
        "symbol": symbol,
        "asset_class": str(packet_row.get("asset_class") or port_row.get("asset_class") or (unlock_row or {}).get("asset_class") or ""),
        "current_status": str((unlock_row or {}).get("current_status") or "missing_starter_next_row"),
        "blocker_reason": str((unlock_row or {}).get("blocker_reason") or "Starter-next row is missing from the unlock board."),
        "next_honest_move": str(port_row.get("highest_leverage_gap") or packet_row.get("suggested_seed_action") or ""),
        "why_this_lane": "This is the next cheap candidate behind the starter in the unlock ladder. Its blocker may be policy, launch-contract follow-through, or fresh proof, so it should be read directly from current authority rather than assumed.",
        "machine_truth": {
            "priority": str(packet_row.get("priority") or "") if has_active_policy_debt(port_row) else "",
            "priority_score": int(packet_row.get("priority_score") or 0) if has_active_policy_debt(port_row) else 0,
            "suggested_seed_action": str(packet_row.get("suggested_seed_action") or "") if has_active_policy_debt(port_row) else "",
            "estimated_min_lot_margin_usd": as_float(unlock_machine_truth.get("estimated_min_lot_margin_usd")),
            "generalization_status": str(port_row.get("generalization_status") or ""),
            "deployment_verdict": str(port_row.get("deployment_verdict") or ""),
            "guardrail_status": str(port_row.get("guardrail_status") or ""),
            "launch_contract_followthrough": str(port_row.get("generalization_status") or "") == "portable_missing_launch_contract",
            "family_default_timeframe": str(packet_row.get("family_default_timeframe") or ""),
            "family_default_base_step": packet_row.get("family_default_base_step"),
            "evidence_net_usd": as_float(packet_row.get("evidence_net_usd")),
            "evidence_closes": int(packet_row.get("evidence_closes") or 0),
        },
    }


def build_policy_debt_row(
    lane: str,
    symbol: str,
    portability_row: dict[str, Any] | None,
    seed_row: dict[str, Any] | None,
) -> dict[str, Any]:
    port_row = dict(portability_row or {})
    packet_row = dict(seed_row or {})
    return {
        "lane": lane,
        "symbol": symbol,
        "asset_class": str(packet_row.get("asset_class") or port_row.get("asset_class") or ""),
        "current_status": str(port_row.get("generalization_status") or "missing_policy_row"),
        "blocker_reason": str(packet_row.get("suggested_seed_rationale") or "Canonical policy coverage is still incomplete."),
        "next_honest_move": str(packet_row.get("suggested_seed_action") or ""),
        "why_this_lane": "This is policy debt, not the current unlocked starter slot. It should not be conflated with whichever symbol currently leads the small-account rollout ladder.",
        "machine_truth": {
            "priority": str(packet_row.get("priority") or ""),
            "priority_score": int(packet_row.get("priority_score") or 0),
            "deployment_verdict": str(port_row.get("deployment_verdict") or ""),
            "guardrail_status": str(port_row.get("guardrail_status") or ""),
            "suggested_seed_action": str(packet_row.get("suggested_seed_action") or ""),
            "family_default_timeframe": str(packet_row.get("family_default_timeframe") or ""),
            "family_default_base_step": packet_row.get("family_default_base_step"),
            "evidence_net_usd": as_float(packet_row.get("evidence_net_usd")),
            "evidence_closes": int(packet_row.get("evidence_closes") or 0),
        },
    }


def build_ready_shadow_row(symbol: str, portability_row: dict[str, Any] | None) -> dict[str, Any]:
    port_row = dict(portability_row or {})
    return {
        "lane": "full_stack_ready_nonstarter",
        "symbol": symbol,
        "asset_class": str(port_row.get("asset_class") or ""),
        "current_status": str(port_row.get("generalization_status") or "missing_portability_row"),
        "blocker_reason": "This symbol is already past missing-policy and missing-launch-contract debt; the remaining honest blocker is fresh shadow proof, not starter readiness.",
        "next_honest_move": "fresh_shadow_proof",
        "why_this_lane": "These symbols matter for broad family rollout, but they are no longer the small-account starter question.",
        "machine_truth": {
            "deployment_verdict": str(port_row.get("deployment_verdict") or ""),
            "guardrail_status": str(port_row.get("guardrail_status") or ""),
            "surface_coverage_complete": bool(port_row.get("surface_coverage_complete")),
            "launch_contract_count": int(port_row.get("launch_contract_count") or 0),
            "highest_leverage_gap": str(port_row.get("highest_leverage_gap") or ""),
        },
    }


def build_manual_review_row(symbol: str, portability_row: dict[str, Any] | None) -> dict[str, Any]:
    port_row = dict(portability_row or {})
    return {
        "lane": "launch_contract_manual_review",
        "symbol": symbol,
        "asset_class": str(port_row.get("asset_class") or ""),
        "current_status": str(port_row.get("generalization_status") or "missing_portability_row"),
        "blocker_reason": "This is still launch-contract debt, but not the cheap promotable kind because the portability surface still flags manual review.",
        "next_honest_move": "manual_review_launch_contract_followthrough",
        "why_this_lane": "Manual-review launch debt should not be conflated with the already-cleared starter or ready-for-shadow symbols.",
        "machine_truth": {
            "deployment_verdict": str(port_row.get("deployment_verdict") or ""),
            "guardrail_status": str(port_row.get("guardrail_status") or ""),
            "manual_review_reasons": list(port_row.get("manual_review_reasons") or []),
            "highest_leverage_gap": str(port_row.get("highest_leverage_gap") or ""),
        },
    }


def build_payload(
    account_unlock_payload: dict[str, Any],
    next_action_payload: dict[str, Any],
    portability_payload: dict[str, Any],
    policy_gap_payload: dict[str, Any],
    policy_seed_packet_payload: dict[str, Any],
    research_contribution_payload: dict[str, Any],
) -> dict[str, Any]:
    unlock_summary = dict(account_unlock_payload.get("summary") or {})
    unlock_rows = list(account_unlock_payload.get("rows") or [])
    next_action_rows = list(next_action_payload.get("rows") or [])
    portability_summary = dict(portability_payload.get("summary") or {})
    portability_rows = list(portability_payload.get("rows") or [])
    policy_gap_summary = dict(policy_gap_payload.get("summary") or {})
    seed_rows = list(policy_seed_packet_payload.get("rows") or [])
    research_rows = list(research_contribution_payload.get("research_areas") or [])

    ladder_action = find_action(next_action_rows, "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout")
    ladder_truth = dict((ladder_action or {}).get("machine_truth") or {})
    any_symbol_area = find_research_area(research_rows, "any_symbol_portability_followthrough_gap")
    any_symbol_truth = dict((any_symbol_area or {}).get("machine_truth") or {})

    growth_ladder_symbols = [str(symbol) for symbol in list(unlock_summary.get("growth_ladder_symbols") or []) if str(symbol)]
    starter_symbol = str(unlock_summary.get("lead_symbol") or (growth_ladder_symbols[0] if growth_ladder_symbols else ""))
    starter_next_symbol = str(growth_ladder_symbols[1] if len(growth_ladder_symbols) > 1 else "")
    proof_lead_symbol = str(unlock_summary.get("proof_lead_symbol") or "")

    starter_unlock_row = find_symbol(unlock_rows, starter_symbol)
    starter_next_unlock_row = find_symbol(unlock_rows, starter_next_symbol)
    starter_next_seed_row = find_symbol(seed_rows, starter_next_symbol)
    starter_portability_row = find_symbol(portability_rows, starter_symbol)
    starter_next_portability_row = find_symbol(portability_rows, starter_next_symbol)
    proof_lead_row = find_symbol(portability_rows, proof_lead_symbol)

    policy_seed_now_symbols = [str(symbol) for symbol in list(policy_gap_summary.get("policy_seed_now_symbols") or []) if str(symbol)]
    policy_seed_next_symbols = [str(symbol) for symbol in list(policy_gap_summary.get("policy_seed_next_symbols") or []) if str(symbol)]
    starter_policy_symbol = str(policy_seed_now_symbols[0] if policy_seed_now_symbols else "")
    starter_policy_next_symbol = str(policy_seed_next_symbols[0] if policy_seed_next_symbols else "")
    starter_policy_seed_row = find_symbol(seed_rows, starter_policy_symbol)
    starter_policy_portability_row = find_symbol(portability_rows, starter_policy_symbol)
    starter_policy_next_seed_row = find_symbol(seed_rows, starter_policy_next_symbol)
    starter_policy_next_portability_row = find_symbol(portability_rows, starter_policy_next_symbol)

    ready_for_shadow_symbols = [str(symbol) for symbol in list(portability_summary.get("ready_for_shadow_discussion_symbols") or []) if str(symbol)]
    ready_for_shadow_nonstarter = [
        symbol for symbol in ready_for_shadow_symbols if symbol.upper() not in {starter_symbol.upper(), starter_next_symbol.upper()}
    ]
    cheap_promotable_launch_contract_symbols = [
        str(symbol) for symbol in list(any_symbol_truth.get("promotable_missing_launch_contract_symbols") or []) if str(symbol)
    ]
    manual_review_launch_contract_symbols = [
        str(symbol) for symbol in list(any_symbol_truth.get("manual_review_missing_launch_contract_symbols") or []) if str(symbol)
    ]

    rows: list[dict[str, Any]] = []
    if starter_symbol:
        rows.append(build_starter_candidate_row(starter_symbol, starter_unlock_row, starter_portability_row))
    if proof_lead_symbol:
        rows.append(build_proof_lead_row(proof_lead_symbol, unlock_summary, proof_lead_row))
    if starter_policy_symbol:
        rows.append(build_policy_debt_row("starter_policy_debt", starter_policy_symbol, starter_policy_portability_row, starter_policy_seed_row))
    if starter_policy_next_symbol:
        rows.append(
            build_policy_debt_row(
                "starter_policy_next",
                starter_policy_next_symbol,
                starter_policy_next_portability_row,
                starter_policy_next_seed_row,
            )
        )
    if starter_next_symbol:
        rows.append(build_starter_next_row(starter_next_symbol, starter_next_unlock_row, starter_next_portability_row, starter_next_seed_row))
    for symbol in sorted(ready_for_shadow_nonstarter):
        rows.append(build_ready_shadow_row(symbol, find_symbol(portability_rows, symbol)))
    for symbol in manual_review_launch_contract_symbols:
        rows.append(build_manual_review_row(symbol, find_symbol(portability_rows, symbol)))

    leadership_read = [
        f"Proof lead, starter candidate, and starter policy debt are separate truths right now: proof lead is `{proof_lead_symbol or 'none'}`, starter candidate is `{starter_symbol or 'none'}`, and starter policy debt is `{starter_policy_symbol or 'none'}`."
    ]
    if starter_policy_symbol:
        leadership_read.append(
            f"Immediate starter debt is `{starter_policy_symbol}` canonical policy follow-through, while the current unlock-ladder candidate is `{starter_symbol or 'none'}`."
        )
    else:
        leadership_read.append(
            f"Current starter policy debt is cleared in the authority surfaces; the next small-account blocker is no longer policy seeding for `{starter_symbol or 'none'}`."
        )
    leadership_read.append(
        (
            f"There is no cheap promotable launch-contract debt left in the current authority surfaces; the ready-for-shadow nonstarter set is `{ready_for_shadow_nonstarter}`."
            if not cheap_promotable_launch_contract_symbols
            else f"Cheap promotable launch-contract debt is `{cheap_promotable_launch_contract_symbols}`, which is distinct from the starter-policy question."
        )
    )
    leadership_read.append(
        f"Starter-next queue from the unlock ladder is `{starter_next_symbol or 'none'}`, while policy-seed-next is `{starter_policy_next_symbol or 'none'}`; those should not be treated as the same queue."
    )

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(ACCOUNT_UNLOCK_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
            str(PORTABILITY_PATH.relative_to(ROOT)),
            str(POLICY_GAP_PATH.relative_to(ROOT)),
            str(POLICY_SEED_PACKET_PATH.relative_to(ROOT)),
            str(RESEARCH_CONTRIBUTION_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "current_unlocked_slot_count": int(unlock_summary.get("current_unlocked_slot_count") or 0),
            "starter_candidate_symbol": starter_symbol,
            "starter_candidate_status": str((starter_unlock_row or {}).get("current_status") or ""),
            "starter_next_symbol": starter_next_symbol,
            "proof_lead_symbol": proof_lead_symbol,
            "proof_lead_status": str((proof_lead_row or {}).get("generalization_status") or ""),
            "starter_policy_debt_symbol": starter_policy_symbol,
            "starter_policy_next_symbol": starter_policy_next_symbol,
            "policy_seed_now_symbols": policy_seed_now_symbols,
            "policy_seed_next_symbols": policy_seed_next_symbols,
            "cheap_promotable_launch_contract_symbols": cheap_promotable_launch_contract_symbols,
            "manual_review_launch_contract_symbols": manual_review_launch_contract_symbols,
            "ready_for_shadow_discussion_symbols": ready_for_shadow_symbols,
            "ready_for_shadow_discussion_nonstarter_symbols": ready_for_shadow_nonstarter,
            "family_portable_count": int(portability_summary.get("family_portable_count") or 0),
            "surface_coverage_complete_count": int(portability_summary.get("surface_coverage_complete_count") or 0),
            "missing_policy_symbol_count": int(portability_summary.get("status_counts", {}).get("portable_missing_policy") or portability_summary.get("missing_policy_symbol_count") or 0),
            "starter_and_proof_are_same_symbol": starter_symbol.upper() == proof_lead_symbol.upper() if starter_symbol and proof_lead_symbol else False,
            "best_any_symbol_contribution": str(research_contribution_payload.get("best_any_symbol_contribution") or ""),
            "balance_growth_action_present": ladder_action is not None,
            "policy_seed_now_single_asset_class": bool(ladder_truth.get("policy_seed_now_single_asset_class")),
        },
        "leadership_read": leadership_read,
        "rows": rows,
        "notes": [
            "This board is a support surface. It separates starter selection from proof-lead selection and from cross-symbol follow-through debt.",
            "If this board disagrees with older taskboard narration, the fresher generated authority boards win.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Starter Readiness Board",
        "",
        f"Generated at: `{payload.get('generated_at')}`",
        "",
        "## Leadership Read",
        "",
    ]
    for item in list(payload.get("leadership_read") or []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Starter candidate: `{summary.get('starter_candidate_symbol')}`",
            f"- Starter candidate status: `{summary.get('starter_candidate_status')}`",
            f"- Starter next symbol: `{summary.get('starter_next_symbol')}`",
            f"- Starter policy debt symbol: `{summary.get('starter_policy_debt_symbol')}`",
            f"- Starter policy next symbol: `{summary.get('starter_policy_next_symbol')}`",
            f"- Proof lead: `{summary.get('proof_lead_symbol')}`",
            f"- Proof lead status: `{summary.get('proof_lead_status')}`",
            f"- Cheap promotable launch-contract symbols: `{summary.get('cheap_promotable_launch_contract_symbols')}`",
            f"- Manual-review launch-contract symbols: `{summary.get('manual_review_launch_contract_symbols')}`",
            f"- Ready-for-shadow nonstarter symbols: `{summary.get('ready_for_shadow_discussion_nonstarter_symbols')}`",
            f"- Best any-symbol contribution: `{summary.get('best_any_symbol_contribution')}`",
            "",
            "## Lanes",
            "",
        ]
    )

    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### {row.get('lane')}: {row.get('symbol')}",
                "",
                f"- Asset class: `{row.get('asset_class')}`",
                f"- Current status: `{row.get('current_status')}`",
                f"- Blocker: {row.get('blocker_reason')}",
                f"- Next honest move: `{row.get('next_honest_move')}`",
                f"- Why this lane: {row.get('why_this_lane')}",
                f"- Machine truth: `{json.dumps(row.get('machine_truth') or {}, sort_keys=True)}`",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    payload = build_payload(
        load_json(ACCOUNT_UNLOCK_PATH),
        load_json(NEXT_ACTION_PATH),
        load_json(PORTABILITY_PATH),
        load_json(POLICY_GAP_PATH),
        load_json(POLICY_SEED_PACKET_PATH),
        load_json(RESEARCH_CONTRIBUTION_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
