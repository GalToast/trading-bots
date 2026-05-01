#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import load_bars
from penetration_lattice_lab_v3_bounded import simulate_symbol as simulate_v3_bounded
from sweep_stateful_rearm_churn import VARIANTS, simulate_stateful_rearm


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark a cleaner hybrid basket: GBPUSD/EURUSD on stateful re-arm, USDJPY on bounded V3."
    )
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument(
        "--output-csv",
        default=str(ROOT / "reports" / "stateful_hybrid_apex.csv"),
    )
    return parser.parse_args()


def make_usdjpy_v3_cfg():
    return type(
        "V3Cfg",
        (),
        {
            "step_pips": 0.5,
            "max_open_per_side": 20,
            "max_floating_loss_usd": -10.0,
            "vwap_lookback": 20,
            "regime_lookback_bars": 60,
            "max_range_pips": 24.0,
            "breakout_buffer_pips": 5.0,
            "max_lattice_window_bars": 240,
            "cooldown_bars": 60,
            "adaptive_step_threshold_1": 10,
            "adaptive_step_threshold_2": 20,
            "adaptive_step_multiplier_1": 1.5,
            "adaptive_step_multiplier_2": 2.0,
        },
    )()


def main() -> int:
    args = parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        gbp_info = mt5.symbol_info("GBPUSD")
        eur_info = mt5.symbol_info("EURUSD")
        jpy_info = mt5.symbol_info("USDJPY")
        if not gbp_info or not eur_info or not jpy_info:
            print("Missing symbol info")
            return 1

        gbp_bars = load_bars("GBPUSD", args.days)
        eur_bars = load_bars("EURUSD", args.days)
        jpy_bars = load_bars("USDJPY", args.days)
        if not gbp_bars or not eur_bars or not jpy_bars:
            print("Missing bars")
            return 1

        baseline_gbp = simulate_raw_close2("GBPUSD", gbp_bars, gbp_info, RawConfig(step_pips=2.0, max_open_per_side=20, close_mode="two_level"))
        baseline_eur = simulate_raw_close2("EURUSD", eur_bars, eur_info, RawConfig(step_pips=3.0, max_open_per_side=20, close_mode="two_level"))
        baseline_jpy = simulate_v3_bounded("USDJPY", jpy_bars, jpy_info, make_usdjpy_v3_cfg())

        baseline_total = (
            float(baseline_gbp["combined_net_usd"])
            + float(baseline_eur["combined_net_usd"])
            + float(baseline_jpy["combined_net_usd"])
        )

        rows: list[dict] = []
        for variant in VARIANTS:
            gbp_variant = simulate_stateful_rearm(
                "GBPUSD",
                gbp_bars,
                gbp_info,
                RawConfig(step_pips=2.0, max_open_per_side=20, close_mode="two_level"),
                variant,
            )
            eur_variant = simulate_stateful_rearm(
                "EURUSD",
                eur_bars,
                eur_info,
                RawConfig(step_pips=3.0, max_open_per_side=20, close_mode="two_level"),
                variant,
            )
            hybrid_total = (
                float(gbp_variant["combined_net_usd"])
                + float(eur_variant["combined_net_usd"])
                + float(baseline_jpy["combined_net_usd"])
            )
            rows.append(
                {
                    "variant": variant.name,
                    "baseline_total_usd": round(baseline_total, 3),
                    "hybrid_total_usd": round(hybrid_total, 3),
                    "delta_total_usd": round(hybrid_total - baseline_total, 3),
                    "gbp_baseline_usd": baseline_gbp["combined_net_usd"],
                    "gbp_variant_usd": gbp_variant["combined_net_usd"],
                    "gbp_delta_usd": round(gbp_variant["combined_net_usd"] - baseline_gbp["combined_net_usd"], 3),
                    "eur_baseline_usd": baseline_eur["combined_net_usd"],
                    "eur_variant_usd": eur_variant["combined_net_usd"],
                    "eur_delta_usd": round(eur_variant["combined_net_usd"] - baseline_eur["combined_net_usd"], 3),
                    "jpy_bounded_usd": baseline_jpy["combined_net_usd"],
                }
            )

        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [
                "variant",
                "baseline_total_usd",
                "hybrid_total_usd",
                "delta_total_usd",
                "gbp_baseline_usd",
                "gbp_variant_usd",
                "gbp_delta_usd",
                "eur_baseline_usd",
                "eur_variant_usd",
                "eur_delta_usd",
                "jpy_bounded_usd",
            ])
            writer.writeheader()
            writer.writerows(rows)

        print(f"Wrote {out_path}")
        for row in rows:
            print(
                f"{row['variant']}: hybrid={row['hybrid_total_usd']} delta={row['delta_total_usd']} "
                f"gbp_delta={row['gbp_delta_usd']} eur_delta={row['eur_delta_usd']} jpy={row['jpy_bounded_usd']}"
            )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
