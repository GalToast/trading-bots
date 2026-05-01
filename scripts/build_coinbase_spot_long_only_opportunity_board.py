#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
MD_PATH = REPORTS / "coinbase_spot_long_only_opportunity_board.md"
JSON_PATH = REPORTS / "coinbase_spot_long_only_opportunity_board.json"

LONG_ONLY_RSI_PRODUCTS_PATH = REPORTS / "coinbase_spot_cross_asset_products.csv"
RSI_SCOREBOARD_PATH = REPORTS / "coinbase_spot_rsi_scoreboard.csv"
TACTICS_PATH = REPORTS / "coinbase_spot_tactics_72h.csv"
PIRANHA_PATHS = [
    REPORTS / "coinbase_spot_shadow_xrpusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_dogeusd_piranha_state.json",
    REPORTS / "coinbase_spot_shadow_solusd_piranha_state.json",
]
PIRANHA_CANDIDATES_PATHS = [
    REPORTS / "coinbase_spot_piranha_candidates_72h.csv",
    REPORTS / "coinbase_spot_piranha_candidates.csv",
]
RECLAIM_PATH = REPORTS / "coinbase_spot_flush_reclaim_72h.csv"
PULLBACK_PATH = REPORTS / "coinbase_spot_pullback_resume_72h.csv"
RECLAIM_SWEEP_PATH = REPORTS / "coinbase_spot_reclaim_param_sweep.csv"


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def pick_existing_path(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def long_only_rsi_rows() -> list[dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    for row in load_csv(RSI_SCOREBOARD_PATH):
        product_id = str(row.get("product_id") or "")
        lane_name = str(row.get("lane_name") or "")
        if not product_id or lane_name == "TOTAL":
            continue
        agg = aggregates.setdefault(
            product_id,
            {
                "product_id": product_id,
                "realized_net_usd": 0.0,
                "closes": 0,
                "lane_count": 0,
                "positive_lanes": 0,
                "best_lane_name": "",
                "best_lane_net_usd": float("-inf"),
            },
        )
        realized = to_float(row.get("realized_net_usd"))
        agg["realized_net_usd"] += realized
        agg["closes"] += to_int(row.get("realized_closes"))
        agg["lane_count"] += 1
        agg["positive_lanes"] += 1 if realized > 0.0 else 0
        if realized > float(agg["best_lane_net_usd"]):
            agg["best_lane_name"] = lane_name
            agg["best_lane_net_usd"] = realized

    rows = []
    for agg in aggregates.values():
        rows.append(
            {
                "product_id": str(agg["product_id"]),
                "realized_net_usd": round(float(agg["realized_net_usd"]), 4),
                "closes": int(agg["closes"]),
                "lane_count": int(agg["lane_count"]),
                "positive_lanes": int(agg["positive_lanes"]),
                "best_lane_name": str(agg["best_lane_name"]),
            }
        )
    rows.sort(key=lambda row: (-float(row["realized_net_usd"]), -int(row["closes"]), str(row["product_id"])))
    return rows


def tactics_summary() -> dict[str, dict[str, Any]]:
    rows = load_csv(TACTICS_PATH)
    return {
        str(row.get("tactic") or ""): {
            "best_product_id": str(row.get("best_product_id") or ""),
            "realized_net_usd": round(to_float(row.get("realized_net_usd")), 4),
            "trades": to_int(row.get("trades")),
            "median_hold_minutes": round(to_float(row.get("median_hold_minutes")), 1),
            "notes": str(row.get("notes") or ""),
        }
        for row in rows
    }


def piranha_candidate_rows() -> list[dict[str, Any]]:
    path = pick_existing_path(PIRANHA_CANDIDATES_PATHS)
    if path is None:
        return []
    rows = []
    for row in load_csv(path):
        rows.append(
            {
                "product_id": str(row.get("Product") or row.get("product_id") or ""),
                "sim_pnl": round(to_float(row.get("Sim PnL") or row.get("sim_realized_usd") or row.get("realized_net_usd")), 4),
                "closes": to_int(row.get("Closes") or row.get("sim_closes") or row.get("trades")),
                "median_hold_minutes": round(to_float(row.get("Median Hold (m)") or row.get("sim_median_hold_minutes") or row.get("median_hold_minutes")), 1),
                "buy_step": str(row.get("Buy Step") or row.get("buy_step") or ""),
                "target": str(row.get("Target") or row.get("profit_target") or ""),
            }
        )
    rows.sort(key=lambda row: (-float(row["sim_pnl"]), -int(row["closes"]), str(row["product_id"])))
    return rows


def live_piranha_rows() -> list[dict[str, Any]]:
    rows = []
    for path in PIRANHA_PATHS:
        payload = load_json(path)
        metadata = payload.get("metadata") or {}
        symbols = payload.get("symbols") or {}
        product_id = str(metadata.get("product_id") or "")
        symbol = symbols.get(product_id) or {}
        runner = payload.get("runner") or {}
        rows.append(
            {
                "product_id": product_id,
                "cash_usd": round(to_float(symbol.get("cash_usd")), 4),
                "inventory_units": round(to_float(symbol.get("inventory_units")), 6),
                "realized_net_usd": round(to_float(symbol.get("realized_net_usd")), 4),
                "realized_closes": to_int(symbol.get("realized_closes")),
                "open_lots": len(symbol.get("open_lots") or []),
                "heartbeat_at": str(runner.get("heartbeat_at") or ""),
                "pid": to_int(runner.get("pid")),
            }
        )
    rows.sort(key=lambda row: (float(row["realized_net_usd"]), -int(row["open_lots"]), str(row["product_id"])), reverse=True)
    return rows


def negative_tactic_summary(path: Path) -> dict[str, Any]:
    rows = load_csv(path)
    if not rows:
        return {"positive_products": 0, "worst_cum_net_pct": 0.0, "signals": 0}
    positive_products = sum(1 for row in rows if to_float(row.get("cumulative_net_pct")) > 0.0)
    worst = min((to_float(row.get("cumulative_net_pct")) for row in rows), default=0.0)
    total_signals = sum(to_int(row.get("signals")) for row in rows)
    return {
        "positive_products": positive_products,
        "worst_cum_net_pct": round(worst, 4),
        "signals": total_signals,
    }


def reclaim_sweep_summary() -> dict[str, Any]:
    rows = load_csv(RECLAIM_SWEEP_PATH)
    if not rows:
        return {"best_positive_products": 0, "best_cumulative_net_pct": 0.0}
    first = rows[0]
    return {
        "best_positive_products": to_int(first.get("positive_products")),
        "best_cumulative_net_pct": round(to_float(first.get("cumulative_net_pct")), 4),
        "best_config": str(first.get("config") or ""),
    }


def build_payload() -> dict[str, Any]:
    return {
        "rsi_products": long_only_rsi_rows(),
        "tactics": tactics_summary(),
        "piranha_candidates": piranha_candidate_rows(),
        "live_piranha": live_piranha_rows(),
        "flush_reclaim": negative_tactic_summary(RECLAIM_PATH),
        "pullback_resume": negative_tactic_summary(PULLBACK_PATH),
        "reclaim_sweep": reclaim_sweep_summary(),
    }


def write_reports(payload: dict[str, Any], *, md_path: Path = MD_PATH, json_path: Path = JSON_PATH) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    tactics = payload["tactics"]
    lines = [
        "# Coinbase Spot Long-Only Opportunity Board",
        "",
        "## Current Read",
        "",
        "- Deployable now means long-only and fee-aware on Coinbase spot.",
        "- Deprioritized means current tests are negative after fees.",
        "- Research-only means useful for understanding behavior but not directly deployable under spot constraints.",
        "",
        "## Best Deployable Families",
        "",
        f"- `maker_scavenger`: best benchmark product `{tactics.get('maker_scavenger', {}).get('best_product_id', '')}` with `${tactics.get('maker_scavenger', {}).get('realized_net_usd', 0.0):.4f}` over `{tactics.get('maker_scavenger', {}).get('trades', 0)}` closes.",
        f"- Long-only RSI pack still has positive live product rows led by `{payload['rsi_products'][0]['product_id'] if payload['rsi_products'] else ''}`.",
        "",
        "## Long-Only RSI Products",
        "",
        "| Product | Net $ | Closes | Lane Count | Positive Lanes | Best Lane |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["rsi_products"][:10]:
        lines.append(
            "| {product_id} | {realized_net_usd:.4f} | {closes} | {lane_count} | {positive_lanes} | {best_lane_name} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Maker / Piranha Candidate Benchmarks",
            "",
            "| Product | Sim PnL $ | Closes | Median Hold (m) | Buy Step | Target |",
            "| --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in payload["piranha_candidates"][:10]:
        lines.append(
            "| {product_id} | {sim_pnl:.4f} | {closes} | {median_hold_minutes:.1f} | {buy_step} | {target} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Live Piranha Lanes",
            "",
            "| Product | Realized $ | Closes | Cash $ | Inventory Units | Open Lots | PID |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["live_piranha"]:
        lines.append(
            "| {product_id} | {realized_net_usd:.4f} | {realized_closes} | {cash_usd:.4f} | {inventory_units:.6f} | {open_lots} | {pid} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Deprioritized Right Now",
            "",
            f"- `flush_reclaim`: `{payload['flush_reclaim']['positive_products']}` positive products in current board; worst cumulative net `{payload['flush_reclaim']['worst_cum_net_pct']:.4f}%`.",
            f"- `pullback_resume`: `{payload['pullback_resume']['positive_products']}` positive products in current board; worst cumulative net `{payload['pullback_resume']['worst_cum_net_pct']:.4f}%`.",
            f"- reclaim sweep: best config still had `{payload['reclaim_sweep']['best_positive_products']}` positive products and `{payload['reclaim_sweep']['best_cumulative_net_pct']:.4f}%` cumulative net.",
            f"- `relative_strength_rotator`: benchmark result `{tactics.get('relative_strength_rotator', {}).get('realized_net_usd', 0.0):.4f}`.",
            f"- `pump_rider_breakout`: benchmark result `{tactics.get('pump_rider_breakout', {}).get('realized_net_usd', 0.0):.4f}`.",
            "",
            "## Recommendation",
            "",
            "- Near-term deployable focus: long-only RSI names plus maker/piranha inventory harvesting.",
            "- Near-term research focus: product selector and portfolio governor for the long-only families that are still positive after fees.",
            "- Do not spend the next chunk of time on reclaim variants unless the fee structure or event gate changes materially.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_reports(payload)
    print(json.dumps({"md_path": str(MD_PATH), "json_path": str(JSON_PATH), "payload": payload}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
