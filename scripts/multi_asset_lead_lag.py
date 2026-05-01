import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Use the Top 34 Universe Map from qwen-trading
PRODUCTS = [
    "RAVE-USD", "MOG-USD", "A8-USD", "IDEX-USD", "LRDS-USD", "BAL-USD", "STRK-USD", "DRIFT-USD",
    "ALEPH-USD", "MATH-USD", "IOTX-USD", "KARRAT-USD", "BLUR-USD", "PERP-USD", "SKL-USD", "VOXEL-USD",
    "OSMO-USD", "ARPA-USD", "FIS-USD", "FORT-USD", "DOGINME-USD", "T-USD", "RARE-USD", "00-USD",
    "VELO-USD", "ALT-USD", "DEGEN-USD", "IRYS-USD", "AST-USD", "VTHO-USD", "WELL-USD", "SUKU-USD", "ACS-USD", "GMT-USD"
]

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
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600 # 72 hours

    print("Fetching 72h M1 data for Multi-Asset Lead-Lag Audit...")
    product_returns = {}
    for pid in PRODUCTS:
        print(f"  {pid}...", end="\r")
        cands = fetch_candles(client, pid, start, now)
        if not cands: continue
        rets = []
        for i in range(1, len(cands)):
            rets.append((float(cands[i]["close"]) - float(cands[i-1]["close"])) / float(cands[i-1]["close"]))
        product_returns[pid] = rets

    # Align
    min_len = min(len(r) for r in product_returns.values())
    for pid in product_returns:
        product_returns[pid] = product_returns[pid][-min_len:]

    print("\n--- DISCOVERING SHADOW LEADERS (Lag 1) ---")
    print(f"{ 'Leader':12s} | { 'Lagger':12s} | { 'Correlation':5s}")
    
    found = 0
    for lead in product_returns:
        for lag in product_returns:
            if lead == lag: continue
            
            r1 = product_returns[lead][:-1]
            r2 = product_returns[lag][1:]
            
            # Pearson
            mean1 = sum(r1)/len(r1); mean2 = sum(r2)/len(r2)
            num = sum((r1[k]-mean1)*(r2[k]-mean2) for k in range(len(r1)))
            den = math.sqrt(sum((r1[k]-mean1)**2 for k in range(len(r1))) * sum((r2[k]-mean2)**2 for k in range(len(r1))))
            corr = num/den if den > 0 else 0
            
            if corr > 0.5:
                print(f"{lead:12s} | {lag:12s} | {corr:5.2f}")
                found += 1

    if not found:
        print("No strong microcap lead-lag found in this window.")

if __name__ == "__main__":
    main()
