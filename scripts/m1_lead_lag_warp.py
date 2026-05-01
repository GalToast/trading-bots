import json
import time
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Use the top volume coins
PRODUCTS = ["BTC-USD", "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]

def fetch_candles(client, pid, start, end):
    chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="ONE_MINUTE")
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.5)
        except:
            cs = ce
            time.sleep(1.0)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 24 * 3600 # 24 hours of M1 data

    print("Fetching 24h M1 data for Multi-Lead-Lag Warp analysis...")
    product_returns = {}
    for pid in PRODUCTS:
        cands = fetch_candles(client, pid, start, now)
        rets = []
        for i in range(1, len(cands)):
            rets.append((float(cands[i]["close"]) - float(cands[i-1]["close"])) / float(cands[i-1]["close"]))
        product_returns[pid] = rets
        print(f"  {pid}: {len(rets)} returns")

    # Align
    min_len = min(len(r) for r in product_returns.values())
    for pid in product_returns:
        product_returns[pid] = product_returns[pid][-min_len:]

    print("\n--- M1 LEAD-LAG CORRELATION (Lag 1) ---")
    print(f"{ 'Lead Asset':12s} | { 'Lag Asset':12s} | { 'Corr':5s}")
    
    for lead in PRODUCTS:
        for lag in PRODUCTS:
            if lead == lag: continue
            
            r1 = product_returns[lead][:-1]
            r2 = product_returns[lag][1:]
            
            # Pearson
            mean1 = sum(r1)/len(r1); mean2 = sum(r2)/len(r2)
            num = sum((r1[k]-mean1)*(r2[k]-mean2) for k in range(len(r1)))
            den = math.sqrt(sum((r1[k]-mean1)**2 for k in range(len(r1))) * sum((r2[k]-mean2)**2 for k in range(len(r1))))
            corr = num/den if den > 0 else 0
            
            if corr > 0.4:
                print(f"{lead:12s} | {lag:12s} | {corr:5.2f}")

if __name__ == "__main__":
    main()
