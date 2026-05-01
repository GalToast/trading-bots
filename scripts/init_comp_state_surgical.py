import sys
from pathlib import Path
import json
import time
from dataclasses import asdict

# Add scripts to path for core imports
sys.path.append(str(Path(__file__).resolve().parent))
from tick_penetration_lattice_core import TickStatefulRearmEngine, RawConfig, REARM_VARIANTS, TickEngineState

def main():
    symbol = "COMP-USD"
    timeframe = "M5"
    step = 0.05
    max_open = 12
    variant_name = "rearm_lvl2_exc1"
    variant = REARM_VARIANTS[variant_name]
    
    # Manually construct a Mock symbol_info since it's Coinbase only
    class MockSymbolInfo:
        def __init__(self):
            self.name = "COMP-USD"
            self.trade_tick_value = 1.0
            self.trade_tick_size = 0.01
            self.point = 0.01
            self.spread = 1
            self.digits = 2
            
    symbol_info = MockSymbolInfo()
    cfg = RawConfig(step_pips=step / 0.01, max_open_per_side=max_open, close_mode="two_level")
    
    engine = TickStatefulRearmEngine(
        symbol,
        cfg,
        symbol_info,
        timeframe_name=timeframe,
        variant=variant,
        close_alpha=1.0,
        momentum_gate=True,
        cooldown_bars=0,
        sell_gap=1,
        buy_gap=1,
        max_floating_loss_usd=-15.0,
        max_lattice_window_bars=240,
        breakout_buffer_pips=0.0
    )
    
    current_price = 21.06
    engine.prime(current_price, int(time.time()))
    
    state_path = Path("reports/penetration_lattice_shadow_compusd_m5_warp_state.json")
    
    metadata = {
        "symbols": [symbol],
        "timeframe": timeframe,
        "step": step,
        "max_open_per_side": max_open,
        "raw_close_alpha": 1.0,
        "raw_rearm_variant": variant_name,
        "raw_rearm_cooldown_bars": 0,
        "raw_rearm_momentum_gate": True,
        "raw_sell_gap": 1,
        "raw_buy_gap": 1,
        "tick_native": True,
        "live_close_realism_mode": "tick_native",
        "live_open_realism_mode": "tick_native",
        "direct_live": False,
        "max_floating_loss_usd": -15.0,
        "max_lattice_window_bars": 240,
        "breakout_buffer_pips": 0.0
    }
    
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
        
    print(f"COMP state initialized (MOCK INFO) at {state_path}")

if __name__ == "__main__":
    main()
