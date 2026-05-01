#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

ADMISSION_BOARD_PATH = REPORTS / "coinbase_same_coin_stack_admission_board.json"
STACK_BOARD_PATH = REPORTS / "coinbase_product_lane_stack_board.json"
JSON_PATH = REPORTS / "coinbase_same_coin_overlap_queue.json"
MD_PATH = REPORTS / "coinbase_same_coin_overlap_queue.md"


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


def plural_verb(items: list[Any], singular: str, plural: str) -> str:
    return singular if len(items) == 1 else plural


def build_stack_row_map() -> dict[str, dict[str, Any]]:
    payload = load_json(STACK_BOARD_PATH)
    return {str(row.get("coin") or ""): row for row in list(payload.get("rows") or []) if row.get("coin")}


def summarize_lanes(stack_row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lane_summaries = list(stack_row.get("lane_summaries") or [])
    primary_label = str(stack_row.get("preferred_primary_lane") or "")
    primary = {}
    secondaries: list[dict[str, Any]] = []
    for lane in lane_summaries:
        if str(lane.get("strategy") or "") == primary_label and not primary:
            primary = lane
        else:
            secondaries.append(lane)
    if not primary and lane_summaries:
        primary = lane_summaries[0]
        secondaries = lane_summaries[1:]
    return primary, secondaries


def build_pending_row(admission_row: dict[str, Any], stack_row: dict[str, Any]) -> dict[str, Any]:
    primary, secondaries = summarize_lanes(stack_row)
    best_secondary = max(secondaries, key=lambda lane: to_float(lane.get("reconciliation_30d_net_usd")), default={})
    caution = "cautious" in str(admission_row.get("current_stack_policy") or "") or str(best_secondary.get("router_decision") or "") == "reconcile_first"
    combined_strength = round(
        to_float(primary.get("reconciliation_30d_net_usd")) + to_float(best_secondary.get("reconciliation_30d_net_usd")),
        2,
    )
    return {
        "coin": str(admission_row.get("coin") or ""),
        "queue_status": "run_overlap_study",
        "priority": "high" if not caution else "medium",
        "preferred_primary_lane": str(admission_row.get("preferred_primary_lane") or ""),
        "candidate_secondary_lane": str(best_secondary.get("strategy") or ""),
        "primary_30d_net_usd": round(to_float(primary.get("reconciliation_30d_net_usd")), 2),
        "secondary_30d_net_usd": round(to_float(best_secondary.get("reconciliation_30d_net_usd")), 2),
        "primary_30d_closes": int(primary.get("reconciliation_30d_closes") or 0),
        "secondary_30d_closes": int(best_secondary.get("reconciliation_30d_closes") or 0),
        "combined_strength_score": combined_strength,
        "admission_reason": str(admission_row.get("reason") or ""),
        "router_caution": caution,
        "suggested_next_action": "run_same_coin_overlap_analysis",
    }


def build_completed_row(admission_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "coin": str(admission_row.get("coin") or ""),
        "queue_status": "benchmark_completed",
        "priority": "benchmark",
        "preferred_primary_lane": str(admission_row.get("preferred_primary_lane") or ""),
        "candidate_secondary_lane": ", ".join(admission_row.get("secondary_candidates") or []),
        "primary_30d_net_usd": None,
        "secondary_30d_net_usd": None,
        "primary_30d_closes": None,
        "secondary_30d_closes": None,
        "combined_strength_score": round(to_float(admission_row.get("combined_uplift_vs_best_single")), 2),
        "admission_reason": str(admission_row.get("reason") or ""),
        "router_caution": False,
        "suggested_next_action": "use_as_overlap_benchmark",
        "overlap_pct_5m": round(to_float(admission_row.get("overlap_pct_5m")), 1),
        "combined_uplift_vs_best_single": round(to_float(admission_row.get("combined_uplift_vs_best_single")), 2),
    }


def build_leadership_read(pending_rows: list[dict[str, Any]], completed_rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    if completed_rows:
        benchmark = completed_rows[0]
        lines.append(
            f"{benchmark['coin'].replace('-USD', '')} stays the reference overlap case at {benchmark['overlap_pct_5m']:.1f}% 5-minute overlap and ${benchmark['combined_uplift_vs_best_single']:.2f} additive uplift."
        )
    if pending_rows:
        top = pending_rows[0]
        lines.append(
            f"{top['coin'].replace('-USD', '')} is the next overlap study because its primary/secondary pair sums to ${top['combined_strength_score']:.2f} of 30-day bench strength without a router block."
        )
        cautious = [row["coin"].replace("-USD", "") for row in pending_rows if row["router_caution"]]
        if cautious:
            lines.append(
                f"{', '.join(cautious)} {plural_verb(cautious, 'stays', 'stay')} behind the lead candidate because reconcile-first or caution flags mean overlap evidence alone would not be enough to promote them."
            )
    else:
        lines.append("No pending same-coin overlap studies remain on the current board.")
    return lines


def build_payload() -> dict[str, Any]:
    admission_payload = load_json(ADMISSION_BOARD_PATH)
    stack_map = build_stack_row_map()
    pending_rows: list[dict[str, Any]] = []
    completed_rows: list[dict[str, Any]] = []

    for row in list(admission_payload.get("rows") or []):
        coin = str(row.get("coin") or "")
        status = str(row.get("admission_status") or "")
        if status == "overlap_verified":
            completed_rows.append(build_completed_row(row))
        elif status == "shadow_only_pending_overlap" and coin in stack_map:
            pending_rows.append(build_pending_row(row, stack_map[coin]))

    pending_rows.sort(
        key=lambda row: (
            row["priority"] != "high",
            -to_float(row["combined_strength_score"]),
            row["coin"],
        )
    )
    completed_rows.sort(key=lambda row: row["coin"])
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(pending_rows, completed_rows),
        "pending_rows": pending_rows,
        "completed_rows": completed_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Same-Coin Overlap Queue",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Pending Rows",
            "",
            "| Coin | Status | Priority | Primary Lane | Secondary Lane | Primary 30d $ | Secondary 30d $ | Primary Closes | Secondary Closes | Combined Strength | Router Caution | Next Action |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["pending_rows"]:
        lines.append(
            "| {coin} | {queue_status} | {priority} | {preferred_primary_lane} | {candidate_secondary_lane} | {primary_30d_net_usd:.2f} | {secondary_30d_net_usd:.2f} | {primary_30d_closes} | {secondary_30d_closes} | {combined_strength_score:.2f} | {router_caution} | {suggested_next_action} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Completed Rows",
            "",
            "| Coin | Status | Primary Lane | Secondary Lane | Overlap 5m | Combined Uplift $ | Next Action |",
            "| --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in payload["completed_rows"]:
        lines.append(
            "| {coin} | {queue_status} | {preferred_primary_lane} | {candidate_secondary_lane} | {overlap_pct_5m:.1f} | {combined_uplift_vs_best_single:.2f} | {suggested_next_action} |".format(
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
