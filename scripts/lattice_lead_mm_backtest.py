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
BTC = "BTC-USD"

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

    print(f"🚀 LATTICE-LEAD MARKET MAKING on {PRODUCT}...")
    iotx_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    btc_m1 = fetch_candles(client, BTC, start, now, "ONE_MINUTE")
    
    btc_lookup = {int(c["start"]): c for c in btc_m1}
    
    FEE_RATE = 0.0025 # 25bps
    
    for lead_threshold in [0.0005, 0.0010]: # 0.05% or 0.1% lead move in BTC
        
        cash = 1000.0
        closes = 0
        wins = 0
        inventory = 0.0
        entry_p = 0.0
        
        for i in range(1, len(iotx_candles)):
            c = iotx_candles[i]
            ts = int(c["start"])
            o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            
            # 1. Exit Logic
            if inventory > 0:
                target = entry_p * 1.01 # 1% target
                if h >= target:
                    cash_back = (inventory * target) * (1 - FEE_RATE)
                    pnl = cash_back - (100.0 * (1 + FEE_RATE))
                    cash += cash_back; closes += 1; wins += 1; inventory = 0.0
                    continue
                if l < entry_p * 0.99: # 1% Stop
                    exit_p = entry_p * 0.99
                    cash_back = (inventory * exit_p) * (1 - 0.0060)
                    pnl = cash_back - (100.0 * (1 + FEE_RATE))
                    cash += cash_back; closes += 1; inventory = 0.0
                    continue

            # 2. Entry Logic: THE LATTICE FRONT-RUN
            # If BTC moved UP in the same 1-min window (proxy for lead)
            if ts in btc_lookup:
                btc_c = btc_lookup[ts]
                btc_ret = (float(btc_c["close"]) - float(btc_c["open"])) / float(btc_c["open"])
                
                if btc_ret >= lead_threshold:
                    # BTC is surging. Market makers are about to raise their bids.
                    # We 'Ghost' the bid by placing an order at the CURRENT open.
                    if l < o: # We got filled at the 'old' price
                        inventory = 100.0 / o
                        entry_p = o
                        cash -= (100.0 * (1 + FEE_RATE))

        net = cash - 1000.0
        wr = wins / max(1, closes) * 100
        print(f"BTC_Lead={lead_threshold*100:.2f}% | Net=${net:8.2f} | Closes={closes:4d} | WR={wr:4.1f}%")

if __name__ == "__main__":
    main()
