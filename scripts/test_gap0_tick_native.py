#!/usr/bin/env python3
"""Quick tick-native gap=0 test.
Compare gap=0 vs gap=1 in tick engine on recent BTC data.
"""
import json
import MetaTrader5 as mt5
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from tick_penetration_lattice_core import TickStatefulRearmEngine

mt5.initialize()

# Get recent ticks (last 2 hours of M15 = 8 bars worth of ticks)
ticks = mt5.copy_ticks_range("BTCUSD", int(mt5.time() - 7200), int(mt5.time()), mt5.TICKS_ALL)
if ticks is None or len(ticks) < 100:
    print(f"Not enough ticks: {len(ticks) if ticks is not None else 0}")
    mt5.shutdown()
    exit()

print(f"Loaded {len(ticks)} ticks over 2 hours")

# Run tick engine with gap=0 and gap=1
for gap in [0, 1, 2]:
    cfg = type("Cfg", (), {
        "step_pips": 15.0,
        "max_open_per_side": 12,
        "close_alpha": 1.0,
        "close_gap": gap,
        "max_floating_loss_usd": -3500.0,
        "sell_gap": gap,
        "buy_gap": gap,
    })()
    
    engine = TickStatefulRearmEngine(
        symbol="BTCUSD", timeframe_name="M15", cfg=cfg,
        volume=0.01, state_path=None, event_path=None,
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
    closes = state["symbols"]["BTCUSD"]["realized_closes"]
    net = state["symbols"]["BTCUSD"]["realized_net_usd"]
    avg = net / closes if closes > 0 else 0
    print(f"  gap={gap}: {closes}c, ${net:.2f} net, ${avg:.2f}/close")

mt5.shutdown()
