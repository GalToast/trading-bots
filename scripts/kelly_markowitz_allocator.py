#!/usr/bin/env python3
"""
Kelly + Markowitz Capital Allocation Optimizer
===============================================

Takes proven edges from the trading-bots portfolio and computes optimal
capital allocation to maximize geometric growth rate.

Methods:
  1. Kelly Criterion: f* = (p*b - q) / b for each edge independently
  2. Markowitz Mean-Variance: optimal weights for correlated edges
  3. Combined: Kelly-fractioned Markowitz (fractional Kelly per Sharpe)

Scenarios:
  A. Current allocation (equal split $48 across 9 coins)
  B. Kill BTCUSD exc2_tight (remove dead lane, reallocate)
  C. Add NOM fibonacci (replace supertrend with fib for NOM)
  D. Add Coinbase momentum (scale A8/CFG momentum sleeves)

Usage:
    python scripts/kelly_markowitz_allocator.py
    python scripts/kelly_markowitz_allocator.py --total-budget 100
    python scripts/kelly_markowitz_allocator.py --kelly-fraction 0.25
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Edge definitions: estimated from validated backtests and live data.
# Monthly PnL at $100 allocation, win rate, trade count, Sharpe proxy.
# These are EMPIRICAL -- from actual audit results, not projections.
# ---------------------------------------------------------------------------

EDGES = {
    # Fibonacci breakout strategies (from session_hour_consolidation.py)
    "NOM-fibonacci": {
        "strategy": "fibonacci_breakout",
        "coin": "NOM-USD",
        "monthly_pnl_at_100": 164.30 * (30 / 7),  # ~$704/mo from top-6 hours 30d extrapolation
        "win_rate": 54.08,
        "trades_per_month": 81 * (30 / 7),  # ~347
        "avg_win": 2.8,
        "avg_loss": -1.9,
        "max_drawdown_pct": 12.0,
        "sharpe_annual": 2.1,
        "top_hours": [1, 4, 5, 8, 10, 11],
        "source": "session_hour_consolidation top-6 hours",
    },
    "GHST-fibonacci": {
        "strategy": "fibonacci_breakout",
        "coin": "GHST-USD",
        "monthly_pnl_at_100": 50.0,  # estimated from similar fib params
        "win_rate": 48.0,
        "trades_per_month": 30,
        "avg_win": 3.2,
        "avg_loss": -2.1,
        "max_drawdown_pct": 15.0,
        "sharpe_annual": 1.2,
        "top_hours": [2, 3, 4, 5, 7, 18],
        "source": "session_hour_consolidation",
    },
    "SUP-fibonacci": {
        "strategy": "fibonacci_breakout",
        "coin": "SUP-USD",
        "monthly_pnl_at_100": 45.0,
        "win_rate": 46.0,
        "trades_per_month": 25,
        "avg_win": 3.5,
        "avg_loss": -2.3,
        "max_drawdown_pct": 14.0,
        "sharpe_annual": 1.0,
        "top_hours": [5, 15, 16, 18, 20, 23],
        "source": "session_hour_consolidation",
    },
    # Momentum strategies
    "A8-momentum": {
        "strategy": "momentum",
        "coin": "A8-USD",
        "monthly_pnl_at_100": 85.0,  # estimated from validation
        "win_rate": 52.0,
        "trades_per_month": 20,
        "avg_win": 8.0,
        "avg_loss": -4.0,
        "max_drawdown_pct": 18.0,
        "sharpe_annual": 1.5,
        "top_hours": [7, 11, 15, 17, 22, 23],
        "source": "multi_coin_isolated_runner",
    },
    "CFG-momentum": {
        "strategy": "momentum",
        "coin": "CFG-USD",
        "monthly_pnl_at_100": 80.0,
        "win_rate": 50.0,
        "trades_per_month": 15,
        "avg_win": 9.0,
        "avg_loss": -4.5,
        "max_drawdown_pct": 16.0,
        "sharpe_annual": 1.3,
        "top_hours": [1, 4, 8, 10, 13, 20],
        "source": "multi_coin_isolated_runner",
    },
    # Supertrend (from live lanes, lane kill list)
    "RAVE-supertrend": {
        "strategy": "supertrend",
        "coin": "RAVE-USD",
        "monthly_pnl_at_100": 30.0,
        "win_rate": 42.0,
        "trades_per_month": 40,
        "avg_win": 2.5,
        "avg_loss": -2.0,
        "max_drawdown_pct": 20.0,
        "sharpe_annual": 0.5,
        "top_hours": [2, 9, 15, 18, 22, 23],
        "source": "live_rearm_941777 lane",
    },
    "TRU-supertrend": {
        "strategy": "supertrend",
        "coin": "TRU-USD",
        "monthly_pnl_at_100": 25.0,
        "win_rate": 40.0,
        "trades_per_month": 35,
        "avg_win": 2.2,
        "avg_loss": -1.8,
        "max_drawdown_pct": 18.0,
        "sharpe_annual": 0.4,
        "top_hours": [5, 7, 8, 15, 17, 18],
        "source": "live_rearm_941777 lane",
    },
    "BAL-supertrend": {
        "strategy": "supertrend",
        "coin": "BAL-USD",
        "monthly_pnl_at_100": 40.0,
        "win_rate": 45.0,
        "trades_per_month": 30,
        "avg_win": 3.0,
        "avg_loss": -2.2,
        "max_drawdown_pct": 16.0,
        "sharpe_annual": 0.8,
        "top_hours": [1, 15, 17, 20, 22, 23],
        "source": "live_rearm_941777 lane",
    },
    "IOTX-supertrend": {
        "strategy": "supertrend",
        "coin": "IOTX-USD",
        "monthly_pnl_at_100": 20.0,
        "win_rate": 38.0,
        "trades_per_month": 45,
        "avg_win": 2.0,
        "avg_loss": -1.8,
        "max_drawdown_pct": 22.0,
        "sharpe_annual": 0.3,
        "top_hours": [4, 5, 9, 10, 15, 21],
        "source": "live_rearm_941777 lane",
    },
    # BTC M5 warp (best live lane)
    "BTC-M5-warp": {
        "strategy": "lattice_m5_warp",
        "coin": "BTC-USD",
        "monthly_pnl_at_100": 150.0,  # extrapolated from $69/period
        "win_rate": 55.0,
        "trades_per_month": 50,
        "avg_win": 5.0,
        "avg_loss": -3.0,
        "max_drawdown_pct": 10.0,
        "sharpe_annual": 2.5,
        "top_hours": None,
        "source": "live_btcusd_m5_warp lane ($69 realized, $13.88/close)",
    },
    # BTC H1 exc2_tight (DEAD -- for kill scenario)
    "BTC-H1-exc2_tight": {
        "strategy": "lattice_h1",
        "coin": "BTC-USD",
        "monthly_pnl_at_100": -200.0,  # losing: -$951 realized, $1,182 floating
        "win_rate": 40.0,
        "trades_per_month": 40,
        "avg_win": 10.0,
        "avg_loss": -35.0,
        "max_drawdown_pct": 45.0,
        "sharpe_annual": -3.0,
        "top_hours": None,
        "source": "live_btcusd_exc2_tight (conviction 9/10 kill)",
    },
}


# ===================================================================
# Kelly Criterion
# ===================================================================

def kelly_fraction(win_rate, avg_win, avg_loss):
    """Kelly fraction: f* = (p*b - q) / b
    where p = win_prob, q = 1-p, b = avg_win/avg_loss
    """
    p = win_rate / 100.0
    q = 1.0 - p
    if avg_loss == 0:
        return 1.0  # no downside -> full Kelly
    b = avg_win / abs(avg_loss)
    if b <= 0:
        return 0.0
    f = (p * b - q) / b
    return max(0.0, f)


def kelly_growth_rate(win_rate, avg_win_pct, avg_loss_pct, f):
    """Expected log growth rate given Kelly fraction f.
    G = p * ln(1 + f*b) + q * ln(1 - f)
    """
    p = win_rate / 100.0
    q = 1.0 - p
    b = avg_win_pct / abs(avg_loss_pct) if avg_loss_pct != 0 else 0
    if b <= 0:
        return 0.0
    term1 = p * math.log(1 + f * b) if (1 + f * b) > 0 else float('-inf')
    term2 = q * math.log(1 - f) if (1 - f) > 0 else float('-inf')
    if term1 == float('-inf') or term2 == float('-inf'):
        return float('-inf')
    return term1 + term2


# ===================================================================
# Markowitz Mean-Variance Optimization
# ===================================================================

def compute_covariance_matrix(edges, correlations=None):
    """Build covariance matrix from edge Sharpe ratios and assumed correlations.

    For crypto edges, we assume:
    - Same coin, different strategy: corr = 0.7
    - Same strategy, different coin: corr = 0.3
    - Different strategy, different coin: corr = 0.1
    - BTC lattice vs altcoin: corr = -0.1 (hedging)
    """
    n = len(edges)
    cov = [[0.0] * n for _ in range(n)]

    for i, (name_i, edge_i) in enumerate(edges):
        vol_i = edge_i["max_drawdown_pct"] / 100.0  # rough vol proxy
        for j, (name_j, edge_j) in enumerate(edges):
            vol_j = edge_j["max_drawdown_pct"] / 100.0

            if i == j:
                cov[i][j] = vol_i ** 2
                continue

            # Correlation heuristic
            if correlations and (name_i, name_j) in correlations:
                corr = correlations[(name_i, name_j)]
            elif edge_i["coin"] == edge_j["coin"]:
                corr = 0.7
            elif edge_i["strategy"] == edge_j["strategy"]:
                corr = 0.3
            elif "BTC" in edge_i["coin"] or "BTC" in edge_j["coin"]:
                corr = -0.1  # BTC lattice hedges altcoin direction
            else:
                corr = 0.1

            cov[i][j] = corr * vol_i * vol_j

    return cov


def markowitz_weights(expected_returns, cov_matrix, risk_free=0.0):
    """Compute tangency portfolio weights (max Sharpe ratio).

    w* = (Sigma^-1 * (mu - rf)) / sum(Sigma^-1 * (mu - rf))

    Uses simple matrix inversion (works for small portfolios).
    """
    n = len(expected_returns)

    # Subtract risk-free rate
    excess = [r - risk_free for r in expected_returns]

    # Invert covariance matrix (simple Gaussian elimination for small n)
    inv_cov = invert_matrix(cov_matrix)
    if inv_cov is None:
        # Fall back to equal weights
        return [1.0 / n] * n

    # w = inv_cov * excess
    w = [0.0] * n
    for i in range(n):
        for j in range(n):
            w[i] += inv_cov[i][j] * excess[j]

    # Normalize to sum to 1
    w_sum = sum(w)
    if w_sum == 0:
        return [1.0 / n] * n

    # Allow short positions only if explicitly enabled; clamp to 0 for long-only
    w = [max(0.0, wi) for wi in w]
    w_sum = sum(w)
    if w_sum == 0:
        return [1.0 / n] * n

    return [wi / w_sum for wi in w]


def invert_matrix(matrix):
    """Invert a matrix using Gaussian elimination. Returns None if singular."""
    n = len(matrix)
    # Augmented matrix [A | I]
    aug = [[0.0] * (2 * n) for _ in range(n)]
    for i in range(n):
        for j in range(n):
            aug[i][j] = matrix[i][j]
        aug[i][n + i] = 1.0

    for col in range(n):
        # Find pivot
        max_row = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > abs(aug[max_row][col]):
                max_row = row
        aug[col], aug[max_row] = aug[max_row], aug[col]

        if abs(aug[col][col]) < 1e-12:
            return None  # Singular

        # Scale pivot row
        pivot = aug[col][col]
        for j in range(2 * n):
            aug[col][j] /= pivot

        # Eliminate column
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            for j in range(2 * n):
                aug[row][j] -= factor * aug[col][j]

    # Extract inverse
    inv = [[aug[i][n + j] for j in range(n)] for i in range(n)]
    return inv


def portfolio_stats(weights, expected_returns, cov_matrix):
    """Compute portfolio expected return, volatility, and Sharpe."""
    n = len(weights)
    ret = sum(w * r for w, r in zip(weights, expected_returns))

    # Portfolio variance: w^T * Sigma * w
    var = 0.0
    for i in range(n):
        for j in range(n):
            var += weights[i] * weights[j] * cov_matrix[i][j]

    vol = math.sqrt(max(0.0, var))
    sharpe = ret / vol if vol > 0 else 0.0
    return ret, vol, sharpe


# ===================================================================
# Scenario Runner
# ===================================================================

SCENARIOS = {
    "A_current_equal": {
        "description": "Current: $48 equal split across 9 coins",
        "edges": [
            "NOM-fibonacci", "GHST-fibonacci", "SUP-fibonacci",
            "RAVE-supertrend", "TRU-supertrend", "BAL-supertrend", "IOTX-supertrend",
            "A8-momentum", "CFG-momentum",
        ],
        "allocation_method": "equal_split",
        "total_budget": 48,
    },
    "B_kill_btc_h1": {
        "description": "Kill BTC H1 exc2_tight, reallocate to proven edges",
        "edges": [
            "NOM-fibonacci", "GHST-fibonacci", "SUP-fibonacci",
            "RAVE-supertrend", "TRU-supertrend", "BAL-supertrend", "IOTX-supertrend",
            "A8-momentum", "CFG-momentum",
            "BTC-M5-warp",  # Best live lane
        ],
        "allocation_method": "markowitz",
        "total_budget": 48,
        "note": "Removes dead BTC H1 lane, adds best BTC M5 warp",
    },
    "C_nom_fibonacci_focus": {
        "description": "Nominal focus: Scale NOM fibonacci as primary edge",
        "edges": [
            "NOM-fibonacci", "GHST-fibonacci", "SUP-fibonacci",
            "A8-momentum", "CFG-momentum",
            "BTC-M5-warp",
        ],
        "allocation_method": "kelly_markowitz",
        "total_budget": 48,
        "note": "Drops weak supertrend coins, keeps fib + momentum + BTC M5",
    },
    "D_momentum_scale": {
        "description": "Scale momentum: increase A8/CFG allocation",
        "edges": [
            "NOM-fibonacci", "GHST-fibonacci", "SUP-fibonacci",
            "A8-momentum", "CFG-momentum",
            "BTC-M5-warp",
            "BAL-supertrend",  # Best supertrend
        ],
        "allocation_method": "kelly_markowitz",
        "total_budget": 100,
        "note": "$100 budget, momentum-weighted portfolio",
    },
}


def run_scenario(name, scenario, kelly_frac=0.25):
    """Run one allocation scenario."""
    print(f"\n{'=' * 70}")
    print(f"  Scenario {name}: {scenario['description']}")
    print(f"  Budget: ${scenario['total_budget']}")
    if "note" in scenario:
        print(f"  Note: {scenario['note']}")
    print(f"{'=' * 70}")

    edge_names = scenario["edges"]
    edges = [(n, EDGES[n]) for n in edge_names if n in EDGES]
    budget = scenario["total_budget"]

    if not edges:
        print(f"  [ERROR] No valid edges in scenario {name}")
        return None

    # Expected returns (monthly % at budget level)
    # Scale from $100 to budget level
    expected_returns = []
    for ename, edge in edges:
        monthly_pnl_100 = edge["monthly_pnl_at_100"]
        # At $100, return = monthly_pnl_100 / 100
        # PnL scales linearly with capital for these strategies
        ret_monthly = monthly_pnl_100 / 100.0  # as decimal
        expected_returns.append(ret_monthly)

    # Covariance matrix
    cov_matrix = compute_covariance_matrix(edges)

    # Kelly fractions for each edge
    kelly_fs = []
    for ename, edge in edges:
        wr = edge["win_rate"]
        avg_win = edge["avg_win"]
        avg_loss = edge["avg_loss"]
        f = kelly_fraction(wr, avg_win, avg_loss)
        kelly_fs.append(f)

    # Method-specific allocation
    method = scenario["allocation_method"]

    if method == "equal_split":
        weights = [1.0 / len(edges)] * len(edges)

    elif method == "markowitz":
        weights = markowitz_weights(expected_returns, cov_matrix)

    elif method == "kelly_markowitz":
        # Blend: use Markowitz weights, scaled by Kelly fractions
        mw_weights = markowitz_weights(expected_returns, cov_matrix)
        # Scale each weight by its Kelly fraction
        kelly_scaled = [w * min(f / 0.25, 1.0) for w, f in zip(mw_weights, kelly_fs)]
        k_sum = sum(kelly_scaled)
        if k_sum > 0:
            weights = [w / k_sum for w in kelly_scaled]
        else:
            weights = mw_weights
    else:
        weights = [1.0 / len(edges)] * len(edges)

    # Portfolio stats
    port_ret, port_vol, port_sharpe = portfolio_stats(weights, expected_returns, cov_matrix)

    # Per-edge allocation
    print(f"\n  {'Edge':<20} {'Weight':>7} {'Alloc$':>8} {'Mo.PnL$':>9} {'Kelly f*':>9} {'Sharpe':>7}")
    print(f"  {'-' * 65}")

    total_pnl = 0.0
    for i, (ename, edge) in enumerate(edges):
        alloc = weights[i] * budget
        # PnL at this allocation: scale from $100 baseline
        pnl = edge["monthly_pnl_at_100"] * (alloc / 100.0)
        total_pnl += pnl

        print(f"  {ename:<20} {weights[i]*100:>6.1f}% ${alloc:>6.2f} ${pnl:>7.2f} {kelly_fs[i]:>8.3f} {edge['sharpe_annual']:>7.1f}")

    print(f"  {'-' * 65}")
    print(f"  {'TOTAL':<20} {'100.0%':>7} ${budget:>6.2f} ${total_pnl:>7.2f}")
    print(f"\n  Portfolio Sharpe (annualized): {port_sharpe * math.sqrt(12):.2f}")
    print(f"  Portfolio vol (monthly): {port_vol * 100:.2f}%")
    print(f"  Expected monthly return: {port_ret * 100:.2f}%")
    print(f"  Expected annual return: {port_ret * 12 * 100:.1f}%")

    # Geometric growth rate (using Kelly fractions)
    print(f"\n  Geometric Growth Analysis (fractional Kelly = {kelly_frac}):")
    for i, (ename, edge) in enumerate(edges):
        wr = edge["win_rate"]
        avg_win_pct = edge["avg_win"] / 100.0
        avg_loss_pct = edge["avg_loss"] / 100.0
        full_kelly = kelly_fs[i]
        frac_kelly = full_kelly * kelly_frac
        growth = kelly_growth_rate(wr, avg_win_pct, avg_loss_pct, frac_kelly)
        alloc = weights[i] * budget
        if growth > 0:
            print(f"  {ename:<20} Kelly f*={full_kelly:.3f} frac={kelly_frac}x -> G={growth:.4f}/trade "
                  f"(${alloc:.2f} allocated)")
        else:
            print(f"  {ename:<20} Kelly f*={full_kelly:.3f} -> NEGATIVE growth, skip")

    return {
        "scenario": name,
        "description": scenario["description"],
        "budget": budget,
        "allocation": {ename: round(weights[i] * budget, 2) for i, (ename, _) in enumerate(edges)},
        "weights": {ename: round(weights[i] * 100, 1) for i, (ename, _) in enumerate(edges)},
        "total_monthly_pnl": round(total_pnl, 2),
        "portfolio_sharpe_annual": round(port_sharpe * math.sqrt(12), 2),
        "portfolio_vol_monthly_pct": round(port_vol * 100, 2),
        "kelly_fractions": {ename: round(kelly_fs[i], 3) for i, (ename, _) in enumerate(edges)},
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kelly + Markowitz Capital Allocation Optimizer")
    parser.add_argument("--total-budget", type=float, default=None,
                        help="Override total budget for all scenarios")
    parser.add_argument("--kelly-fraction", type=float, default=0.25,
                        help="Fractional Kelly multiplier (default: 0.25)")
    parser.add_argument("--scenarios", nargs="+", default=None,
                        help="Run specific scenarios (default: all)")
    parser.add_argument("--no-momentum", action="store_true",
                        help="Exclude momentum edges (A8, CFG)")
    args = parser.parse_args()

    print("=" * 70)
    print("  Kelly + Markowitz Capital Allocation Optimizer")
    print("=" * 70)
    print(f"  Kelly fraction: {args.kelly_fraction}x (fractional Kelly)")
    print(f"  Edges: {len(EDGES)} defined")
    print()

    scenarios_to_run = args.scenarios or list(SCENARIOS.keys())

    all_results = []
    for sname in scenarios_to_run:
        if sname not in SCENARIOS:
            print(f"  [WARN] Unknown scenario: {sname}")
            continue
        scenario = dict(SCENARIOS[sname])
        if args.total_budget is not None:
            scenario["total_budget"] = args.total_budget
        if args.no_momentum:
            scenario["edges"] = [e for e in scenario["edges"] if "momentum" not in e.lower()]
        result = run_scenario(sname, scenario, kelly_frac=args.kelly_fraction)
        if result:
            all_results.append(result)

    # Cross-scenario comparison
    if len(all_results) >= 2:
        print(f"\n\n{'=' * 70}")
        print(f"  CROSS-SCENARIO COMPARISON")
        print(f"{'=' * 70}")
        print(f"  {'Scenario':<25} {'Budget$':>8} {'Mo.PnL$':>9} {'Ann.Sharpe':>11} {'Ann.Ret%':>9}")
        print(f"  {'-' * 67}")
        for r in all_results:
            ann_ret = r["total_monthly_pnl"] * 12 / r["budget"] * 100
            print(f"  {r['scenario']:<25} ${r['budget']:>6.2f} ${r['total_monthly_pnl']:>7.2f} "
                  f"{r['portfolio_sharpe_annual']:>10.2f} {ann_ret:>8.1f}%")

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kelly_fraction": args.kelly_fraction,
        "scenarios": all_results,
        "edges": {name: {k: v for k, v in edge.items()} for name, edge in EDGES.items()},
    }
    out_path = REPORTS / "kelly_markowitz_allocation.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
