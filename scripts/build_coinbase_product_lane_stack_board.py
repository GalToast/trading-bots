#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

NEXT_LAUNCH_WAVE_PATH = REPORTS / "coinbase_spot_next_launch_wave.json"
RUNTIME_BOARD_PATH = REPORTS / "coinbase_spot_runtime_board.json"
ROUTER_CONFLICT_PATH = REPORTS / "coinbase_spot_router_conflict_board.json"
DEPLOYABILITY_BOARD_PATH = REPORTS / "coinbase_spot_deployability_board.json"

JSON_PATH = REPORTS / "coinbase_product_lane_stack_board.json"
MD_PATH = REPORTS / "coinbase_product_lane_stack_board.md"

WAVE_PRIORITY = {
    "maintain_live": 0,
    "launch_now": 1,
    "launch_after_wave_1": 2,
    "router_hold": 3,
    "debug_hold": 4,
    "watch_only": 5,
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


def to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def format_coin_list(coins: list[str]) -> str:
    labels = [coin.replace("-USD", "") for coin in coins]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def choose_stack_policy(
    *,
    coin: str,
    lane_rows: list[dict[str, Any]],
    active_rsi_row: dict[str, Any] | None,
    rejected_rsi_row: dict[str, Any] | None,
    conflict_action: str,
) -> tuple[str, int, str, str]:
    strategies = {str(row.get("strategy") or "") for row in lane_rows}
    breakout_row = next((row for row in lane_rows if str(row.get("strategy") or "") == "range_breakout_shadow"), None)
    momentum_row = next((row for row in lane_rows if str(row.get("strategy") or "").startswith("mom_")), None)

    if conflict_action == "anchor_momentum_keep_rsi_secondary" and active_rsi_row:
        return (
            "dual_live_allowed",
            2,
            "momentum remains primary, but the active positive RSI lane is strong enough to stay live as a secondary edge",
            "mom_10",
        )

    if active_rsi_row and conflict_action == "keep_rsi_primary_momentum_shadow_candidate":
        if breakout_row:
            return (
                "keep_rsi_primary_shadow_cap_1",
                1,
                "keep RSI primary and allow at most one same-coin shadow lane behind it until product-level overlap is understood better",
                str(active_rsi_row.get("lane") or "rsi_mean_reversion"),
            )
        return (
            "keep_rsi_primary_shadow_cap_1",
            1,
            "RSI is already live-positive, so any additional same-coin lane should stay capped to one shadow slot",
            str(active_rsi_row.get("lane") or "rsi_mean_reversion"),
        )

    if active_rsi_row and conflict_action == "keep_rsi_only_for_now":
        return (
            "rsi_only_for_now",
            1,
            "the active RSI lane is stronger than the same-coin alternatives right now, so do not stack additional live lanes on this product",
            str(active_rsi_row.get("lane") or "rsi_mean_reversion"),
        )

    if rejected_rsi_row and momentum_row:
        return (
            "replace_negative_rsi_with_momentum",
            1,
            "the active RSI probe is losing, so replace it with the confirmed momentum lane instead of carrying both",
            str(momentum_row.get("strategy") or ""),
        )

    if breakout_row and len(lane_rows) >= 2:
        best_breakout = to_float(breakout_row.get("reconciliation_30d_net_usd"))
        best_other = max(
            (to_float(row.get("reconciliation_30d_net_usd")) for row in lane_rows if row is not breakout_row),
            default=0.0,
        )
        has_reconcile_first = any(str(row.get("router_decision") or "") == "reconcile_first" for row in lane_rows)
        if has_reconcile_first:
            return (
                "parallel_shadows_allowed_cautious",
                2,
                "breakout is strong enough to be primary, but any secondary same-coin lane should stay shadow-only until the reconcile-first evidence gap is closed",
                str(breakout_row.get("strategy") or ""),
            )
        if best_breakout >= best_other:
            return (
                "parallel_shadows_allowed",
                2,
                "both lanes are positive and non-conflicting, so run breakout as primary and keep one secondary shadow lane behind it",
                str(breakout_row.get("strategy") or ""),
            )
        return (
            "parallel_shadows_allowed",
            2,
            "both lanes are positive and non-conflicting, so the product can support a two-lane shadow stack",
            str(lane_rows[0].get("strategy") or ""),
        )

    if len(lane_rows) == 1:
        return (
            "single_lane_only",
            1,
            "only one credible lane exists on this product right now, so keep the stack simple",
            str(lane_rows[0].get("strategy") or ""),
        )

    return (
        "watch_only_multi_lane",
        1,
        "multiple hints exist, but the board still lacks a clean reason to stack them live",
        str(lane_rows[0].get("strategy") or ""),
    )


def build_payload(*, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    next_launch = load_json(NEXT_LAUNCH_WAVE_PATH)
    runtime = load_json(RUNTIME_BOARD_PATH)
    conflicts = load_json(ROUTER_CONFLICT_PATH)
    deployability = load_json(DEPLOYABILITY_BOARD_PATH)

    next_rows = list(next_launch.get("rows") or [])
    runtime_rsi_map = {
        str(row.get("product_id") or ""): row
        for row in list(runtime.get("rsi_shadow_queue") or []) + list(runtime.get("key_lanes") or [])
        if str(row.get("family") or "") == "rsi_mean_reversion" and str(row.get("status") or "") == "active"
    }
    rejected_map = {
        str(row.get("product_id") or ""): row
        for row in deployability.get("rejects") or []
        if str(row.get("family") or "") == "rsi_mean_reversion" and str(row.get("runner_status") or "") == "active"
    }
    conflict_map = {str(row.get("coin") or ""): row for row in conflicts.get("rows") or []}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in next_rows:
        coin = str(row.get("coin") or "")
        if not coin:
            continue
        grouped.setdefault(coin, []).append(row)

    stack_rows: list[dict[str, Any]] = []
    for coin, lane_rows in grouped.items():
        if len(lane_rows) < 2 and coin not in runtime_rsi_map and coin not in rejected_map:
            continue

        lane_rows = sorted(
            lane_rows,
            key=lambda row: (
                WAVE_PRIORITY.get(str(row.get("launch_wave") or ""), 99),
                -to_float(row.get("reconciliation_30d_net_usd")),
                str(row.get("strategy") or ""),
            ),
        )
        active_rsi_row = runtime_rsi_map.get(coin)
        rejected_rsi_row = rejected_map.get(coin)
        conflict_action = str((conflict_map.get(coin) or {}).get("conflict_action") or "")

        stack_policy, max_live_lanes, rationale, preferred_primary = choose_stack_policy(
            coin=coin,
            lane_rows=lane_rows,
            active_rsi_row=active_rsi_row,
            rejected_rsi_row=rejected_rsi_row,
            conflict_action=conflict_action,
        )

        lane_summaries = [
            {
                "strategy": str(row.get("strategy") or ""),
                "launch_wave": str(row.get("launch_wave") or ""),
                "reconciliation_30d_net_usd": round(to_float(row.get("reconciliation_30d_net_usd")), 4),
                "reconciliation_30d_closes": to_int(row.get("reconciliation_30d_closes")),
                "router_decision": str(row.get("router_decision") or ""),
            }
            for row in lane_rows
        ]
        if active_rsi_row and not any(summary["strategy"] == "rsi_mean_reversion_active" for summary in lane_summaries):
            lane_summaries.insert(
                1,
                {
                    "strategy": "rsi_mean_reversion_active",
                    "launch_wave": "active_runtime",
                    "reconciliation_30d_net_usd": round(to_float(active_rsi_row.get("realized_net_usd")), 4),
                    "reconciliation_30d_closes": to_int(active_rsi_row.get("closes")),
                    "router_decision": str(active_rsi_row.get("action") or ""),
                },
            )
        if rejected_rsi_row:
            lane_summaries.append(
                {
                    "strategy": "rsi_mean_reversion_reject",
                    "launch_wave": "reject",
                    "reconciliation_30d_net_usd": round(to_float(rejected_rsi_row.get("observed_net_usd")), 4),
                    "reconciliation_30d_closes": to_int(rejected_rsi_row.get("realized_closes")),
                    "router_decision": str(rejected_rsi_row.get("action") or ""),
                }
            )

        stack_rows.append(
            {
                "coin": coin,
                "stack_policy": stack_policy,
                "max_live_lanes": max_live_lanes,
                "preferred_primary_lane": preferred_primary,
                "active_rsi_lane": str(active_rsi_row.get("lane") or "") if active_rsi_row else "",
                "active_rsi_realized_usd": round(to_float(active_rsi_row.get("realized_net_usd")), 4) if active_rsi_row else None,
                "lane_count": len(lane_summaries),
                "lane_summaries": lane_summaries,
                "rationale": rationale,
            }
        )

    stack_rows.sort(
        key=lambda row: (
            0 if row["stack_policy"] in {"dual_live_allowed", "parallel_shadows_allowed", "parallel_shadows_allowed_cautious"} else 1,
            -max((to_float(l.get("reconciliation_30d_net_usd")) for l in row["lane_summaries"]), default=0.0),
            row["coin"],
        )
    )

    dual_ok = [row["coin"] for row in stack_rows if row["stack_policy"] == "dual_live_allowed"]
    parallel_ok = [row["coin"] for row in stack_rows if row["stack_policy"] == "parallel_shadows_allowed"]
    parallel_cautious = [row["coin"] for row in stack_rows if row["stack_policy"] == "parallel_shadows_allowed_cautious"]
    rsi_primary = [row["coin"] for row in stack_rows if row["stack_policy"] in {"keep_rsi_primary_shadow_cap_1", "rsi_only_for_now"}]

    leadership_read = []
    if dual_ok:
        leadership_read.append(f"{format_coin_list(dual_ok)} can honestly carry two live same-coin lanes right now because both sides already have positive evidence.")
    if parallel_ok:
        leadership_read.append(f"{format_coin_list(parallel_ok)} can support parallel shadow lanes, but keep the stack capped at two and treat breakout as the primary same-coin expansion path.")
    if parallel_cautious:
        leadership_read.append(f"{format_coin_list(parallel_cautious)} can carry a breakout-primary stack, but any secondary same-coin lane should stay cautious until reconcile-first gaps are closed.")
    if rsi_primary:
        leadership_read.append(f"{format_coin_list(rsi_primary)} still require router discipline: keep RSI primary and do not stack extra same-coin lanes recklessly.")
    leadership_read.append("Same-coin multi-strategy does not mean infinite stacking. The board should cap products at one primary lane plus at most one secondary shadow lane unless live evidence proves more.")

    return {
        "generated_at": now.isoformat(),
        "leadership_read": leadership_read,
        "rows": stack_rows,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)

    lines = [
        "# Coinbase Product Lane Stack Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Product Stack Policies",
            "",
            "| Coin | Stack Policy | Max Live Lanes | Preferred Primary | Active RSI Lane | Active RSI $ | Lane Count | Rationale |",
            "| --- | --- | ---: | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in payload["rows"]:
        active_rsi = "" if row["active_rsi_realized_usd"] is None else f"{float(row['active_rsi_realized_usd']):.4f}"
        lines.append(
            "| {coin} | {stack_policy} | {max_live_lanes} | {preferred_primary_lane} | {active_rsi_lane} | {active_rsi} | {lane_count} | {rationale} |".format(
                coin=row["coin"],
                stack_policy=row["stack_policy"],
                max_live_lanes=row["max_live_lanes"],
                preferred_primary_lane=row["preferred_primary_lane"],
                active_rsi_lane=row["active_rsi_lane"],
                active_rsi=active_rsi,
                lane_count=row["lane_count"],
                rationale=row["rationale"],
            )
        )
    for row in payload["rows"]:
        lines.extend(
            [
                "",
                f"## {row['coin']}",
                "",
                "| Strategy | Wave | Recon/Runtime $ | Closes | Router |",
                "| --- | --- | ---: | ---: | --- |",
            ]
        )
        for lane in row["lane_summaries"]:
            lines.append(
                "| {strategy} | {launch_wave} | {reconciliation_30d_net_usd:.4f} | {reconciliation_30d_closes} | {router_decision} |".format(
                    **lane
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
