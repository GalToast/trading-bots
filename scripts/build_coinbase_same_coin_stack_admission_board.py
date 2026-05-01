#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

STACK_BOARD_PATH = REPORTS / "coinbase_product_lane_stack_board.json"
NOM_OVERLAP_PATH = REPORTS / "nom_strategy_overlap_analysis.json"
JSON_PATH = REPORTS / "coinbase_same_coin_stack_admission_board.json"
MD_PATH = REPORTS / "coinbase_same_coin_stack_admission_board.md"


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


def plural_verb(items: list[dict[str, Any]], singular: str, plural: str) -> str:
    return singular if len(items) == 1 else plural


def lane_label(summary: dict[str, Any]) -> str:
    return str(summary.get("strategy") or "")


def load_overlap_map() -> dict[str, dict[str, Any]]:
    payload = load_json(NOM_OVERLAP_PATH)
    coin = str(payload.get("coin") or "")
    if not coin:
        return {}
    return {coin: payload}


def overlap_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    overlap_5m = ((payload.get("overlap_analysis") or {}).get("1bar_5min") or {})
    combined = payload.get("combined") or {}
    momentum = payload.get("momentum") or {}
    breakout = payload.get("range_breakout") or {}
    return {
        "overlap_pct_5m": round(to_float(overlap_5m.get("overlap_pct")), 1),
        "combined_total_pnl": round(to_float(combined.get("total_pnl")), 2),
        "combined_total_trades": int(combined.get("total_trades") or 0),
        "range_breakout_total_pnl": round(to_float(breakout.get("total_pnl")), 2),
        "momentum_total_pnl": round(to_float(momentum.get("total_pnl")), 2),
        "combined_uplift_vs_best_single": round(
            to_float(combined.get("total_pnl")) - max(to_float(momentum.get("total_pnl")), to_float(breakout.get("total_pnl"))),
            2,
        ),
    }


def build_row(stack_row: dict[str, Any], overlap_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    coin = str(stack_row.get("coin") or "")
    policy = str(stack_row.get("stack_policy") or "")
    preferred_primary = str(stack_row.get("preferred_primary_lane") or "")
    lane_summaries = list(stack_row.get("lane_summaries") or [])
    secondary_candidates = [lane_label(summary) for summary in lane_summaries if lane_label(summary) != preferred_primary]

    row = {
        "coin": coin,
        "current_stack_policy": policy,
        "preferred_primary_lane": preferred_primary,
        "secondary_candidates": secondary_candidates,
        "current_max_live_lanes": int(stack_row.get("max_live_lanes") or 0),
        "admission_decision": "hold",
        "admission_status": "needs_review",
        "overlap_evidence_status": "missing",
        "recommended_max_live_lanes": int(stack_row.get("max_live_lanes") or 0),
        "reason": "",
        "overlap_pct_5m": None,
        "combined_uplift_vs_best_single": None,
    }

    if policy == "dual_live_allowed":
        row.update(
            {
                "admission_decision": "keep_dual_live",
                "admission_status": "runtime_proven",
                "overlap_evidence_status": "not_required_live_proven",
                "recommended_max_live_lanes": 2,
                "reason": "two same-coin lanes are already runtime-real and positive, so admission is earned by live evidence rather than overlap inference",
            }
        )
        return row

    overlap_payload = overlap_map.get(coin)
    if overlap_payload:
        snapshot = overlap_snapshot(overlap_payload)
        row["overlap_pct_5m"] = snapshot["overlap_pct_5m"]
        row["combined_uplift_vs_best_single"] = snapshot["combined_uplift_vs_best_single"]
        if snapshot["combined_uplift_vs_best_single"] > 0.0 and snapshot["overlap_pct_5m"] < 50.0:
            row.update(
                {
                    "admission_decision": "allow_dual_shadow_stack",
                    "admission_status": "overlap_verified",
                    "overlap_evidence_status": "verified_moderate_overlap",
                    "recommended_max_live_lanes": 2,
                    "reason": f"5-minute overlap is only {snapshot['overlap_pct_5m']:.1f}% and combined PnL adds ${snapshot['combined_uplift_vs_best_single']:.2f} over the best single lane",
                }
            )
        else:
            row.update(
                {
                    "admission_decision": "keep_single_primary_until_better_overlap_case",
                    "admission_status": "overlap_unconvincing",
                    "overlap_evidence_status": "verified_but_not_additive",
                    "recommended_max_live_lanes": 1,
                    "reason": "overlap was checked, but the additive case is not strong enough to justify stacking",
                }
            )
        return row

    if policy in {"parallel_shadows_allowed", "parallel_shadows_allowed_cautious"}:
        row.update(
            {
                "admission_decision": "require_overlap_check_before_dual_live",
                "admission_status": "shadow_only_pending_overlap",
                "overlap_evidence_status": "missing_overlap_proof",
                "recommended_max_live_lanes": 1,
                "reason": "two positive shadow lanes are not enough on their own; same-coin stacking needs overlap or combined-bankroll evidence first",
            }
        )
        return row

    if policy in {"keep_rsi_primary_shadow_cap_1", "rsi_only_for_now", "replace_negative_rsi_with_momentum"}:
        row.update(
            {
                "admission_decision": "keep_single_lane_cap",
                "admission_status": "router_or_quality_capped",
                "overlap_evidence_status": "not_applicable",
                "recommended_max_live_lanes": 1,
                "reason": "router conflict or weak same-coin alternatives still dominate the decision, so overlap analysis would be premature",
            }
        )
        return row

    row["reason"] = "no specific admission rule matched this coin"
    return row


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No same-coin stack admission rows were available."]

    lines: list[str] = []
    runtime_proven = [row for row in rows if row["admission_status"] == "runtime_proven"]
    overlap_verified = [row for row in rows if row["admission_status"] == "overlap_verified"]
    pending = [row for row in rows if row["admission_status"] == "shadow_only_pending_overlap"]
    capped = [row for row in rows if row["admission_status"] == "router_or_quality_capped"]

    if runtime_proven:
        coins = ", ".join(row["coin"].replace("-USD", "") for row in runtime_proven)
        lines.append(
            f"{coins} already {plural_verb(runtime_proven, 'earns', 'earn')} same-coin stacking by runtime evidence, so no overlap gate is needed there."
        )
    if overlap_verified:
        row = overlap_verified[0]
        lines.append(
            f"{row['coin'].replace('-USD', '')} is the first formal overlap-admitted shadow stack: {row['overlap_pct_5m']:.1f}% 5-minute overlap and ${row['combined_uplift_vs_best_single']:.2f} additive PnL over the best single lane."
        )
    if pending:
        coins = ", ".join(row["coin"].replace("-USD", "") for row in pending)
        lines.append(f"{coins} still need overlap or combined-bankroll evidence before the board should approve same-coin dual-lane promotion.")
    if capped:
        coins = ", ".join(row["coin"].replace("-USD", "") for row in capped)
        lines.append(f"{coins} remain capped at one live lane because router conflict or weak alternatives still matter more than stacking ambition.")
    return lines


def build_payload() -> dict[str, Any]:
    stack_payload = load_json(STACK_BOARD_PATH)
    overlap_map = load_overlap_map()
    rows = [build_row(row, overlap_map) for row in list(stack_payload.get("rows") or [])]
    rows.sort(key=lambda row: (row["recommended_max_live_lanes"] != 2, row["coin"]))
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(rows),
        "rows": rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Same-Coin Stack Admission Board",
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
            "| Coin | Current Policy | Primary Lane | Secondary Candidates | Decision | Status | Overlap Evidence | Overlap 5m | Combined Uplift $ | Recommended Max Live Lanes | Reason |",
            "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {coin} | {current_stack_policy} | {preferred_primary_lane} | {secondary_candidates} | {admission_decision} | {admission_status} | {overlap_evidence_status} | {overlap_pct_5m} | {combined_uplift_vs_best_single} | {recommended_max_live_lanes} | {reason} |".format(
                coin=row["coin"],
                current_stack_policy=row["current_stack_policy"],
                preferred_primary_lane=row["preferred_primary_lane"],
                secondary_candidates=", ".join(row["secondary_candidates"]),
                admission_decision=row["admission_decision"],
                admission_status=row["admission_status"],
                overlap_evidence_status=row["overlap_evidence_status"],
                overlap_pct_5m="" if row["overlap_pct_5m"] is None else f"{row['overlap_pct_5m']:.1f}",
                combined_uplift_vs_best_single="" if row["combined_uplift_vs_best_single"] is None else f"{row['combined_uplift_vs_best_single']:.2f}",
                recommended_max_live_lanes=row["recommended_max_live_lanes"],
                reason=row["reason"],
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
