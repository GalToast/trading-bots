#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
LIVE_LANE_DASHBOARD_JSON = ROOT / "reports" / "live_lane_dashboard.json"
CRYPTO_TRIGGER_JSON = ROOT / "reports" / "live_crypto_trigger_proximity_board.json"
OUT_JSON = ROOT / "reports" / "live_lane_idle_cause_board.json"
OUT_MD = ROOT / "reports" / "live_lane_idle_cause_board.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def crypto_rows_by_lane() -> dict[str, dict[str, Any]]:
    payload = load_json(CRYPTO_TRIGGER_JSON)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return {
        str(row.get("lane") or ""): row
        for row in rows or []
        if isinstance(row, dict) and str(row.get("lane") or "").strip()
    }


def classify_idle_cause(row: dict[str, Any], crypto_row: dict[str, Any] | None) -> tuple[str, str, str]:
    evidence_basis = str(row.get("evidence_basis") or "")
    operator_posture = str(row.get("operator_posture") or "")
    kind = str(row.get("kind") or "")
    fresh_session_booked_usd = to_float(row.get("fresh_session_booked_usd"))

    if evidence_basis in {"intentional_hold_live", "trapped_hold_live"}:
        return (
            "intentional_hold",
            "wait_profitable_unwind",
            "lane is intentionally standing down while it unwinds inventory under positive-only hold rules",
        )
    if evidence_basis == "contract_invalid_live":
        return (
            "contract_friction_invalid",
            "fix_contract_before_recycle",
            "runner is healthy but current contract is not venue-admissible under observed spread friction",
        )
    if kind == "live_crypto" and crypto_row:
        execution_read = str(crypto_row.get("execution_read") or "")
        nearest_side = str(crypto_row.get("nearest_side") or "")
        nearest_gap_steps = to_float(crypto_row.get("nearest_gap_steps"))
        if execution_read == "waiting_for_first_fill":
            return (
                "waiting_for_first_fill",
                "wait_for_trigger_cross",
                f"lane is broker-flat, spread-admissible, and still {nearest_gap_steps:.3f} steps from the next {nearest_side} trigger",
            )
        if execution_read == "spread_blocked_before_first_fill":
            return (
                "pre_first_fill_spread_block",
                "inspect_spread_gate",
                "lane is still blocked by spread admission before its first fill",
            )
        if execution_read == "crossed_waiting_first_fill":
            return (
                "crossed_waiting_first_fill",
                "inspect_execution_path",
                "lane is across a live trigger but still waiting for its first fill",
            )
    if evidence_basis == "carry_weighted_live" and abs(fresh_session_booked_usd) <= 1e-9:
        return (
            "quiet_carry_weighted_live",
            "require_fresh_forward_sample",
            "lane is healthy, but the current window is quiet and cumulative totals are still carry-weighted",
        )
    if evidence_basis == "thin_live_sample":
        return (
            "thin_live_sample",
            operator_posture or "wait_more_sample",
            "lane is live but still too thin to classify as a proven active earner",
        )
    if abs(fresh_session_booked_usd) > 1e-9:
        return (
            "fresh_forward_active",
            "monitor_current_cycle",
            "lane is still monetizing in the current runner window",
        )
    return (
        "quiet_monitor_only",
        operator_posture or "review_runtime",
        "lane is healthy but not currently monetizing in this window",
    )


def build_payload() -> dict[str, Any]:
    dashboard = load_json(LIVE_LANE_DASHBOARD_JSON)
    rows = dashboard.get("rows") if isinstance(dashboard, dict) else []
    crypto_by_lane = crypto_rows_by_lane()

    built_rows: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lane = str(row.get("lane") or "").strip()
        if not lane:
            continue
        crypto_row = crypto_by_lane.get(lane)
        idle_cause, current_action, rationale = classify_idle_cause(row, crypto_row)
        built_rows.append(
            {
                "lane": lane,
                "kind": str(row.get("kind") or ""),
                "status": str(row.get("status") or ""),
                "evidence_basis": str(row.get("evidence_basis") or ""),
                "operator_posture": str(row.get("operator_posture") or ""),
                "idle_cause": idle_cause,
                "current_action": current_action,
                "managed_open_count": to_int(row.get("managed_open_count")),
                "display_close_count": to_int(row.get("display_close_count") or row.get("close_count")),
                "fresh_session_booked_usd": to_float(row.get("fresh_session_booked_usd")),
                "fresh_session_usd_per_hour": to_float(row.get("fresh_session_usd_per_hour")),
                "runner_status": str(row.get("runner_status") or ""),
                "crypto_execution_read": str((crypto_row or {}).get("execution_read") or ""),
                "crypto_nearest_side": str((crypto_row or {}).get("nearest_side") or ""),
                "crypto_nearest_gap_steps": to_float((crypto_row or {}).get("nearest_gap_steps")),
                "rationale": rationale,
            }
        )

    built_rows.sort(key=lambda item: (item["idle_cause"], item["lane"]))
    cause_counts: dict[str, int] = {}
    for row in built_rows:
        cause = row["idle_cause"]
        cause_counts[cause] = cause_counts.get(cause, 0) + 1

    return {
        "generated_at": utc_now_iso(),
        "summary": {
            "lane_count": len(built_rows),
            "cause_counts": cause_counts,
        },
        "rows": built_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Live Lane Idle Cause Board",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Cause counts: `{payload['summary']['cause_counts']}`",
        "",
        "## Current Read",
        "",
        "- `intentional_hold` means the lane is intentionally blocked from adding risk while it unwinds inventory.",
        "- `waiting_for_first_fill` means the lane is broker-flat, spread-admissible, and still waiting for the first executable trigger cross.",
        "- `contract_friction_invalid` means the lane is alive but its current contract is not venue-admissible enough to keep trading cleanly.",
        "- `quiet_carry_weighted_live` means the lane is healthy, but this window is quiet and the cumulative PnL still includes carry.",
        "- `fresh_forward_active` means the lane is still monetizing in the current runner window.",
        "",
        "## Rows",
        "",
        "| Lane | Kind | Idle Cause | Action | Managed Open | Closes | Fresh Booked USD | Fresh $/hr | Crypto Read | Rationale |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in payload.get("rows") or []:
        crypto_read = "-"
        if row["crypto_execution_read"]:
            crypto_read = (
                f"`{row['crypto_execution_read']}`"
                f" {row['crypto_nearest_side']} {row['crypto_nearest_gap_steps']:.3f}"
            ).strip()
        lines.append(
            f"| `{row['lane']}` | `{row['kind']}` | `{row['idle_cause']}` | `{row['current_action']}` | "
            f"{row['managed_open_count']} | {row['display_close_count']} | "
            f"{row['fresh_session_booked_usd']:+.2f} | {row['fresh_session_usd_per_hour']:+.2f} | {crypto_read} | {row['rationale']} |"
        )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
