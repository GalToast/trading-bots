#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

ACCOUNT_UNLOCK_PATH = REPORTS / "hungry_hippo_account_unlock_gate_board.json"
STARTER_READINESS_PATH = REPORTS / "hungry_hippo_starter_readiness_board.json"
PORTABILITY_PATH = REPORTS / "hungry_hippo_symbol_portability_board.json"
NEXT_ACTION_PATH = REPORTS / "hungry_hippo_next_action_board.json"

OUTPUT_JSON_PATH = REPORTS / "hungry_hippo_parallel_rollout_gate_board.json"
OUTPUT_MD_PATH = REPORTS / "hungry_hippo_parallel_rollout_gate_board.md"


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


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_payload(
    account_unlock_payload: dict[str, Any],
    starter_readiness_payload: dict[str, Any],
    portability_payload: dict[str, Any],
    next_action_payload: dict[str, Any],
) -> dict[str, Any]:
    unlock_summary = dict(account_unlock_payload.get("summary") or {})
    unlock_rows = list(account_unlock_payload.get("rows") or [])
    starter_summary = dict(starter_readiness_payload.get("summary") or {})
    starter_rows = list(starter_readiness_payload.get("rows") or [])
    portability_rows = list(portability_payload.get("rows") or [])
    next_action_rows = list(next_action_payload.get("rows") or [])

    growth_ladder = [str(symbol) for symbol in list(unlock_summary.get("growth_ladder_symbols") or []) if str(symbol)]
    starter_symbol = str(starter_summary.get("starter_candidate_symbol") or (growth_ladder[0] if growth_ladder else ""))
    starter_next_symbol = str(starter_summary.get("starter_next_symbol") or (growth_ladder[1] if len(growth_ladder) > 1 else ""))
    slot3_symbol = str(growth_ladder[2] if len(growth_ladder) > 2 else "")

    starter_row = find_symbol(starter_rows, starter_symbol) or find_symbol(unlock_rows, starter_symbol) or {}
    starter_next_row = find_symbol(starter_rows, starter_next_symbol) or find_symbol(unlock_rows, starter_next_symbol) or {}
    slot3_unlock_row = find_symbol(unlock_rows, slot3_symbol) or {}

    starter_portability_row = find_symbol(portability_rows, starter_symbol) or {}
    starter_next_portability_row = find_symbol(portability_rows, starter_next_symbol) or {}
    slot3_portability_row = find_symbol(portability_rows, slot3_symbol) or {}

    ladder_action = {}
    for row in next_action_rows:
        if str(row.get("action") or "") == "define_balance_growth_symbol_unlock_ladder_before_parallel_rollout":
            ladder_action = dict(row)
            break
    ladder_truth = dict(ladder_action.get("machine_truth") or {})

    slot3_surface_disagreement = bool(
        slot3_symbol
        and str(slot3_unlock_row.get("current_status") or "")
        and str(slot3_portability_row.get("generalization_status") or "")
        and str(slot3_unlock_row.get("current_status") or "") != str(slot3_portability_row.get("generalization_status") or "")
    )

    rows = [
        {
            "max_active_lanes": 1,
            "current_status": "blocked_until_slot1_forward_proof",
            "blocker_reason": str(
                starter_row.get("blocker_reason")
                or f"`{starter_symbol}` still needs fresh forward shadow proof before the first active lane is honest."
            ),
            "unlock_when": "The starter lane is live on current code, accumulates fresh forward shadow proof, and active-set floating drawdown stays below the 5% freeze gate.",
            "kill_when": "Cheap margin is treated as permission to activate the first lane before the starter has any forward proof.",
            "machine_truth": {
                "starter_candidate_symbol": starter_symbol,
                "starter_candidate_status": str(starter_row.get("current_status") or ""),
                "starter_candidate_next_move": str(starter_row.get("next_honest_move") or ""),
                "starter_candidate_margin_usd": as_float(dict(starter_row.get("machine_truth") or {}).get("estimated_min_lot_margin_usd")),
                "drawdown_freeze_pct": as_float(unlock_summary.get("drawdown_freeze_pct")),
                "drawdown_reduce_pct": as_float(unlock_summary.get("drawdown_reduce_pct")),
                "drawdown_block_pct": as_float(unlock_summary.get("drawdown_block_pct")),
                "max_symbol_risk_pct_of_equity": as_float(unlock_summary.get("max_symbol_risk_pct_of_equity")),
            },
        },
        {
            "max_active_lanes": 2,
            "current_status": "blocked_until_slot1_proven_and_slot2_complete",
            "blocker_reason": (
                f"Lane 2 is blocked by two stacked dependencies: slot #1 `{starter_symbol}` is still `{starter_row.get('current_status')}`, "
                f"and slot #2 `{starter_next_symbol}` is still `{starter_next_row.get('current_status')}`."
            ),
            "unlock_when": "Slot #1 has fresh forward proof under the tiny-account gates, slot #2 has a checked-in runnable launch contract, and combined floating drawdown remains below the 5% freeze gate.",
            "kill_when": "A second lane is activated because both symbols are cheap on margin while slot #1 is still unproved or slot #2 is still unresolved.",
            "machine_truth": {
                "slot1_symbol": starter_symbol,
                "slot1_status": str(starter_row.get("current_status") or ""),
                "slot1_next_move": str(starter_row.get("next_honest_move") or ""),
                "slot2_symbol": starter_next_symbol,
                "slot2_status": str(starter_next_row.get("current_status") or ""),
                "slot2_next_move": str(starter_next_row.get("next_honest_move") or ""),
                "slot2_generalization_status": str(starter_next_portability_row.get("generalization_status") or ""),
                "slot2_deployment_verdict": str(starter_next_portability_row.get("deployment_verdict") or ""),
                "slot2_margin_usd": as_float(dict(starter_next_row.get("machine_truth") or {}).get("estimated_min_lot_margin_usd")),
            },
        },
        {
            "max_active_lanes": 3,
            "current_status": "blocked_until_slot1_and_slot2_are_resolved",
            "blocker_reason": (
                f"Lane 3 remains premature because slot #2 `{starter_next_symbol}` is unresolved, and slot #3 `{slot3_symbol}` still carries upstream ambiguity."
                if slot3_symbol
                else "Lane 3 remains premature because upstream slot ordering is not stable enough to authorize a third active lane."
            ),
            "unlock_when": "Slots #1 and #2 are both proven under forward conditions, slot #3 has one coherent blocker story across the authority surfaces, and the active set still fits inside the 10% portfolio cap.",
            "kill_when": "A third lane is added because margin is cheap before slots #1 and #2 have proven they can compound without consuming the drawdown budget.",
            "machine_truth": {
                "slot3_symbol": slot3_symbol,
                "slot3_unlock_board_status": str(slot3_unlock_row.get("current_status") or ""),
                "slot3_portability_status": str(slot3_portability_row.get("generalization_status") or ""),
                "slot3_deployment_verdict": str(slot3_portability_row.get("deployment_verdict") or ""),
                "slot3_highest_leverage_gap": str(slot3_portability_row.get("highest_leverage_gap") or ""),
                "slot3_surface_disagreement": slot3_surface_disagreement,
                "policy_seed_now_symbols": list(starter_summary.get("policy_seed_now_symbols") or []),
                "promotable_missing_launch_contract_symbols": list(starter_summary.get("promotable_missing_launch_contract_symbols") or starter_summary.get("cheap_promotable_launch_contract_symbols") or []),
            },
        },
    ]

    return {
        "generated_at": utc_now_iso(),
        "sources": [
            str(ACCOUNT_UNLOCK_PATH.relative_to(ROOT)),
            str(STARTER_READINESS_PATH.relative_to(ROOT)),
            str(PORTABILITY_PATH.relative_to(ROOT)),
            str(NEXT_ACTION_PATH.relative_to(ROOT)),
        ],
        "summary": {
            "current_max_honest_active_lanes": 0,
            "starter_candidate_symbol": starter_symbol,
            "starter_candidate_status": str(starter_row.get("current_status") or ""),
            "starter_next_symbol": starter_next_symbol,
            "starter_next_status": str(starter_next_row.get("current_status") or ""),
            "slot3_symbol": slot3_symbol,
            "slot3_surface_disagreement": slot3_surface_disagreement,
            "proof_lead_symbol": str(starter_summary.get("proof_lead_symbol") or unlock_summary.get("proof_lead_symbol") or ""),
            "proof_lead_status": str(starter_summary.get("proof_lead_status") or ""),
            "promotable_missing_launch_contract_symbols": list(starter_summary.get("promotable_missing_launch_contract_symbols") or starter_summary.get("cheap_promotable_launch_contract_symbols") or []),
            "manual_review_launch_contract_symbols": list(starter_summary.get("manual_review_launch_contract_symbols") or []),
            "policy_seed_now_symbols": list(starter_summary.get("policy_seed_now_symbols") or []),
            "drawdown_freeze_pct": as_float(unlock_summary.get("drawdown_freeze_pct")),
            "drawdown_reduce_pct": as_float(unlock_summary.get("drawdown_reduce_pct")),
            "drawdown_block_pct": as_float(unlock_summary.get("drawdown_block_pct")),
            "max_symbol_risk_pct_of_equity": as_float(unlock_summary.get("max_symbol_risk_pct_of_equity")),
            "max_portfolio_risk_pct": as_float(unlock_summary.get("max_portfolio_risk_pct")),
            "parallel_rollout_doctrine": "cheap_margin_is_not_permission_until_slot1_proves_and_slot2_is_real",
            "next_action_balance_growth_present": bool(ladder_action),
            "next_action_policy_seed_now_symbols": list(ladder_truth.get("policy_seed_now_symbols") or []),
        },
        "leadership_read": [
            f"Cheap FX margin is not permission for parallel rollout yet: current honest `max_active_lanes` is still `0` because slot #1 `{starter_symbol}` is not forward-proven.",
            f"The next blocker after starter proof is not generic drawdown math; it is concrete follow-through on slot #2 `{starter_next_symbol}`, which still reads `{starter_next_row.get('current_status')}`.",
            (
                f"Slot #3 `{slot3_symbol}` still has mixed upstream truth: unlock board says `{slot3_unlock_row.get('current_status')}`, portability says `{slot3_portability_row.get('generalization_status')}`."
                if slot3_surface_disagreement
                else f"Slot #3 `{slot3_symbol or 'none'}` is not the active question yet; it stays behind slot #1 proof and slot #2 completion."
            ),
            "The correct tiny-account order is still sequential proof, not simultaneous cheap-lane activation: prove slot #1, complete slot #2, then revisit whether slot #3 belongs at all.",
        ],
        "rows": rows,
        "notes": [
            "This board is doctrine and gating only. It does not authorize live deployment or shadow launches by itself.",
            "If slot #3 truth disagrees across authority surfaces, the disagreement itself is a blocker for parallel rollout.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    lines = [
        "# Hungry Hippo Parallel Rollout Gate Board",
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
            f"- Current max honest active lanes: `{summary.get('current_max_honest_active_lanes')}`",
            f"- Starter candidate: `{summary.get('starter_candidate_symbol')}`",
            f"- Starter candidate status: `{summary.get('starter_candidate_status')}`",
            f"- Starter next: `{summary.get('starter_next_symbol')}`",
            f"- Starter next status: `{summary.get('starter_next_status')}`",
            f"- Slot #3 symbol: `{summary.get('slot3_symbol')}`",
            f"- Slot #3 surface disagreement: `{summary.get('slot3_surface_disagreement')}`",
            f"- Parallel rollout doctrine: `{summary.get('parallel_rollout_doctrine')}`",
            "",
            "## Gates",
            "",
        ]
    )
    for row in list(payload.get("rows") or []):
        lines.extend(
            [
                f"### Max Active Lanes = {row.get('max_active_lanes')}",
                "",
                f"- Current status: `{row.get('current_status')}`",
                f"- Blocker: {row.get('blocker_reason')}",
                f"- Unlock when: {row.get('unlock_when')}",
                f"- Kill when: {row.get('kill_when')}",
                f"- Machine truth: `{json.dumps(row.get('machine_truth') or {}, sort_keys=True)}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    payload = build_payload(
        load_json(ACCOUNT_UNLOCK_PATH),
        load_json(STARTER_READINESS_PATH),
        load_json(PORTABILITY_PATH),
        load_json(NEXT_ACTION_PATH),
    )
    OUTPUT_JSON_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD_PATH.write_text(render_markdown(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
