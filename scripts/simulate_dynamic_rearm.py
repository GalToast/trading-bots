"""Dynamic Rearm Below Anchor — Simulation Prototype

Simulates what WOULD have happened if dynamic rearm was active during the
BTC rally. Tests the hypothesis that injecting SELL rearm tokens when
price moves N steps beyond the highest open SELL captures missed alpha.

Usage: python scripts/simulate_dynamic_rearm.py
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def load_state(path):
    with open(path) as f:
        return json.load(f)

def simulate(m5_state, exc2_state, dynamic_rearm_steps=3):
    """Simulate dynamic rearm for M5 Warp and exc2_tight."""

    m5 = m5_state["symbols"]["BTCUSD"]
    exc2 = exc2_state["symbols"]["BTCUSD"]

    # Current BTC price (approximate from recent triggers)
    btc_price = 74561.0

    # M5 Warp analysis
    m5_anchor = m5["anchor"]
    m5_sells = [t for t in m5["open_tickets"] if t["direction"] == "SELL"]
    m5_highest_sell = max(t["entry_price"] for t in m5_sells) if m5_sells else m5_anchor
    m5_step = m5.get("base_step_sell_px", 100.0)
    m5_rearm_tokens = m5.get("rearm_tokens", [])

    # exc2_tight analysis
    exc2_anchor = exc2["anchor"]
    exc2_sells = [t for t in exc2["open_tickets"] if t["direction"] == "SELL"]
    exc2_highest_sell = max(t["entry_price"] for t in exc2_sells) if exc2_sells else exc2_anchor
    exc2_step = exc2.get("base_step_sell_px", 45.0)
    exc2_rearm_tokens = exc2.get("rearm_tokens", [])

    print("="*60)
    print("  DYNAMIC REARM BELOW ANCHOR — SIMULATION")
    print("="*60)

    print(f"\n  BTC Price: ~${btc_price:,.0f}")

    # M5 Warp
    print(f"\n  M5 Warp (step=${m5_step:.0f}):")
    print(f"    Anchor: ${m5_anchor:,.2f}")
    print(f"    Highest SELL: ${m5_highest_sell:,.2f}")
    print(f"    Existing rearm tokens: {len(m5_rearm_tokens)}")
    print(f"    Price beyond highest SELL: ${btc_price - m5_highest_sell:,.0f} ({(btc_price - m5_highest_sell)/m5_step:.0f} steps)")

    # How many dynamic rearm tokens would have been injected?
    m5_steps_beyond = int((btc_price - m5_highest_sell) / m5_step)
    m5_dynamic_tokens = max(0, m5_steps_beyond - dynamic_rearm_steps)
    m5_dynamic_levels = []
    for i in range(m5_dynamic_tokens):
        level = m5_highest_sell + (dynamic_rearm_steps + i + 1) * m5_step
        if level < m5_anchor:  # Only below anchor
            m5_dynamic_levels.append(level)

    print(f"    Dynamic tokens that would be injected: {len(m5_dynamic_levels)}")
    for level in m5_dynamic_levels:
        print(f"      SELL @ ${level:,.2f}")

    # Alpha capture estimate
    if m5_dynamic_levels:
        avg_entry = sum(m5_dynamic_levels) / len(m5_dynamic_levels)
        mean_rev_target = m5_anchor  # Price reverts to anchor
        alpha_per_pos = (avg_entry - mean_rev_target) * 0.01 * 100
        total_alpha = alpha_per_pos * len(m5_dynamic_levels)
        print(f"    Avg entry: ${avg_entry:,.2f}")
        print(f"    Est alpha on reversion to anchor: ${total_alpha:,.2f}")

        # Risk analysis
        btc_76k = 76000
        risk_per_pos = (btc_76k - avg_entry) * 0.01 * 100
        total_risk = risk_per_pos * len(m5_dynamic_levels)
        print(f"    Risk if BTC -> $76,000: ${total_risk:,.2f}")
        if total_alpha > 0:
            print(f"    Reward/Risk: {total_alpha/total_risk:.1f}x")

    # exc2_tight
    print(f"\n  BTC exc2 Tight (step=${exc2_step:.0f}):")
    print(f"    Anchor: ${exc2_anchor:,.2f}")
    print(f"    Highest SELL: ${exc2_highest_sell if exc2_sells else 'N/A (flat)'}")
    print(f"    Existing rearm tokens: {len(exc2_rearm_tokens)}")
    print(f"    Open SELLs: {len(exc2_sells)}")

    if not exc2_sells:
        print(f"    LANE IS FLAT — dynamic rearm can't help (no highest SELL to measure from)")
        print(f"    The lane needs initial SELL tokens to begin the lattice")
        print(f"    Fix: inject initial SELL token at next_sell_level when flat + rally")

        # What if we injected a token at next_sell_level?
        next_sell = exc2.get("next_sell_level", exc2_anchor + exc2_step)
        if btc_price > next_sell:
            print(f"\n    Simulated: Inject SELL token at ${next_sell:,.2f}")
            alpha = (next_sell - exc2_anchor) * 0.01 * 100
            print(f"    Alpha on reversion: ${alpha:,.2f}")
    else:
        exc2_steps_beyond = int((btc_price - exc2_highest_sell) / exc2_step)
        exc2_dynamic_tokens = max(0, exc2_steps_beyond - dynamic_rearm_steps)
        print(f"    Price beyond highest SELL: {exc2_steps_beyond} steps")
        print(f"    Dynamic tokens: {exc2_dynamic_tokens}")

    print(f"\n  {'='*60}")
    print(f"  RECOMMENDATION")
    print(f"  {'='*60}")
    print()
    print(f"  For M5 Warp: Dynamic rearm below anchor captures ${total_alpha:,.2f}")
    print(f"    with ${total_risk:,.2f} worst-case risk ({total_alpha/total_risk:.1f}x R/R)")
    print()
    print(f"  For exc2_tight: Lane is flat, needs initial SELL token injection")
    print(f"    when flat + price above next_sell_level")
    print()
    print(f"  Implementation:")
    print(f"    1. In _update_token_arming(), check if price moved N steps")
    print(f"       beyond highest open position (same direction, below anchor)")
    print(f"    2. If yes, inject new TickRearmToken at next level")
    print(f"    3. Only inject up to max_open_per_side limit")
    print(f"    4. New tokens start un-armed, must pass excursion check")

    # Write report
    report = {
        "btc_price": btc_price,
        "m5_warp": {
            "anchor": m5_anchor,
            "highest_sell": m5_highest_sell,
            "step": m5_step,
            "existing_tokens": len(m5_rearm_tokens),
            "dynamic_tokens_simulated": len(m5_dynamic_levels),
            "dynamic_levels": m5_dynamic_levels,
            "est_alpha_on_reversion": total_alpha if m5_dynamic_levels else 0,
            "worst_case_risk_76k": total_risk if m5_dynamic_levels else 0,
        },
        "exc2_tight": {
            "anchor": exc2_anchor,
            "open_sells": len(exc2_sells),
            "existing_tokens": len(exc2_rearm_tokens),
            "flat": len(exc2_sells) == 0,
        },
        "recommendation": "Implement dynamic rearm below anchor for M5 Warp. "
                          "For exc2_tight, inject initial token when flat + rally.",
    }

    report_path = REPO / "reports" / "dynamic_rearm_simulation.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report: {report_path}")


if __name__ == "__main__":
    m5_state = load_state(REPO / "reports/penetration_lattice_live_btcusd_m5_warp_state.json")
    exc2_state = load_state(REPO / "reports/penetration_lattice_shadow_btcusd_exc2_tight_state.json")
    simulate(m5_state, exc2_state, dynamic_rearm_steps=3)
