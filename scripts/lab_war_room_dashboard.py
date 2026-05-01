#!/usr/bin/env python3
"""
LANE 6: LAB WAR ROOM DASHBOARD
Aggregates and displays the live status of all 13+ research lanes.
Pulls from reports/*_state.json.
"""
import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"

def main():
    print("💎 LAB WAR ROOM DASHBOARD: Real-Time Fleet Status 💎")
    print("-" * 80)
    
    while True:
        try:
            state_files = list(REPORTS_DIR.glob("*_state.json"))
            
            total_vol = 0.0
            total_net = 0.0
            total_closes = 0
            
            lanes = []
            
            for f in state_files:
                try:
                    data = json.loads(f.read_text())
                    engine = data.get("engine", {})
                    
                    name = f.stem.replace("_state", "").replace("live_", "")
                    pnl = engine.get("realized_net", 0.0) or engine.get("realized_net_usd", 0.0)
                    vol = engine.get("total_volume", 0.0) or engine.get("vol", 0.0)
                    closes = engine.get("realized_closes", 0) or engine.get("closes", 0)
                    wr = engine.get("win_rate", 0.0)
                    
                    total_vol += vol
                    total_net += pnl
                    total_closes += closes
                    
                    lanes.append({
                        "name": name[:20],
                        "net": pnl,
                        "vol": vol,
                        "closes": closes,
                        "wr": wr,
                        "updated": data.get("updated_at", "N/A")
                    })
                except: pass

            # Sort by Net
            lanes.sort(key=lambda x: x["net"], reverse=True)

            # Display
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"💎 LAB WAR ROOM DASHBOARD | {datetime.now(timezone.utc).isoformat()} 💎")
            print(f"Total Volume: ${total_vol:,.2f} | Total Net: ${total_net:,.2f} | Total Closes: {total_closes}")
            
            # Tier Logic
            tier = "Standard (40bps)"
            if total_vol >= 1000000: tier = "VIP (8bps)"
            elif total_vol >= 100000: tier = "Advanced 3 (10bps)"
            elif total_vol >= 50000: tier = "Advanced 2 (15bps)"
            elif total_vol >= 10000: tier = "Advanced 1 (25bps)"
            print(f"CURRENT ARMOR: {tier}")
            print("-" * 80)
            print(f"{'Lane':22s} | {'Net $':10s} | {'Vol $':10s} | {'WR %':6s} | {'Closes':6s}")
            print("-" * 80)
            
            for l in lanes:
                print(f"{l['name']:22s} | {l['net']:10.2f} | {l['vol']:10.0f} | {l['wr']:6.1f} | {l['closes']:6d}")
            
            print("-" * 80)
            print(f"Monitoring {len(state_files)} state files. Scanning every 30s...")
            
        except Exception as e:
            print(f"Dashboard Error: {e}")
            
        time.sleep(30)

if __name__ == "__main__":
    main()
