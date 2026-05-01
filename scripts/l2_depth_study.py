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
    print(f"Starting L2 Depth Study for {PRODUCT}...")
    
    try:
        while True:
            # 1. Fetch Order Book L2 (if available in client, else best_bid_ask)
            # Coinbase Advanced 'best_bid_ask' is L1. 
            # Full L2 requires a different endpoint or websocket.
            # Let's check if the client has a full book method.
            try:
                # Get Product Book (Level 2)
                # Note: Coinbase API v3 'get_product_book' provides L2 depth.
                resp = client._request("GET", f"/api/v3/brokerage/product_book?product_id={PRODUCT}&limit=20")
                book = resp.get("pricebook", {})
                
                bids = book.get("bids", [])
                asks = book.get("asks", [])
                
                # Calculate Cumulative Depth within 1% of mid-price
                if bids and asks:
                    best_bid = float(bids[0]["price"])
                    best_ask = float(asks[0]["price"])
                    mid = (best_bid + best_ask) / 2
                    
                    depth_limit_bid = mid * 0.99
                    depth_limit_ask = mid * 1.01
                    
                    cum_bid_size = sum(float(b["size"]) for b in bids if float(b["price"]) >= depth_limit_bid)
                    cum_ask_size = sum(float(a["size"]) for a in asks if float(a["price"]) <= depth_limit_ask)
                    
                    imbalance = (cum_bid_size - cum_ask_size) / (cum_bid_size + cum_ask_size) if (cum_bid_size + cum_ask_size) > 0 else 0
                    
                    print(f"[{datetime.now(timezone.utc).isoformat()}] MID={mid:.4f} | CUM_BID={cum_bid_size:.1f} | CUM_ASK={cum_ask_size:.1f} | IMB={imbalance:.2f}")
            except Exception as e:
                print(f"L2 Error: {e}")

            time.sleep(10)
            
    except KeyboardInterrupt:
        print("L2 Study stopped.")

if __name__ == "__main__":
    main()
