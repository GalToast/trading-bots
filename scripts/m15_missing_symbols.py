#!/usr/bin/env python3
"""
Missing Symbols M15 Sweep — DOGEUSD, ADAUSD, DOTUSD
Testing step sizes relative to each symbol's price to complete the $3M portfolio.
"""
from __future__ import annotations

import argparse
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


def simulate_engine(symbol: str, bars: list[dict], step: float, max_open: int, alpha: float, gap: int, mom: bool) -> dict:
    from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state
    cfg = {
        "step": step,
        "max_open_per_side": max_open,
        "close_alpha": alpha,
        "close_gap": gap,
        "momentum_gate": mom,
        "rearm_cooldown_bars": 0,
        "rearm_excursion_levels": 0,  # Immediate re-entry (matches qwen-main's engine)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "m15_missing_symbols.csv"))
    args = parser.parse_args()

    if not mt5.initialize():
        print("MT5 initialize failed")
        return 1

    try:
        # DOGEUSD: ~$0.10, test $0.001, $0.002, $0.005
        # ADAUSD: ~$0.25, test $0.005, $0.01, $0.02
        # DOTUSD: ~$2.00, test $0.05, $0.10, $0.20
        tests = {
            "DOGEUSD": [
                (0.001, 80, True), (0.002, 80, True), (0.005, 80, True),
                (0.001, 80, False), (0.002, 80, False), (0.005, 80, False),
            ],
            "ADAUSD": [
                (0.005, 80, True), (0.01, 80, True), (0.02, 80, True),
                (0.005, 80, False), (0.01, 80, False), (0.02, 80, False),
            ],
            "DOTUSD": [
                (0.05, 80, True), (0.10, 80, True), (0.20, 80, True),
                (0.05, 80, False), (0.10, 80, False), (0.20, 80, False),
            ],
        }

        all_rows = []

        for symbol, configs in tests.items():
            info = mt5.symbol_info(symbol)
            if info is None:
                print(f'{symbol}: NOT AVAILABLE')
                continue

            bars = load_m15_bars(symbol, args.days)
            if not bars:
                print(f'{symbol}: NO M15 BARS')
                continue

            print(f'\n{"="*100}')
            print(f'  M15 SWEEP — {symbol}, {args.days}d ({len(bars)} bars)')
            print(f'{"="*100}\n')

            for step, max_open, mom in configs:
                print(f'  Testing step=${step}, max_open={max_open}, mom={mom}...')
                r = simulate_engine(symbol, bars, step=step, max_open=max_open, alpha=1.0, gap=1, mom=mom)
                print(f'    → ${r["combined_net_usd"]:,.2f}, {r["realized_closes"]} closes, {r["rearm_opens"]} rearm, max_seen={r["max_open_total"]}')

                all_rows.append({
                    "symbol": symbol,
                    "step": step,
                    "max_open": max_open,
                    "momentum_gate": mom,
                    "realized_usd": round(r["combined_net_usd"], 2),
                    "closes": r["realized_closes"],
                    "rearm_opens": r["rearm_opens"],
                    "max_seen": r["max_open_total"],
                })

        all_rows.sort(key=lambda x: x["realized_usd"], reverse=True)

        print(f'\n{"="*100}')
        print(f'  ALL RESULTS (sorted)')
        print(f'{"="*100}')
        print(f'  {"Symbol":<10} {"Step":>10} {"Mom":>5} {"Realized":>14} {"Closes":>8} {"Rearm":>8} {"MaxSeen":>8}')
        print(f'  {"─"*90}')
        for r in all_rows:
            mom_str = "ON" if r["momentum_gate"] else "OFF"
            print(f'  {r["symbol"]:<10} ${r["step"]:>9,.4f} {mom_str:>5} ${r["realized_usd"]:>13,.2f} {r["closes"]:>8} {r["rearm_opens"]:>8} {r["max_seen"]:>8}')

        out_path = Path(args.output_csv)
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
    raise SystemExit(main())
