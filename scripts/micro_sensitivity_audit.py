import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

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
            continue
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600 # 72 hours

    print("🚀 MICRO-AUDIT: Testing IOTX/BAL Sensitivity to BTC Lead...")
    
    btc_m1 = fetch_candles(client, "BTC-USD", start, now, "ONE_MINUTE")
    iotx_m1 = fetch_candles(client, "IOTX-USD", start, now, "ONE_MINUTE")
    bal_m1 = fetch_candles(client, "BAL-USD", start, now, "ONE_MINUTE")
    
    btc_lookup = {int(c["start"]): c for c in btc_m1}
    
    for product, candles in [("IOTX-USD", iotx_m1), ("BAL-USD", bal_m1)]:
        print(f"\n--- {product} SENSITIVITY ---")
        
        # Test various BTC thresholds
        for thresh in [0.0005, 0.0010, 0.0020]: # 0.05%, 0.1%, 0.2%
            events = 0
            followed_by_green = 0
            total_ret = 0.0
            
            for i in range(1, len(candles)):
                ts = int(candles[i]["start"])
                if ts in btc_lookup:
                    bc = btc_lookup[ts]
                    # BTC Return in the SAME minute
                    btc_ret = (float(bc["close"]) - float(bc["open"])) / float(bc["open"])
                    
                    if btc_ret >= thresh:
                        events += 1
                        # Did the microcap also close green?
                        cl = float(candles[i]["close"])
                        op = float(candles[i]["open"])
                        if cl > op:
                            followed_by_green += 1
                        total_ret += (cl - op) / op
            
            wr = followed_by_green / max(1, events) * 100
            avg_ret = total_ret / max(1, events) * 100
            print(f"BTC_Lead > {thresh*100:.2f}% | Events={events:3d} | WR={wr:4.1f}% | Avg_Ret={avg_ret:5.3f}%")

if __name__ == "__main__":
    main()
