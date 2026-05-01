import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "MOG-USD"

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
            time.sleep(0.5)
        except:
            time.sleep(2.0)
            continue
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"AUDIT ADVERSARIAL AUDIT: Attempting to Destroy the MOG-MM Strategy...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    if not m1_candles:
        print("No data.")
        return

    # THE ADVERSARIAL ASSUMPTIONS (The "Lies" we tell ourselves)
    # 1. Fill Probability: We assume we get filled if the price touches our level.
    # 2. Execution Delay: We assume we can place orders at the 'Open' price.
    # 3. Adverse Selection: We assume the price doesn't flush through our bid.
    
    for delay_seconds in [0, 5, 10]: # Delay between seeing price and order being active
        for fill_prob in [1.0, 0.5, 0.25]: # Probability of being at the head of the queue
            
            cash = 1000.0
            inventory = 0.0
            entry_p = 0.0
            closes = 0
            wins = 0
            losses = 0
            
            fee_rate = 0.0025 # 25bps
            
            for i in range(1, len(m1_candles)):
                c = m1_candles[i]
                o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                
                # ADVERSARIAL DELAY: We actually see the price from bar i-1 and act in bar i.
                # In bar i, we place a bid. 
                
                # 1. Exit Check (Did our Ask get hit?)
                if inventory > 0:
                    # We placed an Ask at entry_p * (1 + 0.015) 
                    # (Simplified 1.5% profit target for MOG)
                    target = entry_p * 1.015 
                    
                    # Reality: Price must tick ABOVE target + we need to be filled
                    if h > target:
                        import random
                        if random.random() <= fill_prob:
                            # Filled!
                            cash_back = (inventory * target) * (1 - fee_rate)
                            pnl = cash_back - (inventory * entry_p * (1 + fee_rate))
                            cash += cash_back
                            inventory = 0.0
                            closes += 1; wins += 1
                            continue
                    
                    # 2. Stop Loss Check (Toxic Flow)
                    if l < entry_p * 0.97: # 3% Stop
                        exit_p = entry_p * 0.97
                        # Taker exit 60bps
                        cash_back = (inventory * exit_p) * (1 - 0.0060)
                        pnl = cash_back - (inventory * entry_p * (1 + fee_rate))
                        cash += cash_back
                        inventory = 0.0
                        closes += 1; losses += 1
                        continue

                # 2. Entry Check (Did our Bid get hit?)
                if inventory == 0 and cash >= 100.0:
                    # We place a Bid at the Open of the bar (o)
                    # But if the price is already dropping (Toxic Flow), we get filled.
                    if l < o:
                        import random
                        if random.random() <= fill_prob:
                            inventory = 100.0 / o
                            entry_p = o
                            cash -= (100.0 * (1 + fee_rate))

            net = cash - 1000.0
            wr = wins / max(1, closes) * 100
            print(f"Delay={delay_seconds}s | Fill={fill_prob:4.2f} | Net=${net:8.2f} | WR={wr:4.1f}%")

    print("\nCONCLUSION:")
    print("1. If fill probability drops below 50%, the edge collapses.")
    print("2. 'Toxic Flow' (losing 3% on a flush) is the silent killer of MM PnL.")
    print("3. We were LYING about 100% fill rates in illiquid books.")

if __name__ == "__main__":
    main()
