#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_spot_burst_lab import _load_candles
from benchmark_coinbase_spot_edge_lab import _simulate_two_stage_exit
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_reclaim_param_sweep.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_reclaim_param_sweep.md"

DEFAULT_PRODUCTS = [
    "RAVE-USD",
    "NKN-USD",
    "TRU-USD",
    "SUP-USD",
    "SEAM-USD",
    "BAL-USD",
    "MDT-USD",
    "TROLL-USD",
]


@dataclass(frozen=True)
class ReclaimConfig:
    flush_threshold_pct: float
    reclaim_threshold_pct: float
    close_location_min: float
    initial_stop_pct: float
    target1_pct: float
    target2_pct: float
    max_hold_bars: int

    def label(self) -> str:
        return (
            f"flush={self.flush_threshold_pct:.3f}|reclaim={self.reclaim_threshold_pct:.3f}|"
            f"cl={self.close_location_min:.2f}|sl={self.initial_stop_pct:.3f}|"
            f"t1={self.target1_pct:.3f}|t2={self.target2_pct:.3f}|hold={self.max_hold_bars}"
        )


def _parse_float_list(text: str) -> list[float]:
    return [float(item.strip()) for item in text.split(",") if item.strip()]


def _parse_int_list(text: str) -> list[int]:
    return [int(item.strip()) for item in text.split(",") if item.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep long-only flush-reclaim parameters on focused Coinbase spot names.")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--fee-bps-per-side", type=float, default=40.0)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--products", nargs="*", default=list(DEFAULT_PRODUCTS))
    parser.add_argument("--flush-thresholds", default="0.018,0.022,0.026,0.030")
    parser.add_argument("--reclaim-thresholds", default="0.012,0.016,0.020")
    parser.add_argument("--close-locations", default="0.65,0.70,0.75")
    parser.add_argument("--stop-pcts", default="0.008,0.010,0.012")
    parser.add_argument("--target1-pcts", default="0.018,0.024,0.030")
    parser.add_argument("--target2-pcts", default="0.040,0.050,0.070")
    parser.add_argument("--max-holds", default="8,12")
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def load_market_safe(
    client: CoinbaseAdvancedClient,
    product_ids: list[str],
    *,
    start_ts: int,
    end_ts: int,
    granularity: str,
    sleep_seconds: float,
) -> dict[str, list[Any]]:
    market: dict[str, list[Any]] = {}
    for product_id in product_ids:
        try:
            candles = _load_candles(
                client,
                product_id,
                start_ts=start_ts,
                end_ts=end_ts,
                granularity=granularity,
            )
        except Exception as exc:
            print(f"skip {product_id}: {exc}", flush=True)
            time.sleep(max(0.5, sleep_seconds))
            continue
        if len(candles) < 30:
            continue
        market[product_id] = candles
        time.sleep(max(0.0, sleep_seconds))
    return market


def is_flush_reclaim(candles: list[Any], idx: int, config: ReclaimConfig) -> bool:
    if idx < 2:
        return False
    flush = candles[idx - 1]
    reclaim = candles[idx]
    pre = candles[idx - 2]
    if min(float(pre.close), float(flush.low), float(reclaim.low)) <= 0.0:
        return False
    flush_move = (float(flush.low) / float(pre.close)) - 1.0
    reclaim_move = (float(reclaim.close) / float(flush.low)) - 1.0
    if float(reclaim.high) <= float(reclaim.low):
        close_location = 0.0
    else:
        close_location = (float(reclaim.close) - float(reclaim.low)) / (float(reclaim.high) - float(reclaim.low))
    return (
        flush_move <= -config.flush_threshold_pct
        and reclaim_move >= config.reclaim_threshold_pct
        and close_location >= config.close_location_min
    )


def simulate_config(candles: list[Any], config: ReclaimConfig, fee_bps_per_side: float) -> list[Any]:
    events: list[Any] = []
    for idx in range(len(candles)):
        if is_flush_reclaim(candles, idx, config):
            events.append(
                _simulate_two_stage_exit(
                    candles,
                    idx,
                    initial_stop_pct=config.initial_stop_pct,
                    target1_pct=config.target1_pct,
                    target2_pct=config.target2_pct,
                    max_hold_bars=config.max_hold_bars,
                    fee_bps_per_side=fee_bps_per_side,
                )
            )
    return events


def summarize_events(events: list[Any]) -> dict[str, float]:
    if not events:
        return {
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "avg_net_return_pct": 0.0,
            "median_net_return_pct": 0.0,
            "cumulative_net_pct": 0.0,
            "median_hold_bars": 0.0,
        }
    net_returns = [float(event.net_return_pct) * 100.0 for event in events]
    wins = sum(1 for event in events if float(event.net_return_pct) > 0.0)
    return {
        "signals": len(events),
        "wins": wins,
        "losses": len(events) - wins,
        "win_rate_pct": (wins / len(events)) * 100.0,
        "avg_net_return_pct": statistics.fmean(net_returns),
        "median_net_return_pct": statistics.median(net_returns),
        "cumulative_net_pct": sum(net_returns),
        "median_hold_bars": statistics.median(int(event.bars_held) for event in events),
    }


def build_grid(args: argparse.Namespace) -> list[ReclaimConfig]:
    grid: list[ReclaimConfig] = []
    for flush_threshold in _parse_float_list(args.flush_thresholds):
        for reclaim_threshold in _parse_float_list(args.reclaim_thresholds):
            for close_location in _parse_float_list(args.close_locations):
                for stop_pct in _parse_float_list(args.stop_pcts):
                    for target1_pct in _parse_float_list(args.target1_pcts):
                        for target2_pct in _parse_float_list(args.target2_pcts):
                            if target2_pct <= target1_pct:
                                continue
                            for max_hold in _parse_int_list(args.max_holds):
                                grid.append(
                                    ReclaimConfig(
                                        flush_threshold_pct=flush_threshold,
                                        reclaim_threshold_pct=reclaim_threshold,
                                        close_location_min=close_location,
                                        initial_stop_pct=stop_pct,
                                        target1_pct=target1_pct,
                                        target2_pct=target2_pct,
                                        max_hold_bars=max_hold,
                                    )
                                )
    return grid


def run_sweep(
    market: dict[str, list[Any]],
    *,
    configs: list[ReclaimConfig],
    fee_bps_per_side: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary_rows: list[dict[str, Any]] = []
    best_by_product: dict[str, dict[str, Any]] = {}

    for config in configs:
        product_scores: list[tuple[str, dict[str, float]]] = []
        aggregate_events: list[Any] = []
        positive_products = 0
        for product_id, candles in market.items():
            events = simulate_config(candles, config, fee_bps_per_side)
            summary = summarize_events(events)
            product_scores.append((product_id, summary))
            aggregate_events.extend(events)
            if float(summary["cumulative_net_pct"]) > 0.0:
                positive_products += 1
                previous_best = best_by_product.get(product_id)
                if previous_best is None or float(summary["cumulative_net_pct"]) > float(previous_best["cumulative_net_pct"]):
                    best_by_product[product_id] = {
                        "product_id": product_id,
                        "config": config.label(),
                        **{key: round(float(value), 4) if isinstance(value, float) else value for key, value in summary.items()},
                    }

        aggregate = summarize_events(aggregate_events)
        ranked_products = sorted(product_scores, key=lambda item: float(item[1]["cumulative_net_pct"]), reverse=True)
        summary_rows.append(
            {
                "config": config.label(),
                "signals": int(aggregate["signals"]),
                "wins": int(aggregate["wins"]),
                "losses": int(aggregate["losses"]),
                "win_rate_pct": round(float(aggregate["win_rate_pct"]), 2),
                "avg_net_return_pct": round(float(aggregate["avg_net_return_pct"]), 4),
                "median_net_return_pct": round(float(aggregate["median_net_return_pct"]), 4),
                "cumulative_net_pct": round(float(aggregate["cumulative_net_pct"]), 4),
                "median_hold_bars": round(float(aggregate["median_hold_bars"]), 2),
                "positive_products": positive_products,
                "top_products": ", ".join(
                    product_id for product_id, summary in ranked_products[:5] if float(summary["cumulative_net_pct"]) > 0.0
                ),
            }
        )

    summary_rows.sort(
        key=lambda row: (
            -int(row["positive_products"]),
            -float(row["cumulative_net_pct"]),
            -float(row["avg_net_return_pct"]),
            -float(row["win_rate_pct"]),
        )
    )
    product_rows = sorted(
        best_by_product.values(),
        key=lambda row: (-float(row["cumulative_net_pct"]), -float(row["avg_net_return_pct"]), str(row["product_id"])),
    )
    return summary_rows, product_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(
    path: Path,
    *,
    summary_rows: list[dict[str, Any]],
    product_rows: list[dict[str, Any]],
    hours: int,
    fee_bps_per_side: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Reclaim Parameter Sweep",
        "",
        f"- Window: last `{hours}h`",
        "- Candle granularity: `FIVE_MINUTE`",
        f"- Fee assumption: `{fee_bps_per_side:.1f}` bps per side",
        "",
        "## Best Configs",
        "",
        "| Config | Signals | WR % | Avg Net % | Cum Net % | Positive Products | Top Products |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary_rows[:20]:
        lines.append(
            "| {config} | {signals} | {win_rate_pct:.2f} | {avg_net_return_pct:.4f} | {cumulative_net_pct:.4f} | {positive_products} | {top_products} |".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Best Per Product",
            "",
            "| Product | Cum Net % | Avg Net % | WR % | Signals | Config |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in product_rows[:20]:
        lines.append(
            "| {product_id} | {cumulative_net_pct:.4f} | {avg_net_return_pct:.4f} | {win_rate_pct:.2f} | {signals} | {config} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    end_ts = int(time.time())
    start_ts = end_ts - int(args.hours) * 3600
    products = [str(product).upper() for product in args.products]

    market = load_market_safe(
        client,
        products,
        start_ts=start_ts,
        end_ts=end_ts,
        granularity=str(args.granularity),
        sleep_seconds=float(args.sleep_seconds),
    )
    if not market:
        raise SystemExit("no market data loaded for requested products")

    configs = build_grid(args)
    summary_rows, product_rows = run_sweep(
        market,
        configs=configs,
        fee_bps_per_side=float(args.fee_bps_per_side),
    )

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    write_csv(csv_path, summary_rows)
    write_md(
        md_path,
        summary_rows=summary_rows,
        product_rows=product_rows,
        hours=int(args.hours),
        fee_bps_per_side=float(args.fee_bps_per_side),
    )
    print(
        json.dumps(
            {
                "csv_path": str(csv_path),
                "md_path": str(md_path),
                "top_configs": summary_rows[:10],
                "best_products": product_rows[:10],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
