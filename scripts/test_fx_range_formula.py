#!/usr/bin/env python3
"""
FX Range Formula Test — Does `step = 0.80 × Range` work on FX?
================================================================
The team discovered `step = 0.80 × avg_range` works on crypto M5/M15.
FX lanes run at 0.0001 (1 pip). Is that actually the optimal Range-based step?

If 0.0001 ≈ 0.80 × avg_range for FX M15 bars → THE FORMULA IS UNIVERSAL ACROSS ASSET CLASSES!

Usage:
    python scripts/test_fx_range_formula.py
"""
from pathlib import Path
import json

REPO = Path(__file__).resolve().parent.parent

# FX M15 lane state files
FX_M15_BAR_LANES = {
    "GBPUSD M15 bar": {
        "state": REPO / "reports" / "shadow_fx_m15_micro_gbpusd_bar_state.json",
        "step": 0.0001,
    },
    "EURUSD M15 bar": {
        "state": REPO / "reports" / "shadow_fx_m15_micro_eurusd_bar_state.json",
        "step": 0.0001,
    },
    "NZDUSD M15 bar": {
        "state": REPO / "reports" / "shadow_fx_m15_micro_nzdusd_bar_state.json",
        "step": 0.0001,
    },
}

# FX M15 fxmicro lanes (tick-based but M15 context)
FX_M15_FXMICRO = {
    "GBPUSD fxmicro M15": {
        "state": REPO / "reports" / "penetration_lattice_shadow_gbpusd_m15_fxmicro_state.json",
        "step": 0.0001,
    },
    "EURUSD fxmicro M15": {
        "state": REPO / "reports" / "penetration_lattice_shadow_eurusd_m15_fxmicro_state.json",
        "step": 0.0001,
    },
    "NZDUSD fxmicro M15": {
        "state": REPO / "reports" / "penetration_lattice_shadow_nzdusd_m15_fxmicro_state.json",
        "step": 0.0001,
    },
}

# Typical FX M15 bar ranges (in price units) from known market data:
# These are approximate 14-bar average ranges:
# GBPUSD M15: ~0.0012 (12 pips)
# EURUSD M15: ~0.0010 (10 pips)
# NZDUSD M15: ~0.0009 (9 pips)
FX_TYPICAL_RANGE = {
    "GBPUSD": 0.0012,
    "EURUSD": 0.0010,
    "NZDUSD": 0.0009,
}


def load_state(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def extract_metrics(state, label):
    """Extract realized closes, net USD, and opens from state."""
    if state is None:
        return {"label": label, "closes": 0, "net_usd": 0, "opens": 0, "status": "MISSING"}

    realized_closes = 0
    realized_net = 0
    total_opens = 0

    symbols = state.get("symbols", {})
    for sym_name, sym_data in symbols.items():
        realized_closes += sym_data.get("realized_closes", 0)
        realized_net += sym_data.get("realized_net_usd", 0)
        total_opens += len(sym_data.get("open_tickets", []))

    return {
        "label": label,
        "closes": realized_closes,
        "net_usd": realized_net,
        "opens": total_opens,
        "status": "OK",
    }


def main():
    print("=" * 80)
    print("FX RANGE FORMULA TEST — Does step = 0.80 × Range work on FX?")
    print("=" * 80)
    print()

    # Test M15 bar lanes
    print("FX M15 BAR LANES:")
    print("-" * 80)
    print(f"{'Lane':<25} {'Step':>8} {'Range':>8} {'Coeff':>7} {'Opt(0.8x)':>10} {'Diff':>7} {'Closes':>7} {'Net $':>10}")
    print("-" * 80)

    for label, config in {**FX_M15_BAR_LANES, **FX_M15_FXMICRO}.items():
        state = load_state(config["state"])
        metrics = extract_metrics(state, label)

        # Determine symbol from label
        symbol = None
        for sym in FX_TYPICAL_RANGE:
            if sym.lower() in label.lower():
                symbol = sym
                break

        step = config["step"]
        avg_range = FX_TYPICAL_RANGE.get(symbol, 0)
        range_coeff = step / avg_range if avg_range > 0 else 0
        optimal = avg_range * 0.80 if avg_range > 0 else 0
        diff_pct = ((step - optimal) / optimal * 100) if optimal > 0 else 0

        status = "✅" if metrics["net_usd"] > 0 else "❌" if metrics["closes"] > 0 else "⏳"
        print(f"{label:<25} {step:>8.4f} {avg_range:>8.4f} {range_coeff:>7.2f}x "
              f"${optimal:>9.4f} {diff_pct:>+6.0f}% {metrics['closes']:>7} "
              f"${metrics['net_usd']:>9.2f} {status}")

    print()

    # Also check the backtest optimization results
    fx_deep = REPO / "reports" / "fx_m15_deep_opt.csv"
    if fx_deep.exists():
        print(f"FX M15 Deep Optimization (from {fx_deep.name}):")
        print("-" * 80)
        lines = fx_deep.read_text().strip().split('\n')
        for line in lines[:5]:
            print(f"  {line}")
        if len(lines) > 5:
            print(f"  ... ({len(lines)-5} more rows)")
        print()

    # Check validated FX claims
    fx_validated = REPO / "reports" / "validated_fx_m15_micro_claims.csv"
    if fx_validated.exists():
        print(f"Validated FX M15 Micro Claims (from {fx_validated.name}):")
        print("-" * 80)
        lines = fx_validated.read_text().strip().split('\n')
        for line in lines[:5]:
            print(f"  {line}")
        print()

    # Key analysis
    print("=" * 80)
    print("KEY QUESTION: Is 0.0001 close to 0.80 × avg_range for FX M15?")
    print("=" * 80)
    print()

    for symbol, avg_range in FX_TYPICAL_RANGE.items():
        step = 0.0001
        optimal = avg_range * 0.80
        diff_pct = ((step - optimal) / optimal * 100)
        coeff = step / avg_range
        verdict = "CLOSE" if abs(diff_pct) < 30 else "OFF"
        print(f"{symbol}: step=0.0001, range={avg_range:.4f}, optimal={optimal:.4f}, "
              f"coeff={coeff:.2f}x, diff={diff_pct:+.0f}% → {verdict}")

    print()
    print("CONCLUSION:")
    print("-" * 80)
    print()

    # Compute average coefficient
    coeffs = [0.0001 / r for r in FX_TYPICAL_RANGE.values()]
    avg_coeff = sum(coeffs) / len(coeffs)

    print(f"FX average coefficient: {avg_coeff:.2f}x Range")
    print(f"Crypto M5 average coefficient: 0.80x Range")
    print()

    if abs(avg_coeff - 0.80) < 0.20:
        print("🎉 THE FX COEFFICIENT IS CLOSE TO THE CRYPTO COEFFICIENT!")
        print("This means `step ≈ 0.80 × Range` might be UNIVERSAL across asset classes!")
    else:
        print(f"⚠️ FX uses {avg_coeff:.2f}x Range vs crypto's 0.80x.")
        print(f"The formula is similar but NOT identical. FX needs a tighter coefficient.")
        print()
        print("POSSIBLE REASONS:")
        print("  1. FX has much tighter spreads → can use tighter steps")
        print("  2. FX M15 bars are more uniform (no gaps) → less noise")
        print("  3. FX mean-reversion is faster → smaller steps catch more swings")

    print()
    print("RECOMMENDATION:")
    print("-" * 80)
    print()
    print("1. Pull ACTUAL FX M15 Range data from MT5 (not estimates)")
    print("2. Run the same ATR + Range analysis on FX that we did on crypto")
    print("3. Test if the FX-optimal coefficient matches crypto's 0.80x")
    print("4. If yes → THE RANGE FORMULA IS UNIVERSAL ACROSS ALL MARKETS")
    print("5. If no → the formula has an asset-class-specific adjustment factor")
    print()
    print("This could be the BIGGEST discovery yet: a UNIVERSAL step formula")
    print("that works on crypto AND FX, any symbol, any timeframe. 🌊🧠")


if __name__ == "__main__":
    main()
