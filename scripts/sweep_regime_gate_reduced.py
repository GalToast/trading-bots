#!/usr/bin/env python3
"""
Reduced V3 regime gate sweep — 36 configs x 5 symbols = 180 runs.

Parameters:
  lookback: [60, 120, 240]
  max_range: [12, 18, 25, 35]
  buffer: [1.5, 3.0, 5.0]
  window: fixed at 240
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v3_bounded import (
    DEFAULT_SYMBOLS, ROOT, Config,
    load_bars, simulate_symbol,
)


def main() -> int:
    if not mt5.initialize():
        print("MT5 init failed")
        return 1

    lookbacks = [60, 120, 240]
    max_ranges = [12, 18, 25, 35]
    buffers = [1.5, 3.0, 5.0]
    window = 240
    days = 20

    symbols = DEFAULT_SYMBOLS
    symbol_data = {}
    for sym in symbols:
        info = mt5.symbol_info(sym)
        if info is None:
            continue
        bars = load_bars(sym, days)
        if not bars:
            continue
        symbol_data[sym] = (info, bars)
        print(f"Loaded {sym}: {len(bars)} bars")

    output_path = ROOT / "reports" / "penetration_regime_gate_sweep_20d.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "symbol", "lookback", "max_range_pips", "breakout_buffer_pips",
        "realized_closes", "breakout_flushes", "realized_net_usd",
        "breakout_net_usd", "combined_net_usd", "worst_floating_usd",
        "max_open_total", "breakout_kills", "hard_stop_fires",
    ]

    total = len(lookbacks) * len(max_ranges) * len(buffers) * len(symbol_data)
    done = 0
    start = time.time()

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for lb in lookbacks:
            for mr in max_ranges:
                for buf in buffers:
                    cfg = Config(
                        step_pips=1.0,
                        max_open_per_side=20,
                        max_floating_loss_usd=-10.0,
                        vwap_lookback=20,
                        regime_lookback_bars=lb,
                        max_range_pips=mr,
                        breakout_buffer_pips=buf,
                        max_lattice_window_bars=window,
                        cooldown_bars=60,
                    )

                    for sym, (info, bars) in symbol_data.items():
                        row = simulate_symbol(sym, bars, info, cfg)

                        out = {
                            "symbol": sym,
                            "lookback": lb,
                            "max_range_pips": mr,
                            "breakout_buffer_pips": buf,
                            "realized_closes": row["realized_closes"],
                            "breakout_flushes": row["breakout_flushes"],
                            "realized_net_usd": round(row["realized_net_usd"], 2),
                            "breakout_net_usd": round(row["breakout_net_usd"], 2),
                            "combined_net_usd": round(row["combined_net_usd"], 2),
                            "worst_floating_usd": round(row["worst_floating_usd"], 2),
                            "max_open_total": row["max_open_total"],
                            "breakout_kills": row["breakout_kills"],
                            "hard_stop_fires": row["hard_stop_fires"],
                        }
                        writer.writerow(out)
                        done += 1

                        elapsed = time.time() - start
                        rate = done / elapsed if elapsed > 0 else 0
                        eta = (total - done) / rate if rate > 0 else 0
                        sym_short = out["symbol"][:4]
                        print(
                            f"  [{done}/{total}] {sym_short} lb={lb} mr={mr} buf={buf} "
                            f"combined=${out['combined_net_usd']:+.0f} "
                            f"kills={row['breakout_kills']} "
                            f"rate={rate:.1f}/s ETA {eta:.0f}s"
                        )

    elapsed = time.time() - start
    print(f"\nDone. {done} runs in {elapsed:.1f}s ({done/elapsed:.1f}/s)")
    print(f"Saved {output_path}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
