#!/usr/bin/env python3
"""Quick V3 regime param focus on USDJPY + USDCHF at step=0.25."""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v3_bounded import Config, ROOT, load_bars, simulate_symbol

def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    configs = [
        # (range, buffer, window, cooldown, label)
        (18.0, 3.0, 240, 60, "default"),
        (24.0, 3.0, 240, 60, "wider_range"),
        (36.0, 3.0, 240, 60, "wide_range"),
        (18.0, 5.0, 240, 60, "wider_buffer"),
        (18.0, 8.0, 240, 60, "wide_buffer"),
        (18.0, 12.0, 240, 60, "wider_buffer2"),
        (18.0, 3.0, 480, 60, "longer_window"),
        (18.0, 3.0, 720, 60, "long_window"),
        (18.0, 3.0, 1440, 60, "day_window"),
        (36.0, 5.0, 480, 120, "wide_all"),
        (48.0, 8.0, 720, 120, "very_wide"),
        (72.0, 12.0, 1440, 240, "ultra_wide"),
        (24.0, 5.0, 480, 120, "balanced_wide"),
        (36.0, 8.0, 720, 120, "balanced_wider"),
    ]

    try:
        for symbol in ["USDJPY", "USDCHF"]:
            info = mt5.symbol_info(symbol)
            bars = load_bars(symbol, 60)
            if not bars or info is None:
                continue

            rows = []
            print(f"\n=== {symbol} step=0.25 ===")
            for rp, bp, wb, cb, label in configs:
                cfg = Config(
                    step_pips=0.25, max_open_per_side=20, max_floating_loss_usd=-10.0,
                    vwap_lookback=20, regime_lookback_bars=60,
                    max_range_pips=rp, breakout_buffer_pips=bp,
                    max_lattice_window_bars=wb, cooldown_bars=cb,
                )
                r = simulate_symbol(symbol, bars, info, cfg)
                score = r["combined_net_usd"] - abs(r.get("breakout_net_usd", 0))
                r["label"] = label
                r["score"] = score
                rows.append(r)
                print(
                    f"  {label:<16} range={rp:>5.0f} buf={bp:>4.0f} win={wb:>4} "
                    f"cool={cb:>3} combined=${r['combined_net_usd']:+.2f} "
                    f"realized=${r['realized_net_usd']:+.2f} "
                    f"breakout=${r['breakout_net_usd']:+.2f} "
                    f"kills={r['breakout_kills']} timed={r['timed_kills']} "
                    f"worst=${r['worst_floating_usd']:+.2f}"
                )

            best = max(rows, key=lambda x: x["score"])
            print(f"  *** BEST: {best['label']} score=${best['score']:+.2f} combined=${best['combined_net_usd']:+.2f} ***")

        return 0
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    raise SystemExit(main())
