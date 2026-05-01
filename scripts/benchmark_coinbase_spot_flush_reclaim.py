#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_spot_edge_lab import (
    _select_products,
    _simulate_product_tactic,
)
from benchmark_coinbase_spot_burst_lab import _load_candles
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_flush_reclaim_72h.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_flush_reclaim_72h.md"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank Coinbase USD spot products for long-only flush reclaim / pullback resume.")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--min-volume-24h", type=float, default=250000.0)
    parser.add_argument("--top-n", type=int, default=40)
    parser.add_argument("--fee-bps-per-side", type=float, default=40.0)
    parser.add_argument("--tactic", choices=["flush_reclaim", "pullback_resume"], default="flush_reclaim")
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def summarize_events(events: list[Any]) -> dict[str, float]:
    if not events:
        return {
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": 0.0,
            "avg_net_return_pct": 0.0,
            "median_net_return_pct": 0.0,
            "median_hold_bars": 0.0,
            "cumulative_net_pct": 0.0,
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
        "median_hold_bars": statistics.median(int(event.bars_held) for event in events),
        "cumulative_net_pct": sum(net_returns),
    }


def build_rows(
    *,
    market: dict[str, list[Any]],
    tactic: str,
    fee_bps_per_side: float,
    product_meta: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    product_meta = product_meta or {}
    for product_id, candles in market.items():
        events = _simulate_product_tactic(candles, signal_name=tactic, fee_bps_per_side=fee_bps_per_side)
        summary = summarize_events(events)
        rows.append(
            {
                "product_id": product_id,
                "signals": int(summary["signals"]),
                "wins": int(summary["wins"]),
                "losses": int(summary["losses"]),
                "win_rate_pct": round(float(summary["win_rate_pct"]), 2),
                "avg_net_return_pct": round(float(summary["avg_net_return_pct"]), 4),
                "median_net_return_pct": round(float(summary["median_net_return_pct"]), 4),
                "cumulative_net_pct": round(float(summary["cumulative_net_pct"]), 4),
                "median_hold_bars": round(float(summary["median_hold_bars"]), 2),
                "pct24h": round(float(product_meta.get(product_id, {}).get("pct24h", 0.0)), 4),
                "volume_24h": round(float(product_meta.get(product_id, {}).get("volume_24h", 0.0)), 2),
            }
        )

    rows.sort(
        key=lambda row: (
            -float(row["cumulative_net_pct"]),
            -float(row["avg_net_return_pct"]),
            -float(row["win_rate_pct"]),
            -int(row["signals"]),
            str(row["product_id"]),
        )
    )
    return rows


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


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_md(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    hours: int,
    tactic: str,
    fee_bps_per_side: float,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    positive_rows = [row for row in rows if float(row["cumulative_net_pct"]) > 0.0]
    lines = [
        f"# Coinbase Spot {tactic.replace('_', ' ').title()} Benchmark",
        "",
        f"- Window: last `{hours}h`",
        "- Candle granularity: `FIVE_MINUTE`",
        f"- Tactic: `{tactic}`",
        f"- Fee assumption: `{fee_bps_per_side:.1f}` bps per side",
        f"- Positive products: `{len(positive_rows)}/{len(rows)}`",
        "",
        "| Product | Signals | Wins | Losses | WR % | Avg Net % | Median Net % | Cum Net % | Hold Bars | 24h % | Vol 24h |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows[:25]:
        lines.append(
            "| {product_id} | {signals} | {wins} | {losses} | {win_rate_pct:.2f} | {avg_net_return_pct:.4f} | "
            "{median_net_return_pct:.4f} | {cumulative_net_pct:.4f} | {median_hold_bars:.2f} | {pct24h:.4f} | {volume_24h:.2f} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    end_ts = int(time.time())
    start_ts = end_ts - int(args.hours) * 3600

    product_ids, product_meta = _select_products(
        client,
        min_volume_24h=float(args.min_volume_24h),
        top_n=int(args.top_n),
    )
    market = load_market_safe(
        client,
        product_ids,
        start_ts=start_ts,
        end_ts=end_ts,
        granularity=str(args.granularity),
        sleep_seconds=float(args.sleep_seconds),
    )

    rows = build_rows(
        market=market,
        tactic=str(args.tactic),
        fee_bps_per_side=float(args.fee_bps_per_side),
        product_meta=product_meta,
    )
    if not rows:
        raise SystemExit("no products loaded for benchmark")

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    write_csv(csv_path, rows)
    write_md(
        md_path,
        rows=rows,
        hours=int(args.hours),
        tactic=str(args.tactic),
        fee_bps_per_side=float(args.fee_bps_per_side),
    )
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "top_rows": rows[:10]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
