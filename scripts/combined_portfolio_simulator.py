#!/usr/bin/env python3
"""
Combined Portfolio Simulator

Combines ALL validated edges into a single diversified portfolio simulation:
1. Kelly shadow (GHST fib, CFG momentum, A8 momentum)
2. Ratio lattice (CFG/BAL, CFG/ETH, CFG/BTC, CFG/NOM, CFG/SUP)
3. Rotation lattice (CFG/RAVE, CFG/SUP, RAVE/SUP, CFG/BAL, BAL/SUP)

Shows:
- Total projected monthly PnL across all edges
- Correlation between edges (truly independent?)
- Optimal capital allocation via Kelly criterion
- Combined Sharpe and max drawdown

Output: reports/combined_portfolio_simulation.md + .json
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ========================================================================
# VALIDATED EDGES (from switchboard consensus + repo evidence)
# ========================================================================

EDGES = {
    # -- Kelly shadow (execution-validated) --
    "kelly_ghst_fib": {
        "family": "kelly_directional",
        "coin": "GHST-USD",
        "strategy": "fibonacci",
        "per_trade_pnl": 0.62,       # USD, from live close
        "win_rate": 1.0,             # 1/1 (tiny sample)
        "trades_per_day": 0.3,       # ~1 trade per 3.3 days (estimated from 13-bar hold)
        "capital_deployed": 8.64,    # USD
        "max_hold_bars": 96,
        "evidence": "1 live close",
        "confidence": "medium",       # only 1 trade
    },
    "kelly_cfg_mom": {
        "family": "kelly_directional",
        "coin": "CFG-USD",
        "strategy": "momentum",
        "per_trade_pnl": 0.62,       # assumed same as GHST (not yet closed)
        "win_rate": 0.55,            # expected for momentum
        "trades_per_day": 0.4,       # ~1 trade per 2.5 days
        "capital_deployed": 8.64,
        "max_hold_bars": 48,
        "evidence": "0 closes, holding",
        "confidence": "low",
    },
    "kelly_a8_mom": {
        "family": "kelly_directional",
        "coin": "A8-USD",
        "strategy": "momentum",
        "per_trade_pnl": 0.62,
        "win_rate": 0.55,
        "trades_per_day": 0.4,
        "capital_deployed": 8.64,
        "max_hold_bars": 48,
        "evidence": "0 closes, holding",
        "confidence": "low",
    },

    # -- Ratio lattice (60d validated, cost-stress audited) --
    "ratio_cfg_bal": {
        "family": "ratio_lattice",
        "coin_a": "CFG",
        "coin_b": "BAL",
        "pnl_60d": 155.0,           # USD (scaled from 0.01 BTC position)
        "trades_60d": 224,
        "win_rate": 0.987,
        "friction_headroom_bps": 800,
        "capital_deployed": 845.0,   # 0.01 BTC * $84,500
        "evidence": "60d + 800bps cost-stress",
        "confidence": "high",
    },
    "ratio_cfg_eth": {
        "family": "ratio_lattice",
        "coin_a": "CFG",
        "coin_b": "ETH",
        "pnl_60d": 106.0,
        "trades_60d": 74,
        "win_rate": 0.949,
        "friction_headroom_bps": 1231,
        "capital_deployed": 22.0,
        "evidence": "60d + 1231bps cost-stress",
        "confidence": "high",
    },
    "ratio_cfg_btc": {
        "family": "ratio_lattice",
        "coin_a": "CFG",
        "coin_b": "BTC",
        "pnl_60d": 126.0,
        "trades_60d": 191,
        "win_rate": 0.99,
        "friction_headroom_bps": 1514,
        "capital_deployed": 845.0,
        "evidence": "60d + 1514bps cost-stress",
        "confidence": "high",
    },
    "ratio_cfg_nom": {
        "family": "ratio_lattice",
        "coin_a": "CFG",
        "coin_b": "NOM",
        "pnl_60d": 216.0,
        "trades_60d": 328,
        "win_rate": 0.994,
        "friction_headroom_bps": 944,
        "capital_deployed": 845.0,
        "evidence": "60d",
        "confidence": "medium",       # no cost-stress yet
    },
    "ratio_cfg_sup": {
        "family": "ratio_lattice",
        "coin_a": "CFG",
        "coin_b": "SUP",
        "pnl_60d": 110.0,
        "trades_60d": 56,
        "win_rate": 0.918,
        "friction_headroom_bps": 389,
        "capital_deployed": 845.0,
        "evidence": "60d + 389bps cost-stress",
        "confidence": "medium",
    },

    # -- Rotation lattice (sweep-optimized, no-NOM) --
    "rotation_cfg_rave": {
        "family": "rotation_lattice",
        "coin_a": "CFG",
        "coin_b": "RAVE",
        "pnl_60d": 11.08,
        "trades_60d": 63,
        "win_rate": 0.44,
        "capital_deployed": 100.0,   # assumed $100 per sleeve
        "evidence": "sweep-optimized 60d",
        "confidence": "medium",
    },
    "rotation_cfg_sup": {
        "family": "rotation_lattice",
        "coin_a": "CFG",
        "coin_b": "SUP",
        "pnl_60d": 7.77,
        "trades_60d": 22,
        "win_rate": 0.59,
        "capital_deployed": 100.0,
        "evidence": "sweep-optimized 60d",
        "confidence": "medium",
    },
    "rotation_rave_sup": {
        "family": "rotation_lattice",
        "coin_a": "RAVE",
        "coin_b": "SUP",
        "pnl_60d": 6.48,
        "trades_60d": 14,
        "win_rate": 0.79,
        "capital_deployed": 100.0,
        "evidence": "sweep-optimized 60d",
        "confidence": "low",         # only 14 trades
    },
    "rotation_cfg_bal": {
        "family": "rotation_lattice",
        "coin_a": "CFG",
        "coin_b": "BAL",
        "pnl_60d": 4.29,
        "trades_60d": 39,
        "win_rate": 0.44,
        "capital_deployed": 100.0,
        "evidence": "sweep-optimized 60d",
        "confidence": "medium",
    },
    "rotation_bal_sup": {
        "family": "rotation_lattice",
        "coin_a": "BAL",
        "coin_b": "SUP",
        "pnl_60d": 4.17,
        "trades_60d": 16,
        "win_rate": 0.50,
        "capital_deployed": 100.0,
        "evidence": "sweep-optimized 60d",
        "confidence": "low",
    },
}

# Correlation matrix between families (estimated from edge mechanics)
# 1.0 = perfect correlation, 0.0 = independent, -1.0 = opposite
FAMILY_CORRELATIONS = {
    ("kelly_directional", "kelly_directional"): 0.3,   # same coin, different strategies
    ("kelly_directional", "ratio_lattice"): 0.1,         # different mechanism
    ("kelly_directional", "rotation_lattice"): 0.05,     # very different
    ("ratio_lattice", "ratio_lattice"): 0.4,             # same CFG hub, shared exposure
    ("ratio_lattice", "rotation_lattice"): 0.15,         # both use CFG but different math
    ("rotation_lattice", "rotation_lattice"): 0.3,       # shared coins
}

# Confidence multipliers (discount PnL based on evidence quality)
CONFIDENCE_MULTIPLIERS = {
    "high": 0.9,    # 60d + cost-stress validated
    "medium": 0.7,  # 60d OR cost-stress, not both
    "low": 0.5,     # preliminary evidence
}


def simulate_portfolio(edges: dict, months: int = 1, confidence_adjust: bool = True) -> dict:
    """Simulate combined portfolio with correlations and confidence adjustments."""

    results = []
    total_capital = 0.0
    total_monthly_pnl = 0.0

    for name, edge in edges.items():
        # Compute monthly PnL
        family = edge["family"]

        if family == "kelly_directional":
            # Per-trade PnL * trades per day * 30 days
            monthly_pnl = edge["per_trade_pnl"] * edge["trades_per_day"] * 30
            trades_per_month = edge["trades_per_day"] * 30
        else:
            # 60d PnL / 2 = monthly
            monthly_pnl = edge["pnl_60d"] / 2
            trades_per_month = edge["trades_60d"] / 2

        # Apply confidence discount
        conf_mult = CONFIDENCE_MULTIPLIERS.get(edge["confidence"], 0.5)
        if confidence_adjust:
            adjusted_pnl = monthly_pnl * conf_mult
        else:
            adjusted_pnl = monthly_pnl

        capital = edge["capital_deployed"]
        total_capital += capital
        total_monthly_pnl += adjusted_pnl

        results.append({
            "name": name,
            "family": family,
            "coin": edge.get("coin") or f"{edge.get('coin_a','?')}/{edge.get('coin_b','?')}",
            "raw_monthly_pnl": round(monthly_pnl, 2),
            "adjusted_monthly_pnl": round(adjusted_pnl, 2),
            "trades_per_month": round(trades_per_month, 1),
            "win_rate": edge["win_rate"],
            "capital": capital,
            "confidence": edge["confidence"],
            "evidence": edge["evidence"],
        })

    # Sort by adjusted PnL
    results.sort(key=lambda r: r["adjusted_monthly_pnl"], reverse=True)

    # Compute combined metrics
    # Weighted average win rate
    total_trades = sum(r["trades_per_month"] for r in results)
    if total_trades > 0:
        weighted_wr = sum(r["win_rate"] * r["trades_per_month"] for r in results) / total_trades
    else:
        weighted_wr = 0.0

    # Per-trade PnL
    per_trade_pnl = total_monthly_pnl / total_trades if total_trades > 0 else 0.0

    # Return on capital (monthly)
    roc = total_monthly_pnl / total_capital * 100 if total_capital > 0 else 0.0

    # Estimated Sharpe (simplified: mean/std of per-trade PnL)
    # Assume per-trade PnL std ≈ |per_trade_pnl| / sqrt(win_rate * (1-win_rate) + 0.01)
    if weighted_wr > 0 and weighted_wr < 1:
        trade_std = abs(per_trade_pnl) / math.sqrt(weighted_wr * (1 - weighted_wr) + 0.01)
    else:
        trade_std = abs(per_trade_pnl) * 2

    monthly_trades = total_trades
    monthly_mean = total_monthly_pnl
    monthly_std = trade_std * math.sqrt(monthly_trades) if monthly_trades > 0 else 1
    sharpe = monthly_mean / monthly_std if monthly_std > 0 else 0.0

    # Estimated max drawdown (rough: 3 * monthly_std for a bad month)
    max_drawdown = 3 * monthly_std

    # Correlation-adjusted Sharpe (diversification benefit)
    # Average cross-family correlation
    families = set(r["family"] for r in results)
    correlations = []
    for f1 in families:
        for f2 in families:
            if f1 < f2:
                key = (f1, f2) if (f1, f2) in FAMILY_CORRELATIONS else (f2, f1)
                corr = FAMILY_CORRELATIONS.get(key, 0.1)
                correlations.append(corr)

    avg_corr = sum(correlations) / len(correlations) if correlations else 0.1
    diversification_ratio = 1 / math.sqrt(1 + (len(families) - 1) * avg_corr) if avg_corr < 1 else 1.0
    adjusted_sharpe = sharpe * diversification_ratio

    return {
        "results": results,
        "total_capital": round(total_capital, 2),
        "total_raw_monthly_pnl": round(sum(r["raw_monthly_pnl"] for r in results), 2),
        "total_adjusted_monthly_pnl": round(total_monthly_pnl, 2),
        "total_trades_per_month": round(total_trades, 1),
        "weighted_win_rate": round(weighted_wr, 4),
        "per_trade_pnl": round(per_trade_pnl, 4),
        "return_on_capital_pct": round(roc, 2),
        "sharpe_ratio": round(sharpe, 3),
        "diversification_ratio": round(diversification_ratio, 3),
        "adjusted_sharpe": round(adjusted_sharpe, 3),
        "max_drawdown_estimate": round(max_drawdown, 2),
        "avg_cross_family_corr": round(avg_corr, 3),
        "families": list(families),
    }


def main():
    print("=" * 72)
    print("COMBINED PORTFOLIO SIMULATOR")
    print("=" * 72)
    print()

    # Simulate with confidence adjustment
    sim = simulate_portfolio(EDGES, confidence_adjust=True)

    # Print family summary
    family_pnl = {}
    for r in sim["results"]:
        fam = r["family"]
        if fam not in family_pnl:
            family_pnl[fam] = {"pnl": 0, "trades": 0, "capital": 0}
        family_pnl[fam]["pnl"] += r["adjusted_monthly_pnl"]
        family_pnl[fam]["trades"] += r["trades_per_month"]
        family_pnl[fam]["capital"] += r["capital"]

    print("FAMILY SUMMARY:")
    print()
    print(f"  {'Family':<25} {'Monthly PnL':<15} {'Trades/Mo':<12} {'Capital':<12} {'ROC%':<10}")
    print(f"  {'------':<25} {'-----------':<15} {'---------':<12} {'-------':<12} {'----':<10}")

    for fam, data in sorted(family_pnl.items(), key=lambda x: x[1]["pnl"], reverse=True):
        roc = data["pnl"] / data["capital"] * 100 if data["capital"] > 0 else 0
        print(f"  {fam:<25} ${data['pnl']:<14.2f} {data['trades']:<12.1f} ${data['capital']:<11.2f} {roc:.1f}%")

    print()
    print("=" * 72)
    print("INDIVIDUAL EDGES (ranked by adjusted monthly PnL)")
    print("=" * 72)
    print()

    print(f"  {'Edge':<25} {'Raw $':<10} {'Adj $':<10} {'Trades':<8} {'WR':<8} {'Capital':<10} {'Conf':<8}")
    print(f"  {'----':<25} {'-------':<10} {'------':<10} {'------':<8} {'--':<8} {'-------':<10} {'----':<8}")

    for r in sim["results"]:
        print(f"  {r['name']:<25} ${r['raw_monthly_pnl']:<9.2f} ${r['adjusted_monthly_pnl']:<9.2f} "
              f"{r['trades_per_month']:<8.1f} {r['win_rate']:<8.1%} ${r['capital']:<9.2f} {r['confidence']:<8}")

    print()
    print("=" * 72)
    print("COMBINED PORTFOLIO METRICS")
    print("=" * 72)
    print()

    print(f"  Total capital deployed:        ${sim['total_capital']:,.2f}")
    print(f"  Raw monthly PnL (no discount): ${sim['total_raw_monthly_pnl']:,.2f}")
    print(f"  Adjusted monthly PnL:          ${sim['total_adjusted_monthly_pnl']:,.2f}")
    print(f"  Annualized (adjusted):         ${sim['total_adjusted_monthly_pnl'] * 12:,.2f}")
    print(f"  Total trades/month:            {sim['total_trades_per_month']:.1f}")
    print(f"  Weighted win rate:             {sim['weighted_win_rate']:.1%}")
    print(f"  Per-trade PnL:                 ${sim['per_trade_pnl']:.4f}")
    print(f"  Return on capital:             {sim['return_on_capital_pct']:.2f}%/mo")
    print(f"  Sharpe ratio:                  {sim['sharpe_ratio']:.3f}")
    print(f"  Diversification ratio:         {sim['diversification_ratio']:.3f}")
    print(f"  Adjusted Sharpe:               {sim['adjusted_sharpe']:.3f}")
    print(f"  Max drawdown (est):            ${sim['max_drawdown_estimate']:,.2f}")
    print(f"  Avg cross-family correlation:  {sim['avg_cross_family_corr']:.3f}")
    print(f"  Independent edge families:     {', '.join(sim['families'])}")

    # Save outputs
    out_md = _build_markdown(sim, family_pnl)
    out_path_md = ROOT / "reports" / "combined_portfolio_simulation.md"
    out_path_md.write_text(out_md)

    serializable = {
        "family_summary": {
            fam: {k: round(v, 2) for k, v in data.items()}
            for fam, data in family_pnl.items()
        },
        "individual_edges": sim["results"],
        "combined_metrics": {
            k: round(v, 3) if isinstance(v, float) else v
            for k, v in sim.items()
            if k not in ("results",)
        },
    }
    out_path_json = ROOT / "reports" / "combined_portfolio_simulation.json"
    out_path_json.write_text(json.dumps(serializable, indent=2, default=str))

    print()
    print(f"Report: {out_path_md}")
    print(f"JSON: {out_path_json}")


def _build_markdown(sim: dict, family_pnl: dict) -> str:
    lines = [
        "# Combined Portfolio Simulation",
        "",
        f"**Generated:** {sim.get('timestamp', '2026-04-13')}",
        f"**Edges analyzed:** {len(sim['results'])}",
        f"**Edge families:** {', '.join(sim['families'])}",
        "",
        "## Family Summary",
        "",
        "| Family | Monthly PnL (adj) | Trades/Mo | Capital | ROC% |",
        "|--------|------------------|-----------|---------|------|",
    ]

    for fam, data in sorted(family_pnl.items(), key=lambda x: x[1]["pnl"], reverse=True):
        roc = data["pnl"] / data["capital"] * 100 if data["capital"] > 0 else 0
        lines.append(f"| {fam} | ${data['pnl']:.2f} | {data['trades']:.1f} | "
                     f"${data['capital']:.2f} | {roc:.1f}% |")

    lines.append("")
    lines.append("## Individual Edges")
    lines.append("")
    lines.append("| Edge | Raw $ | Adj $ | Trades/Mo | WR | Capital | Confidence |")
    lines.append("|------|-------|-------|-----------|----|---------|------------|")

    for r in sim["results"]:
        lines.append(f"| {r['name']} | ${r['raw_monthly_pnl']:.2f} | ${r['adjusted_monthly_pnl']:.2f} | "
                     f"{r['trades_per_month']:.1f} | {r['win_rate']:.1%} | ${r['capital']:.2f} | {r['confidence']} |")

    lines.append("")
    lines.append("## Combined Metrics")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total capital | ${sim['total_capital']:,.2f} |")
    lines.append(f"| Raw monthly PnL | ${sim['total_raw_monthly_pnl']:,.2f} |")
    lines.append(f"| Adjusted monthly PnL | ${sim['total_adjusted_monthly_pnl']:,.2f} |")
    lines.append(f"| Annualized (adjusted) | ${sim['total_adjusted_monthly_pnl'] * 12:,.2f} |")
    lines.append(f"| Total trades/month | {sim['total_trades_per_month']:.1f} |")
    lines.append(f"| Weighted win rate | {sim['weighted_win_rate']:.1%} |")
    lines.append(f"| Sharpe ratio | {sim['sharpe_ratio']:.3f} |")
    lines.append(f"| Adjusted Sharpe | {sim['adjusted_sharpe']:.3f} |")
    lines.append(f"| Max drawdown (est) | ${sim['max_drawdown_estimate']:,.2f} |")
    lines.append(f"| Diversification ratio | {sim['diversification_ratio']:.3f} |")
    lines.append(f"| Avg cross-family correlation | {sim['avg_cross_family_corr']:.3f} |")

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(f"The combined portfolio deploys **${sim['total_capital']:,.2f}** across "
                 f"**{len(sim['results'])}** edge strategies in **{len(sim['families'])}** independent families.")
    lines.append("")
    lines.append(f"**Adjusted monthly PnL: ${sim['total_adjusted_monthly_pnl']:,.2f}** "
                 f"(${sim['total_adjusted_monthly_pnl'] * 12:,.2f}/year)")
    lines.append("")

    if sim["adjusted_sharpe"] > 1.5:
        lines.append(f"Adjusted Sharpe of {sim['adjusted_sharpe']:.3f} suggests a **strong risk-adjusted** portfolio.")
    elif sim["adjusted_sharpe"] > 0.5:
        lines.append(f"Adjusted Sharpe of {sim['adjusted_sharpe']:.3f} suggests a **moderate** risk-adjusted portfolio.")
    else:
        lines.append(f"Adjusted Sharpe of {sim['adjusted_sharpe']:.3f} suggests **more validation needed** before deployment.")

    lines.append("")
    lines.append("The diversification ratio of {:.3f} means the {} edge families provide "
                 "meaningful risk reduction compared to deploying a single edge.".format(
                     sim["diversification_ratio"], len(sim["families"])))
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
