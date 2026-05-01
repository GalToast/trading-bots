#!/usr/bin/env python3
"""
V3 regime parameter optimization on V3 symbols + ultra-loose V3 on self-healers.
Tests: wider range gate, wider breakout buffer, longer windows, longer cooldowns.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v3_bounded import (
    Config,
    DEFAULT_SYMBOLS,
    ROOT,
    load_bars,
    simulate_symbol,
)


def main() -> int:
    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    # V3 symbol configurations to test
    v3_symbols = {
        "USDJPY": {"step": 0.25, "mode": "v3"},
        "USDCHF": {"step": 0.25, "mode": "v3"},
        "NZDUSD": {"step": 1.0, "mode": "v3"},  # V3 for NZDUSD to compare
        # Self-healers with ultra-loose V3
        "GBPUSD": {"step": 1.75, "mode": "loose_v3"},
        "EURUSD": {"step": 2.50, "mode": "loose_v3"},
    }

    # V3 regime param matrix
    range_pips_list = [12.0, 18.0, 24.0, 36.0, 48.0, 72.0]
    buffer_pips_list = [2.0, 3.0, 5.0, 8.0, 12.0]
    window_bars_list = [120, 240, 480, 720, 1440]  # 2h to 24h
    cooldown_bars_list = [30, 60, 120, 240]

    try:
        rows: list[dict] = []
        for symbol, cfg_info in v3_symbols.items():
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, 60)
            if not bars:
                continue

            step = cfg_info["step"]
            mode = cfg_info["mode"]

            if mode == "loose_v3":
                # Only test wide params for self-healers
                range_pips_test = [36.0, 48.0, 72.0]
                buffer_pips_test = [8.0, 12.0]
                window_test = [720, 1440]
                cooldown_test = [120, 240]
            else:
                range_pips_test = range_pips_list
                buffer_pips_test = buffer_pips_list
                window_test = window_bars_list
                cooldown_test = cooldown_bars_list

            best_score = -999999
            best_row = None

            count = 0
            for rp in range_pips_test:
                for bp in buffer_pips_test:
                    for wb in window_test:
                        for cb in cooldown_test:
                            cfg = Config(
                                step_pips=step,
                                max_open_per_side=20,
                                max_floating_loss_usd=-10.0,
                                vwap_lookback=20,
                                regime_lookback_bars=60,
                                max_range_pips=rp,
                                breakout_buffer_pips=bp,
                                max_lattice_window_bars=wb,
                                cooldown_bars=cb,
                            )
                            r = simulate_symbol(symbol, bars, info, cfg)
                            r["symbol"] = symbol
                            r["step_pips"] = step
                            r["max_range_pips"] = rp
                            r["breakout_buffer_pips"] = bp
                            r["max_lattice_window_bars"] = wb
                            r["cooldown_bars"] = cb
                            rows.append(r)
                            count += 1

                            # Score: combined minus breakout flush cost
                            score = r["combined_net_usd"] - abs(r.get("breakout_net_usd", 0))
                            if score > best_score:
                                best_score = score
                                best_row = r

            # Print top 10
            scored = sorted(rows, key=lambda x: x["combined_net_usd"] - abs(x.get("breakout_net_usd", 0)), reverse=True)
            print(f"\n=== {symbol} (step={step}, {count} configs) ===")
            for i, r in enumerate(scored[:10]):
                print(
                    f"  #{i+1} range={r['max_range_pips']:>5.0f} buf={r['breakout_buffer_pips']:>4.0f} "
                    f"win={r['max_lattice_window_bars']:>4} cool={r['cooldown_bars']:>3} "
                    f"combined=${r['combined_net_usd']:+.2f} "
                    f"realized=${r['realized_net_usd']:+.2f} "
                    f"breakout=${r['breakout_net_usd']:+.2f} "
                    f"kills={r['breakout_kills']} worst=${r['worst_floating_usd']:+.2f}"
                )
            if best_row:
                print(
                    f"  *** BEST: range={best_row['max_range_pips']:.0f} buf={best_row['breakout_buffer_pips']:.0f} "
                    f"win={best_row['max_lattice_window_bars']} cool={best_row['cooldown_bars']} "
                    f"scored=${best_score:+.2f} combined=${best_row['combined_net_usd']:+.2f} ***"
                )

        # Clean basket using best configs
        print("\n=== BEST HYBRID BASKET ===")
        best_per_symbol = {}
        for symbol in v3_symbols:
            symbol_rows = [r for r in rows if r["symbol"] == symbol]
            if symbol_rows:
                best = max(symbol_rows, key=lambda r: r["combined_net_usd"] - abs(r.get("breakout_net_usd", 0)))
                best_per_symbol[symbol] = best

        total = sum(r["combined_net_usd"] for r in best_per_symbol.values())
        total_breakout = sum(r.get("breakout_net_usd", 0) for r in best_per_symbol.values())
        daily = total / 60
        worst_f = max(r["worst_floating_usd"] for r in best_per_symbol.values())

        for symbol, r in best_per_symbol.items():
            print(
                f"  {symbol}: range={r['max_range_pips']:.0f} buf={r['breakout_buffer_pips']:.0f} "
                f"win={r['max_lattice_window_bars']} cool={r['cooldown_bars']} "
                f"${r['combined_net_usd']:+.2f} worst=${r['worst_floating_usd']:+.2f}"
            )

        print(f"\n  TOTAL: ${total:+.2f}/60d  ${daily:+.2f}/day  breakout_cost=${total_breakout:+.2f}  worst_float={worst_f:+.2f}")
        print(f"  At 0.10 lot: ${daily * 10:+.2f}/day  |  At 0.50 lot: ${daily * 50:+.2f}/day")

        output_path = ROOT / "reports" / "penetration_lattice_v3_regime_sweep.csv"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            with output_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"\nSaved {output_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
