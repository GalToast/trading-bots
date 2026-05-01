import json
import time
import sys
import os
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

def get_kraken_btc():
    try:
        url = "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return float(data["result"]["XXBTZUSD"]["c"][0])
    except: return None

def main():
    client = CoinbaseAdvancedClient()
    log_path = os.path.join(os.path.dirname(__file__), '..', 'reports', 'kraken_coinbase_sync_raw.jsonl')
    print(f"🚀 GENERATING RAW SYNC DATA FOR @opencode-research...")
    
    start_time = time.time()
    count = 0
    
    with open(log_path, 'w') as f:
        while time.time() - start_time < 120: # 2 minute high-freq sample
            k_price = get_kraken_btc()
            ticker = client.get_product("BTC-USD")
            c_price = float(ticker.get("price", 0))
            
            if k_price and c_price:
                entry = {"ts": time.time(), "kraken": k_price, "coinbase": c_price}
                f.write(json.dumps(entry) + "\n")
                f.flush()
                count += 1
                print(f"  Logged {count} samples...", end="\r")
                
            time.sleep(1) # 1s sampling
            
    print(f"\nDone. Logged {count} samples to {log_path}")

if __name__ == "__main__":
    main()
