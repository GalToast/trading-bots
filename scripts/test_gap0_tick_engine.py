#!/usr/bin/env python3
"""Test cascade close (gap=0) in the REAL tick engine vs bar replay.

Bar sweep: gap=0 = $8,807/hr (cascade ALL positions).
Bar sweep: gap=1 = $328/hr (close outermost only).

Question: does the tick engine cascade the same way, or does tick granularity limit it?
"""
import json
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

import MetaTrader5 as mt5
from tick_penetration_lattice_core import TickStatefulRearmEngine

mt5.initialize()

# Get 2 hours of recent BTC ticks
now = int(time.time())
ticks = mt5.copy_ticks_range("BTCUSD", now - 7200, now, mt5.TICKS_ALL)
if ticks is None or len(ticks) < 100:
    print(f"Not enough ticks: {len(ticks) if ticks is not None else 0}")
    mt5.shutdown()
    exit()

print(f"Loaded {len(ticks)} ticks over 2 hours")

# Analyze tick granularity
prices = [float(t[1]) for t in ticks]  # bid prices
max_tick_move = max(abs(prices[i+1] - prices[i]) for i in range(len(prices)-1))
avg_tick_move = sum(abs(prices[i+1] - prices[i]) for i in range(len(prices)-1)) / (len(prices)-1)
print(f"Max tick move: ${max_tick_move:.2f}")
print(f"Avg tick move: ${avg_tick_move:.2f}")
print(f"At $15 step: avg tick moves {avg_tick_move/15:.2f} levels, max tick moves {max_tick_move/15:.2f} levels")
print()

class MockCfg:
    def __init__(self, step, gap):
        self.step_pips = step
        self.max_open_per_side = 12
        self.close_alpha = 1.0
        self.sell_gap = gap
        self.buy_gap = gap
        self.max_floating_loss_usd = -3500.0

# Run tick engine with different gap values
for gap in [0, 1, 2]:
    cfg = MockCfg(step=15.0, gap=gap)
    
    engine = TickStatefulRearmEngine(
        symbol="BTCUSD",
        timeframe_name="M15",
        cfg=cfg,
        volume=0.01,
        state_path=None,
        event_path=None,
    )
    engine._prime_anchor(ticks[0])
    
    for tick in ticks:
        tick_dict = {
            "time": int(tick[0]), "time_msc": int(tick[0] * 1000),
            "bid": float(tick[1]), "ask": float(tick[2]),
            "last": float(tick[3]), "volume": int(tick[4]),
        }
        engine.process_tick(tick_dict, emit=False)
    
    state = engine.dump_state()
    btc = state["symbols"]["BTCUSD"]
    closes = btc["realized_closes"]
    net = btc["realized_net_usd"]
    avg = net / closes if closes > 0 else 0
    hours = 2.0
    per_hr = net / hours
    
    print(f"gap={gap}: {closes}c, ${net:.2f} net, ${avg:.2f}/close, ${per_hr:.2f}/hr")

print()
print("If tick engine gap=0 cascades through multiple levels per tick,")
print("it should approach the bar-level $8,807/hr result.")
print("If tick granularity limits it to 1 close per tick, it'll be much lower.")

mt5.shutdown()
