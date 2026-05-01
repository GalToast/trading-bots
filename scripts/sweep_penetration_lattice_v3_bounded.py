#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v3_bounded import (
    Config,
    DEFAULT_SYMBOLS,
    ROOT,
    load_bars,
    simulate_symbol,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sweep bounded penetration lattice regime parameters."
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--step-pips", nargs="*", type=float, default=[1.0, 2.0])
    parser.add_argument("--max-range-pips", nargs="*", type=float, default=[12.0, 18.0, 24.0])
    parser.add_argument("--breakout-buffer-pips", nargs="*", type=float, default=[2.0, 3.0, 5.0])
    parser.add_argument("--max-lattice-window-bars", nargs="*", type=int, default=[120, 240, 480])
    parser.add_argument("--cooldown-bars", nargs="*", type=int, default=[30, 60])
    parser.add_argument("--max-open-per-side", type=int, default=20)
    parser.add_argument("--max-floating-loss-usd", type=float, default=-10.0)
    parser.add_argument("--vwap-lookback", type=int, default=20)
    parser.add_argument("--regime-lookback-bars", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "penetration_lattice_v3_bounded_sweep.csv"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict] = []
        for symbol in args.symbols:
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            if not bars:
                continue
            for step_pips in args.step_pips:
                for max_range_pips in args.max_range_pips:
                    for breakout_buffer_pips in args.breakout_buffer_pips:
                        for max_lattice_window_bars in args.max_lattice_window_bars:
                            for cooldown_bars in args.cooldown_bars:
                                cfg = Config(
                                    step_pips=step_pips,
                                    max_open_per_side=args.max_open_per_side,
                                    max_floating_loss_usd=args.max_floating_loss_usd,
                                    vwap_lookback=args.vwap_lookback,
                                    regime_lookback_bars=args.regime_lookback_bars,
                                    max_range_pips=max_range_pips,
                                    breakout_buffer_pips=breakout_buffer_pips,
                                    max_lattice_window_bars=max_lattice_window_bars,
                                    cooldown_bars=cooldown_bars,
                                )
                                row = simulate_symbol(symbol, bars, info, cfg)
                                row["days"] = args.days
                                row["step_pips"] = step_pips
                                row["max_range_pips"] = max_range_pips
                                row["breakout_buffer_pips"] = breakout_buffer_pips
                                row["max_lattice_window_bars"] = max_lattice_window_bars
                                row["cooldown_bars"] = cooldown_bars
                                row["score"] = round(
                                    float(row["combined_net_usd"]) - abs(float(row["breakout_net_usd"])),
                                    3,
                                )
                                rows.append(row)
                                print(
                                    f"{symbol:<7} step={step_pips:g} range={max_range_pips:>4.0f} "
                                    f"buffer={breakout_buffer_pips:>3.0f} window={max_lattice_window_bars:>3} "
                                    f"cool={cooldown_bars:>3} combined={row['combined_net_usd']:+.2f} "
                                    f"breakout={row['breakout_net_usd']:+.2f} max_open={row['max_open_total']:>3}"
                                )

        rows.sort(key=lambda row: row["score"], reverse=True)
        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            with output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"Saved {output_path}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
