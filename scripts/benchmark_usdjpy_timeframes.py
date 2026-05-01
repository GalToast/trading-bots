#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_usdjpy_same_bar_guard import Variant, make_cfg, simulate_variant


ROOT = Path(__file__).resolve().parent.parent

TIMEFRAMES: dict[str, tuple[int, int]] = {
    "M1": (mt5.TIMEFRAME_M1, 1),
    "M2": (mt5.TIMEFRAME_M2, 2),
    "M5": (mt5.TIMEFRAME_M5, 5),
    "M15": (mt5.TIMEFRAME_M15, 15),
}

VARIANTS = [
    Variant(name="gap1_hold0", close_gap=1, min_hold_bars=0),
    Variant(name="gap1_hold1", close_gap=1, min_hold_bars=1),
    Variant(name="gap2_hold0", close_gap=2, min_hold_bars=0),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quick USDJPY bounded-rearm timeframe sweep.")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "usdjpy_timeframe_sweep.csv"),
    )
    return parser.parse_args()


def load_bars_tf(symbol: str, timeframe_name: str, days: int) -> list[dict]:
    tf_const, minutes = TIMEFRAMES[timeframe_name]
    bars_needed = math.ceil((days * 1440) / minutes)
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, bars_needed)
    if rates is None:
        return []
    return [
        {
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "tick_volume": int(r[5]),
        }
        for r in rates
    ]


def main() -> int:
    args = parse_args()
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1
    try:
        info = mt5.symbol_info("USDJPY")
        if info is None:
            print("Missing USDJPY symbol info")
            return 1

        rows: list[dict] = []
        for tf_name in ("M1", "M2", "M5", "M15"):
            bars = load_bars_tf("USDJPY", tf_name, args.days)
            if not bars:
                continue
            baseline_total = None
            for variant in VARIANTS:
                result = simulate_variant("USDJPY", bars, info, make_cfg(), variant)
                row = {
                    "timeframe": tf_name,
                    "days": args.days,
                    "variant": variant.name,
                    "combined_net_usd": result["combined_net_usd"],
                    "realized_net_usd": result["realized_net_usd"],
                    "floating_net_usd": result["floating_net_usd"],
                    "realized_closes": result["realized_closes"],
                    "same_bar_closes": result["same_bar_closes"],
                    "pct_same_bar_of_closes": round((result["same_bar_closes"] / result["realized_closes"]) * 100.0, 1)
                    if result["realized_closes"] else 0.0,
                    "close_le_005": result["close_le_005"],
                    "close_le_010": result["close_le_010"],
                    "rearm_opens": result["rearm_opens"],
                    "max_open_total": result["max_open_total"],
                }
                if variant.name == "gap1_hold0":
                    baseline_total = float(result["combined_net_usd"])
                row["delta_vs_gap1_hold0"] = round(float(result["combined_net_usd"]) - float(baseline_total or 0.0), 3)
                rows.append(row)

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

        print(f"Wrote {out_path}")
        for tf_name in ("M1", "M2", "M5", "M15"):
            tf_rows = [r for r in rows if r["timeframe"] == tf_name]
            for row in tf_rows:
                print(
                    f"{tf_name} {row['variant']}: total={row['combined_net_usd']} "
                    f"delta={row['delta_vs_gap1_hold0']} same_bar={row['same_bar_closes']} "
                    f"pct_same_bar={row['pct_same_bar_of_closes']}"
                )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
