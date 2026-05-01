import json
import time
import sys
import os
import math
import random
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
            continue
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 MAGNETIC MARKET MAKING on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    FEE_RATE = 0.0025 # 25bps
    
    for proximity in [0.0025, 0.005]: 
        
        cash = 1000.0
        closes = 0
        wins = 0
        losses = 0
        inventory = 0.0
        entry_p = 0.0
        quote = 100.0
        
        for i in range(1, len(m1_candles)):
            if cash < quote: break
            
            c = m1_candles[i]
            o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            
            # 1. Exit Logic
            if inventory > 0:
                target = entry_p * 1.015 # 1.5% Target (Grinder Hardened)
                if h >= target:
                    # Win
                    cash_returned = (inventory * target) * (1 - FEE_RATE)
                    cash += cash_returned
                    closes += 1; wins += 1; inventory = 0.0
                    continue
                if l < entry_p * 0.985: # 1.5% SL
                    # Loss
                    exit_p = entry_p * 0.985
                    cash_returned = (inventory * exit_p) * (1 - 0.0060)
                    cash += cash_returned
                    closes += 1; losses += 1; inventory = 0.0
                    continue

            # 2. Entry Logic
            mag_level = round(o * 20) / 20.0
            if abs(o - mag_level) / mag_level <= proximity:
                limit_p = mag_level + 0.0001
                if l <= limit_p:
                    # Fill
                    buy_cost = quote * (1 + FEE_RATE)
                    cash -= buy_cost
                    inventory = quote / limit_p
                    entry_p = limit_p

        net = cash - 1000.0
        wr = wins / max(1, closes) * 100
        print(f"Prox={proximity*100:.2f}% | Net=${net:8.2f} | Closes={closes:4d} | WR={wr:4.1f}%")

if __name__ == "__main__":
    main()
