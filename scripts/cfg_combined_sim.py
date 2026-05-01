#!/usr/bin/env python3
"""
CFG Sleeve Combined Portfolio Simulator

Tests whether CFG/BTC + CFG/ETH can be deployed together despite capital coupling.
The repeated walk-forward validated BOTH sleeves (4/4 positive each), but the
coupling audit showed 98.5% overlap. This script simulates the combined deployment
to answer: does stacking two CFG sleeves add value, or are they redundant?

Usage:
    python scripts/cfg_combined_sim.py
    python scripts/cfg_combined_sim.py --correlation 0.98  # Test different coupling levels
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# Walk-forward validated results
CFG_BTC = {
    "name": "CFG/BTC",
    "forward_net": 0.0245,
    "forward_closes": 53,
    "forward_windows": 4,
    "positive_windows": 4,
    "win_rate": 0.50,  # estimated
    "capital_required": 845.0,  # BTC denominator
}

CFG_ETH = {
    "name": "CFG/ETH",
    "forward_net": 0.0156,
    "forward_closes": 51,
    "forward_windows": 4,
    "positive_windows": 4,
    "win_rate": 0.50,
    "capital_required": 22.0,  # ETH denominator
}

# Coupling analysis results
COUPLING_FRACTION = 0.985  # 98.5% of signals fire simultaneously


def simulate_combined(correlation=COUPLING_FRACTION):
    """Simulate combined deployment of CFG/BTC + CFG/ETH."""

    # Individual metrics
    btc_total = CFG_BTC["forward_net"]
    eth_total = CFG_ETH["forward_net"]

    # Combined naive (if independent)
    naive_combined = btc_total + eth_total

    # Adjust for coupling: when signals fire simultaneously, the effective
    # edge per combined trade is the average, not the sum
    # Coupled signals: both fire → take average edge
    # Uncoupled signals: one fires → take that edge

    btc_closes = CFG_BTC["forward_closes"]
    eth_closes = CFG_ETH["forward_closes"]

    # Expected coupled trades (both fire simultaneously)
    coupled_trades = int(min(btc_closes, eth_closes) * correlation)
    # Independent trades
    btc_only = btc_closes - coupled_trades
    eth_only = eth_closes - coupled_trades

    # Average edge per trade (from walk-forward)
    btc_avg = btc_total / btc_closes if btc_closes > 0 else 0
    eth_avg = eth_total / eth_closes if eth_closes > 0 else 0

    # Combined PnL with coupling adjustment
    # When coupled, we take the better of the two edges (not the sum)
    coupled_pnl = coupled_trades * max(btc_avg, eth_avg)
    independent_pnl = btc_only * btc_avg + eth_only * eth_avg
    adjusted_combined = coupled_pnl + independent_pnl

    # Capital efficiency
    # If deploying both: need $845 (BTC) + $22 (ETH) = $867
    # But 98.5% of signals fire at same time, so capital is duplicated
    total_capital = CFG_BTC["capital_required"] + CFG_ETH["capital_required"]

    # Results
    return {
        "correlation": correlation,
        "cfg_btc": {
            "name": CFG_BTC["name"],
            "forward_net": btc_total,
            "closes": btc_closes,
            "avg_per_close": btc_avg,
            "capital": CFG_BTC["capital_required"],
        },
        "cfg_eth": {
            "name": CFG_ETH["name"],
            "forward_net": eth_total,
            "closes": eth_closes,
            "avg_per_close": eth_avg,
            "capital": CFG_ETH["capital_required"],
        },
        "combined_naive": naive_combined,
        "combined_adjusted": adjusted_combined,
        "coupling_loss": naive_combined - adjusted_combined,
        "coupling_pct": (1 - adjusted_combined / naive_combined) * 100 if naive_combined > 0 else 0,
        "coupled_trades": coupled_trades,
        "btc_only_trades": btc_only,
        "eth_only_trades": eth_only,
        "total_trades": coupled_trades + btc_only + eth_only,
        "total_capital": total_capital,
        "roc_naive": naive_combined / total_capital * 100,
        "roc_adjusted": adjusted_combined / total_capital * 100,
        "verdict": "STACK" if adjusted_combined > max(btc_total, eth_total) * 1.1 else "SINGLE",
    }


def print_results(results):
    """Print simulation results."""
    print("=" * 80)
    print("  CFG SLEEVE COMBINED PORTFOLIO SIMULATOR")
    print(f"  Correlation: {results['correlation']*100:.1f}%")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)

    print(f"\n  INDIVIDUAL SLEEVES (4/4 walk-forward validated):")
    print(f"  {'Sleeve':<15} {'Fwd Net':>10} {'Closes':>7} {'Avg/Close':>10} {'Capital':>10}")
    print("-" * 80)

    for sleeve in [results["cfg_btc"], results["cfg_eth"]]:
        print(f"  {sleeve['name']:<15} ${sleeve['forward_net']:>9.4f} {sleeve['closes']:>7d} "
              f"${sleeve['avg_per_close']:>9.4f} ${sleeve['capital']:>9.0f}")

    print(f"\n  COMBINED DEPLOYMENT ANALYSIS:")
    print(f"    Naive combined (if independent):  ${results['combined_naive']:.4f}")
    print(f"    Coupling-adjusted combined:       ${results['combined_adjusted']:.4f}")
    print(f"    Coupling loss:                    ${results['coupling_loss']:.4f} ({results['coupling_pct']:.1f}%)")
    print(f"")
    print(f"    Trade breakdown:")
    print(f"      Coupled (both fire):  {results['coupled_trades']} trades → take better edge")
    print(f"      BTC only:             {results['btc_only_trades']} trades")
    print(f"      ETH only:             {results['eth_only_trades']} trades")
    print(f"      Total:                {results['total_trades']} trades")

    print(f"\n  CAPITAL EFFICIENCY:")
    print(f"    Total capital needed: ${results['total_capital']:.0f}")
    print(f"    Naive ROC:            {results['roc_naive']:.2f}%")
    print(f"    Adjusted ROC:         {results['roc_adjusted']:.2f}%")

    print(f"\n  VERDICT: {'✅ STACK BOTH' if results['verdict'] == 'STACK' else '❌ DEPLOY SINGLE (CFG/BTC)'}")

    if results['verdict'] == 'SINGLE':
        print(f"    The 98.5% coupling means stacking adds only {results['coupling_pct']:.1f}% more PnL")
        print(f"    but requires {results['total_capital']:.0f} vs {CFG_BTC['capital_required']:.0f} capital.")
        print(f"    CFG/BTC alone gives ${results['cfg_btc']['forward_net']:.4f} on ${CFG_BTC['capital_required']:.0f}.")
        print(f"    Combined gives ${results['combined_adjusted']:.4f} on ${results['total_capital']:.0f}.")
        print(f"    The marginal gain of adding CFG/ETH: ${results['combined_adjusted'] - results['cfg_btc']['forward_net']:.4f}")
        print(f"    for ${CFG_ETH['capital_required']:.0f} additional capital.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CFG Sleeve Combined Portfolio Simulator")
    parser.add_argument("--correlation", type=float, default=COUPLING_FRACTION,
                       help=f"Signal correlation (default: {COUPLING_FRACTION})")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    results = simulate_combined(args.correlation)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_results(results)

    # Save results
    output = REPORTS / "cfg_combined_simulation.json"
    REPORTS.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved: {output}")


if __name__ == "__main__":
    main()
