#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_coinbase_burst_shadow_scoreboard as burst_scoreboard
import build_coinbase_experimental_shadow_scoreboard as experimental_scoreboard
import build_coinbase_spot_rsi_scoreboard as rsi_scoreboard


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "penetration_lattice_runner_registry.json"
REPORTS = ROOT / "reports"
CSV_PATH = REPORTS / "coinbase_spot_cross_asset_leaderboard.csv"
PRODUCTS_CSV_PATH = REPORTS / "coinbase_spot_cross_asset_products.csv"
FAMILIES_CSV_PATH = REPORTS / "coinbase_spot_cross_asset_families.csv"
MD_PATH = REPORTS / "coinbase_spot_cross_asset_leaderboard.md"


def load_json(path: Path) -> Any:
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


def read_product_metadata(repo_root: Path, state_path_text: str) -> tuple[str, int]:
    state_path = repo_root / state_path_text
    if not state_path.exists():
        return "", 0
    payload = load_json(state_path)
    engine = payload.get("engine") or payload.get("state") or {}
    product_id = str(engine.get("product_id") or "").strip().upper()
    products = [str(item).strip().upper() for item in (engine.get("products") or []) if str(item or "").strip()]
    if not product_id and len(products) == 1:
        product_id = products[0]
    products_tracked = len(products) if products else (1 if product_id else 0)
    return product_id, products_tracked


def normalize_row(
    row: dict[str, Any],
    *,
    family: str,
    repo_root: Path,
) -> dict[str, Any]:
    state_path = str(row.get("state_path") or "")
    product_id = str(row.get("product_id") or "").strip().upper()
    products_tracked = to_int(row.get("products_tracked"))
    if state_path and (not product_id or products_tracked == 0):
        derived_product_id, derived_products_tracked = read_product_metadata(repo_root, state_path)
        if not product_id:
            product_id = derived_product_id
        if products_tracked == 0:
            products_tracked = derived_products_tracked

    closes = to_int(row.get("closes") if "closes" in row else row.get("realized_closes"))
    wins_known = row.get("wins") not in (None, "")
    losses_known = row.get("losses") not in (None, "")
    wins = to_int(row.get("wins")) if wins_known else ""
    losses = to_int(row.get("losses")) if losses_known else ""
    realized_net_usd = to_float(row.get("realized_net_usd"))
    if closes > 0 and wins_known and not losses_known:
        losses = max(0, closes - to_int(wins))
        losses_known = True

    win_rate_known = row.get("win_rate") not in (None, "")
    win_rate = to_float(row.get("win_rate")) if win_rate_known else ""
    if closes > 0 and not win_rate_known and wins_known:
        win_rate = (to_int(wins) / closes) * 100.0
        win_rate_known = True

    avg_pnl_per_close = to_float(row.get("avg_pnl_per_close"))
    if closes > 0 and avg_pnl_per_close == 0.0:
        avg_pnl_per_close = realized_net_usd / closes

    scope = "single_product" if product_id and products_tracked <= 1 else "multi_product"
    verdict = str(row.get("readiness_verdict") or "")
    if realized_net_usd > 0 and closes >= 10:
        status = "validated"
    elif realized_net_usd > 0:
        status = "incubating"
    else:
        status = "underwater"

    return {
        "family": family,
        "lane_name": str(row.get("lane_name") or ""),
        "style": str(row.get("style") or verdict or family),
        "product_id": product_id,
        "scope": scope,
        "status": status,
        "realized_net_usd": round(realized_net_usd, 4),
        "closes": closes,
        "wins": to_int(wins) if wins_known else "",
        "losses": to_int(losses) if losses_known else "",
        "win_rate": round(float(win_rate), 2) if win_rate_known else "",
        "avg_pnl_per_close": round(avg_pnl_per_close, 4),
        "cash_usd": round(to_float(row.get("cash_usd")), 4),
        "fees_usd": round(to_float(row.get("fees_usd") if "fees_usd" in row else row.get("total_fees")), 4),
        "open_count": to_int(row.get("open_count") if "open_count" in row else row.get("in_position")),
        "products_tracked": products_tracked,
        "signals_generated": to_int(row.get("signals_generated")),
        "heartbeat_age_seconds": row.get("heartbeat_age_seconds", ""),
        "note": str(row.get("note") or ""),
        "state_path": state_path,
    }


def build_lane_rows(
    *,
    registry_path: Path = REGISTRY_PATH,
    readiness_paths: list[Path] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    repo_root = registry_path.resolve().parent.parent
    now_utc = now or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for row in rsi_scoreboard.build_rows(
        registry_path=registry_path,
        readiness_paths=readiness_paths,
        now=now_utc,
    ):
        if str(row.get("lane_name") or "") == "TOTAL":
            continue
        rows.append(normalize_row(row, family="rsi", repo_root=repo_root))

    for row in burst_scoreboard.build_rows(registry_path=registry_path, now=now_utc):
        if str(row.get("lane_name") or "") == "TOTAL":
            continue
        rows.append(normalize_row(row, family="burst", repo_root=repo_root))

    for row in experimental_scoreboard.build_rows(registry_path=registry_path, now=now_utc):
        if str(row.get("lane_name") or "") == "TOTAL":
            continue
        rows.append(normalize_row(row, family="experimental", repo_root=repo_root))

    rows.sort(
        key=lambda row: (
            0 if row["scope"] == "single_product" else 1,
            -float(row["realized_net_usd"]),
            -int(row["closes"]),
            str(row["lane_name"]),
        )
    )
    return rows


def build_product_rows(lane_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    for row in lane_rows:
        product_id = str(row.get("product_id") or "")
        if row.get("scope") != "single_product" or not product_id:
            continue
        agg = aggregates.setdefault(
            product_id,
            {
                "product_id": product_id,
                "lane_count": 0,
                "positive_lanes": 0,
                "realized_net_usd": 0.0,
                "closes": 0,
                "cash_usd": 0.0,
                "fees_usd": 0.0,
                "open_count": 0,
                "families": set(),
                "best_lane_name": "",
                "best_lane_family": "",
                "best_lane_net_usd": float("-inf"),
            },
        )
        agg["lane_count"] += 1
        agg["positive_lanes"] += 1 if to_float(row.get("realized_net_usd")) > 0 else 0
        agg["realized_net_usd"] += to_float(row.get("realized_net_usd"))
        agg["closes"] += to_int(row.get("closes"))
        agg["cash_usd"] += to_float(row.get("cash_usd"))
        agg["fees_usd"] += to_float(row.get("fees_usd"))
        agg["open_count"] += to_int(row.get("open_count"))
        agg["families"].add(str(row.get("family") or ""))
        if to_float(row.get("realized_net_usd")) > float(agg["best_lane_net_usd"]):
            agg["best_lane_name"] = str(row.get("lane_name") or "")
            agg["best_lane_family"] = str(row.get("family") or "")
            agg["best_lane_net_usd"] = to_float(row.get("realized_net_usd"))

    rows: list[dict[str, Any]] = []
    for agg in aggregates.values():
        closes = int(agg["closes"])
        realized = float(agg["realized_net_usd"])
        rows.append(
            {
                "product_id": str(agg["product_id"]),
                "lane_count": int(agg["lane_count"]),
                "positive_lanes": int(agg["positive_lanes"]),
                "realized_net_usd": round(realized, 4),
                "closes": closes,
                "avg_pnl_per_close": round(realized / closes, 4) if closes else 0.0,
                "cash_usd": round(float(agg["cash_usd"]), 4),
                "fees_usd": round(float(agg["fees_usd"]), 4),
                "open_count": int(agg["open_count"]),
                "families": ",".join(sorted(item for item in agg["families"] if item)),
                "best_lane_name": str(agg["best_lane_name"]),
                "best_lane_family": str(agg["best_lane_family"]),
                "best_lane_net_usd": round(float(agg["best_lane_net_usd"]), 4),
            }
        )

    rows.sort(key=lambda row: (-float(row["realized_net_usd"]), -int(row["closes"]), str(row["product_id"])))
    return rows


def build_family_rows(lane_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    for row in lane_rows:
        family = str(row.get("family") or "")
        agg = aggregates.setdefault(
            family,
            {
                "family": family,
                "lane_count": 0,
                "single_product_lanes": 0,
                "multi_product_lanes": 0,
                "realized_net_usd": 0.0,
                "closes": 0,
                "open_count": 0,
                "products_tracked_sum": 0,
            },
        )
        agg["lane_count"] += 1
        agg["single_product_lanes"] += 1 if row.get("scope") == "single_product" else 0
        agg["multi_product_lanes"] += 1 if row.get("scope") == "multi_product" else 0
        agg["realized_net_usd"] += to_float(row.get("realized_net_usd"))
        agg["closes"] += to_int(row.get("closes"))
        agg["open_count"] += to_int(row.get("open_count"))
        agg["products_tracked_sum"] += max(1, to_int(row.get("products_tracked")))

    rows: list[dict[str, Any]] = []
    for agg in aggregates.values():
        closes = int(agg["closes"])
        realized = float(agg["realized_net_usd"])
        rows.append(
            {
                "family": str(agg["family"]),
                "lane_count": int(agg["lane_count"]),
                "single_product_lanes": int(agg["single_product_lanes"]),
                "multi_product_lanes": int(agg["multi_product_lanes"]),
                "realized_net_usd": round(realized, 4),
                "closes": closes,
                "avg_pnl_per_close": round(realized / closes, 4) if closes else 0.0,
                "open_count": int(agg["open_count"]),
                "products_tracked_sum": int(agg["products_tracked_sum"]),
            }
        )
    rows.sort(key=lambda row: (-float(row["realized_net_usd"]), str(row["family"])))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def write_markdown(
    lane_rows: list[dict[str, Any]],
    product_rows: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    *,
    md_path: Path = MD_PATH,
) -> None:
    single_product_rows = [row for row in lane_rows if row["scope"] == "single_product"]
    multi_product_rows = [row for row in lane_rows if row["scope"] == "multi_product"]
    lines = [
        "# Coinbase Spot Cross-Asset Leaderboard",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Single-product lanes: {len(single_product_rows)}",
        f"- Multi-product lanes: {len(multi_product_rows)}",
        "",
        "## Product Summary",
        "",
    ]
    lines.extend(
        markdown_table(
            ["Product", "Net $", "Closes", "Lanes", "Positive", "Families", "Best Lane"],
            [
                [
                    str(row["product_id"]),
                    f"{float(row['realized_net_usd']):.4f}",
                    str(row["closes"]),
                    str(row["lane_count"]),
                    str(row["positive_lanes"]),
                    str(row["families"]),
                    f"{row['best_lane_name']} ({row['best_lane_family']})",
                ]
                for row in product_rows
            ],
        )
        if product_rows
        else ["No single-product Coinbase spot lanes found."]
    )
    lines.extend(
        [
            "",
            "## Single-Product Lane Leaderboard",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            ["Lane", "Family", "Style", "Product", "Status", "Net $", "Closes", "WR %", "Avg/Close $", "Cash $"],
            [
                [
                    str(row["lane_name"]),
                    str(row["family"]),
                    str(row["style"]),
                    str(row["product_id"]),
                    str(row["status"]),
                    f"{float(row['realized_net_usd']):.4f}",
                    str(row["closes"]),
                    str(row["win_rate"]) if str(row["win_rate"]) else "-",
                    f"{float(row['avg_pnl_per_close']):.4f}",
                    f"{float(row['cash_usd']):.4f}",
                ]
                for row in single_product_rows
            ],
        )
        if single_product_rows
        else ["No single-product lanes found."]
    )
    lines.extend(
        [
            "",
            "## Multi-Product Lane Leaderboard",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            ["Lane", "Family", "Style", "Net $", "Closes", "WR %", "Products", "Open", "Cash $"],
            [
                [
                    str(row["lane_name"]),
                    str(row["family"]),
                    str(row["style"]),
                    f"{float(row['realized_net_usd']):.4f}",
                    str(row["closes"]),
                    str(row["win_rate"]) if str(row["win_rate"]) else "-",
                    str(row["products_tracked"]),
                    str(row["open_count"]),
                    f"{float(row['cash_usd']):.4f}",
                ]
                for row in multi_product_rows
            ],
        )
        if multi_product_rows
        else ["No multi-product lanes found."]
    )
    lines.extend(
        [
            "",
            "## Family Summary",
            "",
        ]
    )
    lines.extend(
        markdown_table(
            ["Family", "Net $", "Closes", "Lanes", "Single", "Multi", "Open"],
            [
                [
                    str(row["family"]),
                    f"{float(row['realized_net_usd']):.4f}",
                    str(row["closes"]),
                    str(row["lane_count"]),
                    str(row["single_product_lanes"]),
                    str(row["multi_product_lanes"]),
                    str(row["open_count"]),
                ]
                for row in family_rows
            ],
        )
        if family_rows
        else ["No family summary rows found."]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_reports(
    *,
    registry_path: Path = REGISTRY_PATH,
    readiness_paths: list[Path] | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    lane_rows = build_lane_rows(registry_path=registry_path, readiness_paths=readiness_paths, now=now)
    product_rows = build_product_rows(lane_rows)
    family_rows = build_family_rows(lane_rows)
    return lane_rows, product_rows, family_rows


def main() -> int:
    lane_rows, product_rows, family_rows = build_reports()
    if not lane_rows:
        raise SystemExit("no Coinbase spot lanes found")
    write_csv(CSV_PATH, lane_rows)
    write_csv(PRODUCTS_CSV_PATH, product_rows)
    write_csv(FAMILIES_CSV_PATH, family_rows)
    write_markdown(lane_rows, product_rows, family_rows)
    print(
        json.dumps(
            {
                "lane_csv_path": str(CSV_PATH),
                "products_csv_path": str(PRODUCTS_CSV_PATH),
                "families_csv_path": str(FAMILIES_CSV_PATH),
                "md_path": str(MD_PATH),
                "lane_rows": lane_rows,
                "product_rows": product_rows,
                "family_rows": family_rows,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
