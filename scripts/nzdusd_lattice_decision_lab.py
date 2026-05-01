#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import ROOT, load_bars
from penetration_lattice_lab_v3_bounded import Config as BoundedConfig
from penetration_lattice_lab_v3_bounded import simulate_symbol as simulate_bounded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare NZDUSD lattice branches directly: raw close2, lighter-cap raw, "
            "and bounded V3 on the same sample."
        )
    )
    parser.add_argument("--symbol", default="NZDUSD")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--raw-step-pips", nargs="*", type=float, default=[1.0, 1.5, 2.0, 3.0, 5.0])
    parser.add_argument("--raw-caps", nargs="*", type=int, default=[8, 10, 12, 15, 20])
    parser.add_argument("--bounded-step-pips", nargs="*", type=float, default=[0.5, 1.0, 1.5, 2.0])
    parser.add_argument("--bounded-caps", nargs="*", type=int, default=[10, 15, 20])
    parser.add_argument("--bounded-ranges", nargs="*", type=float, default=[18.0, 24.0])
    parser.add_argument("--bounded-buffers", nargs="*", type=float, default=[3.0, 5.0])
    parser.add_argument("--bounded-windows", nargs="*", type=int, default=[120, 240])
    parser.add_argument("--bounded-cooldowns", nargs="*", type=int, default=[60])
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "nzdusd_lattice_decision_60d.csv"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        info = mt5.symbol_info(args.symbol)
        if info is None:
            print(f"Missing symbol info for {args.symbol}")
            return 1
        bars = load_bars(args.symbol, args.days)
        if not bars:
            print(f"No bars for {args.symbol}")
            return 1

        rows: list[dict] = []

        for step_pips in args.raw_step_pips:
            for cap in args.raw_caps:
                cfg = RawConfig(step_pips=step_pips, max_open_per_side=cap, close_mode="two_level")
                row = simulate_raw_close2(args.symbol, bars, info, cfg)
                row.update(
                    {
                        "symbol": args.symbol,
                        "variant": "raw_close2",
                        "days": args.days,
                        "step_pips": step_pips,
                        "max_open_per_side": cap,
                        "max_range_pips": "",
                        "breakout_buffer_pips": "",
                        "max_lattice_window_bars": "",
                        "cooldown_bars": "",
                        "safety_score": round(float(row["combined_net_usd"]) - abs(float(row.get("worst_floating_usd", 0.0))), 3),
                    }
                )
                rows.append(row)
                print(
                    f"RAW     step={step_pips:<4.1f} cap={cap:>2} combined={row['combined_net_usd']:+.2f} "
                    f"float={row['floating_net_usd']:+.2f} worst={row.get('worst_floating_usd', 0):+.2f} "
                    f"max_open={row['max_open_total']:>3}"
                )

        for step_pips in args.bounded_step_pips:
            for cap in args.bounded_caps:
                for max_range_pips in args.bounded_ranges:
                    for breakout_buffer_pips in args.bounded_buffers:
                        for max_lattice_window_bars in args.bounded_windows:
                            for cooldown_bars in args.bounded_cooldowns:
                                cfg = BoundedConfig(
                                    step_pips=step_pips,
                                    max_open_per_side=cap,
                                    max_floating_loss_usd=-10.0,
                                    vwap_lookback=20,
                                    regime_lookback_bars=60,
                                    max_range_pips=max_range_pips,
                                    breakout_buffer_pips=breakout_buffer_pips,
                                    max_lattice_window_bars=max_lattice_window_bars,
                                    cooldown_bars=cooldown_bars,
                                )
                                row = simulate_bounded(args.symbol, bars, info, cfg)
                                row.update(
                                    {
                                        "symbol": args.symbol,
                                        "variant": "v3_bounded",
                                        "days": args.days,
                                        "step_pips": step_pips,
                                        "max_open_per_side": cap,
                                        "max_range_pips": max_range_pips,
                                        "breakout_buffer_pips": breakout_buffer_pips,
                                        "max_lattice_window_bars": max_lattice_window_bars,
                                        "cooldown_bars": cooldown_bars,
                                        "safety_score": round(float(row["combined_net_usd"]) - abs(float(row.get("breakout_net_usd", 0.0))), 3),
                                    }
                                )
                                rows.append(row)
                                print(
                                    f"BOUND   step={step_pips:<4.1f} cap={cap:>2} range={max_range_pips:>4.0f} "
                                    f"buffer={breakout_buffer_pips:>3.0f} window={max_lattice_window_bars:>3} "
                                    f"combined={row['combined_net_usd']:+.2f} breakout={row.get('breakout_net_usd', 0):+.2f} "
                                    f"worst={row.get('worst_floating_usd', 0):+.2f} max_open={row['max_open_total']:>3}"
                                )

        rows.sort(key=lambda row: float(row["safety_score"]), reverse=True)
        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            fieldnames = sorted({key for row in rows for key in row.keys()})
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            print(f"Saved {output_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
