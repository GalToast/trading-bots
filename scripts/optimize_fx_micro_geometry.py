#!/usr/bin/env python3
"""
FX Micro Lane Geometry Optimization
=====================================
Compares current FX micro configs vs validated geometry configs.

Current: step=0.0001, alpha=1.0, gap=1/1, rearm_exc1
Validated:
  - GBPUSD: sell_step=0.5/buy_step=1.0, gap=1/3
  - EURUSD: step=1.0/1.0, gap=3/3
  - NZDUSD: FAILED realism (park)

Usage:
    python scripts/optimize_fx_micro_geometry.py
"""
import json
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
from tick_penetration_lattice_core import (
    engine_from_args,
    load_recent_bars,
    tick_pnl_usd,
)

# Current config (generic)
CURRENT_CONFIG = {
    "step_sell": 0.0001,
    "step_buy": 0.0001,
    "sell_gap": 1,
    "buy_gap": 1,
    "close_alpha": 1.0,
    "variant": "rearm_lvl2_exc1",
    "momentum_gate": True,
    "max_open": 80,
}

# Validated configs (from fx_fixed_shape_side_gap + fx_low_step_realism_audit)
VALIDATED_CONFIGS = {
    "GBPUSD": {
        "step_sell": 0.00005,  # 0.5 pips in FX units
        "step_buy": 0.00010,   # 1.0 pips
        "sell_gap": 1,
        "buy_gap": 3,
        "close_alpha": 1.0,
        "variant": "rearm_lvl2_exc1",
        "momentum_gate": True,
        "max_open": 80,
    },
    "EURUSD": {
        "step_sell": 0.00010,
        "step_buy": 0.00010,
        "sell_gap": 3,
        "buy_gap": 3,
        "close_alpha": 1.0,
        "variant": "rearm_lvl2_exc1",
        "momentum_gate": True,
        "max_open": 80,
    },
}

SYMBOLS = ["GBPUSD", "EURUSD"]
TIMEFRAME = "M15"
BARS = 500  # ~5 days of M15


def replay_config(name: str, symbol: str, config: dict) -> dict:
    """Replay a config against recent tick data and return results."""
    engine = engine_from_args(
        symbol=symbol,
        timeframe_name=TIMEFRAME,
        step=max(config["step_sell"], config["step_buy"]),
        max_open_per_side=config["max_open"],
        variant_name=config["variant"],
        close_alpha=config["close_alpha"],
        momentum_gate=config["momentum_gate"],
        cooldown_bars=0,
        sell_gap=config["sell_gap"],
        buy_gap=config["buy_gap"],
        step_sell=config["step_sell"],
        step_buy=config["step_buy"],
        volume=0.01,
    )
    
    # Load recent bars
    bars = load_recent_bars(symbol, TIMEFRAME, count=BARS)
    if not bars:
        return {"error": f"No bars for {symbol}"}
    
    # Initialize engine
    engine.state.last_bar_time = int(bars[-1]["time"])
    engine.prime(float(bars[-1]["close"]), int(bars[-1]["time"]))
    
    # Process bars as ticks (simplified - uses bar OHLC as tick approximations)
    for bar in bars:
        tick_data = {
            "time": int(bar["time"]),
            "time_msc": int(bar["time"]) * 1000,
            "bid": float(bar["close"]),
            "ask": float(bar["close"]) + 0.0001,  # 1 pip spread approx
            "last": float(bar["close"]),
            "flags": 0,
            "volume": int(bar.get("tick_volume", 0)),
        }
        engine.process_ticks([tick_data], action_sink=None, event_path=None, emit=False)
    
    return {
        "realized": float(engine.state.realized_net_usd or 0.0),
        "closes": int(engine.state.realized_closes or 0),
        "open_count": len(engine.state.open_tickets or []),
        "resets": int(engine.state.anchor_resets or 0),
        "per_close": float(engine.state.realized_net_usd or 0.0) / max(1, int(engine.state.realized_closes or 1)),
    }


def main():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return

    print("=" * 72)
    print("FX MICRO LANE GEOMETRY OPTIMIZATION")
    print("=" * 72)
    print(f"Timeframe: {TIMEFRAME}, Bars: {BARS}")
    print()

    for symbol in SYMBOLS:
        print(f"--- {symbol} ---")
        
        # Current config
        print("  Current (gap 1/1, step 0.0001):")
        curr = replay_config("current", symbol, CURRENT_CONFIG)
        if "error" in curr:
            print(f"    ERROR: {curr['error']}")
        else:
            print(f"    Realized: ${curr['realized']:+.2f} ({curr['closes']}c)")
            print(f"    $/close: ${curr['per_close']:+.3f} | Open: {curr['open_count']}")
        
        # Validated config
        val_config = VALIDATED_CONFIGS.get(symbol)
        if val_config:
            print(f"  Validated (gap {val_config['sell_gap']}/{val_config['buy_gap']}, asymmetric step):")
            val = replay_config("validated", symbol, val_config)
            if "error" in val:
                print(f"    ERROR: {val['error']}")
            else:
                print(f"    Realized: ${val['realized']:+.2f} ({val['closes']}c)")
                print(f"    $/close: ${val['per_close']:+.3f} | Open: {val['open_count']}")
                
                # Compare
                if curr.get('realized') and val.get('realized'):
                    delta = val['realized'] - curr['realized']
                    pct = delta / max(0.01, abs(curr['realized'])) * 100
                    direction = "BETTER" if delta > 0 else "WORSE"
                    print(f"    Delta: ${delta:+.2f} ({pct:+.1f}%) → {direction}")
        
        print()

    mt5.shutdown()


if __name__ == "__main__":
    main()
