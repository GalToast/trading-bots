#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

ALLOCATOR_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
STACK_ADMISSION_PATH = REPORTS / "coinbase_same_coin_stack_admission_board.json"
OVERLAP_QUEUE_PATH = REPORTS / "coinbase_same_coin_overlap_queue.json"
CLAIM_INTEGRITY_PATH = REPORTS / "coinbase_claim_integrity_board.json"
RUNTIME_BOARD_PATH = REPORTS / "coinbase_spot_runtime_board.json"
GRADUATION_GAP_PATH = REPORTS / "coinbase_spot_graduation_gap_board.json"

JSON_PATH = REPORTS / "coinbase_reserve_activation_board.json"
MD_PATH = REPORTS / "coinbase_reserve_activation_board.md"

STATUS_RANK = {
    "ready_when_reserve_exists": 0,
    "blocked_runtime_and_graduation": 1,
    "blocked_missing_saved_overlap": 2,
    "blocked_overlap_and_reconcile": 3,
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_rows() -> list[dict[str, Any]]:
    allocator = load_json(ALLOCATOR_PATH)
    stack_admission = load_json(STACK_ADMISSION_PATH)
    overlap_queue = load_json(OVERLAP_QUEUE_PATH)
    integrity = load_json(CLAIM_INTEGRITY_PATH)
    runtime_board = load_json(RUNTIME_BOARD_PATH)
    graduation_gap = load_json(GRADUATION_GAP_PATH)

    allocator_reserves = {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in list(allocator.get("conditional_reserve_candidates") or [])
    }
    stack_rows = {
        str(row.get("coin") or ""): row
        for row in list(stack_admission.get("rows") or [])
        if row.get("coin")
    }
    overlap_pending = {
        str(row.get("coin") or ""): row
        for row in list(overlap_queue.get("pending_rows") or [])
        if row.get("coin")
    }
    overlap_completed = {
        str(row.get("coin") or ""): row
        for row in list(overlap_queue.get("completed_rows") or [])
        if row.get("coin")
    }
    integrity_rows = {
        str(row.get("subject") or ""): row
        for row in list(integrity.get("rows") or [])
        if row.get("subject")
    }
    runtime_key_rows = {
        str(row.get("lane") or ""): row
        for row in list(runtime_board.get("key_lanes") or [])
        if row.get("lane")
    }
    graduation_rows = {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in list(graduation_gap.get("rows") or [])
        if row.get("coin") and row.get("strategy")
    }

    rows: list[dict[str, Any]] = []

    nom_stack = stack_rows.get("NOM-USD") or {}
    nom_overlap = overlap_completed.get("NOM-USD") or {}
    nom_reserve = allocator_reserves.get(("NOM-USD", "momentum_registry_validation")) or {}
    orchestrator = runtime_key_rows.get("multi_coin_portfolio") or {}
    rows.append(
        {
            "coin": "NOM-USD",
            "secondary_lane": "momentum_registry_validation",
            "reserve_status": "ready_when_reserve_exists",
            "reserve_rank": 1,
            "evidence_class": "artifact_backed_overlap_benchmark",
            "activation_gate": str(nom_reserve.get("activation_gate") or "after_primary_book_is_funded"),
            "blocking_reason": "no saved dual-shadow runtime state exists yet, so activate only as reserve capital after the primary book is funded",
            "stack_policy": str(nom_stack.get("current_stack_policy") or ""),
            "overlap_pct_5m": round(to_float(nom_overlap.get("overlap_pct_5m")), 1),
            "combined_uplift_vs_best_single": round(to_float(nom_overlap.get("combined_uplift_vs_best_single")), 2),
            "runtime_dependency_status": str(orchestrator.get("status") or ""),
            "runtime_dependency_note": str(orchestrator.get("note") or ""),
            "recommended_action": "fund_only_as_the_first_reserve_add_on_after_wave_1",
        }
    )

    rave_stack = stack_rows.get("RAVE-USD") or {}
    rave_reserve = allocator_reserves.get(("RAVE-USD", "rsi_mean_reversion_active")) or {}
    rave_runtime = runtime_key_rows.get("rave_rsi_mr_live_v2") or {}
    rave_gap = graduation_rows.get(("RAVE-USD", "rsi_mr")) or {}
    rows.append(
        {
            "coin": "RAVE-USD",
            "secondary_lane": "rsi_mean_reversion_active",
            "reserve_status": "blocked_runtime_and_graduation",
            "reserve_rank": 2,
            "evidence_class": "runtime_proven_but_not_graduated",
            "activation_gate": str(rave_reserve.get("activation_gate") or "restore_live_then_close_runtime_and_forward_gaps"),
            "blocking_reason": "saved live lane is offline and the supervised lane is still probationary",
            "stack_policy": str(rave_stack.get("current_stack_policy") or ""),
            "runtime_dependency_status": str(rave_runtime.get("status") or ""),
            "runtime_dependency_note": str(rave_runtime.get("action") or ""),
            "missing_proof_count": int(rave_gap.get("missing_proof_count") or 0),
            "missing_proofs": list(rave_gap.get("missing_proofs") or []),
            "recommended_action": "restore_live_then_finish_the_three_explicit_graduation_gaps",
        }
    )

    sup_stack = stack_rows.get("SUP-USD") or {}
    sup_pending = overlap_pending.get("SUP-USD") or {}
    sup_integrity = integrity_rows.get("SUP momentum + range_breakout overlap") or {}
    rows.append(
        {
            "coin": "SUP-USD",
            "secondary_lane": "momentum_registry_validation",
            "reserve_status": "blocked_missing_saved_overlap",
            "reserve_rank": 3,
            "evidence_class": "script_without_saved_report",
            "activation_gate": "save_overlap_report_before_any_dual_lane_capital",
            "blocking_reason": str(sup_integrity.get("summary") or "same-coin overlap is not saved as an artifact yet"),
            "stack_policy": str(sup_stack.get("current_stack_policy") or ""),
            "combined_strength_score": round(to_float(sup_pending.get("combined_strength_score")), 2),
            "runtime_dependency_status": "",
            "runtime_dependency_note": "",
            "recommended_action": "run_and_save_sup_same_coin_overlap_report_before_promotion",
        }
    )

    bal_stack = stack_rows.get("BAL-USD") or {}
    bal_pending = overlap_pending.get("BAL-USD") or {}
    rows.append(
        {
            "coin": "BAL-USD",
            "secondary_lane": "mom_50",
            "reserve_status": "blocked_overlap_and_reconcile",
            "reserve_rank": 4,
            "evidence_class": "pending_overlap_plus_reconcile_caution",
            "activation_gate": "close_overlap_and_reconcile_first_gaps_before_dual_lane_capital",
            "blocking_reason": "BAL still needs a saved overlap study and the secondary momentum lane still carries reconcile-first caution",
            "stack_policy": str(bal_stack.get("current_stack_policy") or ""),
            "combined_strength_score": round(to_float(bal_pending.get("combined_strength_score")), 2),
            "runtime_dependency_status": "",
            "runtime_dependency_note": "",
            "recommended_action": "keep_breakout_primary_only_until_overlap_and_reconcile_gaps_close",
        }
    )

    rows.sort(
        key=lambda row: (
            STATUS_RANK.get(str(row.get("reserve_status") or ""), 99),
            int(row.get("reserve_rank") or 99),
            str(row.get("coin") or ""),
        )
    )
    return rows


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    nom = next((row for row in rows if row["coin"] == "NOM-USD"), {})
    rave = next((row for row in rows if row["coin"] == "RAVE-USD"), {})
    sup = next((row for row in rows if row["coin"] == "SUP-USD"), {})
    bal = next((row for row in rows if row["coin"] == "BAL-USD"), {})
    return [
        f"NOM momentum is the first honest reserve add-on: overlap is already artifact-backed at {to_float(nom.get('overlap_pct_5m')):.1f}% with {to_float(nom.get('combined_uplift_vs_best_single')):+.2f} uplift, so the only remaining gate is reserve capital plus a real dual-shadow runtime lane.",
        f"RAVE RSI stays the second reserve candidate, but it is still blocked by saved runtime and graduation gaps ({int(rave.get('missing_proof_count') or 0)} explicit proofs left).",
        f"SUP and BAL are not reserve candidates yet. SUP is still missing a saved overlap artifact, and BAL still has both overlap and reconcile-first caution in front of it.",
    ]


def build_payload() -> dict[str, Any]:
    rows = build_rows()
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows),
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Reserve Activation Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Coin | Secondary Lane | Status | Evidence Class | Gate | Runtime Status | Recommended Action |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {coin} | {secondary_lane} | {reserve_status} | {evidence_class} | {activation_gate} | {runtime_dependency_status} | {recommended_action} |".format(
                **row
            )
        )
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    payload = build_payload()
    write_reports(payload)
    print(f"wrote {MD_PATH}")
    print(f"wrote {JSON_PATH}")


if __name__ == "__main__":
    main()
