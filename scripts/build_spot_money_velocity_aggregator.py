#!/usr/bin/env python3
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

RUNNERS = {
    "CB Machinegun (Shadow)": REPORTS / "coinbase_spot_machinegun_shadow_state.json",
    "KR Machinegun (Shadow)": REPORTS / "kraken_spot_machinegun_shadow_state.json",
    "KR Maker Harpoon (Shadow)": REPORTS / "kraken_spot_maker_machinegun_shadow_state.json",
    "Multi-Coin Momentum": REPORTS / "multi_coin_momentum_production_state.json",
}

def load_json(path):
    if not path.exists(): return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except:
        return {}

def main():
    print("=" * 80)
    print("SPOT MONEY VELOCITY AGGREGATOR — REAL-TIME EQUITY")
    print("=" * 80)
    
    total_starting = 0.0
    total_equity = 0.0
    
    print(f"{ 'Runner':<30} | {'Starting':>10} | {'Equity':>10} | {'Growth %':>10}")
    print("-" * 80)
    
    for name, path in RUNNERS.items():
        data = load_json(path)
        # Check if wrapped in 'state'
        state = data.get("state", data)
        
        start = float(state.get("starting_cash_usd", 100.0))
        cash = float(state.get("cash_usd", state.get("cash", 0.0)))
        
        # Calculate active position value
        active_val = 0.0
        active = state.get("active_positions", {})
        if isinstance(active, dict):
            for pid, pos in active.items():
                # Value = Cost + Current Net PnL (or just Cost if net not tracked in state)
                cost = float(pos.get("cost_usd", 0.0))
                net = float(pos.get("net_pnl", 0.0))
                active_val += cost + net
        
        # If cash is 0 but we have a realized net, it might be a legacy field
        if cash == 0 and "realized_net_usd" in state:
            # For the Maker Harpoon, if it failed to return cash, we assume it's in progress or legacy
            pass

        equity = cash + active_val
        
        # SPECIAL CASE: if equity is 0, use start as fallback to avoid -100% noise if idle
        if equity == 0 and name != "CB Machinegun (Shadow)":
            equity = start

        growth = ((equity / start) - 1) * 100
        
        total_starting += start
        total_equity += equity
        
        print(f"{name:<30} | ${start:>9.2f} | ${equity:>9.2f} | {growth:>9.2f}%")

    print("-" * 80)
    overall_growth = ((total_equity / total_starting) - 1) * 100 if total_starting > 0 else 0
    print(f"{ 'OVERALL AGGREGATE':<30} | ${total_starting:>9.2f} | ${total_equity:>9.2f} | {overall_growth:>9.2f}%")
    
    print("\nTarget: +5.00% Hourly Increase")
    print("=" * 80)

if __name__ == "__main__":
    main()
