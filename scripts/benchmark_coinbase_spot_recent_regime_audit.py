#!/usr/bin/env python3
"""
Audit whether the strongest discovered Coinbase spot sleeves depend on the most recent regime.

This keeps the tuned discovery shape (pair, threshold, max levels) fixed and
replays it across nested time windows so recent pumps do not dominate the read.

Outputs:
- reports/coinbase_spot_recent_regime_audit.csv
- reports/coinbase_spot_recent_regime_audit.md
- reports/coinbase_spot_recent_regime_audit.json
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


DEFAULT_DISCOVERY_JSON = ROOT / "reports" / "coinbase_spot_lattice_discovery.json"
DEFAULT_CSV = ROOT / "reports" / "coinbase_spot_recent_regime_audit.csv"
DEFAULT_MD = ROOT / "reports" / "coinbase_spot_recent_regime_audit.md"
DEFAULT_JSON = ROOT / "reports" / "coinbase_spot_recent_regime_audit.json"
DEFAULT_FOCUS_PAIRS: tuple[str, ...] = (
    "RAVE/BAL",
    "RAVE/BTC",
    "RAVE/ETH",
    "RAVE/IOTX",
    "IOTX/ETH",
    "IOTX/BTC",
    "CFG/BTC",
    "CFG/ETH",
)

WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("full_60d", 60, 0),
    ("recent_30d", 30, 0),
    ("prior_30d", 60, 30),
    ("recent_15d", 15, 0),
    ("prior_15d", 30, 15),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay discovered spot sleeves across recent/prior windows.")
    parser.add_argument("--discovery-json", default=str(DEFAULT_DISCOVERY_JSON))
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--position-size", type=float, default=0.01)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--fee-bps-per-leg", type=float, default=40.0)
    parser.add_argument("--pairs", nargs="*", default=list(DEFAULT_FOCUS_PAIRS))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV))
    parser.add_argument("--md-path", default=str(DEFAULT_MD))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON))
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_rows(rows: list[dict[str, Any]], focus_pairs: set[str]) -> list[dict[str, Any]]:
    return [row for row in rows if row["pair"].upper() in focus_pairs]


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
        snapshot[product_id] = {"bid": bid, "ask": ask}
    return snapshot


def slice_series(series: list[dict[str, Any]], start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    return [row for row in series if start_ts <= int(row["t"]) < end_ts]


def slice_price_map(price_map: dict[int, float], start_ts: int, end_ts: int) -> list[float]:
    points = [(ts, price) for ts, price in price_map.items() if start_ts <= int(ts) < end_ts]
    points.sort()
    return [price for _, price in points]


def pct_return(prices: list[float]) -> float | None:
    if len(prices) < 2 or prices[0] <= 0:
        return None
    return (prices[-1] / prices[0] - 1.0) * 100.0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "pair",
        "group",
        "window",
        "profit_threshold",
        "max_levels",
        "net_realized_pnl_den",
        "gross_realized_pnl_den",
        "total_closes",
        "closure_rate",
        "max_open_seen",
        "avg_net_per_close_den",
        "symbol_a_return_pct",
        "symbol_b_return_pct",
        "ratio_return_pct",
        "pump_bias_flag",
        "recent_vs_prior_30x",
        "recency_concentrated",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_markdown(path: Path, *, summary_rows: list[dict[str, Any]], all_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Recent Regime Audit",
        "",
        "- This holds the discovered tuned sleeve geometry fixed and replays it across recent/prior windows so one late pump cannot dominate the read.",
        "- `pump_bias_flag=yes` means the sleeve stayed positive in `full_60d` and `recent_30d` but turned non-positive in `prior_30d`.",
        "- `recency_concentrated=yes` means `recent_30d` stayed positive but was at least `10x` the `prior_30d` net, so the edge may be real but is still heavily dominated by the latest regime.",
        "",
        "## Pair Summary",
        "",
        "| Pair | Full 60d | Recent 30d | Prior 30d | Recent 15d | Prior 15d | Recent/Prior 30x | Pump-Bias Flag | Recency Concentrated |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in summary_rows:
        lines.append(
            f"| `{row['pair']}` | `{row['full_60d']:+.6f}` | `{row['recent_30d']:+.6f}` | "
            f"`{row['prior_30d']:+.6f}` | `{row['recent_15d']:+.6f}` | `{row['prior_15d']:+.6f}` | "
            f"`{row['recent_vs_prior_30x']}` | `{row['pump_bias_flag']}` | `{row['recency_concentrated']}` |"
        )

    lines.extend(
        [
            "",
            "## Window Detail",
            "",
            "| Pair | Window | Net PnL | Closes | Num Return | Den Return | Ratio Return |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in all_rows:
        num_ret = "n/a" if row["symbol_a_return_pct"] is None else f"{row['symbol_a_return_pct']:+.1f}%"
        den_ret = "n/a" if row["symbol_b_return_pct"] is None else f"{row['symbol_b_return_pct']:+.1f}%"
        ratio_ret = "n/a" if row["ratio_return_pct"] is None else f"{row['ratio_return_pct']:+.1f}%"
        lines.append(
            f"| `{row['pair']}` | `{row['window']}` | `{row['net_realized_pnl_den']:+.6f}` | "
            f"`{row['total_closes']}` | `{num_ret}` | `{den_ret}` | `{ratio_ret}` |"
        )

    lines.extend(["", "## Read", ""])
    for row in summary_rows:
        if row["pump_bias_flag"] == "yes":
            lines.append(
                f"- `{row['pair']}` looks pump-skewed: positive in `full_60d` and `recent_30d`, but non-positive in `prior_30d`."
            )
        elif row["recency_concentrated"] == "yes":
            lines.append(
                f"- `{row['pair']}` survives the prior-window check, but recent performance is still heavily concentrated: `recent_30d` is `{row['recent_vs_prior_30x']}x` the `prior_30d` net."
            )
        else:
            lines.append(
                f"- `{row['pair']}` survives the pump-bias check: `prior_30d` stays positive at `{row['prior_30d']:+.6f}`."
            )
    lines.extend(
        [
            "- Treat any `yes` flag as a regime-dependent shadow candidate, not a durable promotion read.",
            "- If a sleeve stays positive in both `recent_30d` and `prior_30d`, the edge is harder to explain away as a single late pump.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    discovery = load_json(Path(args.discovery_json))
    tuned_rows = select_rows(discovery["pair_best_rows"], {pair.upper() for pair in args.pairs})
    if not tuned_rows:
        raise SystemExit("No matching pairs found in discovery JSON.")

    symbols: set[str] = set()
    for row in tuned_rows:
        symbols.add(row["symbol_a"])
        symbols.add(row["symbol_b"])

    client = CoinbaseAdvancedClient()
    now_ts = int(time.time())
    start_ts = now_ts - int(args.days) * 86400

    price_maps: dict[str, dict[int, float]] = {}
    candle_counts: dict[str, int] = {}
    for symbol in sorted(symbols):
        candles = fetch_candles(client, SYMBOL_TO_PRODUCT[symbol], start_ts, now_ts)
        price_maps[symbol] = build_price_map(candles)
        candle_counts[symbol] = len(candles)

    quotes = build_quote_snapshot(client, sorted(symbols))

    all_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for row in tuned_rows:
        symbol_a = row["symbol_a"]
        symbol_b = row["symbol_b"]
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
        per_pair_window: dict[str, float] = {}

        for window_name, outer_days, inner_days in WINDOWS:
            window_start = now_ts - outer_days * 86400
            window_end = now_ts - inner_days * 86400
            window_series = slice_series(series, window_start, window_end)
            attractors = find_attractors_kde(window_series)
            if not window_series or not attractors:
                result_row = {
                    "pair": row["pair"],
                    "group": row["group"],
                    "window": window_name,
                    "profit_threshold": float(row["profit_threshold"]),
                    "max_levels": int(row["max_levels"]),
                    "gross_realized_pnl_den": 0.0,
                    "net_realized_pnl_den": 0.0,
                    "total_closes": 0,
                    "closure_rate": 0.0,
                    "max_open_seen": 0,
                    "avg_net_per_close_den": 0.0,
                    "symbol_a_return_pct": pct_return(slice_price_map(price_maps[symbol_a], window_start, window_end)),
                    "symbol_b_return_pct": pct_return(slice_price_map(price_maps[symbol_b], window_start, window_end)),
                    "ratio_return_pct": None,
                }
            else:
                result = run_attractor_lattice(
                    window_series,
                    attractors,
                    position_size=float(args.position_size),
                    profit_threshold=float(row["profit_threshold"]),
                    max_concurrent=int(args.max_concurrent),
                    max_levels=int(row["max_levels"]),
                )
                closes = int(result["total_closes"])
                gross = float(result["realized_pnl"])
                net = gross - closes * float(args.position_size) * cost_fraction
                ratio_prices = [float(point["ratio"]) for point in window_series]
                ratio_return = None if len(ratio_prices) < 2 or ratio_prices[0] <= 0 else (ratio_prices[-1] / ratio_prices[0] - 1.0) * 100.0
                result_row = {
                    "pair": row["pair"],
                    "group": row["group"],
                    "window": window_name,
                    "profit_threshold": float(row["profit_threshold"]),
                    "max_levels": int(row["max_levels"]),
                    "gross_realized_pnl_den": gross,
                    "net_realized_pnl_den": net,
                    "total_closes": closes,
                    "closure_rate": float(result["closure_rate"]),
                    "max_open_seen": int(result["max_open_seen"]),
                    "avg_net_per_close_den": net / closes if closes else 0.0,
                    "symbol_a_return_pct": pct_return(slice_price_map(price_maps[symbol_a], window_start, window_end)),
                    "symbol_b_return_pct": pct_return(slice_price_map(price_maps[symbol_b], window_start, window_end)),
                    "ratio_return_pct": ratio_return,
                }
            all_rows.append(result_row)
            per_pair_window[window_name] = float(result_row["net_realized_pnl_den"])

        summary_rows.append(
            {
                "pair": row["pair"],
                "full_60d": per_pair_window.get("full_60d", 0.0),
                "recent_30d": per_pair_window.get("recent_30d", 0.0),
                "prior_30d": per_pair_window.get("prior_30d", 0.0),
                "recent_15d": per_pair_window.get("recent_15d", 0.0),
                "prior_15d": per_pair_window.get("prior_15d", 0.0),
                "recent_vs_prior_30x": (
                    "inf"
                    if per_pair_window.get("prior_30d", 0.0) == 0 and per_pair_window.get("recent_30d", 0.0) > 0
                    else f"{(per_pair_window.get('recent_30d', 0.0) / per_pair_window.get('prior_30d', 1.0)):.1f}"
                    if per_pair_window.get("prior_30d", 0.0) > 0
                    else "n/a"
                ),
                "pump_bias_flag": (
                    "yes"
                    if per_pair_window.get("full_60d", 0.0) > 0
                    and per_pair_window.get("recent_30d", 0.0) > 0
                    and per_pair_window.get("prior_30d", 0.0) <= 0
                    else "no"
                ),
                "recency_concentrated": (
                    "yes"
                    if per_pair_window.get("recent_30d", 0.0) > 0
                    and per_pair_window.get("prior_30d", 0.0) > 0
                    and (per_pair_window.get("recent_30d", 0.0) / per_pair_window.get("prior_30d", 1.0)) >= 10.0
                    else "no"
                ),
            }
        )

    bias_map = {row["pair"]: row["pump_bias_flag"] for row in summary_rows}
    ratio_map = {row["pair"]: row["recent_vs_prior_30x"] for row in summary_rows}
    recency_map = {row["pair"]: row["recency_concentrated"] for row in summary_rows}
    for row in all_rows:
        row["pump_bias_flag"] = bias_map[row["pair"]]
        row["recent_vs_prior_30x"] = ratio_map[row["pair"]]
        row["recency_concentrated"] = recency_map[row["pair"]]

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    json_path = Path(args.json_path)
    write_csv(csv_path, all_rows)
    write_markdown(md_path, summary_rows=summary_rows, all_rows=all_rows)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "run_params": {
                    "discovery_json": str(args.discovery_json),
                    "days": args.days,
                    "position_size": args.position_size,
                    "max_concurrent": args.max_concurrent,
                    "fee_bps_per_leg": args.fee_bps_per_leg,
                    "pairs": args.pairs,
                    "windows": [{"name": name, "outer_days": outer, "inner_days": inner} for name, outer, inner in WINDOWS],
                },
                "candle_counts": candle_counts,
                "summary": summary_rows,
                "rows": all_rows,
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
