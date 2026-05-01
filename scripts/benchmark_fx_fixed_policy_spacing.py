#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import MetaTrader5 as mt5

from benchmark_fx_fixed_step_close_policy import ClosePolicy, simulate_close_policy
from benchmark_inside_geometry_churn import default_raw_configs
from penetration_lattice_hybrid_apex import RawConfig
from penetration_lattice_lab_v2 import load_bars


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_CSV = ROOT / "reports" / "fx_fixed_policy_spacing_ladder.csv"
DEFAULT_OUTPUT_MD = ROOT / "reports" / "fx_fixed_policy_spacing_ladder.md"

FIXED_POLICIES: dict[str, ClosePolicy] = {
    "GBPUSD": ClosePolicy(name="allprof_gap1_alpha50", close_gap=1, close_alpha=0.5, close_style="all_profitable"),
    "EURUSD": ClosePolicy(name="outer_gap2_alpha50", close_gap=2, close_alpha=0.5, close_style="outer"),
    "NZDUSD": ClosePolicy(name="allprof_gap1_alpha50", close_gap=1, close_alpha=0.5, close_style="all_profitable"),
}

STEP_LADDERS: dict[str, list[float]] = {
    "GBPUSD": [1.0, 1.5, 2.0, 2.5, 3.0],
    "EURUSD": [1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
    "NZDUSD": [0.5, 1.0, 1.5, 2.0, 2.5],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark FX spacing with stronger close policy held fixed.")
    parser.add_argument("--symbols", nargs="*", default=["GBPUSD", "EURUSD", "NZDUSD"])
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_CSV))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    return parser.parse_args()


def build_markdown(rows: list[dict[str, str]]) -> str:
    by_symbol: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], []).append(row)

    lines: list[str] = []
    lines.append("# FX Fixed-Policy Spacing Ladder")
    lines.append("")
    lines.append("This ladder holds the stronger close policy fixed per symbol and varies only step size.")
    lines.append("")
    lines.append("## Best By Symbol")
    lines.append("")

    basket_baseline = 0.0
    basket_best = 0.0
    for symbol in ("GBPUSD", "EURUSD", "NZDUSD"):
        symbol_rows = by_symbol.get(symbol, [])
        if not symbol_rows:
            continue
        baseline_row = next(row for row in symbol_rows if row["is_validated_default"] == "1")
        best_row = max(symbol_rows, key=lambda row: float(row["variant_combined_usd"]))
        basket_baseline += float(baseline_row["variant_combined_usd"])
        basket_best += float(best_row["variant_combined_usd"])
        lines.append(
            f"- `{symbol}` fixed policy `{best_row['policy']}`: best step is `{best_row['step_pips']}` -> "
            f"`${float(best_row['variant_combined_usd']):.2f}`. Validated default step `{baseline_row['step_pips']}` "
            f"was `${float(baseline_row['variant_combined_usd']):.2f}`, delta `${float(best_row['delta_vs_validated_step']):.2f}`."
        )

    lines.append("")
    lines.append("## Basket Read")
    lines.append("")
    lines.append(
        f"- Independent best-step basket under fixed close policies: `${basket_best:.2f}` vs "
        f"validated-step fixed-policy basket `${basket_baseline:.2f}`, delta `${basket_best - basket_baseline:.2f}`."
    )
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- If the best-step deltas are small, close policy is still the dominant lever and spacing should stay conservative.")
    lines.append("- If a symbol moves materially at fixed close policy, that symbol has a real spacing problem instead of a close-policy problem.")
    lines.append("- Use this ladder before any asymmetric side-spacing experiments; it answers whether symmetric step still matters first.")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    cfg_map = default_raw_configs()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        rows: list[dict[str, object]] = []
        for symbol in args.symbols:
            if symbol not in cfg_map or symbol not in FIXED_POLICIES:
                continue
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            policy = FIXED_POLICIES[symbol]
            validated_step = cfg_map[symbol].step_pips
            validated_cap = cfg_map[symbol].max_open_per_side

            ladder_results: list[tuple[float, dict[str, object]]] = []
            for step in STEP_LADDERS[symbol]:
                cfg = RawConfig(step_pips=step, max_open_per_side=validated_cap, close_mode=cfg_map[symbol].close_mode)
                result = simulate_close_policy(symbol, bars, info, cfg, policy)
                ladder_results.append((step, result))

            validated_result = next(result for step, result in ladder_results if step == validated_step)
            for step, result in ladder_results:
                rows.append(
                    {
                        "symbol": symbol,
                        "policy": policy.name,
                        "days": args.days,
                        "step_pips": step,
                        "max_open_per_side": validated_cap,
                        "variant_combined_usd": result["combined_net_usd"],
                        "variant_realized_usd": result["realized_net_usd"],
                        "variant_floating_usd": result["floating_net_usd"],
                        "variant_closes": result["realized_closes"],
                        "close_events": result["close_events"],
                        "variant_max_open": result["max_open_total"],
                        "validated_step_combined_usd": validated_result["combined_net_usd"],
                        "delta_vs_validated_step": round(float(result["combined_net_usd"]) - float(validated_result["combined_net_usd"]), 3),
                        "is_validated_default": 1 if step == validated_step else 0,
                    }
                )

        out_csv = Path(args.output_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "symbol",
                    "policy",
                    "days",
                    "step_pips",
                    "max_open_per_side",
                    "variant_combined_usd",
                    "variant_realized_usd",
                    "variant_floating_usd",
                    "variant_closes",
                    "close_events",
                    "variant_max_open",
                    "validated_step_combined_usd",
                    "delta_vs_validated_step",
                    "is_validated_default",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

        markdown = build_markdown([{key: str(value) for key, value in row.items()} for row in rows])
        out_md = Path(args.output_md)
        out_md.write_text(markdown, encoding="utf-8")

        print(f"Wrote {out_csv}")
        print(f"Wrote {out_md}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
