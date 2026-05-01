#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "reports" / "stopless_lattice_experiment_board.md"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_float(value: str | float | int | None) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def parse_int(value: str | float | int | None) -> int:
    if value in (None, ""):
        return 0
    return int(float(value))


def alpha_findings(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for symbol in ("GBPUSD", "EURUSD", "NZDUSD"):
        candidates = [
            row
            for row in rows
            if row["symbol"] == symbol
            and parse_int(row.get("days")) == 7
            and row["variant"].startswith("cool12_alpha")
            and row["variant"] != "cool12_alpha50_noNZD"
        ]
        if not candidates:
            continue
        best = max(candidates, key=lambda row: parse_float(row["variant_combined_usd"]))
        baseline = parse_float(best["baseline_combined_usd"])
        variant_total = parse_float(best["variant_combined_usd"])
        findings.append(
            {
                "symbol": symbol,
                "best_variant": best["variant"],
                "baseline_combined_usd": baseline,
                "variant_combined_usd": variant_total,
                "delta_combined_usd": variant_total - baseline,
                "baseline_closes": parse_int(best["baseline_closes"]),
                "variant_closes": parse_int(best["variant_closes"]),
                "variant_alpha_closes": parse_int(best["variant_alpha_closes"]),
            }
        )
    return findings


def best_alpha_variant(rows: list[dict[str, str]]) -> dict[str, object]:
    candidates = [row for row in rows if row["variant"].startswith("cool12_alpha")]
    if not candidates:
        return {
            "variant": "n/a",
            "baseline_total_usd": 0.0,
            "variant_total_usd": 0.0,
            "delta_total_usd": 0.0,
        }
    best = max(candidates, key=lambda row: parse_float(row["variant_total_usd"]))
    return {
        "variant": best["variant"],
        "baseline_total_usd": parse_float(best["baseline_total_usd"]),
        "variant_total_usd": parse_float(best["variant_total_usd"]),
        "delta_total_usd": parse_float(best["delta_total_usd"]),
    }


def inside_geometry_findings(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for row in rows:
        findings.append(
            {
                "symbol": row["symbol"],
                "baseline_combined_usd": parse_float(row["baseline_combined_usd"]),
                "repeat_combined_usd": parse_float(row["repeat_combined_usd"]),
                "delta_combined_usd": parse_float(row["delta_combined_usd"]),
                "repeat_interior_reopens": parse_int(row["repeat_interior_reopens"]),
            }
        )
    return findings


def canonical_fx_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    fx_rows = [row for row in rows if row["type"] == "FX"]
    return [
        {
            "symbol": row["symbol"],
            "step_sell": parse_float(row["step_s"]),
            "step_buy": parse_float(row["step_b"]),
            "alpha": parse_float(row["alpha"]),
            "net": parse_float(row["net"]),
            "closes": parse_int(row["closes"]),
        }
        for row in fx_rows
    ]


def best_asym_variant(rows: list[dict[str, str]]) -> dict[str, object]:
    if not rows:
        return {
            "name": "n/a",
            "sell_gap": 0,
            "buy_gap": 0,
            "alpha": 0.0,
            "total": 0.0,
            "delta": 0.0,
        }
    best = max(rows, key=lambda row: parse_float(row["total"]))
    return {
        "name": best["name"],
        "sell_gap": parse_int(best["sell_gap"]),
        "buy_gap": parse_int(best["buy_gap"]),
        "alpha": parse_float(best["alpha"]),
        "total": parse_float(best["total"]),
        "delta": parse_float(best["delta"]),
    }


def ratio_findings(payload: dict) -> dict[str, object]:
    return {
        "realized_usd": parse_float(payload.get("realized_usd")),
        "total_opens": parse_int(payload.get("total_opens")),
        "total_closes": parse_int(payload.get("total_closes")),
        "max_open_seen": parse_int(payload.get("max_open_seen")),
        "n_attractors_used": parse_int(payload.get("n_attractors_used")),
    }


def build_markdown(
    alpha_rows: list[dict[str, str]],
    alpha_summary_rows: list[dict[str, str]],
    inside_rows: list[dict[str, str]],
    canonical_rows: list[dict[str, str]],
    asym_rows: list[dict[str, str]],
    ratio_payload: dict,
) -> str:
    alpha_by_symbol = alpha_findings(alpha_rows)
    alpha_basket = best_alpha_variant(alpha_summary_rows)
    inside_findings = inside_geometry_findings(inside_rows)
    canonical_fx = canonical_fx_rows(canonical_rows)
    asym_best = best_asym_variant(asym_rows)
    ratio = ratio_findings(ratio_payload)

    lines: list[str] = []
    lines.append("# Stopless Lattice Experiment Board")
    lines.append("")
    lines.append("This board ranks the next stopless-lattice experiments from repo-grounded evidence only.")
    lines.append("")
    lines.append("## Current Read")
    lines.append("")
    lines.append("### Close Policy Is The Strongest Proven Lever")
    lines.append("")
    lines.append(
        f"- Basket-wide, `{alpha_basket['variant']}` improved total net by `${alpha_basket['delta_total_usd']:.2f}` "
        f"over the raw baseline (`${alpha_basket['baseline_total_usd']:.2f}` -> "
        f"`${alpha_basket['variant_total_usd']:.2f}`) in `reports/alpha_aware_rearm_summary.csv`."
    )
    for finding in alpha_by_symbol:
        lines.append(
            f"- `{finding['symbol']}`: best current close-policy row is `{finding['best_variant']}` with "
            f"`{finding['baseline_closes']}` baseline closes vs `{finding['variant_closes']}` variant closes and "
            f"`${finding['delta_combined_usd']:.2f}` net improvement in `reports/alpha_aware_rearm_quick.csv`."
        )

    lines.append("")
    lines.append("### Spacing And Interior Reopen Geometry Still Matter")
    lines.append("")
    for finding in inside_findings:
        lines.append(
            f"- Repeated interior geometry was worse on `{finding['symbol']}`: "
            f"`{finding['baseline_combined_usd']:.2f}` baseline vs `{finding['repeat_combined_usd']:.2f}` repeat "
            f"(`{finding['delta_combined_usd']:.2f}` delta, `{finding['repeat_interior_reopens']}` interior reopens) "
            f"in `reports/inside_geometry_churn_benchmark.csv`."
        )
    lines.append(
        f"- The strongest current asymmetric gap row is `{asym_best['name']}` "
        f"(sell_gap=`{asym_best['sell_gap']}`, buy_gap=`{asym_best['buy_gap']}`, alpha=`{asym_best['alpha']:.2f}`) "
        f"at `${asym_best['total']:.2f}` total in `reports/asym_gap3_sweep.csv`."
    )
    for row in canonical_fx:
        lines.append(
            f"- `reports/unified_canonical_basket.csv` already points to asymmetric FX spacing on `{row['symbol']}`: "
            f"step_sell=`{row['step_sell']:.2f}`, step_buy=`{row['step_buy']:.2f}`, alpha=`{row['alpha']:.2f}`, "
            f"net=`${row['net']:.2f}`."
        )

    lines.append("")
    lines.append("### Relationship Lattices Have Real Shadow Evidence")
    lines.append("")
    lines.append(
        f"- `reports/eth_btc_ratio_lattice.json` is already positive: `${ratio['realized_usd']:.2f}` realized over "
        f"`{ratio['total_closes']}` closes with max_open_seen=`{ratio['max_open_seen']}` using "
        f"`{ratio['n_attractors_used']}` attractors."
    )

    lines.append("")
    lines.append("## Ranked Next Experiments")
    lines.append("")
    lines.append("1. **FX Close-Policy Ladder At Fixed Step**")
    lines.append(
        "   Hold the validated raw steps fixed (`GBPUSD 2.0`, `EURUSD 3.0`, `NZDUSD 1.5 cap12`) and sweep close_alpha, "
        "close_gap, and close order only. Reuse `scripts/sweep_alpha_aware_rearm.py` plus `reports/alpha_aware_rearm_*` "
        "as the baseline. This is the clearest proven lever."
    )
    lines.append("2. **FX Spacing Ladder At Fixed Close Policy**")
    lines.append(
        "   After locking a close-policy reference, sweep step size and adaptive spacing without changing close logic. "
        "Use `scripts/sweep_stateful_rearm_v2.py`, `scripts/benchmark_inside_geometry_churn.py`, and "
        "`scripts/sweep_vol_adaptive_geometry.py` to isolate whether spacing improves terminal net or just churn."
    )
    lines.append("3. **Asymmetric Side Geometry**")
    lines.append(
        "   Promote side-asymmetric sweeps from ad hoc evidence into a clean benchmark surface: hold alpha fixed, then "
        "sweep `sell_gap/buy_gap` and `step_sell/step_buy`. Reuse `scripts/sweep_asym_gap3.py`, "
        "`scripts/sweep_black_market_research.py`, and `scripts/unified_canonical_basket.py`."
    )
    lines.append("4. **Relationship-Lattice Expansion (Shadow Only)**")
    lines.append(
        "   Extend the ETH/BTC ratio scaffold to another relationship object before touching live lanes. Start with one "
        "additional ratio or residual and benchmark closure rate, realized net, and attractor stability using "
        "`scripts/eth_btc_ratio_lattice.py` plus any cross-symbol residual adapter."
    )
    lines.append("5. **Anchor / First-Trade Latency Instrumentation**")
    lines.append(
        "   Keep this as supporting infrastructure, not the next alpha hunt. The current BTC H1 candidate-shadow evidence "
        "says flat re-anchoring can be low-signal waiting behavior, so measure it cleanly but do not let it outrank the "
        "FX close-policy and spacing work."
    )

    lines.append("")
    lines.append("## Do Not Chase Blindly")
    lines.append("")
    lines.append(
        "- Do not treat BTC H1 BUY rearm restoration as an obvious fix; `reports/live_btcusd_h1_rearm_gate_benchmark.md` "
        "already showed the naive restore was worse."
    )
    lines.append(
        "- Do not assume wider BTC H1 spacing is safer or more profitable; the current replay evidence says `45 -> 75` "
        "was benchmark-negative while `30` and `50` looked better."
    )
    lines.append(
        "- Do not read fresh flat candidate shadows as automatic failure; `seeded_flat` plus anchor resets can still be "
        "normal first-trade latency."
    )

    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a stopless lattice experiment board from existing artifacts.")
    parser.add_argument("--alpha-quick", default=str(ROOT / "reports" / "alpha_aware_rearm_quick.csv"))
    parser.add_argument("--alpha-summary", default=str(ROOT / "reports" / "alpha_aware_rearm_summary.csv"))
    parser.add_argument("--inside-geometry", default=str(ROOT / "reports" / "inside_geometry_churn_benchmark.csv"))
    parser.add_argument("--canonical-basket", default=str(ROOT / "reports" / "unified_canonical_basket.csv"))
    parser.add_argument("--asym-gap", default=str(ROOT / "reports" / "asym_gap3_sweep.csv"))
    parser.add_argument("--ratio-json", default=str(ROOT / "reports" / "eth_btc_ratio_lattice.json"))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    markdown = build_markdown(
        alpha_rows=read_csv_rows(Path(args.alpha_quick)),
        alpha_summary_rows=read_csv_rows(Path(args.alpha_summary)),
        inside_rows=read_csv_rows(Path(args.inside_geometry)),
        canonical_rows=read_csv_rows(Path(args.canonical_basket)),
        asym_rows=read_csv_rows(Path(args.asym_gap)),
        ratio_payload=load_json(Path(args.ratio_json)),
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
