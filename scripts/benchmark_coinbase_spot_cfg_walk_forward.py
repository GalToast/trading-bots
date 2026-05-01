#!/usr/bin/env python3
"""
Repeated frozen walk-forward audit for the mature CFG spot-control sleeves.

For each rolling split:
- choose the best shape on the training window
- freeze both the chosen shape and the training-window attractor levels
- replay the held-out forward window without re-tuning

This extends the single held-out forward-shadow pass into repeated splits so
the current CFG leaders can be judged on repeatability, not one lucky holdout.

Outputs:
- reports/coinbase_spot_cfg_walk_forward.csv
- reports/coinbase_spot_cfg_walk_forward.md
- reports/coinbase_spot_cfg_walk_forward.json
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


DEFAULT_CSV = ROOT / "reports" / "coinbase_spot_cfg_walk_forward.csv"
DEFAULT_MD = ROOT / "reports" / "coinbase_spot_cfg_walk_forward.md"
DEFAULT_JSON = ROOT / "reports" / "coinbase_spot_cfg_walk_forward.json"
DEFAULT_PAIRS: tuple[tuple[str, str], ...] = (
    ("CFG", "BTC"),
    ("CFG", "ETH"),
)
DEFAULT_PROFIT_THRESHOLDS: tuple[float, ...] = (1.002, 1.003, 1.004, 1.006, 1.008, 1.010, 1.012)
DEFAULT_MAX_LEVELS: tuple[int, ...] = (3, 5, 8, 10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repeated frozen walk-forward audit for mature CFG spot-control sleeves.")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--train-days", type=int, default=20)
    parser.add_argument("--forward-days", type=int, default=10)
    parser.add_argument("--step-days", type=int, default=10)
    parser.add_argument("--position-size", type=float, default=0.01)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--fee-bps-per-leg", type=float, default=40.0)
    parser.add_argument("--pairs", nargs="*", default=[f"{a}/{b}" for a, b in DEFAULT_PAIRS])
    parser.add_argument("--profit-thresholds", nargs="*", type=float, default=list(DEFAULT_PROFIT_THRESHOLDS))
    parser.add_argument("--max-levels-grid", nargs="*", type=int, default=list(DEFAULT_MAX_LEVELS))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV))
    parser.add_argument("--md-path", default=str(DEFAULT_MD))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON))
    return parser.parse_args()


def parse_pair_labels(labels: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for label in labels:
        parts = label.upper().split("/")
        if len(parts) != 2:
            raise ValueError(f"Invalid pair label: {label}")
        pairs.append((parts[0], parts[1]))
    return pairs


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

    for point in ratio_series:
        ratio = float(point["ratio"])
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

    closure_rate = total_closes / total_opens if total_opens else 0.0
    return {
        "realized_pnl": realized_pnl,
        "total_opens": total_opens,
        "total_closes": total_closes,
        "closure_rate": closure_rate,
        "max_open_seen": max_open_seen,
    }


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


def summarize_pair(pair_windows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [row for row in pair_windows if row["window_status"] != "insufficient_data"]
    windows_count = len(completed)
    positive_windows = sum(1 for row in completed if float(row["forward_net_pnl_den"]) > 0.0)
    windows_with_closes = sum(1 for row in completed if int(row["forward_closes"]) > 0)
    positive_with_closes = sum(
        1
        for row in completed
        if float(row["forward_net_pnl_den"]) > 0.0 and int(row["forward_closes"]) > 0
    )
    total_forward_net = sum(float(row["forward_net_pnl_den"]) for row in completed)
    total_forward_closes = sum(int(row["forward_closes"]) for row in completed)
    avg_forward_net = total_forward_net / windows_count if windows_count else 0.0
    avg_forward_closes = total_forward_closes / windows_count if windows_count else 0.0
    best_window = max(completed, key=lambda row: float(row["forward_net_pnl_den"]), default=None)
    worst_window = min(completed, key=lambda row: float(row["forward_net_pnl_den"]), default=None)

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
        "windows_with_closes": windows_with_closes,
        "positive_with_closes": positive_with_closes,
        "total_forward_net_pnl_den": total_forward_net,
        "total_forward_closes": total_forward_closes,
        "avg_forward_net_pnl_den": avg_forward_net,
        "avg_forward_closes": avg_forward_closes,
        "best_window_net_pnl_den": float(best_window["forward_net_pnl_den"]) if best_window else 0.0,
        "worst_window_net_pnl_den": float(worst_window["forward_net_pnl_den"]) if worst_window else 0.0,
        "verdict": verdict,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "pair",
        "split_idx",
        "train_start_ts",
        "train_end_ts",
        "forward_start_ts",
        "forward_end_ts",
        "profit_threshold",
        "max_levels",
        "train_net_pnl_den",
        "train_closes",
        "forward_net_pnl_den",
        "forward_closes",
        "forward_closure_rate",
        "forward_max_open_seen",
        "window_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_markdown(path: Path, summaries: list[dict[str, Any]], windows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot CFG Walk-Forward",
        "",
        "- This is the repeated frozen walk-forward follow-on for the first mature control sleeves.",
        f"- Rolling schedule: `{args.train_days}d train / {args.forward_days}d forward / {args.step_days}d step` across `{args.days}d` of `FIVE_MINUTE` candles.",
        "- Each split freezes the training-window shape and training-window attractor levels before replaying the held-out forward window.",
        "",
        "## Summary",
        "",
        "| Pair | Positive Windows | Total Forward Net | Total Forward Closes | Verdict |",
        "| --- | ---: | ---: | ---: | --- |",
    ]

    for row in summaries:
        lines.append(
            f"| `{row['pair']}` | `{row['positive_windows']}/{row['windows_count']}` | "
            f"`{row['total_forward_net_pnl_den']:+.6f}` | `{row['total_forward_closes']}` | `{row['verdict']}` |"
        )

    lines.extend(["", "## Window Detail", ""])
    for summary in summaries:
        pair = summary["pair"]
        lines.extend(
            [
                f"### `{pair}`",
                "",
                "| Split | Frozen Shape | Train Net | Forward Net | Forward Closes | Status |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for row in [window for window in windows if window["pair"] == pair]:
            shape = f"thr={row['profit_threshold']:.3f} levels={row['max_levels']}" if row["max_levels"] else "n/a"
            lines.append(
                f"| `{row['split_idx']}` | `{shape}` | `{row['train_net_pnl_den']:+.6f}` | "
                f"`{row['forward_net_pnl_den']:+.6f}` | `{row['forward_closes']}` | `{row['window_status']}` |"
            )
        lines.append("")

    lines.extend(["## Read", ""])
    for row in summaries:
        lines.append(
            f"- `{row['pair']}`: `{row['positive_windows']}/{row['windows_count']}` positive windows, "
            f"`{row['total_forward_net_pnl_den']:+.6f}` total forward net, `{row['total_forward_closes']}` total forward closes, verdict `{row['verdict']}`."
        )
    lines.extend(
        [
            "- `repeatable_positive` means every completed forward split stayed positive and the sleeve printed enough closes to avoid a thin-window false positive.",
            "- `positive_but_thin` means every completed forward split stayed positive but the combined close count is still light.",
            "- `mixed_positive` means the sleeve stayed positive in a majority of splits but not all of them.",
            "- `unstable` means positive forward behavior did not repeat often enough to treat the sleeve as repeatable yet.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    pairs = parse_pair_labels(args.pairs)

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
    window_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

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
        cost_per_close = float(args.position_size) * cost_fraction

        pair_windows: list[dict[str, Any]] = []
        for window in windows:
            train_series = slice_series(series, window["train_start_ts"], window["train_end_ts"])
            forward_series = slice_series(series, window["forward_start_ts"], window["forward_end_ts"])
            train_attractors = find_attractors_kde(train_series)

            base_row: dict[str, Any] = {
                "pair": pair,
                "split_idx": window["split_idx"],
                "train_start_ts": window["train_start_ts"],
                "train_end_ts": window["train_end_ts"],
                "forward_start_ts": window["forward_start_ts"],
                "forward_end_ts": window["forward_end_ts"],
                "profit_threshold": 0.0,
                "max_levels": 0,
                "train_net_pnl_den": 0.0,
                "train_closes": 0,
                "forward_net_pnl_den": 0.0,
                "forward_closes": 0,
                "forward_closure_rate": 0.0,
                "forward_max_open_seen": 0,
                "window_status": "insufficient_data",
            }
            if not train_series or not forward_series or not train_attractors:
                pair_windows.append(base_row)
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
                    gross = float(train_result["realized_pnl"])
                    net = gross - closes * cost_per_close
                    candidate = {
                        "profit_threshold": float(profit_threshold),
                        "max_levels": int(max_levels),
                        "train_net_pnl_den": net,
                        "train_closes": closes,
                    }
                    if best_row is None or float(candidate["train_net_pnl_den"]) > float(best_row["train_net_pnl_den"]):
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
            forward_gross = float(forward_result["realized_pnl"])
            forward_net = forward_gross - forward_closes * cost_per_close
            window_status = "holding_up" if forward_net > 0 else "lagging"

            pair_windows.append(
                {
                    **base_row,
                    "profit_threshold": best_row["profit_threshold"],
                    "max_levels": best_row["max_levels"],
                    "train_net_pnl_den": best_row["train_net_pnl_den"],
                    "train_closes": best_row["train_closes"],
                    "forward_net_pnl_den": forward_net,
                    "forward_closes": forward_closes,
                    "forward_closure_rate": float(forward_result["closure_rate"]),
                    "forward_max_open_seen": int(forward_result["max_open_seen"]),
                    "window_status": window_status,
                }
            )

        window_rows.extend(pair_windows)
        summary_rows.append(summarize_pair(pair_windows))

    summary_rows.sort(key=lambda row: (row["positive_windows"], row["total_forward_net_pnl_den"]), reverse=True)

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    json_path = Path(args.json_path)
    write_csv(csv_path, window_rows)
    write_markdown(md_path, summary_rows, window_rows, args)
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
                    "pairs": args.pairs,
                    "profit_thresholds": args.profit_thresholds,
                    "max_levels_grid": args.max_levels_grid,
                },
                "candle_counts": candle_counts,
                "summary_rows": summary_rows,
                "window_rows": window_rows,
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
