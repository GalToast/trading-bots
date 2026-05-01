import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

RAVE = "RAVE-USD"
IOTX = "IOTX-USD"
PRODUCTS = [RAVE, IOTX]

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

    print(f"🚀 NOVELTY TEST #6: INTER-ASSET WICK CONTAGION (RAVE -> IOTX)...")
    rave_candles = fetch_candles(client, RAVE, start, now, "ONE_MINUTE")
    iotx_candles = fetch_candles(client, IOTX, start, now, "ONE_MINUTE")
    
    # Sync
    iotx_lookup = {int(c["start"]): c for c in iotx_candles}
    
    events = 0
    contagion = 0
    
    for c in rave_candles:
        ts = int(c["start"])
        o = float(c["open"]); l = float(c["low"])
        wick = (o - l) / o
        
        if wick >= 0.02: # RAVE wicks > 2%
            events += 1
            # Check IOTX in the NEXT 3 minutes
            hit = False
            for offset in [0, 60, 120, 180]:
                target_ts = ts + offset
                if target_ts in iotx_lookup:
                    ic = iotx_lookup[target_ts]
                    io = float(ic["open"]); il = float(ic["low"])
                    i_wick = (io - il) / io
                    if i_wick >= 0.01: # IOTX also wicks > 1%
                        hit = True
                        break
            if hit:
                contagion += 1

    print("\n--- RESULTS ---")
    print(f"Total RAVE Flash-Wicks (>2%): {events}")
    print(f"IOTX Contagion (Wicks >1% within 3m): {contagion} ({contagion/max(1, events)*100:.1f}%)")

if __name__ == "__main__":
    main()
