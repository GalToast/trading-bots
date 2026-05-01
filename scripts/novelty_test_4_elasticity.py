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

    print(f"🚀 NOVELTY TEST #4: SPREAD ELASTICITY on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # Hypothesis: When 1-minute High-Low (Range) expands beyond 2%, 
    # it ALMOST ALWAYS contracts back to < 1% in the next 3 bars.
    # We can trade the 'Volatility Mean Reversion'.
    
    events = 0
    success = 0
    
    for i in range(len(m1_candles) - 3):
        c = m1_candles[i]
        range_pct = (float(c["high"]) - float(c["low"])) / float(c["low"]) * 100
        
        if range_pct >= 2.0:
            events += 1
            # Check next 3 bars
            contracted = False
            for j in range(1, 4):
                next_c = m1_candles[i+j]
                next_range = (float(next_c["high"]) - float(next_c["low"])) / float(next_c["low"]) * 100
                if next_range <= 1.0:
                    contracted = True
                    break
            if contracted:
                success += 1

    print("\n--- RESULTS ---")
    print(f"Total Volatility Explosions (>2% range): {events}")
    print(f"Successful Contractions (<1% range within 3 bars): {success} ({success/max(1, events)*100:.1f}%)")

if __name__ == "__main__":
    main()
