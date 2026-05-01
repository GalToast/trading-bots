import json
import time
import sys
import os
import math
import random
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "IOTX-USD"

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
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 DEEP-LIQUIDITY GOBBLIN: The Final Stand for the Grinder on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    FEE_RATE = 0.0025 # 25bps
    
    # Logic:
    # We place a Limit Buy at 0.5% BELOW the current close.
    # We exit at 1.0% ABOVE our entry (Maker Sell).
    
    for offset in [0.005, 0.01]: # 0.5% or 1.0% below current price
        cash = 1000.0
        closes = 0
        wins = 0
        
        for i in range(1, len(m1_candles)):
            c = m1_candles[i]
            prev = m1_candles[i-1]
            pc = float(prev["close"])
            l = float(c["low"]); h = float(c["high"])
            
            # 1. Entry Check: Did price drop 0.5% to hit our deep bid?
            ep = pc * (1 - offset)
            if l <= ep:
                # FILLED at deep liquidity
                # Target = ep + 1.5% (clears fees + 1% net)
                target = ep * 1.015
                
                # Check next 60 bars for recovery
                success = False
                for j in range(1, 61):
                    if i + j < len(m1_candles):
                        nc = m1_candles[i+j]
                        if float(nc["high"]) >= target:
                            success = True; break
                        if float(nc["low"]) < ep * 0.97: # 3% Stop
                            break
                
                if success:
                    # Win: 1.5% gross - 0.5% fees = 1.0% net
                    cash += 1.0 # 1% of $100
                    wins += 1
                else:
                    # Loss: -3% gross - 0.8% fees = -3.8% net
                    cash -= 3.8
                closes += 1

        net = cash - 1000.0
        wr = wins / max(1, closes) * 100
        print(f"Offset={offset*100:.2f}% | Net=${net:8.2f}% | Closes={closes:4d} | WR={wr:4.1f}%")

if __name__ == "__main__":
    main()
