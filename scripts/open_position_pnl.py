#!/usr/bin/env python3
"""Current open position PnL summary by lane.

This is a local operator utility. It reads MT5 credentials from environment
variables through mt5_config instead of carrying placeholders or secrets.
"""
import MetaTrader5 as mt5
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mt5_config

if not mt5.initialize(
    login=mt5_config.LOGIN,
    password=mt5_config.PASSWORD,
    server=mt5_config.SERVER,
):
    raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

positions = mt5.positions_get() or []

lanes = {}
for p in positions:
    magic = p.magic
    if magic not in lanes:
        lanes[magic] = {"positions": [], "total_profit": 0, "total_swap": 0}
    lanes[magic]["positions"].append(p)
    lanes[magic]["total_profit"] += p.profit
    lanes[magic]["total_swap"] += p.swap

lane_names = {
    941777: "FX Rearm",
    941778: "FX Momentum",
    941779: "BTC H1",
}

for magic in sorted(lanes.keys()):
    info = lanes[magic]
    name = lane_names.get(magic, f"Unknown({magic})")
    buys = [p for p in info["positions"] if p.type == 0]
    sells = [p for p in info["positions"] if p.type == 1]
    wins = [p for p in info["positions"] if p.profit > 0]
    losers = [p for p in info["positions"] if p.profit < 0]
    
    print(f"\n{name} (magic={magic}): {len(info['positions'])} positions")
    print(f"  Floating PnL: {info['total_profit']:+.2f} | Swap: {info['total_swap']:+.2f}")
    print(f"  BUYs: {len(buys)} | SELLs: {len(sells)}")
    print(f"  In profit: {len(wins)} | In loss: {len(losers)}")
    
    # Show losing positions
    if losers:
        print(f"  Losers:")
        for p in sorted(losers, key=lambda x: x.profit):
            action = "SELL" if p.type == 1 else "BUY"
            print(f"    #{p.ticket} {p.symbol} {action} vol={p.volume} entry={p.price_open:.5f} "
                  f"profit={p.profit:+.2f}")
    
    # Show winning positions
    if wins:
        print(f"  Winners:")
        for p in sorted(wins, key=lambda x: -x.profit):
            action = "SELL" if p.type == 1 else "BUY"
            print(f"    #{p.ticket} {p.symbol} {action} vol={p.volume} entry={p.price_open:.5f} "
                  f"profit={p.profit:+.2f}")

mt5.shutdown()
