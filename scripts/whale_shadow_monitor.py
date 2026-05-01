import json
import time
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]

def main():
    client = CoinbaseAdvancedClient()
    print("Starting WHALE-SHADOW Monitor (Order Size Imbalance)...")
    
    # Wall Tracking State
    wall_state = {} # {pid: {"price": ..., "side": ..., "samples": ...}}
    
    try:
        while True:
            for pid in PRODUCTS:
                try:
                    resp = client.best_bid_ask([pid])
                    book = resp["pricebooks"][0]
                    
                    best_bid = float(book["bids"][0]["price"])
                    bid_size = float(book["bids"][0]["size"])
                    
                    best_ask = float(book["asks"][0]["price"])
                    ask_size = float(book["asks"][0]["size"])
                    
                    product_info = client.get_product(pid)
                    vol_24h = float(product_info.get("volume_24h", 0))
                    avg_min_vol = vol_24h / (24 * 60)
                    
                    # 🐋 Wall Detection (> 5x avg volume)
                    whale_bid = bid_size > (5 * avg_min_vol)
                    whale_ask = ask_size > (5 * avg_min_vol)
                    
                    if whale_bid or whale_ask:
                        side = "BUY" if whale_bid else "SELL"
                        price = best_bid if whale_bid else best_ask
                        size = bid_size if whale_bid else ask_size
                        
                        # PERSISTENCE CHECK (Anti-Spoofing)
                        if pid not in wall_state or wall_state[pid]["price"] != price:
                            wall_state[pid] = {"price": price, "side": side, "samples": 1}
                        else:
                            wall_state[pid]["samples"] += 1
                        
                        samples = wall_state[pid]["samples"]
                        age_sec = samples * 10
                        
                        # Only alert if the wall has persisted for at least 30 seconds
                        if samples >= 3:
                            multiple = size / avg_min_vol
                            status = "REAL WALL" if samples >= 6 else "PERSISTING" # 60s+ is institutional
                            print(f"[{datetime.now(timezone.utc).isoformat()}] 🐋 {status} on {pid}: {side} at {price} ({multiple:.1f}x vol, age={age_sec}s)")
                        else:
                            print(f"[{datetime.now(timezone.utc).isoformat()}] ⚡ SPOOF ALERT? {pid}: {side} wall flashed at {price} (age={age_sec}s)")
                    else:
                        # Wall pulled or eaten
                        if pid in wall_state:
                            del wall_state[pid]
                            
                except: pass
            time.sleep(10)
    except KeyboardInterrupt:
        print("Whale monitor stopped.")

if __name__ == "__main__":
    main()
