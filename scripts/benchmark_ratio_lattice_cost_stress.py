#!/usr/bin/env python3
"""
Stress the tuned long-only ratio lattice winners under wider synthetic spot costs.

Reads the cost-aware execution audit and then:
- selects each pair's tuned winner under the current 40bps-per-leg scenario
- compares it against the old 1.002 / max-level baseline
- sweeps fee ladders and spread-multiplier ladders
- reports survival ceilings and robust-positive coverage

Outputs:
- reports/ratio_lattice_cost_stress.csv
- reports/ratio_lattice_cost_stress.md
- reports/ratio_lattice_cost_stress.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_JSON = ROOT / "reports" / "ratio_lattice_execution_audit.json"
DEFAULT_CSV = ROOT / "reports" / "ratio_lattice_cost_stress.csv"
DEFAULT_MD = ROOT / "reports" / "ratio_lattice_cost_stress.md"
DEFAULT_JSON = ROOT / "reports" / "ratio_lattice_cost_stress.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress tuned ratio lattice winners under synthetic cost widening.")
    parser.add_argument("--audit-json", default=str(DEFAULT_AUDIT_JSON))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV))
    parser.add_argument("--md-path", default=str(DEFAULT_MD))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON))
    parser.add_argument("--fee-bps-grid", nargs="*", type=float, default=[0.0, 10.0, 25.0, 40.0, 60.0, 80.0, 100.0])
    parser.add_argument("--spread-multipliers", nargs="*", type=float, default=[1.0, 1.5, 2.0, 3.0, 5.0, 8.0])
    parser.add_argument("--selection-fee-bps", type=float, default=40.0, help="Use this fee scenario to select each pair's tuned winner from the audit")
    return parser.parse_args()


def synthetic_round_trip_cost_fraction(
    *,
    mid_a: float,
    spread_bps_mid_a: float,
    mid_b: float,
    spread_bps_mid_b: float,
    spread_multiplier: float,
    fee_bps_per_leg: float,
) -> float:
    spread_frac_a = (spread_bps_mid_a / 10000.0) * float(spread_multiplier)
    spread_frac_b = (spread_bps_mid_b / 10000.0) * float(spread_multiplier)

    bid_a = mid_a * (1.0 - spread_frac_a / 2.0)
    ask_a = mid_a * (1.0 + spread_frac_a / 2.0)
    bid_b = mid_b * (1.0 - spread_frac_b / 2.0)
    ask_b = mid_b * (1.0 + spread_frac_b / 2.0)
    fee_rate = fee_bps_per_leg / 10000.0

    usd_after_sell_b = bid_b * (1.0 - fee_rate)
    units_a = (usd_after_sell_b / ask_a) * (1.0 - fee_rate)
    usd_after_sell_a = (units_a * bid_a) * (1.0 - fee_rate)
    final_b_units = (usd_after_sell_a / ask_b) * (1.0 - fee_rate)
    return max(0.0, 1.0 - final_b_units)


def load_audit(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_candidates(rows: list[dict[str, Any]], selection_fee_bps: float) -> list[dict[str, Any]]:
    fee_tag = str(selection_fee_bps).replace(".", "_")
    net_key = f"fee_{fee_tag}_net_pnl_den"
    pairs = sorted(set(row["pair"] for row in rows))
    selected: list[dict[str, Any]] = []

    for pair in pairs:
        pair_rows = [row for row in rows if row["pair"] == pair]
        baseline_candidates = [row for row in pair_rows if math.isclose(float(row["profit_threshold"]), 1.002, rel_tol=0.0, abs_tol=1e-12)]
        baseline = max(baseline_candidates, key=lambda row: int(row["max_levels"]))
        tuned = max(pair_rows, key=lambda row: float(row[net_key]))
        selected.append({"variant": "baseline", **baseline})
        selected.append({"variant": "tuned", **tuned})
    return selected


def stress_rows(
    candidates: list[dict[str, Any]],
    quote_snapshot: dict[str, dict[str, float]],
    *,
    fee_bps_grid: list[float],
    spread_multipliers: list[float],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    detailed_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for candidate in candidates:
        pair = candidate["pair"]
        symbol_a = candidate["symbol_a"]
        symbol_b = candidate["symbol_b"]
        product_a = f"{symbol_a}-USD"
        product_b = f"{symbol_b}-USD"
        quote_a = quote_snapshot[product_a]
        quote_b = quote_snapshot[product_b]
        closes = int(candidate["total_closes"])
        gross_pnl = float(candidate["gross_realized_pnl_den"])
        position_size = 0.01  # durable audit used fixed 0.01 denominator units

        positive_scenarios = 0
        total_scenarios = 0
        max_positive_fee_at_1x: float | None = None
        max_positive_spread_at_40: float | None = None

        for fee_bps in fee_bps_grid:
            for spread_multiplier in spread_multipliers:
                cost_fraction = synthetic_round_trip_cost_fraction(
                    mid_a=float(quote_a["mid"]),
                    spread_bps_mid_a=float(quote_a["spread_bps_mid"]),
                    mid_b=float(quote_b["mid"]),
                    spread_bps_mid_b=float(quote_b["spread_bps_mid"]),
                    spread_multiplier=float(spread_multiplier),
                    fee_bps_per_leg=float(fee_bps),
                )
                cost_per_close = position_size * cost_fraction
                net_pnl = gross_pnl - closes * cost_per_close
                is_positive = net_pnl > 0.0
                positive_scenarios += 1 if is_positive else 0
                total_scenarios += 1

                if math.isclose(spread_multiplier, 1.0, rel_tol=0.0, abs_tol=1e-12) and is_positive:
                    max_positive_fee_at_1x = fee_bps
                if math.isclose(fee_bps, 40.0, rel_tol=0.0, abs_tol=1e-12) and is_positive:
                    max_positive_spread_at_40 = spread_multiplier

                detailed_rows.append(
                    {
                        "pair": pair,
                        "variant": candidate["variant"],
                        "profit_threshold": candidate["profit_threshold"],
                        "max_levels": candidate["max_levels"],
                        "gross_realized_pnl_den": gross_pnl,
                        "total_closes": closes,
                        "fee_bps_per_leg": fee_bps,
                        "spread_multiplier": spread_multiplier,
                        "cost_fraction_bps": cost_fraction * 10000.0,
                        "cost_per_close_den": cost_per_close,
                        "net_pnl_den": net_pnl,
                        "avg_net_per_close_den": net_pnl / closes if closes else 0.0,
                        "positive": is_positive,
                    }
                )

        summary_rows.append(
            {
                "pair": pair,
                "variant": candidate["variant"],
                "profit_threshold": candidate["profit_threshold"],
                "max_levels": candidate["max_levels"],
                "gross_realized_pnl_den": gross_pnl,
                "total_closes": closes,
                "positive_scenarios": positive_scenarios,
                "total_scenarios": total_scenarios,
                "positive_ratio": positive_scenarios / total_scenarios if total_scenarios else 0.0,
                "max_positive_fee_bps_at_1x": max_positive_fee_at_1x,
                "max_positive_spread_multiplier_at_40bps": max_positive_spread_at_40,
            }
        )

    summary_rows.sort(key=lambda row: (row["pair"], row["variant"]))
    return detailed_rows, summary_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "pair",
        "variant",
        "profit_threshold",
        "max_levels",
        "gross_realized_pnl_den",
        "total_closes",
        "fee_bps_per_leg",
        "spread_multiplier",
        "cost_fraction_bps",
        "cost_per_close_den",
        "net_pnl_den",
        "avg_net_per_close_den",
        "positive",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_markdown(path: Path, summary_rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pair_to_rows: dict[str, dict[str, dict[str, Any]]] = {}
    for row in summary_rows:
        pair_to_rows.setdefault(str(row["pair"]), {})[str(row["variant"])] = row

    tuned_rank = sorted(
        [row for row in summary_rows if row["variant"] == "tuned"],
        key=lambda row: (float(row["positive_ratio"]), float(row["gross_realized_pnl_den"])),
        reverse=True,
    )

    lines = [
        "# Ratio Lattice Cost Stress",
        "",
        "- This stress pass starts from the first cost-aware relationship audit and asks a narrower question: how wide can fees and spreads get before the tuned long-only ratio winners stop being positive?",
        "- Variants: `baseline` = old `1.002 / max-level` default for that pair, `tuned` = best row under the current `40 bps` per-leg audit.",
        "",
        "## Tuned Winner Robustness",
        "",
        "| Pair | Tuned Shape | Positive Scenarios | Positivity | Max Fee @ 1x Spread | Max Spread @ 40bps |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in tuned_rank:
        fee_text = "none" if row["max_positive_fee_bps_at_1x"] is None else f"{row['max_positive_fee_bps_at_1x']:.0f}bps"
        spread_text = "none" if row["max_positive_spread_multiplier_at_40bps"] is None else f"{row['max_positive_spread_multiplier_at_40bps']:.1f}x"
        lines.append(
            f"| `{row['pair']}` | `thr={row['profit_threshold']:.3f} levels={row['max_levels']}` | "
            f"`{row['positive_scenarios']}/{row['total_scenarios']}` | `{row['positive_ratio']:.1%}` | `{fee_text}` | `{spread_text}` |"
        )

    lines.extend(["", "## Pair Reads", ""])
    for pair, variants in sorted(pair_to_rows.items()):
        baseline = variants.get("baseline")
        tuned = variants.get("tuned")
        if not baseline or not tuned:
            continue
        delta = float(tuned["positive_ratio"]) - float(baseline["positive_ratio"])
        lines.append(
            f"- `{pair}`: tuned `thr={tuned['profit_threshold']:.3f} levels={tuned['max_levels']}` stays positive in "
            f"`{tuned['positive_scenarios']}/{tuned['total_scenarios']}` stress scenarios "
            f"({tuned['positive_ratio']:.1%}) versus baseline `{baseline['positive_scenarios']}/{baseline['total_scenarios']}` "
            f"({baseline['positive_ratio']:.1%}), delta `{delta:+.1%}`."
        )

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- If a tuned row survives more fee and spread regimes than its baseline, the edge is becoming less path-dependent and more operationally real.",
            "- If a pair only survives at `1x` spread and low fees, it is still a lab curiosity even if the current quote snapshot is positive.",
            "- If deeper exits widen the positive stress envelope, the relationship lane wants patience more than density.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    audit = load_audit(Path(args.audit_json))
    rows = audit["rows"]
    quote_snapshot = audit["quote_snapshot"]

    candidates = select_candidates(rows, float(args.selection_fee_bps))
    detailed_rows, summary_rows = stress_rows(
        candidates,
        quote_snapshot,
        fee_bps_grid=[float(x) for x in args.fee_bps_grid],
        spread_multipliers=[float(x) for x in args.spread_multipliers],
    )

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    json_path = Path(args.json_path)
    write_csv(csv_path, detailed_rows)
    write_markdown(md_path, summary_rows)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "run_params": {
                    "audit_json": str(args.audit_json),
                    "fee_bps_grid": args.fee_bps_grid,
                    "spread_multipliers": args.spread_multipliers,
                    "selection_fee_bps": args.selection_fee_bps,
                },
                "summary_rows": summary_rows,
                "detailed_rows": detailed_rows,
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
