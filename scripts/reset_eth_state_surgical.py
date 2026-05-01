import MetaTrader5 as mt5
import json
from pathlib import Path
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))
from tick_penetration_lattice_core import engine_from_args

def main():
    if not mt5.initialize():
        print("MT5 initialize failed")
        return

    symbol = "ETHUSD"
    timeframe = "M5"
    step = 3.0
    max_open = 12
    variant = "rearm_lvl2_exc1"
    
    engine = engine_from_args(
        symbol=symbol,
        timeframe_name=timeframe,
        step=step,
        max_open_per_side=max_open,
        variant_name=variant,
        momentum_gate=False,
        cooldown_bars=0,
        sell_gap=1,
        buy_gap=1
    )
    
    # Get current price
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print(f"Could not get tick for {symbol}")
        mt5.shutdown()
        return
        
    mid = (tick.bid + tick.ask) / 2.0
    print(f"Resetting {symbol} at anchor {mid:.2f}")
    
    engine.prime(mid, int(tick.time))
    
    state_path = Path("reports/penetration_lattice_shadow_ethusd_m5_warp_state.json")
    metadata = {
        "symbols": [symbol],
        "timeframe": timeframe,
        "step": step,
        "max_open_per_side": max_open,
        "raw_close_alpha": 1.0,
        "raw_rearm_variant": variant,
        "raw_rearm_cooldown_bars": 0,
        "raw_rearm_momentum_gate": False,
        "raw_sell_gap": 1,
        "raw_buy_gap": 1,
        "tick_native": True,
        "live_close_realism_mode": "tick_native",
        "live_open_realism_mode": "tick_native",
        "direct_live": False
    }
    
    # Use the same structure as the existing state file
    with open(state_path, "w") as f:
        json.dump({
            "metadata": metadata,
            "runner": {
                "heartbeat_at": None,
                "last_successful_run_at": None,
                "consecutive_exceptions": 0,
                "pid": 0,
                "script": "live_penetration_lattice_tick_crypto_shadow.py"
            },
            "symbols": {
                symbol: engine.snapshot()
            },
            "updated_at": None
        }, f, indent=2)
        
    print(f"State reset to {state_path}")
    mt5.shutdown()

if __name__ == "__main__":
    main()
