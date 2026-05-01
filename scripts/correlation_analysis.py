import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCTS = [
    "BTC-USD", "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
    "IRYS-USD", "CFG-USD", "DASH-USD", "FARTCOIN-USD",
]

def fetch_candles(client, pid, start, end):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="FIVE_MINUTE")
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
    start = now - 72 * 3600

    print("Fetching 72h data for Correlation analysis...")
    product_returns = {}
    for pid in PRODUCTS:
        cands = fetch_candles(client, pid, start, now)
        rets = []
        for i in range(1, len(cands)):
            rets.append((float(cands[i]["close"]) - float(cands[i-1]["close"])) / float(cands[i-1]["close"]))
        product_returns[pid] = rets

    # Align returns
    min_len = min(len(r) for r in product_returns.values())
    for pid in product_returns:
        product_returns[pid] = product_returns[pid][-min_len:]

    print("\n--- CROSS-CORRELATION (Lag 0) ---")
    for i in range(len(PRODUCTS)):
        for j in range(i+1, len(PRODUCTS)):
            p1 = PRODUCTS[i]; p2 = PRODUCTS[j]
            r1 = product_returns[p1]; r2 = product_returns[p2]
            
            # Simple Pearson
            mean1 = sum(r1)/len(r1); mean2 = sum(r2)/len(r2)
            num = sum((r1[k]-mean1)*(r2[k]-mean2) for k in range(len(r1)))
            den = math.sqrt(sum((r1[k]-mean1)**2 for k in range(len(r1))) * sum((r2[k]-mean2)**2 for k in range(len(r1))))
            corr = num/den if den > 0 else 0
            if corr > 0.6:
                print(f"{p1} <-> {p2}: {corr:.2f}")

    print("\n--- LEAD-LAG CORRELATION (Lag 1) ---")
    for i in range(len(PRODUCTS)):
        for j in range(len(PRODUCTS)):
            if i == j: continue
            p1 = PRODUCTS[i]; p2 = PRODUCTS[j]
            r1 = product_returns[p1][:-1] # Lead
            r2 = product_returns[p2][1:]  # Lag
            
            mean1 = sum(r1)/len(r1); mean2 = sum(r2)/len(r2)
            num = sum((r1[k]-mean1)*(r2[k]-mean2) for k in range(len(r1)))
            den = math.sqrt(sum((r1[k]-mean1)**2 for k in range(len(r1))) * sum((r2[k]-mean2)**2 for k in range(len(r1))))
            corr = num/den if den > 0 else 0
            if corr > 0.3:
                print(f"{p1} (Lead) -> {p2} (Lag): {corr:.2f}")

if __name__ == "__main__":
    main()
