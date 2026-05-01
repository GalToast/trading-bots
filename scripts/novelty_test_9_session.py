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

    print(f"🚀 NOVELTY TEST #9: SESSION TRANSITION VOLATILITY on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # Hypothesis: The exact 1-minute bar after a 'Death Zone' (e.g., 12:00 UTC) ends 
    # has a statistically higher probability of an explosive reversal.
    
    death_zones_end = [13, 20, 7, 1] # UTC hours when Death Zones end
    
    events = 0
    up_moves = 0
    total_ret = 0.0
    
    for c in m1_candles:
        ts = int(c["start"])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        
        if dt.hour in death_zones_end and dt.minute == 0:
            events += 1
            ret = (float(c["close"]) - float(c["open"])) / float(c["open"]) * 100
            total_ret += ret
            if ret > 0:
                up_moves += 1

    print("\n--- RESULTS ---")
    print(f"Total Session Transitions observed: {events}")
    print(f"Positive Reversals at T+0: {up_moves} ({up_moves/max(1, events)*100:.1f}%)")
    print(f"Average T+0 Return: {total_ret/max(1, events):.4f}%")

if __name__ == "__main__":
    main()
