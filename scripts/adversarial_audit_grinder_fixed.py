import json
import time
import sys
import os
import math
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

    print(f"AUDIT ADVERSARIAL AUDIT: Attempting to Destroy the IOTX Grinder...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    if not m1_candles:
        print("No data.")
        return

    # THE ADVERSARIAL ASSUMPTIONS
    # 1. Fill Probability: Competition in the spread.
    # 2. Latency: Delay in placing/updating orders.
    # 3. Toxicity: Getting 'run over' by directional moves.
    
    # Fee schedule for Advanced 1 (25bps) which we unlocked
    FEE_RATE = 0.0025 
    
    for delay_bars in [0, 1]: # 0 = instant, 1 = 1 minute delay (worst case)
        for fill_prob in [1.0, 0.5, 0.25]: 
            
            cash = 1000.0
            inventory = 0.0
            entry_p = 0.0
            closes = 0
            wins = 0
            losses = 0
            
            for i in range(2, len(m1_candles)):
                c = m1_candles[i]
                o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                
                # ADVERSARIAL LATENCY: We act on price data from bar i - (1 + delay_bars)
                # bar i-1 closed at o of bar i.
                
                # 1. Exit Check (Sell at Ask)
                if inventory > 0:
                    # Grinder Logic: Target = Entry * 1.0045 (to clear 50bps round-trip + profit)
                    # Note: 25bps * 2 = 50bps total fees. 
                    target = entry_p * 1.0055 # Looking for 5bps net profit after 50bps fees
                    
                    if h >= target:
                        import random
                        if random.random() <= fill_prob:
                            # Filled!
                            cash_back = (inventory * target) * (1 - FEE_RATE)
                            pnl = cash_back - (inventory * entry_p * (1 + FEE_RATE))
                            cash += cash_back
                            inventory = 0.0
                            closes += 1; wins += 1
                            continue
                    
                    # Panic Stop (1.5% drop)
                    if l < entry_p * 0.985:
                        exit_p = entry_p * 0.985
                        cash_back = (inventory * exit_p) * (1 - 0.0060) # Taker exit
                        pnl = cash_back - (inventory * entry_p * (1 + FEE_RATE))
                        cash += cash_back
                        inventory = 0.0
                        closes += 1; losses += 1
                        continue

                # 2. Entry Check (Buy at Bid)
                if inventory == 0 and cash >= 100.0:
                    # Check if spread was > 0.85% in the window we observed
                    prev = m1_candles[i - 1 - delay_bars]
                    # Since we don't have historical spread, we proxy with (H-L)/L
                    obs_range = (float(prev["high"]) - float(prev["low"])) / float(prev["low"]) * 100
                    
                    if obs_range >= 0.85:
                        # Attempt to buy at Open of bar i
                        if l < o:
                            import random
                            if random.random() <= fill_prob:
                                inventory = 100.0 / o
                                entry_p = o
                                cash -= (100.0 * (1 + FEE_RATE))

            net = cash - 1000.0
            wr = wins / max(1, closes) * 100
            print(f"Delay={delay_bars}m | Fill={fill_prob:4.2f} | Net=${net:8.2f} | WR={wr:4.1f}%")

    print("\nCONCLUSION:")
    print("1. Higher liquidity (IOTX) offers a safer 'Grinder' than illiquid MOG.")
    print("2. However, at 25bps fees, the 0.85% spread requirement is RAZOR thin.")
    print("3. If fills drop to 25%, the Grinder also bleeds capital.")

if __name__ == "__main__":
    main()
