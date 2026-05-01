#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

HYPERGROWTH_ROUTER_PATH = REPORTS / "coinbase_spot_hypergrowth_router_board.json"
NEXT_LAUNCH_WAVE_PATH = REPORTS / "coinbase_spot_next_launch_wave.json"
STACK_ADMISSION_PATH = REPORTS / "coinbase_same_coin_stack_admission_board.json"
GRADUATION_BOARD_PATH = REPORTS / "coinbase_spot_graduation_board.json"
GRADUATION_GAP_PATH = REPORTS / "coinbase_spot_graduation_gap_board.json"
CLAIM_INTEGRITY_PATH = REPORTS / "coinbase_claim_integrity_board.json"
BANKROLL_ARCHITECTURE_PATH = REPORTS / "coinbase_bankroll_architecture_board.json"

JSON_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.json"
MD_PATH = REPORTS / "coinbase_isolated_sleeve_allocator.md"

BASE_SLEEVE_USD = 48
PRIMARY_WAVE_RANK = {
    "maintain_live": 0,
    "launch_now": 1,
    "launch_after_wave_1": 2,
}
TIER_RANK = {
    "active_core": 0,
    "hypergrowth_core": 1,
    "expansion_core": 2,
}
BUCKET_BY_WAVE = {
    "maintain_live": "live_anchor",
    "launch_now": "wave_1_primary",
    "launch_after_wave_1": "wave_2_primary",
}
TIER_BANKROLLS = [48, 96, 144, 192, 240, 288, 336, 384]


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


def pretty_lane(coin: str, strategy: str) -> str:
    return f"{coin} {strategy}"


def build_primary_rows() -> list[dict[str, Any]]:
    router = load_json(HYPERGROWTH_ROUTER_PATH)
    next_wave = load_json(NEXT_LAUNCH_WAVE_PATH)

    next_rows = {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in list(next_wave.get("rows") or [])
    }

    rows: list[dict[str, Any]] = []
    for row in list(router.get("rows") or []):
        launch_wave = str(row.get("primary_wave") or "")
        router_tier = str(row.get("router_tier") or "")
        if launch_wave not in PRIMARY_WAVE_RANK:
            continue
        if router_tier not in TIER_RANK:
            continue

        coin = str(row.get("coin") or "")
        strategy = str(row.get("primary_lane") or "")
        next_row = next_rows.get((coin, strategy), {})
        same_coin_policy = str(row.get("same_coin_stack_policy") or "")
        cautious = (
            "cautious" in same_coin_policy
            or str(next_row.get("router_decision") or "") == "reconcile_first"
        )
        bucket = BUCKET_BY_WAVE.get(launch_wave, "other")
        if cautious:
            bucket = f"{bucket}_cautious"

        rows.append(
            {
                "coin": coin,
                "strategy": strategy,
                "family": str(row.get("primary_family") or ""),
                "router_tier": router_tier,
                "launch_wave": launch_wave,
                "allocation_bucket": bucket,
                "priority_score": round(to_float(row.get("primary_score")), 4),
                "sleeve_size_usd": BASE_SLEEVE_USD,
                "same_coin_stack_policy": same_coin_policy,
                "admission_decision": str(row.get("admission_decision") or ""),
                "reconciliation_30d_net_usd": round(to_float(next_row.get("reconciliation_30d_net_usd")), 2),
                "reconciliation_30d_closes": int(next_row.get("reconciliation_30d_closes") or 0),
                "activation_gate": (
                    "maintain_live_now"
                    if launch_wave == "maintain_live"
                    else ("launch_now" if launch_wave == "launch_now" else "fund_after_wave_1")
                ),
                "reason": str(next_row.get("reason") or row.get("primary_reason") or ""),
                "precondition": str(next_row.get("precondition") or ""),
            }
        )

    rows.sort(
        key=lambda row: (
            PRIMARY_WAVE_RANK.get(str(row.get("launch_wave") or ""), 99),
            TIER_RANK.get(str(row.get("router_tier") or ""), 99),
            -to_float(row.get("priority_score")),
            str(row.get("coin") or ""),
        )
    )

    for index, row in enumerate(rows, start=1):
        row["sleeve_rank"] = index
        row["tier_eligible_from_usd"] = index * BASE_SLEEVE_USD

    return rows


def build_conditional_rows() -> list[dict[str, Any]]:
    stack_admission = load_json(STACK_ADMISSION_PATH)
    graduation = load_json(GRADUATION_BOARD_PATH)
    graduation_gap = load_json(GRADUATION_GAP_PATH)
    next_wave = load_json(NEXT_LAUNCH_WAVE_PATH)
    integrity = load_json(CLAIM_INTEGRITY_PATH)

    next_rows = {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in list(next_wave.get("rows") or [])
    }
    graduation_rows = {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in list(graduation.get("rows") or [])
    }
    graduation_gap_rows = {
        (str(row.get("coin") or ""), str(row.get("strategy") or "")): row
        for row in list(graduation_gap.get("rows") or [])
    }
    integrity_status = {
        str(row.get("subject") or ""): str(row.get("integrity_status") or "")
        for row in list(integrity.get("rows") or [])
    }

    rows: list[dict[str, Any]] = []
    for row in list(stack_admission.get("rows") or []):
        coin = str(row.get("coin") or "")
        decision = str(row.get("admission_decision") or "")
        if decision not in {"allow_dual_shadow_stack", "keep_dual_live"}:
            continue

        secondary_candidates = list(row.get("secondary_candidates") or [])
        if not secondary_candidates:
            continue
        strategy = str(secondary_candidates[0] or "")

        if coin == "SUP-USD" and integrity_status.get("SUP momentum + range_breakout overlap") != "artifact_backed":
            continue

        reserve_priority = 2
        activation_gate = "after_primary_book_is_funded"
        reserve_status = "conditional_secondary"
        reason = str(row.get("reason") or "")
        reconciliation_net = 0.0
        reconciliation_closes = 0

        next_row = next_rows.get((coin, strategy), {})
        if next_row:
            reconciliation_net = round(to_float(next_row.get("reconciliation_30d_net_usd")), 2)
            reconciliation_closes = int(next_row.get("reconciliation_30d_closes") or 0)
            reason = str(next_row.get("reason") or reason)

        if coin == "NOM-USD":
            reserve_priority = 1
            activation_gate = "after_wave_1_once_dual_shadow_orchestrator_is_ready"
            reserve_status = "reserve_stack_candidate"
            reason = (
                f"{reason} Overlap is already artifact-backed at "
                f"{to_float(row.get('overlap_pct_5m')):.1f}% with "
                f"{to_float(row.get('combined_uplift_vs_best_single')):+.2f} additive uplift."
            ).strip()
        elif coin == "RAVE-USD":
            grad_row = graduation_rows.get((coin, "rsi_mr"), {})
            gap_row = graduation_gap_rows.get((coin, "rsi_mr"), {})
            reserve_priority = 2
            activation_gate = "restore_live_then_close_runtime_and_forward_gaps"
            reserve_status = "micro_restore_candidate"
            reconciliation_net = round(to_float(grad_row.get("reconciliation_net_30d_usd")), 2)
            reconciliation_closes = int(grad_row.get("reconciliation_closes_30d") or 0)
            missing = list(gap_row.get("missing_proofs") or [])
            if missing:
                reason = (
                    f"Only move reserve capital here after {', '.join(str(item) for item in missing[:3])}."
                )

        rows.append(
            {
                "coin": coin,
                "strategy": strategy,
                "family": "momentum" if "mom" in strategy or "momentum" in strategy else "rsi_mean_reversion",
                "reserve_priority": reserve_priority,
                "reserve_status": reserve_status,
                "activation_gate": activation_gate,
                "sleeve_size_usd": BASE_SLEEVE_USD,
                "reconciliation_30d_net_usd": reconciliation_net,
                "reconciliation_30d_closes": reconciliation_closes,
                "reason": reason,
            }
        )

    rows.sort(
        key=lambda row: (
            int(row.get("reserve_priority") or 99),
            -to_float(row.get("reconciliation_30d_net_usd")),
            str(row.get("coin") or ""),
        )
    )
    for index, row in enumerate(rows, start=1):
        row["reserve_rank"] = index
    return rows


def build_watchlist_rows(primary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    router = load_json(HYPERGROWTH_ROUTER_PATH)
    funded = {(str(row.get("coin") or ""), str(row.get("strategy") or "")) for row in primary_rows}
    rows: list[dict[str, Any]] = []
    for row in list(router.get("rows") or []):
        coin = str(row.get("coin") or "")
        strategy = str(row.get("primary_lane") or "")
        if (coin, strategy) in funded:
            continue
        if str(row.get("router_tier") or "") != "watchlist":
            continue
        if to_float(row.get("primary_score")) <= 0:
            continue
        rows.append(
            {
                "coin": coin,
                "strategy": strategy,
                "family": str(row.get("primary_family") or ""),
                "priority_score": round(to_float(row.get("primary_score")), 4),
                "launch_wave": str(row.get("primary_wave") or ""),
                "reason": str(row.get("primary_reason") or ""),
            }
        )
    rows.sort(key=lambda row: (-to_float(row.get("priority_score")), str(row.get("coin") or "")))
    return rows


def build_tiers(primary_rows: list[dict[str, Any]], conditional_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tiers: list[dict[str, Any]] = []
    first_reserve = conditional_rows[0] if conditional_rows else {}
    for bankroll in TIER_BANKROLLS:
        sleeves_available = bankroll // BASE_SLEEVE_USD
        deployed_count = min(sleeves_available, len(primary_rows))
        deployed = primary_rows[:deployed_count]
        deployed_capital = deployed_count * BASE_SLEEVE_USD
        cash_reserve = bankroll - deployed_capital
        next_unfunded = primary_rows[deployed_count] if deployed_count < len(primary_rows) else {}

        reserve_action = ""
        if cash_reserve >= BASE_SLEEVE_USD and first_reserve:
            reserve_action = (
                f"Hold reserve for {pretty_lane(str(first_reserve.get('coin') or ''), str(first_reserve.get('strategy') or ''))} "
                f"once {str(first_reserve.get('activation_gate') or '')}."
            )

        tiers.append(
            {
                "bankroll_usd": bankroll,
                "sleeves_available": sleeves_available,
                "deployed_primary_sleeves": deployed_count,
                "deployed_capital_usd": deployed_capital,
                "cash_reserve_usd": cash_reserve,
                "funded_lanes": [pretty_lane(str(row.get("coin") or ""), str(row.get("strategy") or "")) for row in deployed],
                "next_unfunded_primary": (
                    pretty_lane(str(next_unfunded.get("coin") or ""), str(next_unfunded.get("strategy") or ""))
                    if next_unfunded
                    else ""
                ),
                "reserve_action": reserve_action,
            }
        )
    return tiers


def build_summary(primary_rows: list[dict[str, Any]], conditional_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "base_sleeve_usd": BASE_SLEEVE_USD,
        "unconditional_primary_sleeves": len(primary_rows),
        "unconditional_primary_capital_usd": len(primary_rows) * BASE_SLEEVE_USD,
        "conditional_reserve_candidates": len(conditional_rows),
        "first_reserve_candidate": (
            pretty_lane(
                str(conditional_rows[0].get("coin") or ""),
                str(conditional_rows[0].get("strategy") or ""),
            )
            if conditional_rows
            else ""
        ),
    }


def build_leadership_read(
    summary: dict[str, Any],
    primary_rows: list[dict[str, Any]],
    conditional_rows: list[dict[str, Any]],
) -> list[str]:
    wave1 = [pretty_lane(str(row.get("coin") or ""), str(row.get("strategy") or "")) for row in primary_rows if row["launch_wave"] == "launch_now"]
    wave2 = [pretty_lane(str(row.get("coin") or ""), str(row.get("strategy") or "")) for row in primary_rows if row["launch_wave"] == "launch_after_wave_1"]
    lines = [
        f"The isolated-sleeve book currently has {int(summary['unconditional_primary_sleeves'])} unconditional lanes, so the honest fully funded primary stack tops out at ${int(summary['unconditional_primary_capital_usd'])}.",
        f"Fund the live anchor first, then the actual wave-1 promotions ({', '.join(wave1) if wave1 else 'none'}), then move into wave-2 sleeves ({', '.join(wave2) if wave2 else 'none'}).",
    ]
    if conditional_rows:
        first = conditional_rows[0]
        second = conditional_rows[1] if len(conditional_rows) > 1 else {}
        line = (
            f"The eighth sleeve should stay reserve cash until a conditional add-on clears; first in line is {pretty_lane(str(first.get('coin') or ''), str(first.get('strategy') or ''))}"
            f" ({str(first.get('activation_gate') or '')})."
        )
        if second:
            line += f" The next reserve candidate after that is {pretty_lane(str(second.get('coin') or ''), str(second.get('strategy') or ''))}."
        lines.append(line)
    lines.append(
        "That keeps isolated-bankroll discipline intact while still leaving room for aggressive same-coin stacking only where overlap or live proof is already real."
    )
    return lines


def build_payload() -> dict[str, Any]:
    primary_rows = build_primary_rows()
    conditional_rows = build_conditional_rows()
    watchlist_rows = build_watchlist_rows(primary_rows)
    tiers = build_tiers(primary_rows, conditional_rows)
    summary = build_summary(primary_rows, conditional_rows)
    bankroll_architecture = load_json(BANKROLL_ARCHITECTURE_PATH)
    return {
        "generated_at": utc_now_iso(),
        "leadership_read": build_leadership_read(summary, primary_rows, conditional_rows),
        "summary": summary,
        "bankroll_architecture_anchor": next(
            (
                row
                for row in list(bankroll_architecture.get("rows") or [])
                if str(row.get("architecture") or "") == "isolated_per_coin_verified_aggregate"
            ),
            {},
        ),
        "primary_sleeves": primary_rows,
        "conditional_reserve_candidates": conditional_rows,
        "overflow_watchlist": watchlist_rows,
        "bankroll_tiers": tiers,
    }


def write_reports(payload: dict[str, Any]) -> None:
    save_json(JSON_PATH, payload)
    lines = [
        "# Coinbase Isolated Sleeve Allocator",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Base sleeve: `${payload['summary']['base_sleeve_usd']}`",
            f"- Unconditional primary sleeves: `{payload['summary']['unconditional_primary_sleeves']}`",
            f"- Unconditional primary capital: `${payload['summary']['unconditional_primary_capital_usd']}`",
            f"- First reserve candidate: `{payload['summary']['first_reserve_candidate']}`",
            "",
            "## Primary Sleeves",
            "",
            "| Rank | Coin | Strategy | Wave | Bucket | Gate | Score | 30d Net $ | 30d Closes | Tier From $ |",
            "| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["primary_sleeves"]:
        lines.append(
            "| {sleeve_rank} | {coin} | {strategy} | {launch_wave} | {allocation_bucket} | {activation_gate} | {priority_score:.4f} | {reconciliation_30d_net_usd:.2f} | {reconciliation_30d_closes} | {tier_eligible_from_usd} |".format(
                **row
            )
        )

    lines.extend(
        [
            "",
            "## Conditional Reserve Candidates",
            "",
            "| Rank | Coin | Strategy | Status | Gate | 30d Net $ | 30d Closes |",
            "| ---: | --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for row in payload["conditional_reserve_candidates"]:
        lines.append(
            "| {reserve_rank} | {coin} | {strategy} | {reserve_status} | {activation_gate} | {reconciliation_30d_net_usd:.2f} | {reconciliation_30d_closes} |".format(
                **row
            )
        )

    lines.extend(
        [
            "",
            "## Bankroll Tiers",
            "",
            "| Bankroll $ | Sleeves | Deployed Primary | Deployed $ | Reserve $ | Next Unfunded Primary | Reserve Action |",
            "| ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["bankroll_tiers"]:
        lines.append(
            "| {bankroll_usd} | {sleeves_available} | {deployed_primary_sleeves} | {deployed_capital_usd} | {cash_reserve_usd} | {next_unfunded_primary} | {reserve_action} |".format(
                **row
            )
        )

    if payload["overflow_watchlist"]:
        lines.extend(
            [
                "",
                "## Overflow Watchlist",
                "",
                "| Coin | Strategy | Family | Score | Wave |",
                "| --- | --- | --- | ---: | --- |",
            ]
        )
        for row in payload["overflow_watchlist"]:
            lines.append(
                "| {coin} | {strategy} | {family} | {priority_score:.4f} | {launch_wave} |".format(
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
