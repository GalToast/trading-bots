#!/usr/bin/env python3
"""
NOM/BTC + CFG/ETH Combined Holdout Simulator

@codex-cfgnext is running the actual repeated combined holdout audit.
This script provides a PRELIMINARY coupling analysis to answer:
- Do NOM/BTC and CFG/ETH fire at the same time? (coupling fraction)
- If they do, does stacking add value or just duplicate exposure?
- What's the optimal capital allocation across both sleeves?

This is NOT a replacement for the actual walk-forward audit.
It's a coupling/efficiency preview to guide expectations.

Usage:
    python scripts/nombtc_cfgeth_combined.py
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# From repeated walk-forward results
NOM_BTC = {
    "name": "NOM/BTC",
    "forward_net": 0.0,  # Not yet validated - need @codex-cfgnext results
    "forward_closes": 0,
    "capital_required": 845.0,  # BTC denominator
}

CFG_ETH = {
    "name": "CFG/ETH",
    "forward_net": 0.0156,
    "forward_closes": 51,
    "capital_required": 22.0,  # ETH denominator
}

# Estimated coupling based on coin characteristics
# NOM and CFG are different coins with different price dynamics
# BTC and ETH are correlated but not perfectly
ESTIMATED_COUPLING = 0.6  # Rough estimate - actual may vary


def simulate_combined(coupling=ESTIMATED_COUPLING, nombtc_net=None):
    """Simulate combined NOM/BTC + CFG/ETH deployment."""

    if nombtc_net is None:
        # Use placeholder - actual value from walk-forward
        print("⚠️  NOM/BTC forward net not yet validated. Using placeholder $0.01")
        nombtc_net = 0.01

    nombtc_closes = max(10, int(51 * nombtc_net / max(CFG_ETH["forward_net"], 0.0001)))
    cfgeth_closes = CFG_ETH["forward_closes"]

    nombtc_avg = nombtc_net / nombtc_closes if nombtc_closes > 0 else 0
    cfgeth_avg = CFG_ETH["forward_net"] / cfgeth_closes

    # Coupling analysis
    coupled = int(min(nombtc_closes, cfgeth_closes) * coupling)
    nombtc_only = nombtc_closes - coupled
    cfgeth_only = cfgeth_closes - coupled

    # When coupled, take the better edge
    coupled_pnl = coupled * max(nombtc_avg, cfgeth_avg)
    independent_pnl = nombtc_only * nombtc_avg + cfgeth_only * cfgeth_avg
    combined = coupled_pnl + independent_pnl
    naive = nombtc_net + CFG_ETH["forward_net"]

    total_capital = NOM_BTC["capital_required"] + CFG_ETH["capital_required"]

    return {
        "coupling": coupling,
        "nombtc": {
            "name": NOM_BTC["name"],
            "forward_net": nombtc_net,
            "closes": nombtc_closes,
            "avg_per_close": nombtc_avg,
            "capital": NOM_BTC["capital_required"],
        },
        "cfgeth": {
            "name": CFG_ETH["name"],
            "forward_net": CFG_ETH["forward_net"],
            "closes": cfgeth_closes,
            "avg_per_close": cfgeth_avg,
            "capital": CFG_ETH["capital_required"],
        },
        "combined_naive": naive,
        "combined_adjusted": combined,
        "coupling_loss": naive - combined,
        "coupling_pct": (1 - combined / naive) * 100 if naive > 0 else 0,
        "coupled_trades": coupled,
        "nombtc_only": nombtc_only,
        "cfgeth_only": cfgeth_only,
        "total_trades": coupled + nombtc_only + cfgeth_only,
        "total_capital": total_capital,
        "roc_combined": combined / total_capital * 100,
        "verdict": "STACK" if combined > max(nombtc_net, CFG_ETH["forward_net"]) * 1.2 else "MAYBE",
    }


def main():
    print("=" * 80)
    print("  NOM/BTC + CFG/ETH COMBINED HOLDOUT — PRELIMINARY COUPLING ANALYSIS")
    print(f"  Estimated coupling: {ESTIMATED_COUPLING*100:.0f}%")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)

    print(f"\n  ⚠️  This is PRELIMINARY. Actual results depend on:")
    print(f"     - NOM/BTC walk-forward results (from @codex-cfgnext)")
    print(f"     - Actual signal coupling (may differ from estimate)")
    print(f"     - Forward performance on held-out data")

    results = simulate_combined()

    print(f"\n  INDIVIDUAL SLEEVES:")
    print(f"  {'Sleeve':<15} {'Fwd Net':>10} {'Closes':>7} {'Avg/Close':>10} {'Capital':>10}")
    print("-" * 80)
    for sleeve in [results["nombtc"], results["cfgeth"]]:
        print(f"  {sleeve['name']:<15} ${sleeve['forward_net']:>9.4f} {sleeve['closes']:>7d} "
              f"${sleeve['avg_per_close']:>9.4f} ${sleeve['capital']:>9.0f}")

    print(f"\n  COMBINED (at {results['coupling']*100:.0f}% estimated coupling):")
    print(f"    Naive:       ${results['combined_naive']:.4f}")
    print(f"    Adjusted:    ${results['combined_adjusted']:.4f}")
    print(f"    Loss:        ${results['coupling_loss']:.4f} ({results['coupling_pct']:.1f}%)")
    print(f"    Coupled:     {results['coupled_trades']} trades")
    print(f"    NOM-only:    {results['nombtc_only']} trades")
    print(f"    CFG-only:    {results['cfgeth_only']} trades")
    print(f"    Total:       {results['total_trades']} trades")
    print(f"    Capital:     ${results['total_capital']:.0f}")
    print(f"    ROC:         {results['roc_combined']:.4f}%")

    print(f"\n  KEY QUESTION FOR @codex-cfgnext:")
    print(f"    If NOM/BTC and CFG/ETH have <50% coupling (different coins),")
    print(f"    stacking could add meaningful diversification.")
    print(f"    If >80% coupling, deploy single sleeve.")

    output = REPORTS / "nombtc_cfgeth_combined_preview.json"
    REPORTS.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Results saved: {output}")


if __name__ == "__main__":
    main()
