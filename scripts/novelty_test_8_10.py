import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"

def main():
    client = CoinbaseAdvancedClient()
    print(f"🚀 NOVELTY TEST #8 & #10: ICEBERG GULP & DUST-LAYER GRANULARITY on {PRODUCT}...")
    
    last_bid_size = 0.0
    last_ask_size = 0.0
    
    start_time = time.time()
    
    try:
        while time.time() - start_time < 300: # 5 minute live sample
            try:
                resp = client.best_bid_ask([PRODUCT])
                book = resp["pricebooks"][0]
                bid_s = float(book["bids"][0]["size"])
                ask_s = float(book["asks"][0]["size"])
                
                if last_bid_size > 0:
                    bid_delta = last_bid_size - bid_s
                    ask_delta = last_ask_size - ask_s
                    
                    # 1. TEST #10: Granularity (Dust Layer)
                    # If delta is very small (< 10 units) repeatedly, it's a dust layer.
                    if 0 < bid_delta < 10:
                        print(f"[{datetime.now(timezone.utc).isoformat()}] 🌫️ DUST EATEN: Bid decreased by {bid_delta:.2f}")
                    
                    # 2. TEST #8: Iceberg Gulp
                    # If price hasn't moved but wall reloads significantly
                    # (Hard to detect in 2s ticks without full trade feed, but let's look for 'Gulp')
                    # A 'Gulp' is when a large chunk is eaten but the price holds.
                    if bid_delta > 1000:
                         print(f"[{datetime.now(timezone.utc).isoformat()}] 🐋 GULP: Bid wall eaten for {bid_delta:.2f} units!")

                last_bid_size = bid_s
                last_ask_size = ask_s
                
            except: pass
            time.sleep(1) # Faster sampling for granularity
            
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
