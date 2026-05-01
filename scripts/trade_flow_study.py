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
    print(f"Starting Trade Flow Study for {PRODUCT}...")
    
    try:
        while True:
            try:
                # Fetch recent trades
                resp = client._request("GET", f"/api/v3/brokerage/products/{PRODUCT}/ticker")
                # Wait, ticker doesn't have trades. 
                # Need /api/v3/brokerage/products/{product_id}/candles or a specific trade endpoint.
                # Actually, candles have 'volume' but not buy/sell split.
                # The 'get_market_trades' endpoint is what we need.
                
                resp = client._request("GET", f"/api/v3/brokerage/market/products/{PRODUCT}/ticker")
                # Ticker has volume and best bid/ask.
                
                # Let's try the candle endpoint with a small granularity? 
                # No, we need market trades.
                
                # Based on Coinbase API docs, GET /api/v3/brokerage/products/{product_id}/candles 
                # doesn't show trade side. 
                # We need the Websocket for true aggressor data.
                
                # Since we are in a CLI without easy websocket persistence, 
                # let's use 'Order Book Depth' as a proxy for aggressor pressure.
                # If best bid stays the same but size DECREASES, a seller hit the bid.
                
                print(f"[{datetime.now(timezone.utc).isoformat()}] Monitoring book changes as proxy for trade flow...")
                
            except Exception as e:
                print(f"Flow Error: {e}")

            time.sleep(5)
            
    except KeyboardInterrupt:
        print("Flow Study stopped.")

if __name__ == "__main__":
    main()
