#!/usr/bin/env python3
"""
FX M15 Deep Optimization — Smaller Steps for GBPUSD, EURUSD, NZDUSD
Testing steps from $0.0001 to $0.001 to find the true FX optimum.
"""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent


def load_m15_bars(symbol: str, days: int) -> list[dict]:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 24 * 4 * days)
    if rates is None or len(rates) == 0:
        return []
    return [
        {"time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "tick_volume": int(r[5])}
        for r in rates
    ]


def simulate_engine(symbol: str, bars: list[dict], step: float, max_open: int, mom: bool) -> dict:
    from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
    cfg = {
        "step": step,
        "max_open_per_side": max_open,
        "close_alpha": 1.0,
        "close_gap": 1,
        "momentum_gate": mom,
        "rearm_cooldown_bars": 0,
        "rearm_excursion_levels": 0,
        "timeframe": "M15",
    }
    state = init_symbol_state(symbol, cfg, bars)
    state = process_symbol(symbol, cfg, bars, state)
    return {
        "combined_net_usd": state.realized_net_usd,
        "realized_closes": state.realized_closes,
        "rearm_opens": state.rearm_opens,
        "max_open_total": state.max_open_total,
    }


def main():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return 1

    try:
        symbols = {
            "GBPUSD": [0.0001, 0.0002, 0.0003, 0.0005, 0.001],
            "EURUSD": [0.0001, 0.0002, 0.0003, 0.0005, 0.001],
            "NZDUSD": [0.0001, 0.0002, 0.0003, 0.0005, 0.001],
        }

        all_rows = []

        for symbol, steps in symbols.items():
            info = mt5.symbol_info(symbol)
            if info is None:
                print(f'{symbol}: NOT AVAILABLE')
                continue

            bars = load_m15_bars(symbol, 90)
            if not bars:
                print(f'{symbol}: NO M15 BARS')
                continue

            print(f'\n{"="*100}')
            print(f'  FX M15 DEEP OPT — {symbol}, 90d ({len(bars)} bars)')
            print(f'{"="*100}\n')

            for step in steps:
                for mom in [True, False]:
                    print(f'  Testing step=${step:.5f}, mom={mom}...')
                    r = simulate_engine(symbol, bars, step=step, max_open=80, mom=mom)
                    print(f'    → ${r["combined_net_usd"]:,.2f}, {r["realized_closes"]}c, {r["rearm_opens"]}r, max={r["max_open_total"]}')

                    all_rows.append({
                        "symbol": symbol,
                        "step": step,
                        "max_open": 80,
                        "momentum_gate": mom,
                        "realized_usd": round(r["combined_net_usd"], 2),
                        "closes": r["realized_closes"],
                        "rearm_opens": r["rearm_opens"],
                        "max_seen": r["max_open_total"],
                    })

        all_rows.sort(key=lambda x: x["realized_usd"], reverse=True)

        print(f'\n{"="*100}')
        print(f'  FX M15 RESULTS (sorted)')
        print(f'{"="*100}')
        for r in all_rows:
            mom_str = "ON" if r["momentum_gate"] else "OFF"
            print(f'  {r["symbol"]:<10} step=${r["step"]:.5f} mom={mom_str:<4} → ${r["realized_usd"]:>12,.2f}  {r["closes"]:>5}c  {r["rearm_opens"]:>4}r')

        out_path = ROOT / "reports" / "fx_m15_deep_opt.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "step", "max_open", "momentum_gate", "realized_usd", "closes", "rearm_opens", "max_seen"])
            writer.writeheader()
            writer.writerows(all_rows)
        print(f'\n  Wrote {out_path}')

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
