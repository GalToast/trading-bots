#!/usr/bin/env python3
"""
Execution Reality Check — verifying the close-at-extreme edge is real.

Checks:
1. How far past the penetration level does each bar actually go?
2. What % of the extreme edge is "real" vs just the penetration level?
3. Is the 8.7x edge from bar sweeps or a simulation bug?
"""
from __future__ import annotations

import csv
from pathlib import Path

import MetaTrader5 as mt5

from penetration_lattice_lab_v2 import (
    ROOT,
    load_bars,
    pip_size_for,
    spread_price,
)


VOLUME = 0.01


def pnl_usd(symbol, direction, entry, exit_px, spread_px, vol=VOLUME):
    ot = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    gross = mt5.order_calc_profit(ot, symbol, vol, entry, exit_px)
    if gross is None:
        return 0.0
    if direction == "BUY":
        sc = mt5.order_calc_profit(ot, symbol, vol, entry + spread_px, entry)
    else:
        sc = mt5.order_calc_profit(ot, symbol, vol, entry, entry + spread_px)
    return float(gross) - abs(float(sc or 0.0))


class Pos:
    __slots__ = ['direction', 'entry', 'id']
    def __init__(self, direction, entry, id_=0):
        self.direction = direction
        self.entry = entry
        self.id = id_

_next_id = 0
def _new_pos(d, e):
    global _next_id
    p = Pos(d, e, _next_id)
    _next_id += 1
    return p


def run_with_fill_analysis(symbol, bars, info, step_pips, cap):
    """Run lattice tracking both level-fill and extreme-fill for EVERY close."""
    global _next_id
    _next_id = 0

    if not bars:
        return {}

    pip = pip_size_for(info)
    spread = spread_price(info)
    base_step = step_pips * pip

    anchor = bars[0]["close"]
    sell_level = anchor + base_step
    buy_level = anchor - base_step

    positions: list[Pos] = []
    level_pnls: list[float] = []
    extreme_pnls: list[float] = []
    penetration_depths: list[float] = []  # how far past the trigger level

    # Track stats
    same_bar_extreme_count = 0
    next_bar_extreme_count = 0
    total_extreme_bonus = 0.0

    # Open initial
    positions.append(_new_pos("SELL", sell_level))
    positions.append(_new_pos("BUY", buy_level))

    for idx in range(1, len(bars)):
        bar = bars[idx]

        # Open new orders
        oss = sum(1 for p in positions if p.direction == "SELL")
        obs = sum(1 for p in positions if p.direction == "BUY")

        while bar["high"] >= sell_level and oss < cap:
            positions.append(_new_pos("SELL", sell_level))
            oss += 1
            sell_level += base_step

        while bar["low"] <= buy_level and obs < cap:
            positions.append(_new_pos("BUY", buy_level))
            obs += 1
            buy_level -= base_step

        # Close sells
        sells = sorted([p for p in positions if p.direction == "SELL"],
                       key=lambda p: p.entry, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry:
            level_fill = sells[1].entry  # penetration level
            extreme_fill = bar["low"]    # bar extreme

            # Track penetration depth
            depth = sells[1].entry - bar["low"]
            penetration_depths.append(depth)

            level_pnl = pnl_usd(symbol, "SELL", sells[0].entry, level_fill, spread)
            extreme_pnl = pnl_usd(symbol, "SELL", sells[0].entry, extreme_fill, spread)

            level_pnls.append(level_pnl)
            extreme_pnls.append(extreme_pnl)
            total_extreme_bonus += (extreme_pnl - level_pnl)

            # Check if extreme is actually reachable (same bar reversal)
            if depth > 0:
                same_bar_extreme_count += 1

            positions.remove(sells[0])
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)

        # Close buys
        buys = sorted([p for p in positions if p.direction == "BUY"],
                      key=lambda p: p.entry)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry:
            level_fill = buys[1].entry
            extreme_fill = bar["high"]

            depth = bar["high"] - buys[1].entry
            penetration_depths.append(depth)

            level_pnl = pnl_usd(symbol, "BUY", buys[0].entry, level_fill, spread)
            extreme_pnl = pnl_usd(symbol, "BUY", buys[0].entry, extreme_fill, spread)

            level_pnls.append(level_pnl)
            extreme_pnls.append(extreme_pnl)
            total_extreme_bonus += (extreme_pnl - level_pnl)

            if depth > 0:
                same_bar_extreme_count += 1

            positions.remove(buys[0])
            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)

    level_total = sum(level_pnls)
    extreme_total = sum(extreme_pnls)
    avg_depth = sum(penetration_depths) / len(penetration_depths) if penetration_depths else 0
    max_depth = max(penetration_depths) if penetration_depths else 0
    min_depth = min(penetration_depths) if penetration_depths else 0

    # Calculate depth in pips for interpretation
    avg_depth_pips = avg_depth / pip if pip > 0 else 0
    max_depth_pips = max_depth / pip if pip > 0 else 0
    min_depth_pips = min_depth / pip if pip > 0 else 0


    # Count how many closes had meaningful depth (> 0.1 pips)
    meaningful = sum(1 for d in penetration_depths if d > 0.1 * pip)
    zero_depth = sum(1 for d in penetration_depths if d < 0.00001)

    return {
        "total_closes": len(level_pnls),
        "level_total": round(level_total, 3),
        "extreme_total": round(extreme_total, 3),
        "extreme_bonus": round(extreme_total - level_total, 3),
        "extreme_ratio": round(extreme_total / level_total, 2) if level_total != 0 else 0,
        "avg_depth_pips": round(avg_depth_pips, 3),
        "max_depth_pips": round(max_depth_pips, 3),
        "min_depth_pips": round(min_depth_pips, 3),
        "meaningful_closes": meaningful,
        "zero_depth_closes": zero_depth,
        "mean_depth_pct": round(avg_depth_pips / step_pips * 100, 1),
    }


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    print("=" * 90)
    print("EXECUTION REALITY CHECK — Is the extreme-close edge real?")
    print("=" * 90)

    days = 60
    symbols = ["GBPUSD", "EURUSD", "NZDUSD"]

    for symbol in symbols:
        info = mt5.symbol_info(symbol)
        bars = load_bars(symbol, days)
        if not bars or info is None:
            continue

        step = {"GBPUSD": 2.0, "EURUSD": 2.5, "NZDUSD": 1.5}[symbol]
        print(f"\n{'='*80}")
        print(f"=== {symbol} — step={step} pips, cap=20 ===")
        print(f"{'='*80}")

        r = run_with_fill_analysis(symbol, bars, info, step, 20)

        print(f"  Total closes:           {r['total_closes']}")
        print(f"  Level-fill total:       ${r['level_total']:+.2f}")
        print(f"  Extreme-fill total:     ${r['extreme_total']:+.2f}")
        print(f"  Extreme bonus:          ${r['extreme_bonus']:+.2f}  ({r['extreme_ratio']:.1f}x)")
        print(f"  Avg penetration depth:  {r['avg_depth_pips']:.3f} pips  ({r['mean_depth_pct']:.1f}% of step)")
        print(f"  Max penetration depth:  {r['max_depth_pips']:.3f} pips")
        print(f"  Zero-depth closes:      {r['zero_depth_closes']} / {r['total_closes']}")
        print(f"  Meaningful depth (>0.1p): {r['meaningful_closes']} / {r['total_closes']}")

        # Key question: if avg depth is only 0.01 pips, the extreme edge is a bug
        # If avg depth is 0.5-2.0 pips, it's real
        if r['avg_depth_pips'] < 0.05:
            print(f"\n  ⚠️  WARNING: Avg penetration depth is only {r['avg_depth_pips']:.3f} pips.")
            print(f"     The extreme-fill edge may be dominated by floating-point noise.")
            print(f"     The 8.7x result is likely an artifact, not real edge.")
        elif r['avg_depth_pips'] < 0.5:
            print(f"\n  🔍 Borderline: {r['avg_depth_pips']:.3f} pips avg depth.")
            print(f"     Some edge is real but may be inflated by sub-pip fills.")
        else:
            print(f"\n  ✅ REAL: {r['avg_depth_pips']:.3f} pips avg penetration depth.")
            print(f"     The extreme-fill edge is physically meaningful.")

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
