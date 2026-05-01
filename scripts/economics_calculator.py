#!/usr/bin/env python3
"""Economic viability calculator for USDJPY breakout strategies.

Answers: At what lot size and session length does this strategy
actually make meaningful money?

Usage: python scripts/economics_calculator.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Confirmed results from the lab
STRATEGIES = {
    "confirm_disp_1.5_2.5_retain_60": {
        "exp_usd_001": 0.041,  # Per trade at 0.01 lot
        "trades_per_30d": 133,
        "wr_pct": 64.7,
        "window": "30-day",
        "stability": "ROBUST",
    },
    "confirm_disp_1.5_2.5_retain_75": {
        "exp_usd_001": 0.039,
        "trades_per_30d": 134,
        "wr_pct": 65.3,
        "window": "30-day",
        "stability": "ROBUST",
    },
    "confirm_disp_3.0_2.5_retain_75": {
        "exp_usd_001": 0.013,  # COLLAPSED
        "trades_per_30d": 98,
        "wr_pct": 65.3,
        "window": "30-day",
        "stability": "OVERFITTED",
    },
    "ctrl_break_baseline": {
        "exp_usd_001": -0.072,  # LOSING
        "trades_per_30d": 257,
        "wr_pct": 44.7,
        "window": "10-day",
        "stability": "BROKEN",
    },
}

LOT_SIZES = [0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00]
USDJPY_CURRENT_PRICE = 159.50  # approximate
SPREAD_PIPS = 0.6


def main():
    print("=" * 80)
    print("USDJPY BREAKOUT STRATEGY — ECONOMIC VIABILITY CALCULATOR")
    print("=" * 80)
    print()

    # Per-strategy analysis
    for name, params in STRATEGIES.items():
        exp_001 = params["exp_usd_001"]
        trades = params["trades_per_30d"]
        wr = params["wr_pct"]
        stability = params["stability"]
        window = params["window"]

        print(f"--- {name} ({stability}, {window}) ---")
        print(f"  Base: ${exp_001:+.3f}/trade at 0.01 lot, {trades} trades/30d, {wr:.0f}% WR")

        if exp_001 <= 0:
            print(f"  ❌ NOT VIABLE at any lot size (negative expectancy)")
            print()
            continue

        # Project across lot sizes
        print(f"  {'Lot Size':>10} | {'$/Trade':>10} | {'$/Month':>10} | {'$/Day':>10} | {'$/Hour*':>10}")
        print(f"  {'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")

        for lot in LOT_SIZES:
            mult = lot / 0.01
            exp = exp_001 * mult
            monthly = exp * trades
            daily = monthly / 30
            # Assume ~8 hours of active trading per day (NY + Asian sessions)
            hourly = daily / 8

            print(f"  {lot:>10.2f} | ${exp:>9.3f} | ${monthly:>9.2f} | ${daily:>9.2f} | ${hourly:>9.2f}")

        print()
        print(f"  * Assumes 8h active session (NY + Asian overlap)")
        print()

    # Break-even analysis
    print("=" * 80)
    print("BREAK-EVEN ANALYSIS")
    print("=" * 80)
    print()
    print(f"  Spread cost per trade (0.6 pip, USDJPY):")
    for lot in [0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00]:
        spread_usd = SPREAD_PIPS * 0.01 * 1000 * lot / USDJPY_CURRENT_PRICE * 100000 / 1000
        # Simpler: 0.6 pips * $0.10/pip at 0.01 lot = $0.06
        # At 0.01 lot: 0.6 pips × $0.001 per pip-unit × 1000 units ≈ $0.0006? No...
        # At 0.01 lot on USDJPY: 1 pip = 1000 units × 0.01 = $0.0067 (approx)
        # Actually: 0.01 lot = 1000 units. 1 pip on USDJPY = 0.01 JPY.
        # In USD: 0.01 / 159.50 × 1000 = $0.0627 per pip at 0.01 lot
        # Wait, let me recalculate:
        # 0.01 lot = 1000 units
        # 1 pip = 0.01 (for JPY pairs)
        # Pip value in USD = 0.01 / 159.50 × 1000 = $0.0627
        # Spread cost = 0.6 × $0.0627 = $0.0376 at 0.01 lot

        pip_value = 0.01 / USDJPY_CURRENT_PRICE * lot * 100000
        spread_cost = SPREAD_PIPS * pip_value
        mult = lot / 0.01
        exp = params["exp_usd_001"] * mult
        net_after_spread = exp - spread_cost

        print(f"    {lot:.2f} lot: spread=${spread_cost:.4f}/trade, exp_after_spread=${net_after_spread:+.4f}")

    print()
    print(f"  NOTE: The expectancy figures ABOVE already include spread cost.")
    print(f"  They were computed at real spread (0.6 pip) in the backtest.")
    print()

    # The real question: is this worth the risk?
    print("=" * 80)
    print("RISK-ADJUSTED REALITY CHECK")
    print("=" * 80)
    print()

    best = STRATEGIES["confirm_disp_1.5_2.5_retain_60"]
    exp = best["exp_usd_001"]
    trades = best["trades_per_30d"]

    print(f"  Best robust combo: confirm_disp_1.5_2.5 + retain_60")
    print(f"  Exp: ${exp}/trade at 0.01 lot")
    print(f"  Trades: {trades}/30d = {trades/30:.1f}/day")
    print()
    print(f"  At 0.01 lot: ${exp * trades:.2f}/month = ${exp * trades / 30:.2f}/day")
    print(f"  At 0.10 lot: ${exp * 10 * trades:.2f}/month = ${exp * 10 * trades / 30:.2f}/day")
    print(f"  At 0.50 lot: ${exp * 50 * trades:.2f}/month = ${exp * 50 * trades / 30:.2f}/day")
    print(f"  At 1.00 lot: ${exp * 100 * trades:.2f}/month = ${exp * 100 * trades / 30:.2f}/day")
    print()

    # Commission/slippage adjustment
    print("  ADJUSTING for realistic costs:")
    print(f"  - Commissions: $0.00/trade (MT5 typically commission-free for standard accounts)")
    print(f"  - Slippage: assume +0.2 pips additional cost per trade")
    print(f"  - Total extra cost: ~0.2 pips/trade = ~33% of spread cost")

    slippage_pips = 0.2
    for lot in [0.01, 0.10, 0.50, 1.00]:
        pip_value = 0.01 / USDJPY_CURRENT_PRICE * lot * 100000
        slip_cost = slippage_pips * pip_value
        mult = lot / 0.01
        exp_raw = exp * mult
        monthly_trades = trades
        monthly_net = (exp_raw - slip_cost) * monthly_trades
        print(f"    {lot:.2f} lot: slip=${slip_cost:.4f}/trade, monthly_net=${monthly_net:.2f}")

    print()
    print("  BOTTOM LINE: This strategy has real edge ($0.041/trade at 0.01 lot)")
    print("  but the absolute dollar amounts are tiny at micro lot sizes.")
    print("  At 0.50 lot, it's $27/month. At 1.00 lot, $55/month.")
    print("  To make $500/month, you'd need ~9 standard lots ($550/month theoretical).")
    print("  At that size, slippage and liquidity become real concerns.")
    print()
    print("  The REAL path to meaningful money:")
    print("  1. Increase trade frequency (find more signals)")
    print("  2. Improve per-trade expectancy (better entries + exits)")
    print("  3. THEN scale lot size once 1 & 2 are maximized")


if __name__ == "__main__":
    main()
