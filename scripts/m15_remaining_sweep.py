#!/usr/bin/env python3
"""
Remaining M15 Step Optimization — SOLUSD, XRPUSD, FX symbols
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
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


def simulate_engine(symbol: str, bars: list[dict], info, step: float, max_open: int, alpha: float = 1.0, gap: int = 1, momentum_gate: bool = True) -> dict:
    from live_penetration_lattice_unified_shadow import process_symbol, init_symbol_state

    cfg = {
        "step": step,
        "max_open_per_side": max_open,
        "close_alpha": alpha,
        "close_gap": gap,
        "momentum_gate": momentum_gate,
        "rearm_cooldown_bars": 0,
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
    parser = argparse.ArgumentParser(description="M15 Step Optimization — Remaining Symbols")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "m15_remaining_sweep.csv"))
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    try:
        # SOLUSD: ~$86, test $1, $2, $5, $10
        # XRPUSD: ~$1.50, test $0.01, $0.02, $0.05, $0.10
        # FX symbols: test $0.0005, $0.001, $0.002 (small pip steps)

        tests = {
            "SOLUSD": [
                (1.0, 40, True), (2.0, 40, True), (5.0, 40, True), (10.0, 40, True),
                (1.0, 40, False), (2.0, 40, False), (5.0, 40, False),
            ],
            "XRPUSD": [
                (0.01, 40, True), (0.02, 40, True), (0.05, 40, True), (0.10, 40, True),
                (0.01, 40, False), (0.02, 40, False), (0.05, 40, False),
            ],
            "GBPUSD": [
                (0.0005, 40, True), (0.001, 40, True), (0.002, 40, True),
                (0.0005, 40, False), (0.001, 40, False), (0.002, 40, False),
            ],
            "EURUSD": [
                (0.0005, 40, True), (0.001, 40, True), (0.002, 40, True),
                (0.0005, 40, False), (0.001, 40, False), (0.002, 40, False),
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
                r = simulate_engine(symbol, bars, info, step=step, max_open=max_open, alpha=1.0, gap=1, momentum_gate=mom)
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

        # Sort and print results
        all_rows.sort(key=lambda x: x["realized_usd"], reverse=True)

        print(f'\n{"="*100}')
        print(f'  ALL RESULTS (sorted)')
        print(f'{"="*100}')
        print(f'  {"Symbol":<10} {"Step":>10} {"MO":>4} {"Mom":>5} {"Realized":>14} {"Closes":>8} {"Rearm":>8} {"MaxSeen":>8}')
        print(f'  {"─"*90}')
        for r in all_rows:
            mom_str = "ON" if r["momentum_gate"] else "OFF"
            print(f'  {r["symbol"]:<10} ${r["step"]:>9,.4f} {r["max_open"]:>4} {mom_str:>5} ${r["realized_usd"]:>13,.2f} {r["closes"]:>8} {r["rearm_opens"]:>8} {r["max_seen"]:>8}')

        # Write CSV
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
