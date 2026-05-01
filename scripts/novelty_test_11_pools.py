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
    start = now - 24 * 3600 # 24 hours

    print(f"🚀 NOVELTY TEST #11: LIQUIDITY POOL HUNTER on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # We identify 'Liquidity Pools' as price levels with the highest 
    # volume-per-price-tick density. 
    # High volume at a specific price level = a 'Cluster' of activity.
    
    price_density = {} # {rounded_price: volume}
    
    for c in m1_candles:
        cl = float(c["close"])
        v = float(c.get("volume", 0))
        # Round to 0.01 precision
        price_bin = round(cl, 2)
        price_density[price_bin] = price_density.get(price_bin, 0) + v

    # Sort by density
    pools = sorted(price_density.items(), key=lambda x: x[1], reverse=True)
    
    print("\n--- TOP 10 LIQUIDITY POOLS (24H) ---")
    for p, v in pools[:10]:
        print(f"Price: ${p:.2f} | Volume Churn: {v:10.0f}")

    # Hypothesis: If price is ABOVE a high-density pool and dropping, 
    # the pool will act as a 'Stop-Loss Magnet' then a 'Mean Reversion Floor'.
    
    # Let's test buying the most active pool of the day.
    target_pool = pools[0][0]
    print(f"\nTesting 'Pool-Magnet' Reversion at ${target_pool:.2f}...")
    
    events = 0
    success = 0
    for i in range(1, len(m1_candles)):
        c = m1_candles[i]
        l = float(c["low"]); h = float(c["high"])
        if l <= target_pool and float(m1_candles[i-1]["low"]) > target_pool:
            events += 1
            # Did it recover back ABOVE pool in next 10 bars?
            recovered = False
            for j in range(1, 11):
                if i + j < len(m1_candles):
                    if float(m1_candles[i+j]["high"]) > target_pool * 1.01: # 1% bounce
                        recovered = True
                        break
            if recovered: success += 1

    print(f"Pool Touches: {events}")
    print(f"Successful 1% Bounces from Pool: {success} ({success/max(1, events)*100:.1f}%)")

if __name__ == "__main__":
    main()
