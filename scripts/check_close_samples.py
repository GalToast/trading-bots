#!/usr/bin/env python3
"""
Reality check v2 — sample a few actual close events to verify the math.

Print the first 20 close events with:
- Position entry, penetration level, bar high/low, extreme fill
- Depth in pips
- Whether the depth is physically achievable (bar sweeps through it)
"""
from __future__ import annotations

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
    def __init__(self, direction, entry, opened_idx=0):
        self.direction = direction
        self.entry = entry
        self.opened_idx = opened_idx


def main():
    if not mt5.initialize():
        print("MT5 init failed"); return 1

    symbol = "GBPUSD"
    info = mt5.symbol_info(symbol)
    bars = load_bars(symbol, 60)
    pip = pip_size_for(info)
    spread = spread_price(info)
    step_pips = 2.0
    cap = 20
    base_step = step_pips * pip

    anchor = bars[0]["close"]
    sell_level = anchor + base_step
    buy_level = anchor - base_step

    positions: list[Pos] = []
    positions.append(Pos("SELL", sell_level, 0))
    positions.append(Pos("BUY", buy_level, 0))

    close_events = 0
    max_events = 30

    for idx in range(1, len(bars)):
        bar = bars[idx]

        oss = sum(1 for p in positions if p.direction == "SELL")
        obs = sum(1 for p in positions if p.direction == "BUY")

        while bar["high"] >= sell_level and oss < cap:
            positions.append(Pos("SELL", sell_level, idx))
            oss += 1
            sell_level += base_step

        while bar["low"] <= buy_level and obs < cap:
            positions.append(Pos("BUY", buy_level, idx))
            obs += 1
            buy_level -= base_step

        # Close sells
        sells = sorted([p for p in positions if p.direction == "SELL"],
                       key=lambda p: p.entry, reverse=True)
        while len(sells) >= 2 and bar["low"] <= sells[1].entry and close_events < max_events:
            level_fill = sells[1].entry
            extreme_fill = bar["low"]
            depth_pips = (sells[1].entry - bar["low"]) / pip
            level_pnl = pnl_usd(symbol, "SELL", sells[0].entry, level_fill, spread)
            extreme_pnl = pnl_usd(symbol, "SELL", sells[0].entry, extreme_fill, spread)

            close_events += 1
            print(f"Close #{close_events:3d} | SELL entry={sells[0].entry:.5f} "
                  f"trigger={sells[1].entry:.5f} bar_low={bar['low']:.5f} bar_close={bar['close']:.5f} "
                  f"depth={depth_pips:.1f}p | "
                  f"level_fill=${level_pnl:.3f} extreme_fill=${extreme_pnl:.3f} "
                  f"bar_range={(bar['high']-bar['low'])/pip:.1f}p")

            positions.remove(sells[0])
            sells = sorted([p for p in positions if p.direction == "SELL"],
                           key=lambda p: p.entry, reverse=True)

        # Close buys
        buys = sorted([p for p in positions if p.direction == "BUY"],
                      key=lambda p: p.entry)
        while len(buys) >= 2 and bar["high"] >= buys[1].entry and close_events < max_events:
            level_fill = buys[1].entry
            extreme_fill = bar["high"]
            depth_pips = (bar["high"] - buys[1].entry) / pip
            level_pnl = pnl_usd(symbol, "BUY", buys[0].entry, level_fill, spread)
            extreme_pnl = pnl_usd(symbol, "BUY", buys[0].entry, extreme_fill, spread)

            close_events += 1
            print(f"Close #{close_events:3d} | BUY entry={buys[0].entry:.5f} "
                  f"trigger={buys[1].entry:.5f} bar_high={bar['high']:.5f} bar_close={bar['close']:.5f} "
                  f"depth={depth_pips:.1f}p | "
                  f"level_fill=${level_pnl:.3f} extreme_fill=${extreme_pnl:.3f} "
                  f"bar_range={(bar['high']-bar['low'])/pip:.1f}p")

            positions.remove(buys[0])
            buys = sorted([p for p in positions if p.direction == "BUY"],
                          key=lambda p: p.entry)

    mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
