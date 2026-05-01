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
    print("🚀 NOVELTY TEST #3: CROSS-EXCHANGE LATTICE LAG (Kraken -> Coinbase)...")
    
    samples = []
    start_time = time.time()
    
    try:
        while time.time() - start_time < 300: # 5 mins
            # 1. Fetch Kraken
            k_price = get_kraken_btc()
            
            # 2. Fetch Coinbase
            ticker = client.get_product("BTC-USD")
            c_price = float(ticker.get("price", 0))
            
            if k_price and c_price:
                diff = k_price - c_price
                print(f"[{datetime.now(timezone.utc).isoformat()}] Kraken: {k_price:.2f} | Coinbase: {c_price:.2f} | Diff: {diff:.2f}", end="\r")
                samples.append({"k": k_price, "c": c_price, "ts": time.time()})
                
            time.sleep(2)
    except KeyboardInterrupt: pass

    # Analyze Lag
    print("\n\nAnalyzing lead-lag correlation...")
    # (Simplified: if Kraken moves first, does Coinbase follow in next sample?)
    if len(samples) < 5: return
    
    lead_count = 0
    total_moves = 0
    
    for i in range(1, len(samples)):
        k_move = samples[i]["k"] - samples[i-1]["k"]
        c_move_next = 0
        if i + 1 < len(samples):
            c_move_next = samples[i+1]["c"] - samples[i]["c"]
            
        if abs(k_move) > 1.0: # Significant move
            total_moves += 1
            if (k_move > 0 and c_move_next > 0) or (k_move < 0 and c_move_next < 0):
                lead_count += 1
                
    print(f"Kraken Leading Moves: {total_moves}")
    print(f"Coinbase Following within 2s: {lead_count} ({lead_count/max(1, total_moves)*100:.1f}%)")

if __name__ == "__main__":
    main()
