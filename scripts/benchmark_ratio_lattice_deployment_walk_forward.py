#!/usr/bin/env python3
"""
Deployment-oriented repeated walk-forward for the top ratio sleeves and the first
non-contentious combined pair.

This report answers two adjacent questions in one pass:
- Which isolated sleeves keep holding up across repeated frozen train/forward splits?
- Does the first clean-composition candidate (`CFG/ETH + NOM/BTC`) also hold up as
  a combined basket when both sleeves are frozen per split and replayed together?

Outputs:
- reports/ratio_lattice_deployment_walk_forward.csv
- reports/ratio_lattice_deployment_walk_forward.md
- reports/ratio_lattice_deployment_walk_forward.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
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


DEFAULT_CSV = ROOT / "reports" / "ratio_lattice_deployment_walk_forward.csv"
DEFAULT_MD = ROOT / "reports" / "ratio_lattice_deployment_walk_forward.md"
DEFAULT_JSON = ROOT / "reports" / "ratio_lattice_deployment_walk_forward.json"
DEFAULT_PAIRS: tuple[tuple[str, str], ...] = (
    ("CFG", "BTC"),
    ("CFG", "ETH"),
    ("NOM", "BTC"),
)
DEFAULT_BASKET: tuple[str, ...] = ("CFG/ETH", "NOM/BTC")
DEFAULT_PROFIT_THRESHOLDS: tuple[float, ...] = (1.002, 1.003, 1.004, 1.006, 1.008, 1.010, 1.012)
DEFAULT_MAX_LEVELS: tuple[int, ...] = (3, 5, 8, 10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deployment-oriented repeated walk-forward for ratio sleeves and the first non-contentious basket.")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--train-days", type=int, default=20)
    parser.add_argument("--forward-days", type=int, default=10)
    parser.add_argument("--step-days", type=int, default=10)
    parser.add_argument("--position-size", type=float, default=0.01)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--fee-bps-per-leg", type=float, default=40.0)
    parser.add_argument("--pairs", nargs="*", default=[f"{a}/{b}" for a, b in DEFAULT_PAIRS])
    parser.add_argument("--basket-pairs", nargs="*", default=list(DEFAULT_BASKET))
    parser.add_argument("--profit-thresholds", nargs="*", type=float, default=list(DEFAULT_PROFIT_THRESHOLDS))
    parser.add_argument("--max-levels-grid", nargs="*", type=int, default=list(DEFAULT_MAX_LEVELS))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV))
    parser.add_argument("--md-path", default=str(DEFAULT_MD))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON))
    return parser.parse_args()


def parse_pair_label(label: str) -> tuple[str, str]:
    parts = label.upper().split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid pair label: {label}")
    return parts[0], parts[1]


def parse_pair_labels(labels: list[str]) -> list[tuple[str, str]]:
    return [parse_pair_label(label) for label in labels]


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


def slice_series(series: list[dict[str, Any]], start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    return [row for row in series if start_ts <= int(row["t"]) < end_ts]


def build_windows(
    *,
    start_ts: int,
    end_ts: int,
    train_days: int,
    forward_days: int,
    step_days: int,
) -> list[dict[str, int]]:
    train_sec = train_days * 86400
    forward_sec = forward_days * 86400
    step_sec = step_days * 86400
    windows: list[dict[str, int]] = []
    window_start = start_ts
    split_idx = 1

    while window_start + train_sec + forward_sec <= end_ts:
        train_start = window_start
        train_end = train_start + train_sec
        forward_start = train_end
        forward_end = forward_start + forward_sec
        windows.append(
            {
                "split_idx": split_idx,
                "train_start_ts": train_start,
                "train_end_ts": train_end,
                "forward_start_ts": forward_start,
                "forward_end_ts": forward_end,
            }
        )
        split_idx += 1
        window_start += step_sec
    return windows


def replay_with_fixed_levels(
    ratio_series: list[dict[str, Any]],
    attractors: list[dict[str, float]],
    *,
    position_size: float,
    profit_threshold: float,
    max_concurrent: int,
    max_levels: int,
) -> dict[str, Any]:
    top_attractors = attractors[:max_levels]
    positions: list[dict[str, Any]] = []
    realized_pnl = 0.0
    total_opens = 0
    total_closes = 0
    max_open_seen = 0
    active_points: list[dict[str, Any]] = []

    for point in ratio_series:
        ratio = float(point["ratio"])
        ts = int(point["t"])
        for idx, attr in enumerate(top_attractors):
            level_val = float(attr["ratio"])
            occupied = any(pos["level_idx"] == idx for pos in positions)
            if not occupied and ratio <= level_val and len(positions) < max_concurrent:
                positions.append(
                    {
                        "level_idx": idx,
                        "entry_ratio": ratio,
                        "level_value": level_val,
                        "size": position_size,
                    }
                )
                total_opens += 1

        closes_this_bar: list[dict[str, Any]] = []
        for pos in positions:
            exit_level = pos["level_value"] * profit_threshold
            if ratio >= exit_level:
                pnl = pos["size"] * (ratio - pos["entry_ratio"]) / pos["entry_ratio"]
                realized_pnl += pnl
                total_closes += 1
                closes_this_bar.append(pos)

        for pos in closes_this_bar:
            positions.remove(pos)

        max_open_seen = max(max_open_seen, len(positions))
        active_points.append({"ts": ts, "open_count": len(positions), "active": len(positions) > 0})

    closure_rate = total_closes / total_opens if total_opens else 0.0
    active_bars = sum(1 for point in active_points if point["active"])
    return {
        "realized_pnl": realized_pnl,
        "total_opens": total_opens,
        "total_closes": total_closes,
        "closure_rate": closure_rate,
        "max_open_seen": max_open_seen,
        "active_points": active_points,
        "active_bars": active_bars,
    }


def pairwise_overlap(left_points: list[dict[str, Any]], right_points: list[dict[str, Any]]) -> dict[str, Any]:
    left_map = {int(point["ts"]): point for point in left_points}
    right_map = {int(point["ts"]): point for point in right_points}
    common_ts = sorted(set(left_map.keys()) & set(right_map.keys()))
    overlap_bars = 0
    for ts in common_ts:
        if left_map[ts]["active"] and right_map[ts]["active"]:
            overlap_bars += 1
    return {
        "common_bars": len(common_ts),
        "overlap_bars": overlap_bars,
        "overlap_ratio_common": overlap_bars / len(common_ts) if common_ts else 0.0,
    }


def summarize_pair(pair_windows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in pair_windows if row["window_status"] != "insufficient_data"]
    windows_count = len(completed)
    positive_windows = sum(1 for row in completed if float(row["forward_net_usd"]) > 0.0)
    total_forward_net_usd = sum(float(row["forward_net_usd"]) for row in completed)
    total_forward_closes = sum(int(row["forward_closes"]) for row in completed)
    nominal_capital_usd = float(completed[0]["nominal_capital_usd"]) if completed else 0.0
    total_return_on_nominal = total_forward_net_usd / nominal_capital_usd if nominal_capital_usd else 0.0

    if windows_count == 0:
        verdict = "insufficient_data"
    elif positive_windows == windows_count and total_forward_closes >= windows_count * 3:
        verdict = "repeatable_positive"
    elif positive_windows == windows_count:
        verdict = "positive_but_thin"
    elif positive_windows >= math.ceil(windows_count / 2):
        verdict = "mixed_positive"
    else:
        verdict = "unstable"

    return {
        "pair": pair_windows[0]["pair"] if pair_windows else "",
        "windows_count": windows_count,
        "positive_windows": positive_windows,
        "total_forward_net_usd": total_forward_net_usd,
        "total_forward_closes": total_forward_closes,
        "nominal_capital_usd": nominal_capital_usd,
        "total_return_on_nominal": total_return_on_nominal,
        "verdict": verdict,
    }


def summarize_basket(basket_label: str, basket_rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in basket_rows if row["basket_status"] != "insufficient_data"]
    windows_count = len(completed)
    positive_windows = sum(1 for row in completed if float(row["combined_forward_net_usd"]) > 0.0)
    all_components_positive = sum(1 for row in completed if bool(row["all_components_positive"]))
    total_forward_net_usd = sum(float(row["combined_forward_net_usd"]) for row in completed)
    nominal_capital_usd = float(completed[0]["combined_nominal_capital_usd"]) if completed else 0.0
    total_return_on_nominal = total_forward_net_usd / nominal_capital_usd if nominal_capital_usd else 0.0
    avg_overlap_ratio_common = (
        sum(float(row["overlap_ratio_common"]) for row in completed) / windows_count if windows_count else 0.0
    )

    if windows_count == 0:
        verdict = "insufficient_data"
    elif positive_windows == windows_count and all_components_positive == windows_count:
        verdict = "repeatable_combined_positive"
    elif positive_windows == windows_count:
        verdict = "combined_positive"
    elif positive_windows >= math.ceil(windows_count / 2):
        verdict = "mixed_positive"
    else:
        verdict = "unstable"

    return {
        "basket": basket_label,
        "windows_count": windows_count,
        "positive_windows": positive_windows,
        "all_components_positive_windows": all_components_positive,
        "total_forward_net_usd": total_forward_net_usd,
        "combined_nominal_capital_usd": nominal_capital_usd,
        "total_return_on_nominal": total_return_on_nominal,
        "avg_overlap_ratio_common": avg_overlap_ratio_common,
        "verdict": verdict,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "split_idx",
        "pair",
        "profit_threshold",
        "max_levels",
        "train_net_usd",
        "forward_net_usd",
        "forward_closes",
        "nominal_capital_usd",
        "window_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_markdown(
    path: Path,
    *,
    pair_summaries: list[dict[str, Any]],
    basket_summary: dict[str, Any],
    pair_rows: list[dict[str, Any]],
    basket_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Ratio Lattice Deployment Walk-Forward",
        "",
        "- This is a deployment-oriented repeated walk-forward pass on the strongest isolated sleeves plus the first non-contentious combined pair.",
        f"- Rolling schedule: `{args.train_days}d train / {args.forward_days}d forward / {args.step_days}d step` across `{args.days}d` of `FIVE_MINUTE` candles.",
        "- Each sleeve is re-selected on the training split, then frozen with its training-window attractors before the held-out replay.",
        "- USD conversions use the current denominator mid quote for deployment comparability across `BTC` and `ETH` sleeves.",
        "",
        "## Isolated Sleeve Summary",
        "",
        "| Pair | Positive Windows | Total Forward Net USD | Nominal Capital USD | Return On Nominal | Verdict |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in pair_summaries:
        lines.append(
            f"| `{row['pair']}` | `{row['positive_windows']}/{row['windows_count']}` | "
            f"`{row['total_forward_net_usd']:+.2f}` | `${row['nominal_capital_usd']:.2f}` | "
            f"`{row['total_return_on_nominal']:+.1%}` | `{row['verdict']}` |"
        )

    lines.extend(
        [
            "",
            "## Combined Pair Summary",
            "",
            "| Basket | Positive Windows | All Components Positive | Total Forward Net USD | Basket Capital USD | Return On Nominal | Avg Timing Overlap | Verdict |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            f"| `{basket_summary['basket']}` | `{basket_summary['positive_windows']}/{basket_summary['windows_count']}` | "
            f"`{basket_summary['all_components_positive_windows']}/{basket_summary['windows_count']}` | "
            f"`{basket_summary['total_forward_net_usd']:+.2f}` | `${basket_summary['combined_nominal_capital_usd']:.2f}` | "
            f"`{basket_summary['total_return_on_nominal']:+.1%}` | `{basket_summary['avg_overlap_ratio_common']:.1%}` | `{basket_summary['verdict']}` |",
            "",
            "## Basket Window Detail",
            "",
            "| Split | CFG/ETH USD | NOM/BTC USD | Combined USD | All Positive | Timing Overlap | Status |",
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in basket_rows:
        lines.append(
            f"| `{row['split_idx']}` | `{row['component_nets_usd'].get('CFG/ETH', 0.0):+.2f}` | "
            f"`{row['component_nets_usd'].get('NOM/BTC', 0.0):+.2f}` | `{row['combined_forward_net_usd']:+.2f}` | "
            f"`{int(bool(row['all_components_positive']))}` | `{row['overlap_ratio_common']:.1%}` | `{row['basket_status']}` |"
        )

    lines.extend(["", "## Read", ""])
    for row in pair_summaries:
        lines.append(
            f"- `{row['pair']}`: `{row['positive_windows']}/{row['windows_count']}` positive windows, "
            f"`{row['total_forward_net_usd']:+.2f}` total forward net on `${row['nominal_capital_usd']:.2f}` nominal sleeve capital, verdict `{row['verdict']}`."
        )
    lines.append(
        f"- `{basket_summary['basket']}`: `{basket_summary['positive_windows']}/{basket_summary['windows_count']}` combined-positive windows, "
        f"`{basket_summary['all_components_positive_windows']}/{basket_summary['windows_count']}` windows where both sleeves were individually positive, "
        f"`{basket_summary['total_forward_net_usd']:+.2f}` total forward net on `${basket_summary['combined_nominal_capital_usd']:.2f}` nominal basket capital, verdict `{basket_summary['verdict']}`."
    )
    lines.extend(
        [
            "- `repeatable_positive` means an isolated sleeve stayed positive in every completed forward split with enough closes to avoid a thin-window false positive.",
            "- `repeatable_combined_positive` means the basket stayed positive in every completed split and both sleeves were individually positive in each split.",
            "- Timing overlap is not treated as capital conflict by itself; the basket only claims \"non-contentious\" in the shared-asset sense because it avoids shared numerator and denominator assets.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    pairs = parse_pair_labels(args.pairs)
    pair_labels = [f"{a}/{b}" for a, b in pairs]
    basket_labels = [label.upper() for label in args.basket_pairs]

    client = CoinbaseAdvancedClient()
    now_ts = int(time.time())
    full_start_ts = now_ts - int(args.days) * 86400
    windows = build_windows(
        start_ts=full_start_ts,
        end_ts=now_ts,
        train_days=int(args.train_days),
        forward_days=int(args.forward_days),
        step_days=int(args.step_days),
    )

    symbols: set[str] = set()
    for symbol_a, symbol_b in pairs:
        symbols.add(symbol_a)
        symbols.add(symbol_b)

    price_maps: dict[str, dict[int, float]] = {}
    candle_counts: dict[str, int] = {}
    for symbol in sorted(symbols):
        candles = fetch_candles(client, SYMBOL_TO_PRODUCT[symbol], full_start_ts, now_ts)
        price_maps[symbol] = build_price_map(candles)
        candle_counts[symbol] = len(candles)

    quotes = build_quote_snapshot(client, symbols)
    pair_window_rows: list[dict[str, Any]] = []
    pair_summaries: list[dict[str, Any]] = []

    rows_by_pair_and_split: dict[tuple[str, int], dict[str, Any]] = {}

    for symbol_a, symbol_b in pairs:
        pair = f"{symbol_a}/{symbol_b}"
        series = build_ratio_series(price_maps[symbol_a], price_maps[symbol_b])
        quote_a = quotes[SYMBOL_TO_PRODUCT[symbol_a]]
        quote_b = quotes[SYMBOL_TO_PRODUCT[symbol_b]]
        cost_fraction = synthetic_round_trip_cost_fraction(
            bid_a=float(quote_a["bid"]),
            ask_a=float(quote_a["ask"]),
            bid_b=float(quote_b["bid"]),
            ask_b=float(quote_b["ask"]),
            fee_bps_per_leg=float(args.fee_bps_per_leg),
        )
        cost_per_close_den = float(args.position_size) * cost_fraction
        denom_mid = float(quote_b["mid"])
        nominal_capital_usd = float(args.position_size) * denom_mid

        pair_rows: list[dict[str, Any]] = []
        for window in windows:
            train_series = slice_series(series, window["train_start_ts"], window["train_end_ts"])
            forward_series = slice_series(series, window["forward_start_ts"], window["forward_end_ts"])
            train_attractors = find_attractors_kde(train_series)
            base_row: dict[str, Any] = {
                "split_idx": window["split_idx"],
                "pair": pair,
                "profit_threshold": 0.0,
                "max_levels": 0,
                "train_net_usd": 0.0,
                "forward_net_usd": 0.0,
                "forward_closes": 0,
                "nominal_capital_usd": nominal_capital_usd,
                "window_status": "insufficient_data",
                "active_points": [],
            }
            if not train_series or not forward_series or not train_attractors:
                pair_rows.append(base_row)
                rows_by_pair_and_split[(pair, window["split_idx"])] = base_row
                continue

            best_row: dict[str, Any] | None = None
            for profit_threshold in args.profit_thresholds:
                for max_levels in args.max_levels_grid:
                    train_result = run_attractor_lattice(
                        train_series,
                        train_attractors,
                        position_size=float(args.position_size),
                        profit_threshold=float(profit_threshold),
                        max_concurrent=int(args.max_concurrent),
                        max_levels=int(max_levels),
                    )
                    closes = int(train_result["total_closes"])
                    gross_den = float(train_result["realized_pnl"])
                    net_den = gross_den - closes * cost_per_close_den
                    candidate = {
                        "profit_threshold": float(profit_threshold),
                        "max_levels": int(max_levels),
                        "train_net_usd": net_den * denom_mid,
                    }
                    if best_row is None or float(candidate["train_net_usd"]) > float(best_row["train_net_usd"]):
                        best_row = candidate

            assert best_row is not None
            forward_result = replay_with_fixed_levels(
                forward_series,
                train_attractors,
                position_size=float(args.position_size),
                profit_threshold=float(best_row["profit_threshold"]),
                max_concurrent=int(args.max_concurrent),
                max_levels=int(best_row["max_levels"]),
            )
            forward_closes = int(forward_result["total_closes"])
            forward_gross_den = float(forward_result["realized_pnl"])
            forward_net_den = forward_gross_den - forward_closes * cost_per_close_den
            forward_net_usd = forward_net_den * denom_mid
            window_status = "holding_up" if forward_net_usd > 0 else "lagging"

            row = {
                "split_idx": window["split_idx"],
                "pair": pair,
                "profit_threshold": best_row["profit_threshold"],
                "max_levels": best_row["max_levels"],
                "train_net_usd": best_row["train_net_usd"],
                "forward_net_usd": forward_net_usd,
                "forward_closes": forward_closes,
                "nominal_capital_usd": nominal_capital_usd,
                "window_status": window_status,
                "active_points": forward_result["active_points"],
            }
            pair_rows.append(row)
            rows_by_pair_and_split[(pair, window["split_idx"])] = row

        pair_window_rows.extend(pair_rows)
        pair_summaries.append(summarize_pair(pair_rows))

    pair_summaries.sort(key=lambda row: (row["total_return_on_nominal"], row["total_forward_net_usd"]), reverse=True)

    basket_label = " + ".join(basket_labels)
    basket_rows: list[dict[str, Any]] = []
    for window in windows:
        split_idx = window["split_idx"]
        component_rows = [rows_by_pair_and_split.get((pair, split_idx)) for pair in basket_labels]
        if any(row is None or row["window_status"] == "insufficient_data" for row in component_rows):
            basket_rows.append(
                {
                    "split_idx": split_idx,
                    "combined_forward_net_usd": 0.0,
                    "combined_nominal_capital_usd": 0.0,
                    "all_components_positive": False,
                    "overlap_ratio_common": 0.0,
                    "basket_status": "insufficient_data",
                    "component_nets_usd": {},
                }
            )
            continue

        left = component_rows[0]
        right = component_rows[1]
        assert left is not None and right is not None
        overlap = pairwise_overlap(left["active_points"], right["active_points"])
        component_nets = {str(row["pair"]): float(row["forward_net_usd"]) for row in component_rows if row is not None}
        combined_forward_net_usd = sum(component_nets.values())
        combined_nominal_capital_usd = sum(float(row["nominal_capital_usd"]) for row in component_rows if row is not None)
        all_components_positive = all(net > 0 for net in component_nets.values())
        basket_status = "holding_up" if combined_forward_net_usd > 0 else "lagging"
        basket_rows.append(
            {
                "split_idx": split_idx,
                "combined_forward_net_usd": combined_forward_net_usd,
                "combined_nominal_capital_usd": combined_nominal_capital_usd,
                "all_components_positive": all_components_positive,
                "overlap_ratio_common": overlap["overlap_ratio_common"],
                "basket_status": basket_status,
                "component_nets_usd": component_nets,
            }
        )

    basket_summary = summarize_basket(basket_label, basket_rows)

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    json_path = Path(args.json_path)
    write_csv(csv_path, pair_window_rows)
    write_markdown(
        md_path,
        pair_summaries=pair_summaries,
        basket_summary=basket_summary,
        pair_rows=pair_window_rows,
        basket_rows=basket_rows,
        args=args,
    )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "run_params": {
                    "days": args.days,
                    "train_days": args.train_days,
                    "forward_days": args.forward_days,
                    "step_days": args.step_days,
                    "position_size": args.position_size,
                    "max_concurrent": args.max_concurrent,
                    "fee_bps_per_leg": args.fee_bps_per_leg,
                    "pairs": pair_labels,
                    "basket_pairs": basket_labels,
                    "profit_thresholds": args.profit_thresholds,
                    "max_levels_grid": args.max_levels_grid,
                },
                "candle_counts": candle_counts,
                "pair_summaries": pair_summaries,
                "pair_window_rows": [
                    {k: v for k, v in row.items() if k != "active_points"}
                    for row in pair_window_rows
                ],
                "basket_summary": basket_summary,
                "basket_rows": basket_rows,
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
