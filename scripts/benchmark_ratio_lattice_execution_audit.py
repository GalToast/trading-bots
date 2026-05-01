#!/usr/bin/env python3
"""
Benchmark ratio-lattice execution realism on top of the current 60d winners.

This extends the existing attractor-lattice validation by:
- sweeping exit thresholds and attractor counts on the strongest pairs
- estimating synthetic round-trip spot cost from live Coinbase quotes
- ranking the gross and cost-adjusted winners side by side

Outputs:
- reports/ratio_lattice_execution_audit.csv
- reports/ratio_lattice_execution_audit.md
- reports/ratio_lattice_execution_audit.json
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


DEFAULT_PAIRS: tuple[tuple[str, str], ...] = (
    ("CFG", "BTC"),
    ("CFG", "ETH"),
    ("BAL", "ETH"),
    ("BAL", "BTC"),
    ("NOM", "BTC"),
    ("ETH", "BTC"),
)
DEFAULT_PROFIT_THRESHOLDS: tuple[float, ...] = (1.002, 1.003, 1.004, 1.006, 1.008, 1.010, 1.012)
DEFAULT_MAX_LEVELS: tuple[int, ...] = (3, 5, 8, 10)
DEFAULT_FEE_SCENARIOS_BPS: tuple[float, ...] = (0.0, 25.0, 40.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark ratio-lattice execution realism with synthetic spot costs.")
    parser.add_argument("--days", type=int, default=60, help="Lookback days for candle fetch")
    parser.add_argument("--position-size", type=float, default=0.01, help="Denominator-asset size per lattice open")
    parser.add_argument("--max-concurrent", type=int, default=5, help="Max concurrent positions")
    parser.add_argument(
        "--pairs",
        nargs="*",
        default=[f"{a}/{b}" for a, b in DEFAULT_PAIRS],
        help="Ratio pairs to test as A/B labels",
    )
    parser.add_argument(
        "--profit-thresholds",
        nargs="*",
        type=float,
        default=list(DEFAULT_PROFIT_THRESHOLDS),
        help="Exit multipliers to test",
    )
    parser.add_argument(
        "--max-levels-grid",
        nargs="*",
        type=int,
        default=list(DEFAULT_MAX_LEVELS),
        help="Attractor counts to test",
    )
    parser.add_argument(
        "--fee-scenarios-bps",
        nargs="*",
        type=float,
        default=list(DEFAULT_FEE_SCENARIOS_BPS),
        help="Per-spot-leg fee scenarios in bps; total ratio round trip uses 4 legs",
    )
    parser.add_argument(
        "--csv-path",
        default=str(ROOT / "reports" / "ratio_lattice_execution_audit.csv"),
        help="Output CSV path",
    )
    parser.add_argument(
        "--md-path",
        default=str(ROOT / "reports" / "ratio_lattice_execution_audit.md"),
        help="Output markdown path",
    )
    parser.add_argument(
        "--json-path",
        default=str(ROOT / "reports" / "ratio_lattice_execution_audit.json"),
        help="Output JSON path",
    )
    return parser.parse_args()


def parse_pair_labels(labels: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for label in labels:
        parts = label.upper().split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid pair label: {label}")
        pairs.append((parts[0], parts[1]))
    return pairs


def fetch_price_maps(client: CoinbaseAdvancedClient, symbols: set[str], start_ts: int, end_ts: int) -> tuple[dict[str, dict[int, float]], dict[str, int]]:
    price_maps: dict[str, dict[int, float]] = {}
    candle_counts: dict[str, int] = {}
    for symbol in sorted(symbols):
        product_id = SYMBOL_TO_PRODUCT[symbol]
        candles = fetch_candles(client, product_id, start_ts, end_ts)
        price_maps[symbol] = build_price_map(candles)
        candle_counts[symbol] = len(candles)
    return price_maps, candle_counts


def synthetic_round_trip_cost_fraction(
    *,
    bid_a: float,
    ask_a: float,
    bid_b: float,
    ask_b: float,
    fee_bps_per_leg: float,
) -> float:
    """
    Exact current synthetic A/B cost using USD spot legs.

    Start with 1 unit of denominator asset B:
    1. sell B -> USD at bid_b
    2. buy A with USD at ask_a
    3. sell A -> USD at bid_a
    4. buy B with USD at ask_b
    """
    fee_rate = fee_bps_per_leg / 10000.0

    usd_after_sell_b = bid_b * (1.0 - fee_rate)
    units_a = (usd_after_sell_b / ask_a) * (1.0 - fee_rate)
    usd_after_sell_a = (units_a * bid_a) * (1.0 - fee_rate)
    final_b_units = (usd_after_sell_a / ask_b) * (1.0 - fee_rate)
    return max(0.0, 1.0 - final_b_units)


def synthetic_mid_round_trip_cost_fraction(
    *,
    mid_a: float,
    mid_b: float,
    fee_bps_per_leg: float,
) -> float:
    fee_rate = fee_bps_per_leg / 10000.0
    usd_after_sell_b = mid_b * (1.0 - fee_rate)
    units_a = (usd_after_sell_b / mid_a) * (1.0 - fee_rate)
    usd_after_sell_a = (units_a * mid_a) * (1.0 - fee_rate)
    final_b_units = (usd_after_sell_a / mid_b) * (1.0 - fee_rate)
    return max(0.0, 1.0 - final_b_units)


def build_quote_snapshot(client: CoinbaseAdvancedClient, symbols: set[str]) -> dict[str, dict[str, float]]:
    product_ids = [SYMBOL_TO_PRODUCT[symbol] for symbol in sorted(symbols)]
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


def rank_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: row.get(key, float("-inf")), reverse=True)


def write_csv(path: Path, rows: list[dict[str, Any]], fee_scenarios_bps: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "pair",
        "symbol_a",
        "symbol_b",
        "profit_threshold",
        "max_levels",
        "attractors_available",
        "gross_realized_pnl_den",
        "total_closes",
        "closure_rate",
        "max_open_seen",
        "avg_gross_pnl_per_close_den",
        "spread_only_cost_per_close_den",
        "spread_only_net_pnl_den",
        "pair_spread_cost_bps",
    ]
    for fee_bps in fee_scenarios_bps:
        tag = str(fee_bps).replace(".", "_")
        columns.extend(
            [
                f"fee_{tag}_cost_per_close_den",
                f"fee_{tag}_cost_fraction_bps",
                f"fee_{tag}_net_pnl_den",
                f"fee_{tag}_avg_net_per_close_den",
            ]
        )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_markdown(
    path: Path,
    *,
    now_ts: int,
    days: int,
    position_size: float,
    rows: list[dict[str, Any]],
    fee_scenarios_bps: list[float],
    quote_snapshot: dict[str, dict[str, float]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gross_rank = rank_rows(rows, "gross_realized_pnl_den")[:8]
    fee_tag = str(max(fee_scenarios_bps)).replace(".", "_")
    cost_rank = rank_rows(rows, f"fee_{fee_tag}_net_pnl_den")[:8]
    pair_best_rows: list[dict[str, Any]] = []
    pair_baselines: dict[str, dict[str, Any]] = {}
    min_threshold = min(row["profit_threshold"] for row in rows)

    for pair in sorted(set(row["pair"] for row in rows)):
        pair_rows = [row for row in rows if row["pair"] == pair]
        pair_best_rows.append(max(pair_rows, key=lambda row: row[f"fee_{fee_tag}_net_pnl_den"]))

        baseline_candidates = [row for row in pair_rows if row["profit_threshold"] == min_threshold]
        if baseline_candidates:
            pair_baselines[pair] = max(baseline_candidates, key=lambda row: row["max_levels"])

    lines = [
        "# Ratio Lattice Execution Audit",
        "",
        f"- Window: `{days}d` of `FIVE_MINUTE` candles ending at unix `{now_ts}`",
        f"- Position size: `{position_size}` denominator units per open",
        "- Relationship object is traded synthetically via USD spot legs, not assumed direct cross pairs.",
        "- Execution realism is a quote-snapshot audit, not a historical L2 replay: spread uses live Coinbase best bid/ask at report time and fees are scenario ladders.",
        "- Gross lattice logic is the existing attractor lattice from `scripts/ratio_lattice_60d_validation.py`.",
        "",
        "## Quote Snapshot",
        "",
        "| Product | Bid | Ask | Spread | Spread (bps mid) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for product_id, book in sorted(quote_snapshot.items()):
        lines.append(
            f"| `{product_id}` | `{book['bid']:.8f}` | `{book['ask']:.8f}` | `{book['spread_abs']:.8f}` | `{book['spread_bps_mid']:.2f}` |"
        )

    lines.extend(
        [
            "",
            "## Best Gross Rows",
            "",
            "| Rank | Pair | Profit Threshold | Max Levels | Gross PnL | Closes | Avg/Close | Spread-Only Net |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for index, row in enumerate(gross_rank, 1):
        lines.append(
            f"| {index} | `{row['pair']}` | `{row['profit_threshold']:.3f}` | `{row['max_levels']}` | "
            f"`{row['gross_realized_pnl_den']:+.6f}` | `{row['total_closes']}` | `{row['avg_gross_pnl_per_close_den']:+.6f}` | "
            f"`{row['spread_only_net_pnl_den']:+.6f}` |"
        )

    max_fee_bps = max(fee_scenarios_bps)
    lines.extend(
        [
            "",
            f"## Best Cost-Adjusted Rows (`{max_fee_bps:.1f}` bps per spot leg)",
            "",
            "| Rank | Pair | Profit Threshold | Max Levels | Net PnL | Avg Net/Close | Cost/Close | Closes |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for index, row in enumerate(cost_rank, 1):
        cost_key = f"fee_{fee_tag}_cost_per_close_den"
        net_key = f"fee_{fee_tag}_net_pnl_den"
        avg_key = f"fee_{fee_tag}_avg_net_per_close_den"
        lines.append(
            f"| {index} | `{row['pair']}` | `{row['profit_threshold']:.3f}` | `{row['max_levels']}` | "
            f"`{row[net_key]:+.6f}` | `{row[avg_key]:+.6f}` | `{row[cost_key]:.6f}` | `{row['total_closes']}` |"
        )

    lines.extend(
        [
            "",
            "## Pair Reads",
            "",
        ]
    )
    for row in pair_best_rows:
        pair = row["pair"]
        baseline = pair_baselines.get(pair)
        delta = row[f"fee_{fee_tag}_net_pnl_den"] - baseline[f"fee_{fee_tag}_net_pnl_den"] if baseline else 0.0
        baseline_text = ""
        if baseline:
            baseline_text = (
                f" Baseline `thr={baseline['profit_threshold']:.3f} levels={baseline['max_levels']}` "
                f"estimated `{baseline[f'fee_{fee_tag}_net_pnl_den']:+.6f}` net, delta `{delta:+.6f}`."
            )
        lines.append(
            f"- `{pair}`: best gross row is `thr={row['profit_threshold']:.3f} levels={row['max_levels']}` with "
            f"`{row['gross_realized_pnl_den']:+.6f}` gross across `{row['total_closes']}` closes; "
            f"under `{max_fee_bps:.1f}` bps/leg it estimates to `{row[f'fee_{fee_tag}_net_pnl_den']:+.6f}` net."
            f"{baseline_text}"
        )

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- If a pair stays positive only in the gross table but not in the cost-adjusted table, it is a replay curiosity, not a promotion candidate.",
            "- If higher thresholds survive costs better than the baseline `1.002`, the next honest creative variable is pair-specific exit depth, not more pair count.",
            "- If lower `max_levels` beat higher ones after costs, the next honest move is attractor quality concentration rather than denser lattices.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    client = CoinbaseAdvancedClient()
    now_ts = int(time.time())
    start_ts = now_ts - int(args.days) * 86400
    pairs = parse_pair_labels(args.pairs)

    symbols: set[str] = set()
    for symbol_a, symbol_b in pairs:
        symbols.add(symbol_a)
        symbols.add(symbol_b)

    print("=" * 72)
    print("RATIO LATTICE EXECUTION AUDIT")
    print("=" * 72)
    print(f"Pairs: {', '.join(f'{a}/{b}' for a, b in pairs)}")
    print(f"Lookback: {args.days}d")
    print(f"Profit thresholds: {', '.join(f'{x:.3f}' for x in args.profit_thresholds)}")
    print(f"Max levels: {', '.join(str(x) for x in args.max_levels_grid)}")
    print()

    price_maps, candle_counts = fetch_price_maps(client, symbols, start_ts, now_ts)
    quote_snapshot = build_quote_snapshot(client, symbols)

    rows: list[dict[str, Any]] = []
    quote_cost_summary: dict[str, Any] = {}

    for symbol_a, symbol_b in pairs:
        pair = f"{symbol_a}/{symbol_b}"
        product_a = SYMBOL_TO_PRODUCT[symbol_a]
        product_b = SYMBOL_TO_PRODUCT[symbol_b]
        book_a = quote_snapshot.get(product_a)
        book_b = quote_snapshot.get(product_b)
        if not book_a or not book_b:
            print(f"SKIP {pair}: missing quote snapshot")
            continue

        series = build_ratio_series(price_maps[symbol_a], price_maps[symbol_b])
        attractors = find_attractors_kde(series)
        if not series or not attractors:
            print(f"SKIP {pair}: no aligned series or attractors")
            continue

        spread_only_cost_fraction = synthetic_round_trip_cost_fraction(
            bid_a=book_a["bid"],
            ask_a=book_a["ask"],
            bid_b=book_b["bid"],
            ask_b=book_b["ask"],
            fee_bps_per_leg=0.0,
        )
        pair_spread_cost_bps = spread_only_cost_fraction * 10000.0
        quote_cost_summary[pair] = {
            "spread_only_cost_fraction": spread_only_cost_fraction,
            "spread_only_cost_bps": pair_spread_cost_bps,
        }
        for fee_bps in args.fee_scenarios_bps:
            tag = str(fee_bps).replace(".", "_")
            quote_cost_summary[pair][f"fee_{tag}_cost_fraction"] = synthetic_round_trip_cost_fraction(
                bid_a=book_a["bid"],
                ask_a=book_a["ask"],
                bid_b=book_b["bid"],
                ask_b=book_b["ask"],
                fee_bps_per_leg=fee_bps,
            )
            quote_cost_summary[pair][f"fee_{tag}_mid_cost_fraction"] = synthetic_mid_round_trip_cost_fraction(
                mid_a=book_a["mid"],
                mid_b=book_b["mid"],
                fee_bps_per_leg=fee_bps,
            )

        print(f"Analyzing {pair}: {len(series)} points, {len(attractors)} attractors, spread cost ~{pair_spread_cost_bps:.1f} bps")
        for profit_threshold in args.profit_thresholds:
            for max_levels in args.max_levels_grid:
                result = run_attractor_lattice(
                    series,
                    attractors,
                    position_size=args.position_size,
                    profit_threshold=profit_threshold,
                    max_concurrent=args.max_concurrent,
                    max_levels=max_levels,
                )
                closes = result["total_closes"]
                gross_pnl = result["realized_pnl"]
                avg_gross = gross_pnl / closes if closes else 0.0
                row: dict[str, Any] = {
                    "pair": pair,
                    "symbol_a": symbol_a,
                    "symbol_b": symbol_b,
                    "profit_threshold": profit_threshold,
                    "max_levels": max_levels,
                    "attractors_available": len(attractors),
                    "gross_realized_pnl_den": gross_pnl,
                    "total_closes": closes,
                    "closure_rate": result["closure_rate"],
                    "max_open_seen": result["max_open_seen"],
                    "avg_gross_pnl_per_close_den": avg_gross,
                    "pair_spread_cost_bps": pair_spread_cost_bps,
                    "spread_only_cost_per_close_den": args.position_size * spread_only_cost_fraction,
                    "spread_only_net_pnl_den": gross_pnl - closes * args.position_size * spread_only_cost_fraction,
                }
                for fee_bps in args.fee_scenarios_bps:
                    tag = str(fee_bps).replace(".", "_")
                    cost_fraction = quote_cost_summary[pair][f"fee_{tag}_cost_fraction"]
                    cost_per_close = args.position_size * cost_fraction
                    net_pnl = gross_pnl - closes * cost_per_close
                    row[f"fee_{tag}_cost_per_close_den"] = cost_per_close
                    row[f"fee_{tag}_cost_fraction_bps"] = cost_fraction * 10000.0
                    row[f"fee_{tag}_net_pnl_den"] = net_pnl
                    row[f"fee_{tag}_avg_net_per_close_den"] = net_pnl / closes if closes else 0.0
                rows.append(row)

    rows.sort(key=lambda row: (row["pair"], row["profit_threshold"], row["max_levels"]))

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    json_path = Path(args.json_path)

    write_csv(csv_path, rows, list(args.fee_scenarios_bps))
    write_markdown(
        md_path,
        now_ts=now_ts,
        days=int(args.days),
        position_size=float(args.position_size),
        rows=rows,
        fee_scenarios_bps=list(args.fee_scenarios_bps),
        quote_snapshot=quote_snapshot,
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "run_params": {
                    "days": args.days,
                    "position_size": args.position_size,
                    "max_concurrent": args.max_concurrent,
                    "pairs": [f"{a}/{b}" for a, b in pairs],
                    "profit_thresholds": args.profit_thresholds,
                    "max_levels_grid": args.max_levels_grid,
                    "fee_scenarios_bps": args.fee_scenarios_bps,
                },
                "candle_counts": candle_counts,
                "quote_snapshot": quote_snapshot,
                "quote_cost_summary": quote_cost_summary,
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(f"CSV:  {csv_path}")
    print(f"MD:   {md_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
