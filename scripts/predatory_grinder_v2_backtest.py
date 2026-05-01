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

    print(f"🚀 PREDATORY GRINDER V2: The Resurrection of the MM Edge on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    if not m1_candles:
        print("No data.")
        return

    # FEAR RATE: 25bps (Current Armor)
    FEE_RATE = 0.0025
    
    for fill_prob in [1.0, 0.5]:
        for rule in ["Blind MM", "Wick-Physics MM (Predatory)"]:
            
            cash = 1000.0
            inventory = 0.0
            entry_p = 0.0
            closes = 0
            wins = 0
            losses = 0
            
            for i in range(2, len(m1_candles)):
                c = m1_candles[i]
                o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                
                # 1. Exit Logic (Maker Sell)
                if inventory > 0:
                    # Greedier Target: 1.5% profit
                    target = entry_p * 1.015
                    
                    if h >= target:
                        if random.random() <= fill_prob:
                            total_returned = (inventory * target) * (1 - FEE_RATE)
                            pnl = total_returned - (inventory * entry_p * (1 + FEE_RATE))
                            cash += total_returned
                            inventory = 0.0
                            closes += 1; wins += 1
                            continue
                    
                    # Tighter Stop: 1.0% (Cut Toxic Flow early)
                    if l < entry_p * 0.99:
                        exit_p = entry_p * 0.99
                        total_returned = (inventory * exit_p) * (1 - 0.0060) # Taker
                        pnl = total_returned - (inventory * entry_p * (1 + FEE_RATE))
                        cash += total_returned
                        inventory = 0.0
                        closes += 1; losses += 1
                        continue

                # 2. Entry Logic (Maker Buy)
                if inventory == 0 and cash >= 100.0:
                    if rule == "Blind MM":
                        # Assumption: Fill if price touches best bid (open)
                        if l < o:
                            if random.random() <= fill_prob:
                                inventory = 100.0 / o
                                entry_p = o
                                cash -= (100.0 * (1 + FEE_RATE))
                    else:
                        # WICK-PHYSICS (Predatory):
                        # We only place a Bid at the PREVIOUS bar's Low
                        # This ensures we only enter on a confirmed 'flush'
                        prev_l = float(m1_candles[i-1]["low"])
                        if l <= prev_l:
                            # We got filled at the wick!
                            if random.random() <= fill_prob:
                                inventory = 100.0 / prev_l
                                entry_p = prev_l
                                cash -= (100.0 * (1 + FEE_RATE))

            net = cash - 1000.0
            wr = wins / max(1, closes) * 100
            print(f"Prob={fill_prob:4.2f} | Rule={rule:25s} | Net=${net:8.2f} | WR={wr:4.1f}%")

if __name__ == "__main__":
    main()
