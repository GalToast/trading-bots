#!/usr/bin/env python3
"""
Fine-grained step sweep on apex symbols + NZDUSD V3 test.
Testing: GBPUSD 1.5-3.0, EURUSD 2.0-5.0, NZDUSD raw vs V3, USDJPY/USDCHF 0.25-1.0.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import (
    DEFAULT_SYMBOLS,
    ROOT,
    dynamic_step,
    load_bars,
    pip_size_for,
    spread_price,
    unit_pnl_usd,
)
from penetration_lattice_lab_v3_bounded import (
    simulate_symbol as simulate_v3_bounded,
)

VOLUME = 0.01
DEFAULT_SYMBOLS = ["USDJPY", "GBPUSD", "EURUSD", "USDCHF", "NZDUSD"]

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RawConfig:
    step_pips: float
    max_open_per_side: int
    close_mode: str


def simulate_raw_close2(symbol: str, bars: list[dict], symbol_info, cfg: RawConfig) -> dict:
    if not bars:
        return {}

    pip_size = pip_size_for(symbol_info)
    spread_px = spread_price(symbol_info)
    base_step_px = cfg.step_pips * pip_size

    anchor = bars[0]["close"]
    next_sell_level = anchor + base_step_px
    next_buy_level = anchor - base_step_px

    class _Cfg:
        adaptive_step_threshold_1 = 10
        adaptive_step_threshold_2 = 20
        adaptive_step_multiplier_1 = 1.5
        adaptive_step_multiplier_2 = 2.0

    open_tickets: list = []
    realized_pnls: list[float] = []
    max_open = 0
    max_open_buy = 0
    max_open_sell = 0

    for idx in range(1, len(bars)):
        bar = bars[idx]
        open_buy = sum(1 for t in open_tickets if t.direction == "BUY")
        open_sell = sum(1 for t in open_tickets if t.direction == "SELL")

        while bar["high"] >= next_sell_level and open_sell < cfg.max_open_per_side:
            open_tickets.append(type("T", (), {"direction": "SELL", "entry_price": next_sell_level, "opened_idx": idx})())
            open_sell += 1
            next_sell_level += dynamic_step(base_step_px, open_sell, _Cfg())

        while bar["low"] <= next_buy_level and open_buy < cfg.max_open_per_side:
            open_tickets.append(type("T", (), {"direction": "BUY", "entry_price": next_buy_level, "opened_idx": idx})())
            open_buy += 1
            next_buy_level -= dynamic_step(base_step_px, open_buy, _Cfg())

        gap = 1 if cfg.close_mode == "one_level" else 2

        sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)
        while len(sells) > gap and bar["low"] <= sells[gap].entry_price:
            outer = sells[0]
            close_ref = sells[gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "SELL", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            sells = sorted((t for t in open_tickets if t.direction == "SELL"), key=lambda t: t.entry_price, reverse=True)

        buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)
        while len(buys) > gap and bar["high"] >= buys[gap].entry_price:
            outer = buys[0]
            close_ref = buys[gap].entry_price
            realized_pnls.append(unit_pnl_usd(symbol, "BUY", outer.entry_price, close_ref, spread_px))
            open_tickets.remove(outer)
            buys = sorted((t for t in open_tickets if t.direction == "BUY"), key=lambda t: t.entry_price)

        max_open = max(max_open, len(open_tickets))
        max_open_buy = max(max_open_buy, sum(1 for t in open_tickets if t.direction == "BUY"))
        max_open_sell = max(max_open_sell, sum(1 for t in open_tickets if t.direction == "SELL"))

    last_close = bars[-1]["close"]
    floating_pnls = [
        unit_pnl_usd(symbol, t.direction, t.entry_price, last_close, spread_px)
        for t in open_tickets
    ]

    realized_net = sum(realized_pnls)
    floating_net = sum(floating_pnls)
    combined_net = realized_net + floating_net

    return {
        "mode": "raw_close2",
        "realized_closes": len(realized_pnls),
        "realized_net_usd": round(realized_net, 3),
        "open_tickets_left": len(open_tickets),
        "floating_net_usd": round(floating_net, 3),
        "worst_floating_usd": round(min(floating_pnls), 3) if floating_pnls else 0.0,
        "combined_net_usd": round(combined_net, 3),
        "max_open_total": max_open,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-csv", default=str(ROOT / "reports" / "penetration_lattice_fine_sweep.csv"))
    args = parser.parse_args()

    if not mt5.initialize():
        print("MetaTrader5 initialize() failed")
        return 1

    # Fine step ranges per symbol
    fine_steps = {
        "GBPUSD": [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0],
        "EURUSD": [2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
        "NZDUSD": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
        "USDJPY": [0.25, 0.5, 0.75, 1.0, 1.5],
        "USDCHF": [0.25, 0.5, 0.75, 1.0, 1.5],
    }

    try:
        rows: list[dict] = []
        for symbol, steps in fine_steps.items():
            info = mt5.symbol_info(symbol)
            if info is None:
                continue
            bars = load_bars(symbol, args.days)
            if not bars:
                continue

            for step_pips in steps:
                # Raw close2
                cfg = RawConfig(step_pips=step_pips, max_open_per_side=20, close_mode="two_level")
                r = simulate_raw_close2(symbol, bars, info, cfg)
                r["symbol"] = symbol
                r["step_pips"] = step_pips
                rows.append(r)

                # V3 bounded for NZDUSD, USDJPY, USDCHF
                if symbol in ("NZDUSD", "USDJPY", "USDCHF"):
                    v3_cfg = type("V3", (), {
                        "step_pips": step_pips,
                        "max_open_per_side": 20,
                        "max_floating_loss_usd": -10.0,
                        "vwap_lookback": 20,
                        "regime_lookback_bars": 60,
                        "max_range_pips": 18.0,
                        "breakout_buffer_pips": 3.0,
                        "max_lattice_window_bars": 240,
                        "cooldown_bars": 60,
                        "adaptive_step_threshold_1": 10,
                        "adaptive_step_threshold_2": 20,
                        "adaptive_step_multiplier_1": 1.5,
                        "adaptive_step_multiplier_2": 2.0,
                    })()
                    rv3 = simulate_v3_bounded(symbol, bars, info, v3_cfg)
                    rv3["symbol"] = symbol
                    rv3["step_pips"] = step_pips
                    rv3["mode"] = "v3_bounded"
                    rows.append(rv3)

            # Print results
            symbol_rows = [r for r in rows if r["symbol"] == symbol]
            print(f"\n=== {symbol} ===")
            for r in symbol_rows:
                print(
                    f"  {r['mode']:<12} step={r['step_pips']:<5.2f} "
                    f"combined=${r['combined_net_usd']:+.2f} "
                    f"realized=${r['realized_net_usd']:+.2f} "
                    f"worst=${r['worst_floating_usd']:+.2f} "
                    f"max_open={r['max_open_total']:>3}"
                )
            best_raw = max([r for r in symbol_rows if r["mode"] == "raw_close2"], key=lambda r: r["combined_net_usd"])
            print(f"  *** RAW APEX: step={best_raw['step_pips']:.2f} ${best_raw['combined_net_usd']:+.2f} ***")

            v3_rows = [r for r in symbol_rows if r["mode"] == "v3_bounded"]
            if v3_rows:
                best_v3 = max(v3_rows, key=lambda r: r["combined_net_usd"])
                print(f"  *** V3 APEX:  step={best_v3['step_pips']:.2f} ${best_v3['combined_net_usd']:+.2f} ***")

        # Basket: pick best per symbol (comparing raw vs V3)
        print("\n=== HYBRID APEX BASKET ===")
        best_per_symbol = {}
        for symbol in fine_steps:
            symbol_rows = [r for r in rows if r["symbol"] == symbol]
            if not symbol_rows:
                continue
            # Compare best raw vs best V3
            best_raw = max([r for r in symbol_rows if r["mode"] == "raw_close2"], key=lambda r: r["combined_net_usd"])
            v3_rows = [r for r in symbol_rows if r["mode"] == "v3_bounded"]
            if v3_rows:
                best_v3 = max(v3_rows, key=lambda r: r["combined_net_usd"])
                best = best_v3 if best_v3["combined_net_usd"] > best_raw["combined_net_usd"] else best_raw
            else:
                best = best_raw
            best_per_symbol[symbol] = best

        for symbol, r in best_per_symbol.items():
            print(f"  {symbol}: {r['mode']} step={r['step_pips']:.2f} ${r['combined_net_usd']:+.2f} worst={r['worst_floating_usd']:+.2f}")

        total = sum(r["combined_net_usd"] for r in best_per_symbol.values())
        daily = total / args.days
        worst_f = max(r["worst_floating_usd"] for r in best_per_symbol.values())
        print(f"\n  TOTAL: ${total:+.2f}/60d  ${daily:+.2f}/day  worst_floating={worst_f:+.2f}")
        print(f"  At 0.10 lot: ${daily * 10:+.2f}/day  |  At 0.50 lot: ${daily * 50:+.2f}/day")

        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            all_keys = set()
            for r in rows:
                all_keys.update(r.keys())
            with output_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=sorted(all_keys))
                writer.writeheader()
                writer.writerows(rows)
            print(f"\nSaved {output_path}")

        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
