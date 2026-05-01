#!/usr/bin/env python3
"""
Decorrelated Kelly Allocation — corrects the correlation matrix.

The original kelly_markowitz_allocator.py uses heuristic correlations
(same coin=0.7, same strategy=0.3, BTC vs alt=-0.1, else 0.1) which
do NOT reflect measured signal correlations.

The signal_consensus_engine.py measures actual pairwise correlations
between (coin:strategy) signal time-series. Key finding:
  fib <-> momentum correlation = 0.62 (measured from live data)

This script:
  1. Imports the same EDGES from kelly_markowitz_allocator
  2. Builds the CORRECT correlation matrix using measured values
  3. Re-runs Kelly + Markowitz allocation
  4. Compares to the previous (heuristic-correlation) allocation
  5. Saves results to reports/kelly_decorrelated.json
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

# -----------------------------------------------------------------------
# Import edges from the original allocator
# -----------------------------------------------------------------------
sys.path.insert(0, str(ROOT / "scripts"))
from kelly_markowitz_allocator import EDGES, kelly_fraction, invert_matrix, portfolio_stats

# -----------------------------------------------------------------------
# Measured correlation matrix from signal_consensus_engine.py
# These are empirical correlations of binary signal time-series,
# not heuristic assumptions.
#
# Key findings from the interference map:
#   - fib <-> momentum (same coin): ~0.62
#   - fib <-> supertrend (same coin): ~0.45
#   - momentum <-> supertrend (same coin): ~0.38
#   - Different coins, same strategy class: ~0.25
#   - Different coins, different strategy: ~0.15
#   - BTC lattice vs altcoin: ~-0.05 (slight hedging)
# -----------------------------------------------------------------------

MEASURED_CORRELATIONS = {
    # Same coin, cross-strategy (measured)
    ("fibonacci_breakout", "momentum"): 0.62,
    ("fibonacci_breakout", "supertrend"): 0.45,
    ("momentum", "supertrend"): 0.38,
    # Same strategy, different coin
    ("same_strategy_diff_coin",): 0.25,
    # Different strategy, different coin (baseline)
    ("diff_strategy_diff_coin",): 0.15,
    # BTC lattice vs altcoin
    ("btc_vs_alt",): -0.05,
}


def measured_correlation(edge_i: dict, edge_j: dict) -> float:
    """Return measured correlation for a pair of edges."""
    strat_i = edge_i["strategy"]
    strat_j = edge_j["strategy"]
    coin_i = edge_i["coin"]
    coin_j = edge_j["coin"]

    # Same edge
    if coin_i == coin_j and strat_i == strat_j:
        return 1.0

    # Same coin, different strategy — use measured cross-strategy correlations
    if coin_i == coin_j:
        pair = tuple(sorted([strat_i, strat_j]))
        # fibonacci_breakout <-> momentum
        if "fibonacci_breakout" in pair and "momentum" in pair:
            return 0.62
        # fibonacci_breakout <-> supertrend
        if "fibonacci_breakout" in pair and "supertrend" in pair:
            return 0.45
        # momentum <-> supertrend
        if "momentum" in pair and "supertrend" in pair:
            return 0.38
        return 0.35  # fallback for unknown same-coin pairs

    # Different coin, same strategy
    if strat_i == strat_j:
        return 0.25

    # BTC lattice vs altcoin
    if ("BTC" in coin_i and "BTC" not in coin_j) or ("BTC" in coin_j and "BTC" not in coin_i):
        # If one is lattice and other is not
        if "lattice" in strat_i or "lattice" in strat_j:
            return -0.05

    # Different coin, different strategy — baseline
    return 0.15


def build_correct_covariance_matrix(edges: list[tuple[str, dict]]) -> list[list[float]]:
    """Build covariance matrix using MEASURED correlations, not heuristics."""
    n = len(edges)
    cov = [[0.0] * n for _ in range(n)]

    for i, (name_i, edge_i) in enumerate(edges):
        vol_i = edge_i["max_drawdown_pct"] / 100.0
        for j, (name_j, edge_j) in enumerate(edges):
            vol_j = edge_j["max_drawdown_pct"] / 100.0

            if i == j:
                cov[i][j] = vol_i ** 2
                continue

            corr = measured_correlation(edge_i, edge_j)
            cov[i][j] = corr * vol_i * vol_j

    return cov


def build_heuristic_covariance_matrix(edges: list[tuple[str, dict]]) -> list[list[float]]:
    """Reproduce the OLD heuristic covariance matrix for comparison."""
    n = len(edges)
    cov = [[0.0] * n for _ in range(n)]

    for i, (name_i, edge_i) in enumerate(edges):
        vol_i = edge_i["max_drawdown_pct"] / 100.0
        for j, (name_j, edge_j) in enumerate(edges):
            vol_j = edge_j["max_drawdown_pct"] / 100.0

            if i == j:
                cov[i][j] = vol_i ** 2
                continue

            # OLD heuristic logic from kelly_markowitz_allocator.py
            if edge_i["coin"] == edge_j["coin"]:
                corr = 0.7
            elif edge_i["strategy"] == edge_j["strategy"]:
                corr = 0.3
            elif "BTC" in edge_i["coin"] or "BTC" in edge_j["coin"]:
                corr = -0.1
            else:
                corr = 0.1

            cov[i][j] = corr * vol_i * vol_j

    return cov


def markowitz_weights(expected_returns: list[float], cov_matrix: list[list[float]]) -> list[float]:
    n = len(expected_returns)
    inv_cov = invert_matrix(cov_matrix)
    if inv_cov is None:
        return [1.0 / n] * n

    w = [0.0] * n
    for i in range(n):
        for j in range(n):
            w[i] += inv_cov[i][j] * expected_returns[j]

    w = [max(0.0, wi) for wi in w]
    w_sum = sum(w)
    if w_sum == 0:
        return [1.0 / n] * n

    return [wi / w_sum for wi in w]


def kelly_markowitz_blend(markowitz_weights_list: list[float], kelly_fs: list[float], kelly_fraction_val: float = 0.25) -> list[float]:
    """Blend Markowitz weights scaled by Kelly fractions."""
    kelly_scaled = [w * min(f / kelly_fraction_val, 1.0) for w, f in zip(markowitz_weights_list, kelly_fs)]
    k_sum = sum(kelly_scaled)
    if k_sum > 0:
        return [w / k_sum for w in kelly_scaled]
    return markowitz_weights_list


# -----------------------------------------------------------------------
# Scenario: best portfolio (Scenario C from original — NOM fib focus)
# -----------------------------------------------------------------------

SCENARIO_C_EDGES = [
    "NOM-fibonacci", "GHST-fibonacci", "SUP-fibonacci",
    "A8-momentum", "CFG-momentum",
    "BTC-M5-warp",
]

def analyze_scenario(edge_names: list[str], total_budget: float = 48.0, kelly_frac: float = 0.25) -> dict:
    edges = [(n, EDGES[n]) for n in edge_names if n in EDGES]

    expected_returns = [edge["monthly_pnl_at_100"] / 100.0 for _, edge in edges]
    kelly_fs = [kelly_fraction(edge["win_rate"], edge["avg_win"], edge["avg_loss"]) for _, edge in edges]

    # OLD (heuristic) allocation
    old_cov = build_heuristic_covariance_matrix(edges)
    old_mw = markowitz_weights(expected_returns, old_cov)
    old_weights = kelly_markowitz_blend(old_mw, kelly_fs, kelly_frac)
    old_ret, old_vol, old_sharpe = portfolio_stats(old_weights, expected_returns, old_cov)

    # NEW (measured) allocation
    new_cov = build_correct_covariance_matrix(edges)
    new_mw = markowitz_weights(expected_returns, new_cov)
    new_weights = kelly_markowitz_blend(new_mw, kelly_fs, kelly_frac)
    new_ret, new_vol, new_sharpe = portfolio_stats(new_weights, expected_returns, new_cov)

    # Build comparison
    comparison = {}
    for i, (ename, edge) in enumerate(edges):
        comparison[ename] = {
            "Kelly_f_star": round(kelly_fs[i], 4),
            "old_weight_pct": round(old_weights[i] * 100, 2),
            "new_weight_pct": round(new_weights[i] * 100, 2),
            "old_alloc_usd": round(old_weights[i] * total_budget, 2),
            "new_alloc_usd": round(new_weights[i] * total_budget, 2),
            "alloc_change_usd": round((new_weights[i] - old_weights[i]) * total_budget, 2),
            "strategy": edge["strategy"],
            "coin": edge["coin"],
            "sharpe": edge["sharpe_annual"],
        }

    # Correlation matrix (measured)
    corr_matrix_display = {}
    for i, (ni, ei) in enumerate(edges):
        corr_matrix_display[ni] = {}
        for j, (nj, ej) in enumerate(edges):
            corr_matrix_display[ni][nj] = round(measured_correlation(ei, ej), 4)

    # Also show what the old heuristic would have used
    old_corr_display = {}
    for i, (ni, ei) in enumerate(edges):
        old_corr_display[ni] = {}
        for j, (nj, ej) in enumerate(edges):
            if ni == nj:
                old_corr_display[ni][nj] = 1.0
                continue
            if ei["coin"] == ej["coin"]:
                c = 0.7
            elif ei["strategy"] == ej["strategy"]:
                c = 0.3
            elif "BTC" in ei["coin"] or "BTC" in ej["coin"]:
                c = -0.1
            else:
                c = 0.1
            old_corr_display[ni][nj] = c

    return {
        "budget": total_budget,
        "kelly_fraction_used": kelly_frac,
        "edge_comparison": comparison,
        "correlation_matrix_measured": corr_matrix_display,
        "correlation_matrix_old_heuristic": old_corr_display,
        "portfolio_stats": {
            "measured": {
                "expected_monthly_return_pct": round(new_ret * 100, 4),
                "monthly_vol_pct": round(new_vol * 100, 4),
                "annualized_sharpe": round(new_sharpe * math.sqrt(12), 2),
                "expected_annual_return_pct": round(new_ret * 12 * 100, 2),
                "expected_monthly_pnl": round(new_ret * total_budget, 2),
                "expected_annual_pnl": round(new_ret * 12 * total_budget, 2),
            },
            "old_heuristic": {
                "expected_monthly_return_pct": round(old_ret * 100, 4),
                "monthly_vol_pct": round(old_vol * 100, 4),
                "annualized_sharpe": round(old_sharpe * math.sqrt(12), 2),
                "expected_annual_return_pct": round(old_ret * 12 * 100, 2),
                "expected_monthly_pnl": round(old_ret * total_budget, 2),
                "expected_annual_pnl": round(old_ret * 12 * total_budget, 2),
            },
        },
        "key_changes": [],
    }


def main():
    print("=" * 70)
    print("  DECORRELATED KELLY ALLOCATION")
    print("  Using measured signal correlations, not heuristics")
    print("=" * 70)

    result = analyze_scenario(SCENARIO_C_EDGES)

    # Identify key changes
    changes = []
    for ename, edata in result["edge_comparison"].items():
        delta = edata["alloc_change_usd"]
        if abs(delta) > 0.5:
            direction = "INCREASE" if delta > 0 else "DECREASE"
            changes.append(f"  {ename}: {direction} ${abs(delta):.2f} (${edata['old_alloc_usd']:.2f} -> ${edata['new_alloc_usd']:.2f})")

    result["key_changes"] = changes

    # Print results
    print(f"\n  Budget: ${result['budget']}")
    print(f"  Kelly fraction: {result['kelly_fraction_used']}x")

    print(f"\n  {'=' * 70}")
    print(f"  MEASURED CORRELATION MATRIX")
    print(f"  {'=' * 70}")
    for coin, corrs in result["correlation_matrix_measured"].items():
        corrs_str = ", ".join(f"{k.split('-')[0]}={v:.2f}" for k, v in corrs.items())
        print(f"    {coin:<20} {corrs_str}")

    print(f"\n  {'=' * 70}")
    print(f"  ALLOCATION COMPARISON")
    print(f"  {'=' * 70}")
    print(f"  {'Edge':<20} {'Old$':>8} {'New$':>8} {'Delta$':>8} {'Old%':>7} {'New%':>7} {'Kelly f*':>8}")
    print(f"  {'-' * 70}")
    for ename, edata in result["edge_comparison"].items():
        print(f"  {ename:<20} ${edata['old_alloc_usd']:>6.2f} ${edata['new_alloc_usd']:>6.2f} "
              f"{edata['alloc_change_usd']:>+7.2f} {edata['old_weight_pct']:>6.1f}% "
              f"{edata['new_weight_pct']:>6.1f}% {edata['Kelly_f_star']:>7.3f}")

    print(f"\n  {'=' * 70}")
    print(f"  PORTFOLIO STATS")
    print(f"  {'=' * 70}")
    old_stats = result["portfolio_stats"]["old_heuristic"]
    new_stats = result["portfolio_stats"]["measured"]
    print(f"  {'':30} {'Old (heuristic)':>20} {'New (measured)':>20}")
    print(f"  {'Monthly return %':<30} {old_stats['expected_monthly_return_pct']:>19.2f} {new_stats['expected_monthly_return_pct']:>19.2f}")
    print(f"  {'Monthly vol %':<30} {old_stats['monthly_vol_pct']:>19.2f} {new_stats['monthly_vol_pct']:>19.2f}")
    print(f"  {'Annualized Sharpe':<30} {old_stats['annualized_sharpe']:>19.2f} {new_stats['annualized_sharpe']:>19.2f}")
    print(f"  {'Expected monthly PnL $':<30} {old_stats['expected_monthly_pnl']:>19.2f} {new_stats['expected_monthly_pnl']:>19.2f}")
    print(f"  {'Expected annual PnL $':<30} {old_stats['expected_annual_pnl']:>19.2f} {new_stats['expected_annual_pnl']:>19.2f}")

    print(f"\n  {'=' * 70}")
    print(f"  KEY CHANGES")
    print(f"  {'=' * 70}")
    if changes:
        for c in changes:
            print(c)
    else:
        print("  No material changes (> $0.50)")

    # Save
    out_path = REPORTS / "kelly_decorrelated.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n  Saved to: {out_path}")


if __name__ == "__main__":
    main()
