import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "MOG-USD"

def main():
    client = CoinbaseAdvancedClient()
    print(f"🦍 GOBBLIN MOG-CRUSHER: Targeting 15bps Tier in 1 Hour...")
    
    cash = 200.0 # Dedicated high-risk pool
    inventory = 0.0
    entry_price = 0.0
    
    total_volume = 0.0
    
    while True:
        try:
            resp = client.best_bid_ask([PRODUCT])
            book = resp["pricebooks"][0]
            bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"])
            spread = (ask - bid) / bid * 100
            
            fee_rate = 0.0025 # 25bps
            
            if inventory > 0:
                # Target 1% profit (MOG has 7% spread, 1% is instant)
                if ask >= entry_price * 1.01:
                    exit_p = ask; units = inventory
                    cash += (units * exit_p) * (1 - fee_rate)
                    total_volume += (units * entry_price) + (units * exit_p)
                    inventory = 0.0
                    print(f"[{datetime.now(timezone.utc).isoformat()}] MOG CRUSHED! (Vol=${total_volume:,.0f})")
                elif bid < entry_price * 0.985: # Panic
                    exit_p = bid; units = inventory
                    cash += (units * exit_p) * (1 - 0.0060)
                    total_volume += (units * entry_price) + (units * exit_p)
                    inventory = 0.0
            
            if inventory == 0 and cash >= 100.0:
                if spread >= 1.5:
                    quote = 100.0
                    cash -= quote * (1 + fee_rate)
                    inventory = quote / bid
                    entry_price = bid
            
            time.sleep(1.0)
        except: time.sleep(5.0)

if __name__ == "__main__":
    main()
