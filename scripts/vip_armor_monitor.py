import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Path to our state files
ROOT = Path(__file__).resolve().parent.parent
SCALER_PATH = ROOT / "reports" / "swarm_scaler_state.json"
MM_PATH = ROOT / "reports" / "spread_gobblin_mm_state.json"
FORTRESS_PATH = ROOT / "reports" / "omni_vip_fortress_state.json"

def main():
    print("💎 VIP ARMOR MONITOR: Tracking the Blitz to 8bps 💎")
    print("-" * 50)
    
    while True:
        try:
            # We aggregate volume from all our recent successful grinders
            # (In reality, we'd check the Coinbase API for actual volume)
            
            # Since we are using shadow simulators, we track the 'simulated volume credit'
            total_vol = 0.0
            total_net = 0.0
            
            for path in [SCALER_PATH, MM_PATH, FORTRESS_PATH]:
                if path.exists():
                    data = json.loads(path.read_text())
                    total_vol += data.get("vol", 0)
                    total_net += data.get("net", 0)
            
            # Current Milestone Logic
            tier = "Advanced 1 (25bps)"
            if total_vol >= 500000: tier = "VIP (8bps)"
            elif total_vol >= 100000: tier = "Advanced 3 (10bps)"
            elif total_vol >= 50000: tier = "Advanced 2 (15bps)"
            
            next_goal = 50000
            if total_vol >= 50000: next_goal = 100000
            if total_vol >= 100000: next_goal = 500000
            if total_vol >= 500000: next_goal = 1000000
            
            remaining = next_goal - total_vol
            pct = (total_vol / next_goal) * 100
            
            print(f"[{datetime.now(timezone.utc).isoformat()}]")
            print(f"  Live Volume:  ${total_vol:12,.2f}")
            print(f"  Realized Net: ${total_net:12,.2f}")
            print(f"  Current Tier: {tier}")
            print(f"  Next Armor:   ${remaining:12,.2f} remaining ({pct:.1f}% to goal)")
            print("-" * 50)
            
        except Exception as e:
            print(f"Monitor error: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    main()
