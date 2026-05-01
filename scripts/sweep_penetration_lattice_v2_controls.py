#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import (
    Config,
    DEFAULT_SYMBOLS,
    ROOT,
    load_bars,
    simulate_symbol,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep penetration lattice v2 control geometry: floating-loss stop and "
            "time flush horizon."
        )
    )
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--floating-losses",
        nargs="*",
        type=float,
        default=[-3.0, -5.0, -7.0, -10.0],
        help="Per-ticket loss thresholds to trigger whole-book forced unwind.",
    )
    parser.add_argument(
        "--hold-bars",
        nargs="*",
        type=int,
        default=[0, 4320],
        help="Ticket age flush thresholds in bars. Use 0 to disable time flush.",
    )
    parser.add_argument("--step-pips", type=float, default=1.0)
    parser.add_argument("--anchor-reset-pips", type=float, default=3.0)
    parser.add_argument("--max-open-per-side", type=int, default=50)
    parser.add_argument("--vwap-lookback", type=int, default=20)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "penetration_lattice_v2_control_sweep.csv"),
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
            for floating_loss in args.floating_losses:
                for hold_bars in args.hold_bars:
                    cfg = Config(
                        step_pips=args.step_pips,
                        anchor_reset_pips=args.anchor_reset_pips,
                        max_open_per_side=args.max_open_per_side,
                        max_floating_loss_usd=floating_loss,
                        vwap_lookback=args.vwap_lookback,
                        max_hold_bars=hold_bars,
                    )
                    row = simulate_symbol(symbol, bars, info, cfg)
                    row["days"] = args.days
                    row["max_floating_loss_usd"] = floating_loss
                    row["max_hold_bars"] = hold_bars
                    row["score"] = round(
                        float(row["combined_net_usd"]) - abs(float(row["forced_net_usd"])),
                        3,
                    )
                    rows.append(row)
                    print(
                        f"{symbol:<7} stop={floating_loss:>5.1f} hold={hold_bars:>4} "
                        f"combined={row['combined_net_usd']:+.2f} forced={row['forced_net_usd']:+.2f} "
                        f"time={row['time_flush_net_usd']:+.2f} float={row['floating_net_usd']:+.2f} "
                        f"max_open={row['max_open_total']:>3}"
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
