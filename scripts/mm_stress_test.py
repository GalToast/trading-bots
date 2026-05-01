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
    start = now - 7 * 24 * 3600 # 7 days for better stats

    print(f"🚀 STRESS TESTING MARKET MAKING ON {PRODUCT} (7 Days)...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # Stress test parameters
    FEE_RATE = 0.0040
    
    for fill_prob in [1.0, 0.5, 0.25]:
        for min_spread in [0.85, 1.0, 1.2]:
            cash = 1000.0
            inventory = 0.0
            entry_p = 0.0
            closes = 0
            wins = 0
            losses = 0
            
            for c in m1_candles:
                h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                o = float(c["open"])
                
                # 1. Exit Check (Did we sell?)
                if inventory > 0:
                    # We are at the Best Ask. To get filled, price must tick to or above Ask.
                    # We estimate Ask as entry_p * (1 + spread)
                    # For this test, we assume a fixed exit target at entry_p + fees + small profit
                    target = entry_p * (1 + min_spread / 100.0)
                    
                    if h >= target:
                        # Probabilistic fill (Competition stress)
                        import random
                        if random.random() <= fill_prob:
                            pnl = (target - entry_p) / entry_p * 10.0 - (2 * 10.0 * FEE_RATE)
                            cash += 10.0 + pnl
                            closes += 1; wins += 1; inventory = 0.0
                    
                    # 2. Stop Loss Check (Toxic Flow)
                    elif l <= entry_p * 0.98:
                        exit_p = entry_p * 0.98
                        pnl = (exit_p - entry_p) / entry_p * 10.0 - (10.0 * FEE_RATE) - (exit_p * (10.0/entry_p) * 0.0060)
                        cash += 10.0 + pnl # wait, units check
                        closes += 1; losses += 1; inventory = 0.0

                # 2. Entry Check (Did we buy?)
                if inventory == 0 and cash >= 10.0:
                    # To buy at Best Bid, price must tick to or below Bid.
                    # We use 'low' as proxy. 
                    # If we buy at open, we are 'Maker' only if price stays at or above our bid?
                    # No, we assume we place bid at Open - epsilon.
                    # Simplified: if low < open, we assume we filled at open as Maker.
                    if l < o:
                        import random
                        if random.random() <= fill_prob:
                            inventory = 10.0 / o
                            entry_p = o
                            cash -= 10.0

            net = cash - 1000.0
            wr = wins / max(1, closes) * 100
            print(f"Prob={fill_prob:4.2f} | Spread={min_spread:4.2f}% | Net=${net:7.2f} | Closes={closes:4d} | WR={wr:4.1f}%")

if __name__ == "__main__":
    main()
