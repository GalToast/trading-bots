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

    print(f"🚀 WICK PHYSICS AUDIT: Snaring the Flash-Crashes on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # We define a 'Wick' as a 1-minute drop where Low is significantly below Open/Close
    # And 'Recovery' as price returning to Mid within the same bar or the next bar.
    
    wick_thresholds = [1.0, 2.0, 3.0, 5.0] # % below Open
    
    print(f"{'Drop Thresh':15s} | {'Total Ops':10s} | {'Avg Recovery':15s} | {'Net Profit Est'}")
    print("-" * 65)
    
    for thresh in wick_thresholds:
        ops = 0
        total_recovery = 0.0
        net_profit = 0.0
        
        for i in range(len(m1_candles)):
            c = m1_candles[i]
            o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            
            # Did price wick below Open by more than thresh%?
            wick_bottom = o * (1 - thresh / 100.0)
            if l <= wick_bottom:
                # We assume we place a Limit Buy at exactly wick_bottom
                # Did it recover?
                # Recovery target = Open (Mean Reversion)
                exit_p = o
                
                # We check if Close or next High reaches Open
                recovered = False
                if cl >= exit_p: recovered = True
                elif i + 1 < len(m1_candles):
                    if float(m1_candles[i+1]["high"]) >= exit_p: recovered = True
                
                if recovered:
                    ops += 1
                    # Profit = (Exit - Entry) - Fees (80bps)
                    pnl_pct = (exit_p - wick_bottom) / wick_bottom * 100 - 0.80
                    net_profit += pnl_pct
                    total_recovery += pnl_pct
        
        avg_rec = total_recovery / ops if ops > 0 else 0
        print(f"{thresh:4.1f}% below Open | {ops:10d} | {avg_rec:14.2f}% | +{net_profit:8.2f}%")

if __name__ == "__main__":
    main()
