#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_spot_burst_lab import (
    _load_candles,
    evaluate_forward_path,
    is_burst_continuation,
    is_compression_breakout,
    is_panic_reclaim,
)
from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_burst_grid_24h.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_burst_grid_24h.md"


TACTIC_DEFS: dict[str, dict[str, Any]] = {
    "burst": {
        "signal_fn": is_burst_continuation,
        "targets": [0.015, 0.02, 0.03, 0.05],
        "stops": [0.0075, 0.01, 0.015],
        "holds": [3, 6, 12],
    },
    "reclaim": {
        "signal_fn": is_panic_reclaim,
        "targets": [0.02, 0.03, 0.05, 0.08],
        "stops": [0.01, 0.015, 0.02],
        "holds": [3, 6, 12],
    },
    "compression": {
        "signal_fn": is_compression_breakout,
        "targets": [0.015, 0.02, 0.03, 0.04],
        "stops": [0.0075, 0.01, 0.015],
        "holds": [3, 6, 12],
    },
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep burst-capture parameters across Coinbase USD spot products.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--min-volume-24h", type=float, default=250000.0)
    parser.add_argument("--top-n", type=int, default=60)
    parser.add_argument("--fee-bps-per-side", type=float, default=40.0)
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def _iter_configs() -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for tactic_name, definition in TACTIC_DEFS.items():
        for target_pct in definition["targets"]:
            for stop_pct in definition["stops"]:
                if stop_pct >= target_pct:
                    continue
                for max_hold_bars in definition["holds"]:
                    configs.append(
                        {
                            "tactic": tactic_name,
                            "signal_fn": definition["signal_fn"],
                            "target_pct": target_pct,
                            "stop_pct": stop_pct,
                            "max_hold_bars": max_hold_bars,
                        }
                    )
    return configs


def _select_products(client: CoinbaseAdvancedClient, *, min_volume_24h: float, top_n: int) -> tuple[list[str], dict[str, dict[str, float]]]:
    products_payload = client.list_products(get_all_products=True, product_type="SPOT", limit=1000)
    candidates: list[tuple[float, float, str]] = []
    meta: dict[str, dict[str, float]] = {}
    for product in (products_payload.get("products") or []):
        product_id = str(product.get("product_id") or "")
        base = str(product.get("base_currency_id") or "")
        quote = str(product.get("quote_currency_id") or "")
        if quote != "USD" or not product_id or base in {"USD", "USDC", "USDT"} or product_id.startswith("USDC-"):
            continue
        try:
            pct24h = float(product.get("price_percentage_change_24h") or 0.0)
            volume_24h = float(product.get("volume_24h") or 0.0)
        except Exception:
            continue
        if volume_24h < min_volume_24h:
            continue
        candidates.append((abs(pct24h), volume_24h, product_id))
        meta[product_id] = {"pct24h": pct24h, "volume_24h": volume_24h}
    candidates.sort(reverse=True)
    return [product_id for _, _, product_id in candidates[:top_n]], meta


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "tactic",
        "target_pct",
        "stop_pct",
        "max_hold_bars",
        "signals",
        "win_rate_pct",
        "avg_net_return_pct",
        "median_hold_bars",
        "positive_products",
        "cumulative_net_pct",
        "top_products",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def _write_md(path: Path, *, hours: int, fee_bps_per_side: float, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Burst Grid",
        "",
        f"- Window: last `{hours}h`",
        "- Candle granularity: `FIVE_MINUTE`",
        f"- Fee assumption: `{fee_bps_per_side:.1f}` bps per side",
        "- Universe: top high-motion USD spot names above the volume floor",
        "- Goal: find capture geometry that survives fees, not just signal frequency",
        "",
        "| Tactic | Target % | Stop % | Hold Bars | Signals | Win Rate | Avg Net % | Positive Products | Cumulative Net % | Top Products |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows[:20]:
        lines.append(
            "| {tactic} | {target_pct:.2f}% | {stop_pct:.2f}% | {max_hold_bars} | {signals} | {win_rate_pct:.1f}% | {avg_net_return_pct:.3f}% | {positive_products} | {cumulative_net_pct:.3f}% | {top_products} |".format(
                tactic=row["tactic"],
                target_pct=row["target_pct"] * 100.0,
                stop_pct=row["stop_pct"] * 100.0,
                max_hold_bars=row["max_hold_bars"],
                signals=row["signals"],
                win_rate_pct=row["win_rate_pct"],
                avg_net_return_pct=row["avg_net_return_pct"],
                positive_products=row["positive_products"],
                cumulative_net_pct=row["cumulative_net_pct"],
                top_products=row["top_products"],
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    end_ts = int(time.time())
    start_ts = end_ts - int(args.hours) * 3600

    product_ids, _meta = _select_products(
        client,
        min_volume_24h=float(args.min_volume_24h),
        top_n=int(args.top_n),
    )
    candle_map: dict[str, list[Any]] = {}
    for product_id in product_ids:
        candles = _load_candles(
            client,
            product_id,
            start_ts=start_ts,
            end_ts=end_ts,
            granularity=str(args.granularity),
        )
        if len(candles) >= 20:
            candle_map[product_id] = candles

    rows: list[dict[str, Any]] = []
    for config in _iter_configs():
        product_net: dict[str, float] = defaultdict(float)
        product_wins: dict[str, int] = defaultdict(int)
        events: list[Any] = []
        for product_id, candles in candle_map.items():
            signal_fn = config["signal_fn"]
            for idx in range(len(candles)):
                if not signal_fn(candles, idx):
                    continue
                event = evaluate_forward_path(
                    candles,
                    idx,
                    target_pct=float(config["target_pct"]),
                    stop_pct=float(config["stop_pct"]),
                    max_hold_bars=int(config["max_hold_bars"]),
                    fee_bps_per_side=float(args.fee_bps_per_side),
                )
                product_net[product_id] += event.net_return_pct * 100.0
                if event.net_return_pct > 0.0:
                    product_wins[product_id] += 1
                events.append(event)

        if not events:
            continue

        positive_products = [product_id for product_id, net in product_net.items() if net > 0.0]
        ranked_products = sorted(product_net.items(), key=lambda item: item[1], reverse=True)
        rows.append(
            {
                "tactic": config["tactic"],
                "target_pct": config["target_pct"],
                "stop_pct": config["stop_pct"],
                "max_hold_bars": config["max_hold_bars"],
                "signals": len(events),
                "win_rate_pct": (sum(1 for event in events if event.net_return_pct > 0.0) / len(events)) * 100.0,
                "avg_net_return_pct": statistics.fmean(event.net_return_pct for event in events) * 100.0,
                "median_hold_bars": statistics.median(event.bars_held for event in events),
                "positive_products": len(positive_products),
                "cumulative_net_pct": sum(product_net.values()),
                "top_products": ", ".join(product_id for product_id, _ in ranked_products[:5]),
            }
        )

    rows.sort(key=lambda row: (row["positive_products"], row["cumulative_net_pct"], row["avg_net_return_pct"]), reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, rows)
    _write_md(md_path, hours=int(args.hours), fee_bps_per_side=float(args.fee_bps_per_side), rows=rows)
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows[:20]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
