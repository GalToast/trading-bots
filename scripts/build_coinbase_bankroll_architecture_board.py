#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

OPTIMAL_COMBINED_PATH = REPORTS / "optimal_combined_portfolio.json"
VERIFIED_PORTFOLIO_PATH = REPORTS / "verified_portfolio_report.json"
COMPREHENSIVE_PORTFOLIO_PATH = REPORTS / "comprehensive_portfolio.json"
MULTI_STRATEGY_PORTFOLIO_PATH = REPORTS / "multi_strategy_portfolio_results.json"
SHARED_BANKROLL_SIM_PATH = REPORTS / "shared_bankroll_sim.json"
HYPERGROWTH_ROUTER_PATH = REPORTS / "coinbase_spot_hypergrowth_router_board.json"

JSON_PATH = REPORTS / "coinbase_bankroll_architecture_board.json"
MD_PATH = REPORTS / "coinbase_bankroll_architecture_board.md"

STATUS_RANK = {
    "reject_naive_shared_bankroll": 0,
    "isolated_upper_bound_only": 1,
    "noncanonical_positive": 2,
    "niche_shared_success": 3,
    "recommended_default": 4,
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
    optimal = load_json(OPTIMAL_COMBINED_PATH)
    verified = load_json(VERIFIED_PORTFOLIO_PATH)
    comprehensive = load_json(COMPREHENSIVE_PORTFOLIO_PATH)
    multi = load_json(MULTI_STRATEGY_PORTFOLIO_PATH)
    shared = list(load_json(SHARED_BANKROLL_SIM_PATH) or [])
    router = load_json(HYPERGROWTH_ROUTER_PATH)

    best_shared = max(shared, key=lambda row: to_float(row.get("net"))) if shared else {}
    active_core = list((router.get("summary") or {}).get("core_coins") or [])

    rows = [
        {
            "architecture": "shared_cross_coin_best_of_router",
            "status": "reject_naive_shared_bankroll",
            "evidence_class": "saved_shared_bankroll_extinction_test",
            "model": "one shared $48 bankroll across best-per-coin lanes",
            "net_pnl_usd": round(to_float(optimal.get("net_pnl")), 2),
            "return_pct": round(to_float(optimal.get("return_pct")), 1),
            "max_drawdown_pct": round(to_float(optimal.get("max_dd")), 1),
            "capital_base_usd": 48.0,
            "coverage_count": len(list((optimal.get("coins") or {}).keys())),
            "summary": (
                f"Saved combined-portfolio backtest went to {to_float(optimal.get('return_pct')):.1f}% "
                f"with {int(optimal.get('total_trades') or 0)} closes and {to_float(optimal.get('win_rate')):.1f}% WR."
            ),
            "governance_action": "do_not_deploy_a_single_shared_pool_across_signal_hog_coins",
        },
        {
            "architecture": "isolated_per_coin_verified_aggregate",
            "status": "recommended_default",
            "evidence_class": "saved_independent_bankroll_verified_aggregate",
            "model": "one isolated bankroll per verified coin lane",
            "net_pnl_usd": round(to_float(verified.get("estimated_monthly_at_48")), 2),
            "return_pct": round(to_float(verified.get("estimated_monthly_at_48")) / max(1.0, to_float(verified.get("capital_required_48"))) * 100, 1),
            "max_drawdown_pct": None,
            "capital_base_usd": round(to_float(verified.get("capital_required_48")), 2),
            "coverage_count": int(len(list(verified.get("verified_coins") or []))),
            "summary": (
                f"Verified isolated aggregate estimates {to_float(verified.get('estimated_monthly_at_48')):+.2f} "
                f"over ${to_float(verified.get('capital_required_48')):.2f} across "
                f"{len(list(verified.get('verified_coins') or []))} verified coins."
            ),
            "governance_action": "treat_isolated_per_coin_bankrolls_as_the_default_spot_deployment_model",
        },
        {
            "architecture": "isolated_per_coin_broad_claim_stack",
            "status": "isolated_upper_bound_only",
            "evidence_class": "saved_independent_bankroll_broad_claim_aggregate",
            "model": "sum of many per-coin independent $48 sleeves including weaker claims",
            "net_pnl_usd": round(to_float(comprehensive.get("portfolio_pnl")), 2),
            "return_pct": round(to_float(comprehensive.get("monthly_return_pct")), 1),
            "max_drawdown_pct": None,
            "capital_base_usd": round(to_float(comprehensive.get("capital")), 2),
            "coverage_count": int(len(list(comprehensive.get("coins") or []))),
            "summary": (
                f"Broad independent aggregate claims {to_float(comprehensive.get('portfolio_pnl')):+.2f} "
                f"on ${to_float(comprehensive.get('capital')):.2f} across {len(list(comprehensive.get('coins') or []))} coins, "
                f"but it mixes verified and weaker names."
            ),
            "governance_action": "use_only_as_an_upper_bound_not_as_a_deployment_order",
        },
        {
            "architecture": "isolated_multi_strategy_sleeves",
            "status": "noncanonical_positive",
            "evidence_class": "saved_fixed_allocation_sleeve_backtest",
            "model": "fixed sub-allocations per strategy sleeve, no shared first-come-first-serve pool",
            "net_pnl_usd": round(to_float(((multi.get("optimized_allocation") or {}).get("total_pnl"))), 2),
            "return_pct": round(to_float(((multi.get("optimized_allocation") or {}).get("return_pct"))), 1),
            "max_drawdown_pct": round(to_float(((multi.get("optimized_allocation") or {}).get("max_dd"))), 1),
            "capital_base_usd": 48.0,
            "coverage_count": int(((multi.get("optimized_allocation") or {}).get("n_strategies")) or 0),
            "summary": (
                f"Fixed-sleeve portfolio stays positive at {to_float(((multi.get('optimized_allocation') or {}).get('total_pnl'))):+.2f} "
                f"with {to_float(((multi.get('optimized_allocation') or {}).get('max_dd'))):.1f}% DD, while even equal sleeves remain positive."
            ),
            "governance_action": "acceptable_only_as_a_noncanonical_reference_for_sleeved_capital_design",
        },
        {
            "architecture": "niche_shared_bankroll_sniper_grinder",
            "status": "niche_shared_success",
            "evidence_class": "saved_shared_bankroll_complementary_regime_test",
            "model": "shared bankroll across a gated sniper plus grinder fee-tier engine",
            "net_pnl_usd": round(to_float(best_shared.get("net")), 2),
            "return_pct": round(to_float(best_shared.get("rpct")), 1),
            "max_drawdown_pct": round(to_float(best_shared.get("max_dd")), 2),
            "capital_base_usd": 288.0,
            "coverage_count": 3,
            "summary": (
                f"Best shared-bankroll niche config is `{str(best_shared.get('config') or '')}` at "
                f"{to_float(best_shared.get('net')):+.2f} and {to_float(best_shared.get('final_fee_bps')):.1f}bps final fees, "
                f"but it is a regime-gated sniper/grinder design, not the cross-coin best-of router."
            ),
            "governance_action": "allow_only_as_a_special_case_not_as_the_default_cross_coin_architecture",
        },
        {
            "architecture": "hypergrowth_router_deployment_default",
            "status": "recommended_default",
            "evidence_class": "saved_router_governance_board",
            "model": "winner-take-most router with strict per-coin lane caps and isolated sleeves",
            "net_pnl_usd": None,
            "return_pct": None,
            "max_drawdown_pct": None,
            "capital_base_usd": None,
            "coverage_count": len(active_core),
            "summary": (
                f"Router board currently anchors on {', '.join(active_core) if active_core else 'no active core names'}, "
                "with same-coin secondary lanes admitted only where overlap or runtime proof exists."
            ),
            "governance_action": "use_router_selection_plus_isolated_per_coin_sleeves_as_the_spot_operating_model",
        },
    ]
    rows.sort(key=lambda row: (STATUS_RANK.get(str(row.get("status") or ""), 99), str(row.get("architecture") or "")))
    return rows


def build_leadership_read(rows: list[dict[str, Any]]) -> list[str]:
    rejected = next((row for row in rows if row["architecture"] == "shared_cross_coin_best_of_router"), {})
    isolated = next((row for row in rows if row["architecture"] == "isolated_per_coin_verified_aggregate"), {})
    niche = next((row for row in rows if row["architecture"] == "niche_shared_bankroll_sniper_grinder"), {})
    lines = [
        f"Naive cross-coin shared bankroll is the wrong default for Coinbase spot: the saved best-of router test goes to {to_float(rejected.get('return_pct')):.1f}%."
    ]
    lines.append(
        f"The clean default is isolated per-coin sleeves routed by validated edge, with the current verified aggregate estimating {to_float(isolated.get('net_pnl_usd')):+.2f} on ${to_float(isolated.get('capital_base_usd')):.2f}."
    )
    lines.append(
        f"Shared bankroll is not universally dead, but it only survives in niche complementary designs like the sniper/grinder sim at {to_float(niche.get('net_pnl_usd')):+.2f}; that does not justify a general shared pool across the router book."
    )
    return lines


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
        "# Coinbase Bankroll Architecture Board",
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
            "| Architecture | Status | Evidence Class | Model | Net $ | Return % | Max DD % | Capital Base $ | Coverage | Governance Action |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in payload["rows"]:
        lines.append(
            "| {architecture} | {status} | {evidence_class} | {model} | {net} | {ret} | {dd} | {cap} | {coverage_count} | {governance_action} |".format(
                architecture=row["architecture"],
                status=row["status"],
                evidence_class=row["evidence_class"],
                model=row["model"],
                net="" if row["net_pnl_usd"] is None else f"{to_float(row['net_pnl_usd']):.2f}",
                ret="" if row["return_pct"] is None else f"{to_float(row['return_pct']):.1f}",
                dd="" if row["max_drawdown_pct"] is None else f"{to_float(row['max_drawdown_pct']):.2f}",
                cap="" if row["capital_base_usd"] is None else f"{to_float(row['capital_base_usd']):.2f}",
                coverage_count=row["coverage_count"],
                governance_action=row["governance_action"],
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
