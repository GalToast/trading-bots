"""Dynamic Rearm Above Highest — Tighter Step Simulation

Tests the refined approach: inject SELL rearm tokens ABOVE the highest
open SELL (not below anchor), using a tighter step size to capture
quick pullbacks with less deep reversion required.

This addresses the critical nuance from the first simulation:
- Below-anchor rearm requires deep reversion (below avg entry)
- Above-highest rearm captures quick pullbacks, needs shallow reversion

Usage: python scripts/simulate_dynamic_rearm_above_highest.py
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def load_state(path):
    with open(path) as f:
        return json.load(f)

def simulate(m5_state, steps_above=[1, 2, 3], tighter_steps=[50, 75, 100]):
    """Simulate dynamic rearm above highest SELL with various configs."""

    m5 = m5_state["symbols"]["BTCUSD"]
    btc_price = 74561.0  # Current approximate
    anchor = m5["anchor"]
    base_step = m5.get("base_step_sell_px", 100.0)
    sells = [t for t in m5["open_tickets"] if t["direction"] == "SELL"]
    highest_sell = max(t["entry_price"] for t in sells) if sells else anchor
    realized = m5["realized_net_usd"]
    realized_closes = m5["realized_closes"]

    print("="*70)
    print("  DYNAMIC REARM ABOVE HIGHEST — TIGHTER STEP SIMULATION")
    print("="*70)

    print(f"\n  BTC Price: ~${btc_price:,.0f}")
    print(f"  Anchor: ${anchor:,.2f}")
    print(f"  Highest SELL: ${highest_sell:,.2f}")
    print(f"  Base step: ${base_step:.0f}")
    print(f"  Current realized: ${realized:,.2f} ({realized_closes} closes)")

    print(f"\n  {'Steps Above':<14} {'Tighter Step':<13} {'Inject@':<11} "
          f"{'Rev to $74K':<13} {'Rev to $73.5K':<14} {'Rev to $73K':<12} {'Risk $76K':<12}")
    print(f"  {'-'*14} {'-'*13} {'-'*11} {'-'*13} {'-'*14} {'-'*12} {'-'*12}")

    results = []
    for M in steps_above:
        for step in tighter_steps:
            entry = highest_sell + M * step
            if entry > btc_price:
                # Can't inject above current price
                continue
            if entry > anchor:
                # Don't inject above anchor (too risky)
                continue

            # Alpha on various reversion depths
            rev_74k = (entry - 74000) * 0.01 * 100
            rev_73_5k = (entry - 73500) * 0.01 * 100
            rev_73k = (entry - 73000) * 0.01 * 100
            risk_76k = (76000 - entry) * 0.01 * 100

            results.append({
                "M": M,
                "step": step,
                "entry": entry,
                "rev_74k": rev_74k,
                "rev_73_5k": rev_73_5k,
                "rev_73k": rev_73k,
                "risk_76k": risk_76k,
                "rr_74k": rev_74k / risk_76k if risk_76k > 0 else 0,
                "rr_73k": rev_73k / risk_76k if risk_76k > 0 else 0,
            })

            print(f"  {M:<14} ${step:<12.0f} ${entry:<10,.2f} "
                  f"${rev_74k:<12.2f} ${rev_73_5k:<13.2f} ${rev_73k:<11.2f} ${risk_76k:<11.2f}")

    # Find the optimal configuration
    print(f"\n  OPTIMAL CONFIGURATIONS:")
    print(f"  {'Scenario':<20} {'Steps':<8} {'Step $':<8} {'Entry':<12} {'Alpha':<10} {'R/R':<8}")
    print(f"  {'-'*20} {'-'*8} {'-'*8} {'-'*12} {'-'*10} {'-'*8}")

    # Best for shallow reversion ($74K)
    best_74k = max(results, key=lambda x: x["rr_74k"])
    print(f"  {'Shallow ($74K)':<20} {best_74k['M']:<8} ${best_74k['step']:<7.0f} "
          f"${best_74k['entry']:<11,.2f} ${best_74k['rev_74k']:<9.2f} {best_74k['rr_74k']:<7.1f}x")

    # Best for medium reversion ($73.5K)
    best_73_5k = max(results, key=lambda x: x["rev_73_5k"])
    print(f"  {'Medium ($73.5K)':<20} {best_73_5k['M']:<8} ${best_73_5k['step']:<7.0f} "
          f"${best_73_5k['entry']:<11,.2f} ${best_73_5k['rev_73_5k']:<9.2f} "
          f"{best_73_5k['rev_73_5k']/best_73_5k['risk_76k']:<7.1f}x")

    # Best for deep reversion ($73K)
    best_73k = max(results, key=lambda x: x["rr_73k"])
    print(f"  {'Deep ($73K)':<20} {best_73k['M']:<8} ${best_73k['step']:<7.0f} "
          f"${best_73k['entry']:<11,.2f} ${best_73k['rev_73k']:<9.2f} {best_73k['rr_73k']:<7.1f}x")

    # Comparison with below-anchor approach
    print(f"\n  COMPARISON:")
    below_anchor_avg = (highest_sell + anchor) / 2
    below_anchor_alpha_73k = (below_anchor_avg - 73000) * 0.01 * 100
    below_anchor_risk = (76000 - below_anchor_avg) * 0.01 * 100

    print(f"  Below-anchor (naive):")
    print(f"    Avg entry: ${below_anchor_avg:,.2f}")
    print(f"    Alpha at $73K: ${below_anchor_alpha_73k:,.2f}")
    print(f"    Risk at $76K: ${below_anchor_risk:,.2f}")
    print(f"    R/R: {below_anchor_alpha_73k/below_anchor_risk:.1f}x")

    print(f"\n  Above-highest (refined, optimal for shallow):")
    print(f"    Entry: ${best_74k['entry']:,.2f}")
    print(f"    Alpha at $73K: ${best_74k['rev_73k']:,.2f}")
    print(f"    Risk at $76K: ${best_74k['risk_76k']:,.2f}")
    print(f"    R/R: {best_74k['rr_73k']:.1f}x")

    # Write report
    report = {
        "btc_price": btc_price,
        "anchor": anchor,
        "highest_sell": highest_sell,
        "base_step": base_step,
        "configurations_tested": len(results),
        "optimal_shallow": best_74k,
        "optimal_medium": best_73_5k,
        "optimal_deep": best_73k,
        "below_anchor_naive": {
            "avg_entry": below_anchor_avg,
            "alpha_73k": below_anchor_alpha_73k,
            "risk_76k": below_anchor_risk,
            "rr": below_anchor_alpha_73k / below_anchor_risk,
        },
        "recommendation": f"Inject SELL tokens at highest SELL + {best_74k['M']}*${best_74k['step']:.0f} "
                          f"= ${best_74k['entry']:,.2f}. "
                          f"Captures shallow reversion with {best_74k['rr_74k']:.1f}x R/R.",
    }

    report_path = REPO / "reports" / "dynamic_rearm_above_highest_simulation.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report: {report_path}")


if __name__ == "__main__":
    m5_state = load_state(REPO / "reports/penetration_lattice_live_btcusd_m5_warp_state.json")
    simulate(m5_state, steps_above=[1, 2, 3, 4, 5], tighter_steps=[30, 50, 75, 100])
