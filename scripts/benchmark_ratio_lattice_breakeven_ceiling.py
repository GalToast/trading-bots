#!/usr/bin/env python3
"""
Compute exact break-even friction ceilings for tuned long-only ratio winners.

Reads the cost-aware ratio audit and derives:
- break-even round-trip cost budget per close
- max per-leg fee at zero spread
- max per-leg fee at current spread snapshot
- max spread multiplier at zero fee
- max spread multiplier at 40bps per leg

Outputs:
- reports/ratio_lattice_breakeven_ceiling.csv
- reports/ratio_lattice_breakeven_ceiling.md
- reports/ratio_lattice_breakeven_ceiling.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_JSON = ROOT / "reports" / "ratio_lattice_execution_audit.json"
DEFAULT_CSV = ROOT / "reports" / "ratio_lattice_breakeven_ceiling.csv"
DEFAULT_MD = ROOT / "reports" / "ratio_lattice_breakeven_ceiling.md"
DEFAULT_JSON = ROOT / "reports" / "ratio_lattice_breakeven_ceiling.json"
POSITION_SIZE_DEN = 0.01


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute break-even friction ceilings for long-only ratio lattice winners.")
    parser.add_argument("--audit-json", default=str(DEFAULT_AUDIT_JSON))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV))
    parser.add_argument("--md-path", default=str(DEFAULT_MD))
    parser.add_argument("--json-path", default=str(DEFAULT_JSON))
    parser.add_argument("--selection-fee-bps", type=float, default=40.0, help="Use this audit fee scenario to select tuned rows")
    parser.add_argument("--fee-reference-bps", type=float, default=40.0, help="Reference fee scenario for spread ceiling")
    parser.add_argument("--max-search-fee-bps", type=float, default=400.0)
    parser.add_argument("--max-search-spread-multiplier", type=float, default=100.0)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_candidates(rows: list[dict[str, Any]], selection_fee_bps: float) -> list[dict[str, Any]]:
    fee_tag = str(selection_fee_bps).replace(".", "_")
    net_key = f"fee_{fee_tag}_net_pnl_den"
    selected: list[dict[str, Any]] = []
    for pair in sorted(set(row["pair"] for row in rows)):
        pair_rows = [row for row in rows if row["pair"] == pair]
        baseline_rows = [row for row in pair_rows if math.isclose(float(row["profit_threshold"]), 1.002, rel_tol=0.0, abs_tol=1e-12)]
        baseline = max(baseline_rows, key=lambda row: int(row["max_levels"]))
        tuned = max(pair_rows, key=lambda row: float(row[net_key]))
        selected.append({"variant": "baseline", **baseline})
        selected.append({"variant": "tuned", **tuned})
    return selected


def synthetic_round_trip_cost_fraction(
    *,
    mid_a: float,
    spread_bps_mid_a: float,
    mid_b: float,
    spread_bps_mid_b: float,
    spread_multiplier: float,
    fee_bps_per_leg: float,
) -> float:
    spread_frac_a = (spread_bps_mid_a / 10000.0) * spread_multiplier
    spread_frac_b = (spread_bps_mid_b / 10000.0) * spread_multiplier

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


def binary_search_ceiling(
    fn: Callable[[float], float],
    *,
    lo: float,
    hi: float,
    target: float,
    steps: int = 60,
) -> float | None:
    if fn(lo) > target:
        return None
    if fn(hi) <= target:
        return hi
    left = lo
    right = hi
    for _ in range(steps):
        mid = (left + right) / 2.0
        if fn(mid) <= target:
            left = mid
        else:
            right = mid
    return left


def build_rows(
    candidates: list[dict[str, Any]],
    quote_snapshot: dict[str, dict[str, float]],
    *,
    fee_reference_bps: float,
    max_search_fee_bps: float,
    max_search_spread_multiplier: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        symbol_a = candidate["symbol_a"]
        symbol_b = candidate["symbol_b"]
        pair = candidate["pair"]
        product_a = f"{symbol_a}-USD"
        product_b = f"{symbol_b}-USD"
        quote_a = quote_snapshot[product_a]
        quote_b = quote_snapshot[product_b]
        gross = float(candidate["gross_realized_pnl_den"])
        closes = int(candidate["total_closes"])
        budget_per_close = gross / closes if closes else 0.0
        budget_fraction = budget_per_close / POSITION_SIZE_DEN if POSITION_SIZE_DEN > 0 else 0.0
        budget_bps = budget_fraction * 10000.0

        fee_at_zero_spread = binary_search_ceiling(
            lambda fee_bps: synthetic_round_trip_cost_fraction(
                mid_a=float(quote_a["mid"]),
                spread_bps_mid_a=float(quote_a["spread_bps_mid"]),
                mid_b=float(quote_b["mid"]),
                spread_bps_mid_b=float(quote_b["spread_bps_mid"]),
                spread_multiplier=0.0,
                fee_bps_per_leg=fee_bps,
            ),
            lo=0.0,
            hi=max_search_fee_bps,
            target=budget_fraction,
        )

        fee_at_current_spread = binary_search_ceiling(
            lambda fee_bps: synthetic_round_trip_cost_fraction(
                mid_a=float(quote_a["mid"]),
                spread_bps_mid_a=float(quote_a["spread_bps_mid"]),
                mid_b=float(quote_b["mid"]),
                spread_bps_mid_b=float(quote_b["spread_bps_mid"]),
                spread_multiplier=1.0,
                fee_bps_per_leg=fee_bps,
            ),
            lo=0.0,
            hi=max_search_fee_bps,
            target=budget_fraction,
        )

        spread_at_zero_fee = binary_search_ceiling(
            lambda spread_mult: synthetic_round_trip_cost_fraction(
                mid_a=float(quote_a["mid"]),
                spread_bps_mid_a=float(quote_a["spread_bps_mid"]),
                mid_b=float(quote_b["mid"]),
                spread_bps_mid_b=float(quote_b["spread_bps_mid"]),
                spread_multiplier=spread_mult,
                fee_bps_per_leg=0.0,
            ),
            lo=0.0,
            hi=max_search_spread_multiplier,
            target=budget_fraction,
        )

        spread_at_ref_fee = binary_search_ceiling(
            lambda spread_mult: synthetic_round_trip_cost_fraction(
                mid_a=float(quote_a["mid"]),
                spread_bps_mid_a=float(quote_a["spread_bps_mid"]),
                mid_b=float(quote_b["mid"]),
                spread_bps_mid_b=float(quote_b["spread_bps_mid"]),
                spread_multiplier=spread_mult,
                fee_bps_per_leg=fee_reference_bps,
            ),
            lo=0.0,
            hi=max_search_spread_multiplier,
            target=budget_fraction,
        )

        rows.append(
            {
                "pair": pair,
                "variant": candidate["variant"],
                "profit_threshold": candidate["profit_threshold"],
                "max_levels": candidate["max_levels"],
                "gross_realized_pnl_den": gross,
                "total_closes": closes,
                "avg_gross_per_close_den": budget_per_close,
                "breakeven_round_trip_cost_fraction": budget_fraction,
                "breakeven_round_trip_cost_bps": budget_bps,
                "current_spread_bps_sum": float(quote_a["spread_bps_mid"]) + float(quote_b["spread_bps_mid"]),
                "max_fee_bps_per_leg_zero_spread": fee_at_zero_spread,
                "max_fee_bps_per_leg_current_spread": fee_at_current_spread,
                "max_spread_multiplier_zero_fee": spread_at_zero_fee,
                "max_spread_multiplier_ref_fee": spread_at_ref_fee,
                "reference_fee_bps": fee_reference_bps,
            }
        )
    rows.sort(key=lambda row: (row["pair"], row["variant"]))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "pair",
        "variant",
        "profit_threshold",
        "max_levels",
        "gross_realized_pnl_den",
        "total_closes",
        "avg_gross_per_close_den",
        "breakeven_round_trip_cost_fraction",
        "breakeven_round_trip_cost_bps",
        "current_spread_bps_sum",
        "max_fee_bps_per_leg_zero_spread",
        "max_fee_bps_per_leg_current_spread",
        "max_spread_multiplier_zero_fee",
        "max_spread_multiplier_ref_fee",
        "reference_fee_bps",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def fmt_value(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "none"
    return f"{value:.2f}{suffix}"


def write_markdown(path: Path, rows: list[dict[str, Any]], fee_reference_bps: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tuned_rows = [row for row in rows if row["variant"] == "tuned"]
    tuned_rows.sort(key=lambda row: float(row["breakeven_round_trip_cost_bps"]), reverse=True)
    by_pair = {row["pair"]: {} for row in rows}
    for row in rows:
        by_pair[row["pair"]][row["variant"]] = row

    lines = [
        "# Ratio Lattice Breakeven Ceiling",
        "",
        "- This report converts the tuned long-only ratio winners into friction budgets: how much total round-trip cost each close can absorb before the edge goes to zero.",
        "- `max_fee_bps_per_leg_zero_spread` is the pure fee ceiling if the venue gave perfect mid fills.",
        f"- `max_spread_multiplier_ref_fee` uses the current quote snapshot spreads with `{fee_reference_bps:.1f}` bps per leg.",
        "",
        "## Tuned Ceiling Rank",
        "",
        "| Pair | Tuned Shape | Gross/Close | Breakeven RT Cost | Max Fee/Leg @ Zero Spread | Max Fee/Leg @ Current Spread | Max Spread @ Zero Fee | Max Spread @ 40bps |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in tuned_rows:
        lines.append(
            f"| `{row['pair']}` | `thr={row['profit_threshold']:.3f} levels={row['max_levels']}` | "
            f"`{row['avg_gross_per_close_den']:.6f}` | `{row['breakeven_round_trip_cost_bps']:.2f}bps` | "
            f"`{fmt_value(row['max_fee_bps_per_leg_zero_spread'], 'bps')}` | "
            f"`{fmt_value(row['max_fee_bps_per_leg_current_spread'], 'bps')}` | "
            f"`{fmt_value(row['max_spread_multiplier_zero_fee'], 'x')}` | "
            f"`{fmt_value(row['max_spread_multiplier_ref_fee'], 'x')}` |"
        )

    lines.extend(["", "## Pair Reads", ""])
    for pair in sorted(by_pair):
        baseline = by_pair[pair].get("baseline")
        tuned = by_pair[pair].get("tuned")
        if not baseline or not tuned:
            continue
        delta_budget = float(tuned["breakeven_round_trip_cost_bps"]) - float(baseline["breakeven_round_trip_cost_bps"])
        lines.append(
            f"- `{pair}`: tuned `thr={tuned['profit_threshold']:.3f} levels={tuned['max_levels']}` lifts the per-close friction budget "
            f"from `{baseline['breakeven_round_trip_cost_bps']:.2f}bps` to `{tuned['breakeven_round_trip_cost_bps']:.2f}bps`, "
            f"delta `{delta_budget:+.2f}bps`."
        )

    lines.extend(
        [
            "",
            "## Read",
            "",
            "- High break-even cost budgets mean the pair can survive worse venues or sloppier execution before the lattice edge collapses.",
            "- If the tuned row raises the practical fee ceiling at current spread, it is not just prettier in replay; it is more executable.",
            "- This is still a snapshot-based ceiling model. Forward fills and capital coupling remain separate gates.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    audit = load_json(Path(args.audit_json))
    rows = audit["rows"]
    quote_snapshot = audit["quote_snapshot"]
    candidates = select_candidates(rows, float(args.selection_fee_bps))
    result_rows = build_rows(
        candidates,
        quote_snapshot,
        fee_reference_bps=float(args.fee_reference_bps),
        max_search_fee_bps=float(args.max_search_fee_bps),
        max_search_spread_multiplier=float(args.max_search_spread_multiplier),
    )

    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    json_path = Path(args.json_path)
    write_csv(csv_path, result_rows)
    write_markdown(md_path, result_rows, float(args.fee_reference_bps))
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            {
                "run_params": {
                    "audit_json": str(args.audit_json),
                    "selection_fee_bps": args.selection_fee_bps,
                    "fee_reference_bps": args.fee_reference_bps,
                    "max_search_fee_bps": args.max_search_fee_bps,
                    "max_search_spread_multiplier": args.max_search_spread_multiplier,
                },
                "rows": result_rows,
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
