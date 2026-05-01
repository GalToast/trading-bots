#!/usr/bin/env python3
"""
Capital requirements analysis — minimum viable capital per symbol.
Calculates floating drawdown, gap risk, margin requirements, and safe minimum.
"""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_hybrid_apex import RawConfig, simulate_raw_close2
from penetration_lattice_lab_v2 import ROOT, load_bars, pip_size_for
from penetration_lattice_lab_v3_bounded import Config as BoundedConfig
from penetration_lattice_lab_v3_bounded import simulate_symbol as simulate_v3


SYMBOLS_CONFIG = {
    "GBPUSD": {"mode": "raw", "step": 1.75, "cap": 20},
    "EURUSD": {"mode": "raw", "step": 2.50, "cap": 20},
    "NZDUSD": {"mode": "raw", "step": 1.50, "cap": 12},
    "USDJPY": {"mode": "v3", "step": 0.50, "cap": 20},
    "USDCHF": {"mode": "v3", "step": 0.50, "cap": 20},
}

GAP_PIPS = {
    "GBPUSD": 30,
    "EURUSD": 25,
    "NZDUSD": 20,
    "USDJPY": 30,
    "USDCHF": 20,
}


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    results = {}
    total_floating_worst = 0
    total_max_open = 0
    total_margin = 0

    print("=" * 80)
    print("CAPITAL REQUIREMENTS ANALYSIS — Full 5-Symbol Basket @ 0.01 lot")
    print("=" * 80)

    for symbol, cfg in SYMBOLS_CONFIG.items():
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, 60)
        if not bars or info is None:
            continue

        pip = pip_size_for(info)
        point = float(info.point)

        if cfg["mode"] == "raw":
            r = simulate_raw_close2(symbol, bars, info, RawConfig(cfg["step"], cfg["cap"], "two_level"))
        else:
            vc = type("V3", (), {
                "step_pips": cfg["step"], "max_open_per_side": cfg["cap"],
                "max_floating_loss_usd": -10.0, "vwap_lookback": 20,
                "regime_lookback_bars": 60, "max_range_pips": 24.0,
                "breakout_buffer_pips": 5.0, "max_lattice_window_bars": 240,
                "cooldown_bars": 60,
                "adaptive_step_threshold_1": 10, "adaptive_step_threshold_2": 20,
                "adaptive_step_multiplier_1": 1.5, "adaptive_step_multiplier_2": 2.0,
            })()
            r = simulate_v3(symbol, bars, info, vc)

        # Calculate gap risk: if all open positions get gapped
        max_open = r.get("max_open_total", 0)
        gap_pips = GAP_PIPS.get(symbol, 25)
        gap_loss_per_pip = gap_pips * (r.get("max_open_total", 0) * pip_size_for(info) / pip_size_for(info) * 0.10)  # rough $0.10/pip/lot for micro

        # Pip value at 0.01 lot: calculate from tick value
        tick_value = float(info.trade_tick_value or 0.0)
        tick_size = float(info.trade_tick_size or pip)
        # At 0.01 lot: 0.01 * contract_size units, each pip move = tick_value * (pip / tick_size) * 0.01
        pip_value_usd = tick_value * (pip / tick_size) * 0.01 if tick_size > 0 else 0.10
        # Fallback: MT5 calc
        if pip_value_usd < 0.001:
            gross_1pip = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 0.01, float(info.bid or 1.0), float(info.bid or 1.0) + pip)
            pip_value_usd = float(gross_1pip) if gross_1pip else 0.10
        gap_loss = max_open * gap_pips * pip_value_usd

        # Margin: notional at 0.01 lot
        contract_size = float(info.trade_contract_size or 100000)
        leverage = 500  # typical retail
        notional = 0.01 * contract_size
        price = float(info.trade_tick_value or 1.0)
        margin = notional * price / leverage if leverage > 0 else notional

        floating_net = r.get("floating_net_usd", 0)
        worst_float = r.get("worst_floating_usd", 0)
        combined = r.get("combined_net_usd", 0)
        daily = combined / 60

        safe_capital = abs(floating_net) + gap_loss * 0.5  # 50% of gap loss as buffer
        min_capital = abs(floating_net) * 2  # 2x floating as absolute minimum
        comfortable_capital = abs(floating_net) + gap_loss  # Full gap coverage

        print(f"\n{symbol} — {cfg['mode']} step={cfg['step']} cap={cfg['cap']}")
        print(f"  Combined:   ${combined:+.2f}/60d  (${daily:+.2f}/day)")
        print(f"  Max open:   {max_open} positions")
        print(f"  Floating:   ${floating_net:+.2f} net, ${worst_float:+.2f} worst ticket")
        print(f"  Gap risk:   {gap_pips} pips × {max_open} pos × ${pip_value_usd:.3f}/pip = ~${gap_loss:.2f}")
        print(f"  Margin:     ~${margin:.2f} (500:1 leverage)")
        print(f"  ┌─ Min capital (2x floating):  ${min_capital:.2f}")
        print(f"  ├─ Safe capital (floating + 50% gap): ${safe_capital:.2f}")
        print(f"  └─ Comfortable (full gap cover): ${comfortable_capital:.2f}")

        results[symbol] = {
            "combined": combined,
            "floating_net": floating_net,
            "worst_floating": worst_float,
            "max_open": max_open,
            "gap_loss": gap_loss,
            "margin": margin,
            "min_capital": min_capital,
            "safe_capital": safe_capital,
            "comfortable_capital": comfortable_capital,
        }

        total_floating_worst += abs(floating_net)
        total_max_open += max_open
        total_margin += margin

    # Portfolio-level analysis
    print("\n" + "=" * 80)
    print("PORTFOLIO-LEVEL CAPITAL REQUIREMENTS")
    print("=" * 80)

    total_combined = sum(r["combined"] for r in results.values())
    total_daily = total_combined / 60
    total_gap_loss = sum(r["gap_loss"] for r in results.values())

    # Worst case: all floating goes bad simultaneously + gap event
    portfolio_min = total_floating_worst * 2
    portfolio_safe = total_floating_worst + total_gap_loss * 0.5
    portfolio_comfortable = total_floating_worst + total_gap_loss

    print(f"\n  Total combined:   ${total_combined:+.2f}/60d  (${total_daily:+.2f}/day)")
    print(f"  Total max open:   {total_max_open} positions")
    print(f"  Total margin:     ${total_margin:.2f} (500:1 leverage)")
    print(f"  Total floating:   ${total_floating_worst:.2f} worst-case")
    print(f"  Total gap risk:   ${total_gap_loss:.2f} (full gap event)")
    print(f"\n  ┌─ ABSOLUTE MINIMUM:   ${portfolio_min:.2f}")
    print(f"  │  (2x floating, tight — one bad wick = margin call)")
    print(f"  ├─ RECOMMENDED MIN:    ${portfolio_safe:.2f}")
    print(f"  │  (survives gap + floating, but drawdown will hurt)")
    print(f"  └─ COMFORTABLE:        ${portfolio_comfortable:.2f}")
    print(f"     (full gap coverage, drawdown absorbable, stress-free)")

    print(f"\n  At minimum (${portfolio_min:.2f}): ${total_daily:.2f}/day = {total_daily/portfolio_min*100:.1f}% daily return")
    print(f"  At comfortable (${portfolio_comfortable:.2f}): ${total_daily:.2f}/day = {total_daily/portfolio_comfortable*100:.1f}% daily return")
    print(f"  Annualized at comfortable: ${total_daily * 252:.2f}/year = {total_daily*252/portfolio_comfortable*100:.0f}% return")

    # Save CSV
    output_path = ROOT / "reports" / "capital_requirements.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "combined_60d", "daily", "max_open", "floating_net", "worst_floating", "gap_loss", "min_capital", "safe_capital", "comfortable_capital"])
        writer.writeheader()
        for sym, r in results.items():
            writer.writerow({
                "symbol": sym,
                "combined_60d": round(r["combined"], 2),
                "daily": round(r["combined"]/60, 2),
                "max_open": r["max_open"],
                "floating_net": round(r["floating_net"], 2),
                "worst_floating": round(r["worst_floating"], 2),
                "gap_loss": round(r["gap_loss"], 2),
                "min_capital": round(r["min_capital"], 2),
                "safe_capital": round(r["safe_capital"], 2),
                "comfortable_capital": round(r["comfortable_capital"], 2),
            })
    print(f"\nSaved {output_path}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
