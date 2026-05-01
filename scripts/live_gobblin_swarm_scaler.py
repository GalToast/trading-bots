import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "swarm_scaler_state.json"

# Top 5 High-Spread Coins
PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "IOTX-USD", "IRYS-USD"]

def main():
    client = CoinbaseAdvancedClient()
    print("🦍 GOBBLIN SWARM SCALER: Blitzing the $1M Volume Tier...")
    
    cash = 288.0 # Shared bankroll from @main
    inventory = {p: 0.0 for p in PRODUCTS}
    entry_prices = {p: 0.0 for p in PRODUCTS}
    
    realized_net = 0.0
    total_volume = 0.0
    
    try:
        while True:
            for pid in PRODUCTS:
                try:
                    resp = client.best_bid_ask([pid])
                    book = resp["pricebooks"][0]
                    bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"])
                    spread = (ask - bid) / bid * 100
                    
                    # 1. Management
                    if inventory[pid] > 0:
                        if ask > entry_prices[pid] * 1.0045:
                            exit_p = ask; units = inventory[pid]
                            pnl = (exit_p - entry_prices[pid]) * units - (units * entry_prices[pid] * 0.0040) - (units * exit_p * 0.0040)
                            cash += (units * exit_p) - (units * exit_p * 0.0040)
                            realized_net += pnl; total_volume += (units * entry_prices[pid]) + (units * exit_p)
                            inventory[pid] = 0.0
                            print(f"[{datetime.now(timezone.utc).isoformat()}] GOBBLED {pid} (Net=+${pnl:.4f})")
                        elif bid < entry_prices[pid] * 0.98: # Panic
                            exit_p = bid; units = inventory[pid]
                            pnl = (exit_p - entry_prices[pid]) * units - (units * entry_prices[pid] * 0.0040) - (units * exit_p * 0.0060)
                            cash += (units * exit_p) - (units * exit_p * 0.0060)
                            realized_net += pnl; total_volume += (units * entry_prices[pid]) + (units * exit_p)
                            inventory[pid] = 0.0
                    
                    # 2. Deployment (5 concurrent slots)
                    if inventory[pid] == 0 and cash >= 50.0 and spread > 0.85:
                        quote = 50.0 # Aggressive scaling
                        buy_cost = quote + (quote * 0.0040)
                        if cash >= buy_cost:
                            cash -= buy_cost
                            inventory[pid] = quote / bid
                            entry_prices[pid] = bid
                            
                    time.sleep(0.2)
                except: pass
            
            # Save state
            payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "cash": round(cash, 4), "net": round(realized_net, 4), "vol": round(total_volume, 4)}
            STATE_PATH.write_text(json.dumps(payload, indent=2))
            
            print(f"  HB cash=${cash:.2f} net=${realized_net:.2f} vol=${total_volume:.2f}", end="\r")
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\nScaler stopped.")

if __name__ == "__main__":
    main()
