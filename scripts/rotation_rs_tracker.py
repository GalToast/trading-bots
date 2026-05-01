#!/usr/bin/env python3
"""Track CFG/RAVE rotation RS trajectory — is it mean-reverting or heading to timeout?

Checks:
1. Current RS value vs entry RS (-12.04%)
2. RS trajectory over last N cycles (trend direction)
3. Estimated time to exit (mean-reversion vs timeout)
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from rotation_lattice_shadow import (
    fetch_candles, COINS, WINDOW, EXIT_THRESHOLD
)
from itertools import combinations

ALL_PAIRS = {f"{a}/{b}": (a, b) for a, b in combinations(COINS, 2)}

def compute_rs(candles_a, candles_b, window=WINDOW):
    ts_a = {int(c["start"]): float(c["close"]) for c in candles_a}
    ts_b = {int(c["start"]): float(c["close"]) for c in candles_b}
    common_ts = sorted(set(ts_a.keys()) & set(ts_b.keys()))
    
    if len(common_ts) < window + 1:
        return None
    
    i = len(common_ts) - 1
    ts_now = common_ts[i]
    ts_then = common_ts[i - window]
    
    ret_a = (ts_a[ts_now] - ts_a[ts_then]) / ts_a[ts_then]
    ret_b = (ts_b[ts_now] - ts_b[ts_then]) / ts_b[ts_then]
    
    return ret_a - ret_b

def main():
    # Load state
    state_path = ROOT / "reports" / "rotation_shadow_state.json"
    with open(state_path) as f:
        state = json.load(f)
    
    cfg_rave = state["pairs"]["CFG/RAVE"]
    if not cfg_rave["position"]:
        print("CFG/RAVE: FLAT (no active position)")
        return
    
    entry_rs = cfg_rave["position"]["entry_rs"]
    hold = cfg_rave["position"]["hold"]
    entry_price = cfg_rave["position"]["entry_price_a"]
    
    print(f"{'='*60}")
    print(f"CFG/RAVE Rotation Position Tracker")
    print(f"{'='*60}")
    print(f"  Hold: {hold}/96 bars ({hold/96*100:.1f}%)")
    print(f"  Entry RS: {entry_rs:+.4f} ({entry_rs*100:+.2f}%)")
    print(f"  Entry price (CFG): ${entry_price:.4f}")
    print(f"  Exit threshold: RS > {-EXIT_THRESHOLD*100:.1f}% (mean-reversion)")
    print(f"  Timeout at: hold=96")
    print(f"  Bars remaining: {96 - hold}")
    print()
    
    # Fetch current RS
    try:
        from rotation_lattice_shadow import fetch_candles
        now = int(__import__("time").time())
        start = now - 520 * 60
        
        client_json = ROOT / "scripts" / "coinbase_advanced_client.json"
        if not client_json.exists():
            # Try to import from the runner's client
            pass
        
        all_candles = {}
        for coin in COINS:
            try:
                candles = fetch_candles(None, coin, start, now, "FIVE_MINUTE")
                all_candles[coin] = candles
            except Exception as e:
                print(f"  ⚠ Cannot fetch candles for {coin}: {e}")
                print(f"  (Need running API client to compute current RS)")
                return
        
        current_rs = compute_rs(all_candles["CFG-USD"], all_candles["RAVE-USD"])
        if current_rs is None:
            print("  ⚠ Cannot compute RS (insufficient data)")
            return
        
        # Get current price
        ts_cfg = {int(c["start"]): float(c["close"]) for c in all_candles["CFG-USD"]}
        current_price = list(ts_cfg.values())[-1]
        
        print(f"  Current RS: {current_rs:+.4f} ({current_rs*100:+.2f}%)")
        print(f"  Current price (CFG): ${current_price:.4f}")
        print(f"  RS change since entry: {current_rs - entry_rs:+.4f}")
        print(f"  Distance to exit threshold: {current_rs - (-EXIT_THRESHOLD):+.4f}")
        print()
        
        # Trajectory analysis
        rs_change = current_rs - entry_rs
        bars_elapsed = hold
        if bars_elapsed > 0:
            rs_rate_per_bar = rs_change / bars_elapsed
            bars_to_exit = (-EXIT_THRESHOLD - current_rs) / rs_rate_per_bar if rs_rate_per_bar != 0 else float('inf')
            
            print(f"  RS rate: {rs_rate_per_bar:+.6f}/bar")
            if rs_rate_per_bar > 0:
                print(f"  → RS is MEAN-REVERTING (moving toward exit)")
                if bars_to_exit < 96 - hold:
                    print(f"  → Estimated exit in {bars_to_exit:.0f} bars (mean-reversion)")
                    print(f"  → Timeout in {96 - hold} bars")
                    print(f"  → **Most likely exit: MEAN-REVERSION**")
                else:
                    print(f"  → Mean-reversion exit in {bars_to_exit:.0f} bars (beyond timeout)")
                    print(f"  → **Most likely exit: TIMEOUT at bar 96**")
            else:
                print(f"  → RS is DIVERGING (moving away from exit)")
                print(f"  → **Most likely exit: TIMEOUT at bar 96**")
        
        # PnL estimate
        from rotation_lattice_shadow import POSITION_SIZE, FEE_RATE, SPREAD_ESTIMATE
        raw_return = (current_price - entry_price) / entry_price
        net_return = raw_return - 2 * FEE_RATE - SPREAD_ESTIMATE
        estimated_pnl = POSITION_SIZE * net_return
        
        print(f"\n  Estimated PnL if closed now: ${estimated_pnl:+.2f}")
        print(f"    (raw return: {raw_return*100:+.2f}%, net after costs: {net_return*100:+.2f}%)")
        
    except ImportError as e:
        print(f"  ⚠ Cannot import rotation module: {e}")
    except Exception as e:
        print(f"  ⚠ Error: {e}")

if __name__ == "__main__":
    main()
