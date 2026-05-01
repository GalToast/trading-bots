#!/usr/bin/env python3
"""
Deployment-oriented choice report for the two mature isolated CFG sleeves.

This consolidates the current evidence for:
- CFG/BTC
- CFG/ETH

using:
- cost-aware execution audit
- cost-stress summary
- break-even ceiling
- repeated isolated walk-forward
- deployment-oriented walk-forward
- current Coinbase USD-leg product metadata

Outputs:
- reports/ratio_lattice_cfg_sleeve_choice.md
- reports/ratio_lattice_cfg_sleeve_choice.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient, CoinbaseAdvancedClientError


OUTPUT_MD = ROOT / "reports" / "ratio_lattice_cfg_sleeve_choice.md"
OUTPUT_JSON = ROOT / "reports" / "ratio_lattice_cfg_sleeve_choice.json"
PAIRS = ("CFG/BTC", "CFG/ETH")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def select_pair_row(rows: list[dict[str, Any]], pair: str, *, net_key: str) -> dict[str, Any]:
    candidates = [row for row in rows if str(row.get("pair", "")).upper() == pair.upper()]
    if not candidates:
        raise KeyError(f"Missing pair row for {pair}")
    return max(candidates, key=lambda row: float(row[net_key]))


def select_summary_row(rows: list[dict[str, Any]], pair: str, *, variant: str | None = None) -> dict[str, Any]:
    candidates = [row for row in rows if str(row.get("pair", "")).upper() == pair.upper()]
    if variant is not None:
        candidates = [row for row in candidates if str(row.get("variant", "")).lower() == variant.lower()]
    if not candidates:
        raise KeyError(f"Missing summary row for {pair} ({variant})")
    return candidates[0]


def select_walkforward_row(rows: list[dict[str, Any]], pair: str) -> dict[str, Any]:
    candidates = [row for row in rows if str(row.get("pair", "")).upper() == pair.upper()]
    if not candidates:
        raise KeyError(f"Missing walk-forward row for {pair}")
    return candidates[0]


def fetch_product_metadata(client: CoinbaseAdvancedClient, product_id: str) -> dict[str, Any]:
    try:
        return client.get_product(product_id)
    except CoinbaseAdvancedClientError as exc:
        return {"product_id": product_id, "error": str(exc)}


def build_report() -> dict[str, Any]:
    execution = load_json(ROOT / "reports" / "ratio_lattice_execution_audit.json")
    stress = load_json(ROOT / "reports" / "ratio_lattice_cost_stress.json")
    ceiling = load_json(ROOT / "reports" / "ratio_lattice_breakeven_ceiling.json")
    cfg_walk = load_json(ROOT / "reports" / "coinbase_spot_cfg_walk_forward.json")
    deploy_walk = load_json(ROOT / "reports" / "ratio_lattice_deployment_walk_forward.json")
    recommender = load_json(ROOT / "reports" / "single_sleeve_deployment_recommendation.json")

    net_key = "fee_40_0_net_pnl_den"

    client = CoinbaseAdvancedClient()
    products = {
        "CFG-BTC": fetch_product_metadata(client, "CFG-BTC"),
        "CFG-ETH": fetch_product_metadata(client, "CFG-ETH"),
        "CFG-USD": fetch_product_metadata(client, "CFG-USD"),
        "BTC-USD": fetch_product_metadata(client, "BTC-USD"),
        "ETH-USD": fetch_product_metadata(client, "ETH-USD"),
    }

    pair_rows: list[dict[str, Any]] = []
    for pair in PAIRS:
        symbol_a, symbol_b = pair.split("/")
        exec_row = select_pair_row(execution["rows"], pair, net_key=net_key)
        stress_row = select_summary_row(stress["summary_rows"], pair, variant="tuned")
        ceiling_row = select_summary_row(ceiling["rows"], pair, variant="tuned")
        cfg_row = select_walkforward_row(cfg_walk["summary_rows"], pair)
        deploy_row = select_walkforward_row(deploy_walk["pair_summaries"], pair)
        pair_rows.append(
            {
                "pair": pair,
                "symbol_a": symbol_a,
                "symbol_b": symbol_b,
                "tuned_shape": f"thr={exec_row['profit_threshold']:.3f} levels={int(exec_row['max_levels'])}",
                "cost_adjusted_net_den": float(exec_row[net_key]),
                "cost_adjusted_closes": int(exec_row["total_closes"]),
                "stress_positive_ratio": float(stress_row["positive_ratio"]),
                "stress_positive_scenarios": int(stress_row["positive_scenarios"]),
                "stress_total_scenarios": int(stress_row["total_scenarios"]),
                "breakeven_bps": float(ceiling_row["breakeven_round_trip_cost_bps"]),
                "cfg_walk_positive_windows": int(cfg_row["positive_windows"]),
                "cfg_walk_windows": int(cfg_row["windows_count"]),
                "cfg_walk_total_forward_net_den": float(cfg_row["total_forward_net_pnl_den"]),
                "cfg_walk_total_forward_closes": int(cfg_row["total_forward_closes"]),
                "deploy_positive_windows": int(deploy_row["positive_windows"]),
                "deploy_windows": int(deploy_row["windows_count"]),
                "deploy_total_forward_net_usd": float(deploy_row["total_forward_net_usd"]),
                "deploy_nominal_capital_usd": float(deploy_row["nominal_capital_usd"]),
                "deploy_return_on_nominal": float(deploy_row["total_return_on_nominal"]),
                "deploy_verdict": str(deploy_row["verdict"]),
                "synthetic_route": [f"{symbol_a}-USD", f"{symbol_b}-USD"],
            }
        )

    pair_map = {row["pair"]: row for row in pair_rows}

    balanced_reco = None
    for edge in recommender.get("all_edges_ranked", []):
        if edge.get("symbol") in {"CFG/ETH", "CFG/BTC"}:
            if balanced_reco is None or float(edge.get("score", 0.0)) > float(balanced_reco.get("score", 0.0)):
                balanced_reco = edge

    recommendation = {
        "first_proof_sleeve": "CFG/ETH",
        "scale_up_sleeve": "CFG/BTC",
        "reasoning": [
            "CFG/ETH and CFG/BTC are both repeated-positive isolated sleeves, so the choice is now operational rather than existential.",
            "CFG/ETH keeps the same synthetic 4-leg USD routing as CFG/BTC but needs far less nominal denominator capital.",
            "CFG/ETH still carries the slightly stronger friction budget, while the repo's broader recommender scores CFG/BTC slightly higher overall because of its heavier-capital payoff profile.",
            "That makes the honest split explicit: CFG/ETH for first low-capital proof, CFG/BTC for scale-up once synthetic routing is trusted.",
            "CFG/BTC is still the stronger heavier-capital isolated sleeve by USD-normalized repeated walk-forward net and return on nominal capital.",
        ],
        "balanced_recommender_reference": balanced_reco,
    }

    return {
        "products": products,
        "pairs": pair_rows,
        "recommendation": recommendation,
    }


def write_markdown(report: dict[str, Any]) -> None:
    products = report["products"]
    pairs = report["pairs"]
    recommendation = report["recommendation"]

    lines = [
        "# CFG Sleeve Choice",
        "",
        "- This report chooses between the two mature isolated CFG sleeves after repeated walk-forward and deployment-oriented holdout work.",
        "- Both sleeves are synthetic USD-leg programs on Coinbase spot. There is no native `CFG-BTC` or `CFG-ETH` product to route directly.",
        "",
        "## Venue Reality",
        "",
        f"- `CFG-BTC`: `{products['CFG-BTC'].get('error', 'supported')}`",
        f"- `CFG-ETH`: `{products['CFG-ETH'].get('error', 'supported')}`",
        f"- `CFG-USD`: status `{products['CFG-USD'].get('status')}`, `quote_min_size={products['CFG-USD'].get('quote_min_size')}`, `base_min_size={products['CFG-USD'].get('base_min_size')}`, `base_increment={products['CFG-USD'].get('base_increment')}`",
        f"- `BTC-USD`: status `{products['BTC-USD'].get('status')}`, `quote_min_size={products['BTC-USD'].get('quote_min_size')}`, `base_min_size={products['BTC-USD'].get('base_min_size')}`, `base_increment={products['BTC-USD'].get('base_increment')}`",
        f"- `ETH-USD`: status `{products['ETH-USD'].get('status')}`, `quote_min_size={products['ETH-USD'].get('quote_min_size')}`, `base_min_size={products['ETH-USD'].get('base_min_size')}`, `base_increment={products['ETH-USD'].get('base_increment')}`",
        "",
        "## Comparison",
        "",
        "| Pair | Tuned Shape | Stress | Ceiling | Repeated WF | Deployment WF | Nominal Capital | Return On Nominal |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in pairs:
        lines.append(
            f"| `{row['pair']}` | `{row['tuned_shape']}` | "
            f"`{row['stress_positive_scenarios']}/{row['stress_total_scenarios']}` | "
            f"`{row['breakeven_bps']:.0f}bps` | "
            f"`{row['cfg_walk_positive_windows']}/{row['cfg_walk_windows']}` | "
            f"`{row['deploy_positive_windows']}/{row['deploy_windows']}` | "
            f"`$ {row['deploy_nominal_capital_usd']:.2f}` | "
            f"`{row['deploy_return_on_nominal']:+.1%}` |"
        )

    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            f"- First proof sleeve: `{recommendation['first_proof_sleeve']}`",
            f"- Scale-up sleeve: `{recommendation['scale_up_sleeve']}`",
            "",
            "## Read",
        ]
    )
    for reason in recommendation["reasoning"]:
        lines.append(f"- {reason}")

    lines.extend(
        [
            f"- `CFG/ETH`: repeated-positive isolated sleeve, slightly stronger friction budget than `CFG/BTC`, same synthetic routing complexity, and about `$ {pairs[1]['deploy_nominal_capital_usd']:.2f}` nominal denominator capital in the current deployment walk-forward.",
            f"- `CFG/BTC`: repeated-positive isolated sleeve, stronger USD-normalized held-out power and about `{pairs[0]['deploy_return_on_nominal']:+.1%}` return on nominal in the current deployment walk-forward, but it needs about `$ {pairs[0]['deploy_nominal_capital_usd']:.2f}` nominal denominator capital.",
            "- Honest deployment split: use `CFG/ETH` for the first low-capital proof sleeve, then promote `CFG/BTC` as the heavier-capital scale-up sleeve if the synthetic routing and operational burden hold up in live shadowing.",
        ]
    )

    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    report = build_report()
    OUTPUT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report)
    print(f"MD:   {OUTPUT_MD}")
    print(f"JSON: {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
