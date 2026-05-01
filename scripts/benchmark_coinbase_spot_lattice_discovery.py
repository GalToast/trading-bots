#!/usr/bin/env python3
"""
Broaden Coinbase spot lattice discovery across the repo's existing ratio families.

This takes the explicit cross-asset families from `multi_asset_ratio_lattice.py`,
then runs the attractor lattice through the same long-only spot cost lens used in
the current ratio audit work.

Outputs:
- reports/coinbase_spot_lattice_discovery.csv
- reports/coinbase_spot_lattice_discovery.md
- reports/coinbase_spot_lattice_discovery.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient
from ratio_lattice_60d_validation import (  # type: ignore
    SYMBOL_TO_PRODUCT,
    build_price_map,
    build_ratio_series,
    fetch_candles,
    find_attractors_kde,
    run_attractor_lattice,
)


ALT_SYMBOLS = ["NOM", "GHST", "SUP", "RAVE", "BAL", "A8", "CFG", "IOTX", "TRU"]
BENCHMARK_SYMBOLS = ["BTC", "ETH"]
ALL_SYMBOLS = ALT_SYMBOLS + BENCHMARK_SYMBOLS

RATIO_GROUPS: dict[str, list[tuple[str, str]]] = {
    "fibonacci_trio": [("NOM", "GHST"), ("NOM", "SUP"), ("GHST", "SUP")],
    "supertrend_group": [("RAVE", "BAL"), ("RAVE", "IOTX"), ("BAL", "IOTX")],
    "momentum_pair": [("A8", "CFG")],
    "alt_vs_btc": [(symbol, "BTC") for symbol in ALT_SYMBOLS],
    "alt_vs_eth": [(symbol, "ETH") for symbol in ALT_SYMBOLS],
}

DEFAULT_PROFIT_THRESHOLDS = [1.002, 1.004, 1.008, 1.012]
DEFAULT_MAX_LEVELS = [3, 5, 8, 10]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Broaden Coinbase spot lattice discovery with the cost-aware ratio pipeline.")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--position-size", type=float, default=0.01)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--profit-thresholds", nargs="*", type=float, default=list(DEFAULT_PROFIT_THRESHOLDS))
    parser.add_argument("--max-levels-grid", nargs="*", type=int, default=list(DEFAULT_MAX_LEVELS))
    parser.add_argument("--fee-bps-per-leg", type=float, default=40.0)
    parser.add_argument("--csv-path", default=str(ROOT / "reports" / "coinbase_spot_lattice_discovery.csv"))
    parser.add_argument("--md-path", default=str(ROOT / "reports" / "coinbase_spot_lattice_discovery.md"))
    parser.add_argument("--json-path", default=str(ROOT / "reports" / "coinbase_spot_lattice_discovery.json"))
    return parser.parse_args()


def explicit_pairs() -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    for group_name, group_pairs in RATIO_GROUPS.items():
        for symbol_a, symbol_b in group_pairs:
            pairs.append((group_name, symbol_a, symbol_b))
    return pairs


def build_quote_snapshot(client: CoinbaseAdvancedClient, symbols: list[str]) -> dict[str, dict[str, float]]:
    product_ids = [SYMBOL_TO_PRODUCT[symbol] for symbol in sorted(set(symbols))]
    raw = client.best_bid_ask(product_ids)
    books = raw.get("pricebooks") or []
    snapshot: dict[str, dict[str, float]] = {}
    for product_id in product_ids:
        book = next((row for row in books if row.get("product_id") == product_id), None)
        if not book:
            continue
        bid = float(book["bids"][0]["price"])
        ask = float(book["asks"][0]["price"])
        snapshot[product_id] = {
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2.0,
            "spread_abs": ask - bid,
            "spread_bps_mid": ((ask - bid) / ((bid + ask) / 2.0)) * 10000.0 if ask > 0 and bid > 0 else 0.0,
        }
    return snapshot


def synthetic_round_trip_cost_fraction(
    *,
    bid_a: float,
    ask_a: float,
    bid_b: float,
    ask_b: float,
    fee_bps_per_leg: float,
) -> float:
    fee_rate = fee_bps_per_leg / 10000.0
    usd_after_sell_b = bid_b * (1.0 - fee_rate)
    units_a = (usd_after_sell_b / ask_a) * (1.0 - fee_rate)
    usd_after_sell_a = (units_a * bid_a) * (1.0 - fee_rate)
    final_b_units = (usd_after_sell_a / ask_b) * (1.0 - fee_rate)
    return max(0.0, 1.0 - final_b_units)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "group",
        "pair",
        "symbol_a",
        "symbol_b",
        "profit_threshold",
        "max_levels",
        "gross_realized_pnl_den",
        "net_realized_pnl_den",
        "total_closes",
        "closure_rate",
        "max_open_seen",
        "avg_gross_per_close_den",
        "avg_net_per_close_den",
        "breakeven_round_trip_cost_bps",
        "pair_spread_cost_bps",
        "num_attractors",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_markdown(
    path: Path,
    *,
    fee_bps_per_leg: float,
    ranked_rows: list[dict[str, Any]],
    group_best_rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Lattice Discovery",
        "",
        "- This broadens the long-only spot lattice search beyond the current short list by pushing the repo's existing cross-asset ratio families through the same attractor-lattice and synthetic spot-cost lens.",
        f"- Cost model: live Coinbase quote snapshot with `{fee_bps_per_leg:.1f}` bps per spot leg.",
        "",
        "## Top Discovery Rows",
        "",
        "| Rank | Pair | Group | Tuned Shape | Net PnL | Gross/Close | Breakeven RT Cost | Closes |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for index, row in enumerate(ranked_rows[:12], 1):
        lines.append(
            f"| {index} | `{row['pair']}` | `{row['group']}` | `thr={row['profit_threshold']:.3f} levels={row['max_levels']}` | "
            f"`{row['net_realized_pnl_den']:+.6f}` | `{row['avg_gross_per_close_den']:.6f}` | "
            f"`{row['breakeven_round_trip_cost_bps']:.2f}bps` | `{row['total_closes']}` |"
        )

    lines.extend(["", "## Best Per Group", "", "| Group | Best Pair | Tuned Shape | Net PnL | Breakeven RT Cost |", "| --- | --- | --- | ---: | ---: |"])
    for row in group_best_rows:
        lines.append(
            f"| `{row['group']}` | `{row['pair']}` | `thr={row['profit_threshold']:.3f} levels={row['max_levels']}` | "
            f"`{row['net_realized_pnl_den']:+.6f}` | `{row['breakeven_round_trip_cost_bps']:.2f}bps` |"
        )

    lines.extend(["", "## Read", ""])
    for row in ranked_rows[:6]:
        lines.append(
            f"- `{row['pair']}` (`{row['group']}`): `{row['net_realized_pnl_den']:+.6f}` net with "
            f"`{row['breakeven_round_trip_cost_bps']:.2f}bps` per-close friction budget."
        )
    lines.extend(
        [
            "- Use this as a discovery screen, not promotion proof. Anything interesting here still has to clear the stress, ceiling, and capital-coupling layers before it becomes a forward-shadow candidate.",
            "- If intra-alt or cluster pairs show up near the top despite wider spreads, they are the best evidence that spot lattice science is broader than just alt-vs-BTC and alt-vs-ETH relationships.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    now_ts = int(time.time())
    start_ts = now_ts - int(args.days) * 86400

    price_maps: dict[str, dict[int, float]] = {}
    candle_counts: dict[str, int] = {}
    for symbol in ALL_SYMBOLS:
        candles = fetch_candles(client, SYMBOL_TO_PRODUCT[symbol], start_ts, now_ts)
        price_maps[symbol] = build_price_map(candles)
        candle_counts[symbol] = len(candles)

    quotes = build_quote_snapshot(client, ALL_SYMBOLS)

    all_rows: list[dict[str, Any]] = []

    for group_name, symbol_a, symbol_b in explicit_pairs():
        product_a = SYMBOL_TO_PRODUCT[symbol_a]
        product_b = SYMBOL_TO_PRODUCT[symbol_b]
        quote_a = quotes.get(product_a)
        quote_b = quotes.get(product_b)
        if not quote_a or not quote_b:
            continue

        series = build_ratio_series(price_maps[symbol_a], price_maps[symbol_b])
        if not series:
            continue

        attractors = find_attractors_kde(series)
        if not attractors:
            continue

        pair_cost_fraction = synthetic_round_trip_cost_fraction(
            bid_a=quote_a["bid"],
            ask_a=quote_a["ask"],
            bid_b=quote_b["bid"],
            ask_b=quote_b["ask"],
            fee_bps_per_leg=float(args.fee_bps_per_leg),
        )

        for profit_threshold in args.profit_thresholds:
            for max_levels in args.max_levels_grid:
                result = run_attractor_lattice(
                    series,
                    attractors,
                    position_size=float(args.position_size),
                    profit_threshold=float(profit_threshold),
                    max_concurrent=int(args.max_concurrent),
                    max_levels=int(max_levels),
                )
                closes = int(result["total_closes"])
                gross = float(result["realized_pnl"])
                cost_per_close = float(args.position_size) * pair_cost_fraction
                net = gross - closes * cost_per_close
                avg_gross = gross / closes if closes else 0.0
                avg_net = net / closes if closes else 0.0
                all_rows.append(
                    {
                        "group": group_name,
                        "pair": f"{symbol_a}/{symbol_b}",
                        "symbol_a": symbol_a,
                        "symbol_b": symbol_b,
                        "profit_threshold": float(profit_threshold),
                        "max_levels": int(max_levels),
                        "gross_realized_pnl_den": gross,
                        "net_realized_pnl_den": net,
                        "total_closes": closes,
                        "closure_rate": float(result["closure_rate"]),
                        "max_open_seen": int(result["max_open_seen"]),
                        "avg_gross_per_close_den": avg_gross,
                        "avg_net_per_close_den": avg_net,
                        "breakeven_round_trip_cost_bps": (avg_gross / float(args.position_size)) * 10000.0 if closes else 0.0,
                        "pair_spread_cost_bps": pair_cost_fraction * 10000.0,
                        "num_attractors": len(attractors),
                    }
                )

    ranked_rows = sorted(all_rows, key=lambda row: row["net_realized_pnl_den"], reverse=True)
    pair_best_rows: list[dict[str, Any]] = []
    for pair in sorted(set(row["pair"] for row in ranked_rows)):
        pair_rows = [row for row in ranked_rows if row["pair"] == pair]
        pair_best_rows.append(max(pair_rows, key=lambda row: row["net_realized_pnl_den"]))
    pair_best_rows.sort(key=lambda row: row["net_realized_pnl_den"], reverse=True)

    group_best_rows: list[dict[str, Any]] = []
    for group_name in sorted(set(row["group"] for row in pair_best_rows)):
        group_rows = [row for row in pair_best_rows if row["group"] == group_name]
        group_best_rows.append(max(group_rows, key=lambda row: row["net_realized_pnl_den"]))
    group_best_rows.sort(key=lambda row: row["net_realized_pnl_den"], reverse=True)

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    json_path = Path(args.json_path)
    write_csv(csv_path, pair_best_rows)
    write_markdown(md_path, fee_bps_per_leg=float(args.fee_bps_per_leg), ranked_rows=pair_best_rows, group_best_rows=group_best_rows)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "run_params": {
                    "days": args.days,
                    "position_size": args.position_size,
                    "max_concurrent": args.max_concurrent,
                    "profit_thresholds": args.profit_thresholds,
                    "max_levels_grid": args.max_levels_grid,
                    "fee_bps_per_leg": args.fee_bps_per_leg,
                },
                "candle_counts": candle_counts,
                "pair_best_rows": pair_best_rows,
                "group_best_rows": group_best_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"CSV:  {csv_path}")
    print(f"MD:   {md_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
