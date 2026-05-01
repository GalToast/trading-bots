import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "IOTX-USD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def main():
    client = CoinbaseAdvancedClient()
    print(f"🦍 IOTX VOLUME BLITZ: Targeting the 15bps Fee Tier...")
    
    cash = 324.0 # Shared bankroll + realized profit
    inventory = 0.0
    entry_price = 0.0
    
    realized_net = 0.0
    total_volume = 16402.0 # starting from where we left off
    
    try:
        while True:
            try:
                resp = client.best_bid_ask([PRODUCT])
                book = resp["pricebooks"][0]
                bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"])
                spread = (ask - bid) / bid * 100
                
                # Current Fee Rate (we know we are at 25bps now)
                fee_rate = 0.0025
                
                # 1. Management
                if inventory > 0:
                    # Take Profit at Ask
                    if ask > entry_price * 1.0030: # Tight target for high-speed churn
                        exit_p = ask; units = inventory
                        cash += (units * exit_p) - (units * exit_p * fee_rate)
                        pnl = (exit_p - entry_price) * units - (units * entry_price * fee_rate) - (units * exit_p * fee_rate)
                        realized_net += pnl; total_volume += (units * entry_price) + (units * exit_p)
                        inventory = 0.0
                        print(f"[{utc_now_iso()}] IOTX GOBBLED (Net=+${pnl:.4f}) | Volume: ${total_volume:.0f}")
                    # Panic
                    elif bid < entry_price * 0.985:
                        exit_p = bid; units = inventory
                        cash += (units * exit_p) - (units * exit_p * 0.0060)
                        pnl = (exit_p - entry_price) * units - (units * entry_price * fee_rate) - (units * exit_p * 0.0060)
                        realized_net += pnl; total_volume += (units * entry_price) + (units * exit_p)
                        inventory = 0.0
                
                # 2. Deployment
                if inventory == 0 and cash >= 50.0 and spread > 0.60: # Spread thresh lowered for 25bps armor
                    quote = 50.0
                    buy_cost = quote + (quote * fee_rate)
                    if cash >= buy_cost:
                        cash -= buy_cost
                        inventory = quote / bid
                        entry_price = bid
                        
                time.sleep(2.0)
            except: pass
            
            print(f"  HB cash=${cash:.2f} net=${realized_net:.2f} vol=${total_volume:.2f}", end="\r")
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\nBlitz stopped.")

if __name__ == "__main__":
    main()
