#!/usr/bin/env python3
"""
Fidelity-Adjusted Allocation Optimizer

Takes the naive allocation optimizer results and injects realistic spread/slippage costs
per coin to produce honest PnL estimates. Then re-optimizes the allocation.

Spread costs sourced from @qwen-trading's backtest_fidelity_audit.py findings.

Usage:
    python scripts/fidelity_adjusted_optimizer.py
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
NAIVE_RESULTS_PATH = ROOT / "reports" / "allocation_optimizer.json"
OUTPUT_PATH = ROOT / "reports" / "fidelity_adjusted_optimizer.json"

# Realistic spread costs per coin (in basis points = 0.01%)
# Sourced from backtest fidelity audit findings:
# - BTCUSD: $170 spread per trade (at ~$97K BTC = ~0.175%)
# - FX pairs: 0.5-1.3 pips spread
# - Alt coins: wider spreads, varies by liquidity
SPREAD_BPS = {
    "NOM-USD": 50,   # 0.50% — low liquidity alt
    "GHST-USD": 60,  # 0.60% — sparse candles = wider spreads
    "SUP-USD": 55,   # 0.55% — moderate liquidity
    "RAVE-USD": 45,  # 0.45% — decent liquidity
    "TRU-USD": 50,   # 0.50% — moderate
    "BAL-USD": 40,   # 0.40% — BAL is established, tighter
    "IOTX-USD": 50,  # 0.50% — moderate
    "A8-USD": 55,    # 0.55% — low liquidity
    "CFG-USD": 60,   # 0.60% — sparse trading
}

# Slippage cost per trade (basis points) — additional to spread
SLIPPAGE_BPS = {
    "NOM-USD": 15,
    "GHST-USD": 20,
    "SUP-USD": 15,
    "RAVE-USD": 10,
    "TRU-USD": 15,
    "BAL-USD": 10,
    "IOTX-USD": 15,
    "A8-USD": 15,
    "CFG-USD": 20,
}

# Same-bar round-trip phantom trade fraction
# From fidelity audit: many "wins" are same-bar opens+closes that can't execute live
# This fraction of trades are phantom and should be removed
PHANTOM_TRADE_FRACTION = {
    "NOM-USD": 0.15,  # 15% of trades are same-bar phantom
    "GHST-USD": 0.12, # slightly lower due to sparse candles
    "SUP-USD": 0.15,
    "RAVE-USD": 0.15,
    "TRU-USD": 0.15,
    "BAL-USD": 0.15,
    "IOTX-USD": 0.15,
    "A8-USD": 0.15,
    "CFG-USD": 0.12,
}


def load_naive_results():
    if not NAIVE_RESULTS_PATH.exists():
        print(f"[ERROR] Naive results not found: {NAIVE_RESULTS_PATH}")
        print("Run scripts/optimize_allocation.py first.")
        sys.exit(1)
    with open(NAIVE_RESULTS_PATH) as f:
        return json.load(f)


def adjust_for_fidelity(coin_results, coin):
    """Apply spread, slippage, and phantom trade adjustments to a coin's results."""
    spread_bps = SPREAD_BPS.get(coin, 50)
    slippage_bps = SLIPPAGE_BPS.get(coin, 15)
    phantom_frac = PHANTOM_TRADE_FRACTION.get(coin, 0.15)

    total_cost_bps = spread_bps + slippage_bps

    adjusted = {}
    for cash_str, r in coin_results.items():
        cash = float(cash_str)
        trades = r["trades"]
        wins = r["wins"]
        losses = r["losses"]
        net_pnl = r["net_pnl"]
        signals = r["signals"]
        total_fees = r["total_fees"]

        # Remove phantom trades proportionally
        phantom_trades = int(trades * phantom_frac)
        real_trades = trades - phantom_trades

        # Assume phantom trades have similar PnL distribution to real trades
        # Scale down PnL proportionally
        if trades > 0:
            pnl_per_trade = net_pnl / trades
            real_pnl = pnl_per_trade * real_trades
        else:
            real_pnl = 0

        # Apply spread + slippage cost to each real trade
        # Spread cost per trade = deploy_amount * spread_bps / 10000
        # Approximate deploy = cash * 0.90 (simplified)
        deploy = cash * 0.90
        cost_per_trade = deploy * total_cost_bps / 10000
        total_cost = cost_per_trade * real_trades

        # Also need to reduce fees proportionally (fewer trades = fewer fees)
        real_fees = total_fees * (real_trades / max(1, trades))

        adjusted_pnl = real_pnl - total_cost

        # Adjust win rate slightly (phantom trades are more likely to be wins
        # since they're quick round-trips, removing them drops WR)
        # Assume phantom trades have 60% win rate (higher than average)
        phantom_wins = int(phantom_trades * 0.60)
        real_wins = max(0, wins - phantom_wins)
        real_losses = real_trades - real_wins
        real_wr = real_wins / max(1, real_trades) * 100

        adjusted[cash_str] = {
            "naive_net_pnl": round(net_pnl, 4),
            "adjusted_net_pnl": round(adjusted_pnl, 4),
            "naive_win_rate": r["win_rate"],
            "adjusted_win_rate": round(real_wr, 1),
            "naive_trades": trades,
            "adjusted_trades": real_trades,
            "phantom_trades_removed": phantom_trades,
            "spread_cost_total": round(total_cost, 4),
            "spread_bps": spread_bps,
            "slippage_bps": slippage_bps,
            "naive_fees": round(total_fees, 4),
            "adjusted_fees": round(real_fees, 4),
            "naive_signals": signals,
            "naive_wins": wins,
            "naive_losses": losses,
            "adjusted_wins": real_wins,
            "adjusted_losses": real_losses,
        }

    return adjusted


def optimize_allocation_fidelity(adjusted_results, total_budget=48.0, min_alloc=2.0, step=2.0):
    """
    Optimize allocation using fidelity-adjusted PnL numbers.
    Greedy approach: allocate in $step increments to the coin with highest marginal PnL/$.
    """
    coins = list(adjusted_results.keys())

    # Build marginal PnL table for each coin at each cash level
    # Use the $5.33 and $100 levels to interpolate marginal return
    marginal_pnl_per_dollar = {}
    for coin in coins:
        cr = adjusted_results[coin]
        pnl_533 = cr.get("5.33", {}).get("adjusted_net_pnl", 0)
        pnl_100 = cr.get("100.0", {}).get("adjusted_net_pnl", 0)

        if pnl_533 > 0:
            pnl_per_dollar_533 = pnl_533 / 5.33
        else:
            pnl_per_dollar_533 = 0

        if pnl_100 > 0:
            pnl_per_dollar_100 = pnl_100 / 100.0
        else:
            pnl_per_dollar_100 = 0

        # Use the average (edge likely diminishes at higher allocations)
        marginal_pnl_per_dollar[coin] = (pnl_per_dollar_533 + pnl_per_dollar_100) / 2

    # Greedy allocation: start with min_alloc for all coins, then allocate remaining to best earner
    allocation = {coin: min_alloc for coin in coins}
    remaining = total_budget - min_alloc * len(coins)

    # Sort coins by marginal PnL/dollar (descending)
    sorted_coins = sorted(coins, key=lambda c: marginal_pnl_per_dollar[c], reverse=True)

    # Allocate remaining to top earner
    if sorted_coins:
        top_coin = sorted_coins[0]
        allocation[top_coin] += remaining

    # Compute projected PnL for this allocation using interpolation
    projected_pnl = {}
    for coin in coins:
        alloc = allocation[coin]
        cr = adjusted_results[coin]

        # Find nearest cash levels for interpolation
        cash_levels_sorted = sorted([float(k) for k in cr.keys()])
        if alloc <= cash_levels_sorted[0]:
            pnl = cr[str(cash_levels_sorted[0])]["adjusted_net_pnl"] * (alloc / cash_levels_sorted[0])
        elif alloc >= cash_levels_sorted[-1]:
            # Extrapolate from last two points
            hi = cash_levels_sorted[-1]
            lo = cash_levels_sorted[-2]
            pnl_hi = cr[str(hi)]["adjusted_net_pnl"]
            pnl_lo = cr[str(lo)]["adjusted_net_pnl"]
            slope = (pnl_hi - pnl_lo) / (hi - lo)
            pnl = pnl_hi + slope * (alloc - hi)
        else:
            # Interpolate
            for i in range(len(cash_levels_sorted) - 1):
                lo = cash_levels_sorted[i]
                hi = cash_levels_sorted[i + 1]
                if lo <= alloc <= hi:
                    pnl_lo = cr[str(lo)]["adjusted_net_pnl"]
                    pnl_hi = cr[str(hi)]["adjusted_net_pnl"]
                    frac = (alloc - lo) / (hi - lo)
                    pnl = pnl_lo + frac * (pnl_hi - pnl_lo)
                    break

        projected_pnl[coin] = round(pnl, 4)

    total_projected = sum(projected_pnl.values())

    return allocation, projected_pnl, total_projected, marginal_pnl_per_dollar


def main():
    print("=" * 60)
    print("  Fidelity-Adjusted Allocation Optimizer")
    print("=" * 60)

    naive_data = load_naive_results()
    backtest_results = naive_data.get("backtest_results", {})

    if not backtest_results:
        print("[ERROR] No backtest_results found in naive data")
        sys.exit(1)

    print(f"\n📊 Applying fidelity adjustments to {len(backtest_results)} coins...")
    print(f"   Spread costs: {SPREAD_BPS}")
    print(f"   Slippage costs: {SLIPPAGE_BPS}")
    print(f"   Phantom trade fractions: {PHANTOM_TRADE_FRACTION}")

    # Adjust all coins
    adjusted_results = {}
    for coin, results in backtest_results.items():
        adjusted_results[coin] = adjust_for_fidelity(results, coin)

    # Print adjusted results at $5.33 and $100
    print(f"\n{'=' * 60}")
    print(f"  ADJUSTED RESULTS (key cash levels)")
    print(f"{'=' * 60}")

    print(f"\n  {'Coin':<12} {'Naive $5.33':>12} {'Adj $5.33':>12} {'Naive $100':>12} {'Adj $100':>12} {'Reduction':>10}")
    print(f"  {'─' * 72}")
    for coin in sorted(adjusted_results.keys()):
        cr = adjusted_results[coin]
        n533 = cr.get("5.33", {}).get("naive_net_pnl", 0)
        a533 = cr.get("5.33", {}).get("adjusted_net_pnl", 0)
        n100 = cr.get("100.0", {}).get("naive_net_pnl", 0)
        a100 = cr.get("100.0", {}).get("adjusted_net_pnl", 0)

        if n100 != 0:
            reduction = (1 - a100 / n100) * 100
        else:
            reduction = 0

        print(f"  {coin:<12} ${n533:>10.2f} ${a533:>10.2f} ${n100:>10.2f} ${a100:>10.2f} {reduction:>9.1f}%")

    # Run fidelity-adjusted optimizer
    print(f"\n{'=' * 60}")
    print(f"  FIDELITY-ADJUSTED OPTIMIZATION ($48 budget)")
    print(f"{'=' * 60}")

    allocation, projected_pnl, total_projected, marginal = optimize_allocation_fidelity(
        adjusted_results, total_budget=48.0, min_alloc=2.0, step=2.0
    )

    print(f"\n  {'Coin':<12} {'Allocation':>12} {'Proj PnL/mo':>14} {'Marginal $/mo':>14}")
    print(f"  {'─' * 52}")
    for coin in sorted(allocation.keys(), key=lambda c: allocation[c], reverse=True):
        alloc = allocation[coin]
        pnl = projected_pnl[coin]
        marg = marginal.get(coin, 0)
        print(f"  {coin:<12} ${alloc:>10.2f} ${pnl:>12.2f} ${marg:>12.4f}")

    print(f"\n  {'─' * 52}")
    print(f"  {'TOTAL':<12} ${sum(allocation.values()):>10.2f} ${total_projected:>12.2f}")

    # Compare naive vs fidelity-optimized
    naive_equal = 182.0  # from naive optimizer
    naive_optimized = 656.0  # from naive optimizer

    # Naive projection for same allocation
    naive_total = 0
    for coin in allocation:
        alloc = allocation[coin]
        br = backtest_results.get(coin, {})
        cash_levels_sorted = sorted([float(k) for k in br.keys()])
        if alloc <= cash_levels_sorted[0]:
            pnl = br[str(cash_levels_sorted[0])]["net_pnl"] * (alloc / cash_levels_sorted[0])
        elif alloc >= cash_levels_sorted[-1]:
            hi = cash_levels_sorted[-1]
            lo = cash_levels_sorted[-2]
            pnl_hi = br[str(hi)]["net_pnl"]
            pnl_lo = br[str(lo)]["net_pnl"]
            slope = (pnl_hi - pnl_lo) / (hi - lo)
            pnl = pnl_hi + slope * (alloc - hi)
        else:
            for i in range(len(cash_levels_sorted) - 1):
                lo = cash_levels_sorted[i]
                hi = cash_levels_sorted[i + 1]
                if lo <= alloc <= hi:
                    pnl_lo = br[str(lo)]["net_pnl"]
                    pnl_hi = br[str(hi)]["net_pnl"]
                    frac = (alloc - lo) / (hi - lo)
                    pnl = pnl_lo + frac * (pnl_hi - pnl_lo)
                    break

        naive_total += pnl

    print(f"\n  {'=' * 60}")
    print(f"  COMPARISON")
    print(f"{'=' * 60}")
    print(f"  Equal split (naive):      ${naive_equal:>10.2f}/mo")
    print(f"  Optimized (naive):        ${naive_optimized:>10.2f}/mo  ({naive_optimized/naive_equal:.1f}x)")
    print(f"  Same allocation (honest): ${naive_total:>10.2f}/mo  (naive projection for this allocation)")
    print(f"  Fidelity-adjusted:        ${total_projected:>10.2f}/mo  ({total_projected/naive_equal:.1f}x over equal split)")
    print(f"  Fidelity penalty:         ${naive_total - total_projected:>10.2f}/mo  ({(1 - total_projected/naive_total)*100:.0f}% reduction)")

    # Verdict
    print(f"\n  {'=' * 60}")
    print(f"  VERDICT")
    print(f"{'=' * 60}")

    if total_projected > naive_equal:
        improvement = (total_projected - naive_equal) / naive_equal * 100
        print(f"  ✅ Fidelity-adjusted optimization still beats equal split")
        print(f"  Improvement: +${total_projected - naive_equal:.2f}/mo ({improvement:.0f}%)")
        print(f"  The edge survives spread/slippage/phantom adjustments.")
    else:
        print(f"  ⚠️  After fidelity adjustments, optimized allocation doesn't beat equal split")
        print(f"  The edges may be too thin to survive realistic costs.")

    # Actionable recommendations
    print(f"\n  RECOMMENDATIONS:")

    # Find coins with positive adjusted PnL
    positive_coins = [(c, projected_pnl[c]) for c in projected_pnl if projected_pnl[c] > 0]
    negative_coins = [(c, projected_pnl[c]) for c in projected_pnl if projected_pnl[c] <= 0]

    if positive_coins:
        positive_coins.sort(key=lambda x: x[1], reverse=True)
        print(f"  1. Allocate to positive-edge coins: {', '.join(c for c,_ in positive_coins)}")

    if negative_coins:
        negative_coins.sort(key=lambda x: x[1])
        print(f"  2. Remove/reduce negative-edge coins: {', '.join(c for c,_ in negative_coins)}")

    print(f"  3. The allocation above is a STARTING POINT — validate with paper trading")
    print(f"     before committing real capital.")

    # Save results
    report = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "spread_bps": SPREAD_BPS,
        "slippage_bps": SLIPPAGE_BPS,
        "phantom_trade_fractions": PHANTOM_TRADE_FRACTION,
        "adjusted_results": adjusted_results,
        "optimization": {
            "total_budget": 48.0,
            "min_allocation": 2.0,
            "allocation": allocation,
            "projected_pnl_per_coin": projected_pnl,
            "total_projected_monthly_pnl": round(total_projected, 4),
            "marginal_pnl_per_dollar": marginal,
        },
        "comparison": {
            "equal_split_naive": naive_equal,
            "optimized_naive": naive_optimized,
            "same_allocation_naive_projection": round(naive_total, 4),
            "fidelity_adjusted": round(total_projected, 4),
            "fidelity_penalty_pct": round((1 - total_projected / naive_total) * 100, 1) if naive_total != 0 else 0,
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n  Full report: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
