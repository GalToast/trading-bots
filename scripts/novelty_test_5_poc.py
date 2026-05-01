import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"

def fetch_candles(client, pid, start, end, granularity="ONE_MINUTE"):
    chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 NOVELTY TEST #5: VOLUME PROFILE CONSOLIDATION on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # Hypothesis: After a 10x volume spike, the price will consolidate 
    # around the POC (Price of Control) of that spike. 
    # If price drops > 2% below the POC, it's a high-probability buy.
    
    events = 0
    success = 0
    
    # Calculate rolling 60min avg volume
    for i in range(60, len(m1_candles) - 15):
        window = m1_candles[i-60:i]
        avg_vol = sum(float(c.get("volume", 0)) for c in window) / 60
        
        curr = m1_candles[i]
        curr_vol = float(curr.get("volume", 0))
        
        if curr_vol >= 10.0 * avg_vol: # 10x Spike
            events += 1
            poc = (float(curr["high"]) + float(curr["low"]) + float(curr["close"])) / 3
            
            # Check next 15 bars for a dip below POC
            snared = False
            for j in range(1, 16):
                next_c = m1_candles[i+j]
                l = float(next_c["low"]); h = float(next_c["high"])
                if l <= poc * 0.98: # 2% dip below POC
                    # Entry!
                    # Success if it returns to POC within 15 bars
                    for k in range(j+1, 16):
                        final_c = m1_candles[i+k]
                        if float(final_c["high"]) >= poc:
                            snared = True
                            break
                    break
            if snared:
                success += 1

    print("\n--- RESULTS ---")
    print(f"Total 10x Volume Spikes: {events}")
    print(f"Successful POC Mean-Reversions: {success} ({success/max(1, events)*100:.1f}%)")

if __name__ == "__main__":
    main()
