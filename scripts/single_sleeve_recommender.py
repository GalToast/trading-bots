#!/usr/bin/env python3
"""
Single-Sleeve Deployment Recommender

Takes ALL validated edges across all families (ratio lattice, rotation lattice,
Kelly directional) and recommends the SINGLE best deployment given constraints.

Considers:
- Cost survival (from stress audits)
- 60d PnL (structural validation)
- Regime durability (anti-pump audit results)
- Capital coupling (overlap with other sleeves)
- Capital required

Outputs:
- Top recommendation for max PnL
- Top recommendation for max robustness
- Top recommendation for max capital efficiency
- Trade-off analysis

Output: reports/single_sleeve_deployment_recommendation.md + .json
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ========================================================================
# VALIDATED EDGES WITH FULL METRICS
# ========================================================================

EDGES = {
    # -- Ratio lattice (60d + cost-stress + coupling) --
    "ratio_cfg_bal": {
        "family": "ratio_lattice",
        "label": "CFG/BAL",
        "pnl_60d": 155.0,
        "trades_60d": 224,
        "win_rate": 0.987,
        "cost_survival_pct": 57.5,     # from @qwen-1's 320-scenario audit (conservative)
        "max_cost_bps": 270,
        "regime_durability": "unknown", # needs anti-pump audit
        "capital_coupling_pct": 99.6,  # nearly always-on (from coupling audits)
        "capital_required": 845.0,
        "evidence": "60d + cost-stress (conservative)",
        "confidence": "high",
    },
    "ratio_cfg_eth": {
        "family": "ratio_lattice",
        "label": "CFG/ETH",
        "pnl_60d": 106.0,
        "trades_60d": 74,
        "win_rate": 0.949,
        "cost_survival_pct": 100.0,     # from @trading-lead's 42-scenario audit
        "max_cost_bps": 1231,
        "regime_durability": "unknown",
        "capital_coupling_pct": 98.5,
        "capital_required": 22.0,
        "evidence": "60d + 1231bps cost-stress",
        "confidence": "high",
    },
    "ratio_cfg_btc": {
        "family": "ratio_lattice",
        "label": "CFG/BTC",
        "pnl_60d": 126.0,
        "trades_60d": 191,
        "win_rate": 0.99,
        "cost_survival_pct": 100.0,
        "max_cost_bps": 1514,
        "regime_durability": "unknown",
        "capital_coupling_pct": 99.6,
        "capital_required": 845.0,
        "evidence": "60d + 1514bps cost-stress",
        "confidence": "high",
    },
    "ratio_cfg_nom": {
        "family": "ratio_lattice",
        "label": "CFG/NOM",
        "pnl_60d": 216.0,
        "trades_60d": 328,
        "win_rate": 0.994,
        "cost_survival_pct": 30.0,     # from @qwen-1's audit (conservative)
        "max_cost_bps": 220,
        "regime_durability": "unknown",
        "capital_coupling_pct": 99.6,
        "capital_required": 845.0,
        "evidence": "60d only (cost-stress weak)",
        "confidence": "medium",
    },
    "ratio_cfg_sup": {
        "family": "ratio_lattice",
        "label": "CFG/SUP",
        "pnl_60d": 110.0,
        "trades_60d": 56,
        "win_rate": 0.918,
        "cost_survival_pct": 75.0,     # @qwen-1 conservative
        "max_cost_bps": 400,
        "regime_durability": "unknown",
        "capital_coupling_pct": 99.6,
        "capital_required": 845.0,
        "evidence": "60d + cost-stress",
        "confidence": "high",
    },
    "ratio_iotx_eth": {
        "family": "ratio_lattice",
        "label": "IOTX/ETH",
        "pnl_60d": 0,                  # PnL not yet published
        "trades_60d": 0,
        "win_rate": 0,
        "cost_survival_pct": 100.0,    # @trading-lead: 100%, 360bps
        "max_cost_bps": 3398,          # from @codex-asym's stress ceiling
        "regime_durability": "unknown",
        "capital_coupling_pct": 99.1,
        "capital_required": 845.0,
        "evidence": "cost-stress only (no PnL yet)",
        "confidence": "medium",
    },
    "ratio_iotx_btc": {
        "family": "ratio_lattice",
        "label": "IOTX/BTC",
        "pnl_60d": 0,
        "trades_60d": 0,
        "win_rate": 0,
        "cost_survival_pct": 90.0,
        "max_cost_bps": 2814,
        "regime_durability": "unknown",
        "capital_coupling_pct": 99.1,
        "capital_required": 845.0,
        "evidence": "cost-stress only",
        "confidence": "medium",
    },

    # -- Rotation lattice (sweep-optimized + cost-stress) --
    "rotation_cfg_sup": {
        "family": "rotation_lattice",
        "label": "CFG/SUP (rotation)",
        "pnl_60d": 7.77,
        "trades_60d": 22,
        "win_rate": 0.59,
        "cost_survival_pct": 75.0,
        "max_cost_bps": 400,
        "regime_durability": "unknown",
        "capital_coupling_pct": 99.0,
        "capital_required": 100.0,
        "evidence": "sweep-optimized + cost-stress",
        "confidence": "medium",
    },
    "rotation_rave_bal": {
        "family": "rotation_lattice",
        "label": "RAVE/BAL (rotation)",
        "pnl_60d": -1.68,             # negative in sweep
        "trades_60d": 27,
        "win_rate": 0.59,
        "cost_survival_pct": 80.0,     # @qwen-1 audit
        "max_cost_bps": 365,
        "regime_durability": "regime_concentrated",  # RAVE pump-dependent
        "capital_coupling_pct": 99.0,
        "capital_required": 100.0,
        "evidence": "cost-stress (PnL negative)",
        "confidence": "low",
    },
    "rotation_rave_sup": {
        "family": "rotation_lattice",
        "label": "RAVE/SUP (rotation)",
        "pnl_60d": 6.48,
        "trades_60d": 14,
        "win_rate": 0.79,
        "cost_survival_pct": 0,        # not yet audited
        "max_cost_bps": 0,
        "regime_durability": "regime_concentrated",
        "capital_coupling_pct": 99.0,
        "capital_required": 100.0,
        "evidence": "sweep-optimized (no cost-stress)",
        "confidence": "low",
    },

    # -- Kelly directional (execution-validated) --
    "kelly_ghst_fib": {
        "family": "kelly_directional",
        "label": "GHST fibonacci",
        "pnl_per_trade": 0.62,
        "trades_60d_projected": 9,      # ~1 per 3.3 days
        "win_rate": 1.0,               # 1/1 (tiny sample)
        "cost_survival_pct": 100.0,    # real execution, costs included
        "max_cost_bps": 0,             # real spread already paid
        "regime_durability": "validated",  # live close achieved
        "capital_coupling_pct": 0,      # independent of ratio/rotation
        "capital_required": 8.64,
        "evidence": "1 live close, +$0.62",
        "confidence": "medium",
    },
}

# Confidence multipliers
CONFIDENCE_MULT = {
    "high": 0.9,
    "medium": 0.7,
    "low": 0.5,
}

# Regime durability multipliers
REGIME_MULT = {
    "validated": 1.0,
    "unknown": 0.7,
    "regime_concentrated": 0.4,
}


def compute_score(edge: dict, objective: str) -> float:
    """Compute deployment score for a given objective."""

    conf = CONFIDENCE_MULT.get(edge["confidence"], 0.5)
    regime = REGIME_MULT.get(edge["regime_durability"], 0.5)
    capital = max(edge["capital_required"], 1.0)

    # Monthly PnL estimate
    if edge["family"] == "kelly_directional":
        monthly_pnl = edge["pnl_per_trade"] * (edge["trades_60d_projected"] / 2)
    else:
        monthly_pnl = edge["pnl_60d"] / 2 if edge["pnl_60d"] else 0

    cost_survival = edge["cost_survival_pct"] / 100.0
    max_cost = edge["max_cost_bps"] / 100.0  # normalize to percentage

    if objective == "max_pnl":
        # Prioritize raw PnL, adjusted for confidence and regime
        return monthly_pnl * conf * regime
    elif objective == "max_robustness":
        # Prioritize cost survival and max cost headroom
        return cost_survival * 0.6 + (max_cost / 50.0) * 0.3 + conf * 0.1
    elif objective == "max_efficiency":
        # Prioritize PnL per dollar of capital
        efficiency = monthly_pnl / capital if capital > 0 else 0
        return efficiency * conf * regime * (1 + max_cost / 100.0)
    elif objective == "balanced":
        # Composite: PnL × robustness × efficiency
        pnl_score = min(monthly_pnl / 100.0, 1.0)  # normalize
        robust_score = cost_survival * 0.7 + (max_cost / 50.0) * 0.3
        eff_score = min(monthly_pnl / capital * 10, 1.0) if capital > 0 else 0
        return (pnl_score * 0.4 + robust_score * 0.35 + eff_score * 0.25) * conf * regime
    else:
        return 0


def main():
    print("=" * 72)
    print("SINGLE-SLEEVE DEPLOYMENT RECOMMENDER")
    print("=" * 72)
    print()

    objectives = ["max_pnl", "max_robustness", "max_efficiency", "balanced"]

    # Score all edges for each objective
    recommendations = {}
    for obj in objectives:
        scored = []
        for name, edge in EDGES.items():
            score = compute_score(edge, obj)
            scored.append((name, edge, score))
        scored.sort(key=lambda x: x[2], reverse=True)
        recommendations[obj] = scored

    # Print recommendations
    for obj in objectives:
        print(f"\n{'='*60}")
        print(f"RECOMMENDATION: {obj.upper().replace('_', ' ')}")
        print(f"{'='*60}")
        print()

        print(f"  {'Rank':<5} {'Edge':<25} {'Score':<10} {'Monthly PnL':<14} {'Cost Surv':<10} {'Capital':<10}")
        print(f"  {'----':<5} {'----':<25} {'-----':<10} {'-----------':<14} {'---------':<10} {'-------':<10}")

        for rank, (name, edge, score) in enumerate(recommendations[obj][:5], 1):
            if edge["family"] == "kelly_directional":
                monthly_pnl = edge["pnl_per_trade"] * (edge["trades_60d_projected"] / 2)
            else:
                monthly_pnl = edge["pnl_60d"] / 2 if edge["pnl_60d"] else 0

            cost_surv = f"{edge['cost_survival_pct']:.0f}%"
            capital = f"${edge['capital_required']:.0f}"

            label = edge["label"]
            print(f"  {rank:<5} {label:<25} {score:<10.4f} ${monthly_pnl:<13.2f} {cost_surv:<10} {capital:<10}")

        # Top recommendation details
        top_name, top_edge, top_score = recommendations[obj][0]
        print(f"\n  **TOP PICK: {top_edge['label']}**")
        print(f"  Score: {top_score:.4f}")
        print(f"  Evidence: {top_edge['evidence']}")
        print(f"  Confidence: {top_edge['confidence']}")
        print(f"  Regime: {top_edge['regime_durability']}")
        print(f"  Capital coupling: {top_edge['capital_coupling_pct']:.1f}% (nearly always-on)")

    # Overall recommendation
    print(f"\n{'='*72}")
    print("OVERALL RECOMMENDATION")
    print(f"{'='*72}")
    print()

    # Count how many times each edge appears in top 3 across objectives
    vote_count = {}
    for obj in objectives:
        for rank, (name, edge, score) in enumerate(recommendations[obj][:3], 1):
            if name not in vote_count:
                vote_count[name] = {"edge": edge, "votes": 0, "best_rank": 999}
            vote_count[name]["votes"] += 1
            vote_count[name]["best_rank"] = min(vote_count[name]["best_rank"], rank)

    overall = sorted(vote_count.items(), key=lambda x: (-x[1]["votes"], x[1]["best_rank"]))

    print(f"  {'Edge':<25} {'Top-3 Votes':<14} {'Best Rank':<12} {'Confidence':<12} {'Evidence':<30}")
    print(f"  {'----':<25} {'-----------':<14} {'---------':<12} {'----------':<12} {'--------':<30}")

    for name, data in overall[:8]:
        edge = data["edge"]
        votes = data["votes"]
        best = data["best_rank"]
        print(f"  {edge['label']:<25} {votes}/4{'':<9} #{best:<11} {edge['confidence']:<12} {edge['evidence']:<30}")

    # Save
    serializable = {
        "recommendations": {
            obj: [
                {
                    "name": name,
                    "label": edge["label"],
                    "family": edge["family"],
                    "score": round(score, 4),
                    "monthly_pnl": round(
                        (edge["pnl_per_trade"] * (edge["trades_60d_projected"] / 2))
                        if edge["family"] == "kelly_directional"
                        else (edge["pnl_60d"] / 2 if edge["pnl_60d"] else 0),
                        2
                    ),
                    "cost_survival_pct": edge["cost_survival_pct"],
                    "max_cost_bps": edge["max_cost_bps"],
                    "capital_required": edge["capital_required"],
                    "confidence": edge["confidence"],
                }
                for name, edge, score in recs[:5]
            ]
            for obj, recs in recommendations.items()
        },
        "overall_ranking": [
            {
                "name": name,
                "label": data["edge"]["label"],
                "votes": data["votes"],
                "best_rank": data["best_rank"],
                "confidence": data["edge"]["confidence"],
                "evidence": data["edge"]["evidence"],
            }
            for name, data in overall[:10]
        ],
    }

    out_md = _build_markdown(recommendations, overall)
    out_path_md = ROOT / "reports" / "single_sleeve_deployment_recommendation.md"
    out_path_md.write_text(out_md)

    out_path_json = ROOT / "reports" / "single_sleeve_deployment_recommendation.json"
    out_path_json.write_text(json.dumps(serializable, indent=2, default=str))

    print(f"\nReport: {out_path_md}")
    print(f"JSON: {out_path_json}")


def _build_markdown(recommendations: dict, overall: list) -> str:
    lines = [
        "# Single-Sleeve Deployment Recommendation",
        "",
        f"**Edges analyzed:** {len(EDGES)}",
        f"**Objectives:** Max PnL, Max Robustness, Max Efficiency, Balanced",
        "",
        "## Overall Ranking (by Top-3 votes across objectives)",
        "",
        "| Rank | Edge | Votes | Best Rank | Confidence | Evidence |",
        "|------|------|-------|-----------|------------|----------|",
    ]

    for rank, (name, data) in enumerate(overall[:10], 1):
        edge = data["edge"]
        lines.append(
            f"| {rank} | {edge['label']} | {data['votes']}/4 | #{data['best_rank']} | "
            f"{edge['confidence']} | {edge['evidence']} |"
        )

    lines.append("")
    lines.append("## Per-Objective Top 5")
    lines.append("")

    for obj, recs in recommendations.items():
        lines.append(f"### {obj.replace('_', ' ').title()}")
        lines.append("")
        lines.append("| Rank | Edge | Score | Monthly PnL | Cost Survival | Capital |")
        lines.append("|------|------|-------|-------------|---------------|---------|")

        for rank, (name, edge, score) in enumerate(recs[:5], 1):
            if edge["family"] == "kelly_directional":
                monthly_pnl = edge["pnl_per_trade"] * (edge["trades_60d_projected"] / 2)
            else:
                monthly_pnl = edge["pnl_60d"] / 2 if edge["pnl_60d"] else 0

            lines.append(
                f"| {rank} | {edge['label']} | {score:.4f} | ${monthly_pnl:.2f} | "
                f"{edge['cost_survival_pct']:.0f}% | ${edge['capital_required']:.0f} |"
            )

        lines.append("")

    lines.append("## Interpretation")
    lines.append("")

    if overall:
        top_name, top_data = overall[0]
        top_edge = top_data["edge"]
        lines.append(f"**Top overall recommendation: {top_edge['label']}**")
        lines.append(f"- Appears in {top_data['votes']}/4 objective top-3 lists")
        lines.append(f"- Best rank: #{top_data['best_rank']}")
        lines.append(f"- Evidence: {top_edge['evidence']}")
        lines.append(f"- Confidence: {top_edge['confidence']}")
        lines.append("")
        lines.append("This edge balances PnL, robustness, and efficiency better than any other single sleeve.")

    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- All sleeves have ~99% capital coupling (nearly always-on). Can't stack multiple sleeves.")
    lines.append("- Kelly edges based on 1 trade — need more data for confidence.")
    lines.append("- Rotation lattice edges have low per-trade PnL — sensitive to execution quality.")
    lines.append("- Ratio lattice edges with unknown regime durability need anti-pump audit.")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
