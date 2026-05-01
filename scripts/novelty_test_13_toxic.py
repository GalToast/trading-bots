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

    print(f"🚀 NOVELTY TEST #13: TOXIC FLOW DECAY on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # Hypothesis: After a 'Toxic Event' (Volume > 5x avg AND price dropped > 1%), 
    # the market remains 'Toxic' for X minutes. Entries during this window 
    # have a lower win rate.
    
    # Calculate rolling avg volume
    vols = [float(c.get("volume", 0)) for c in m1_candles]
    
    events = 0
    
    for wait_window in [0, 5, 10, 15]:
        wins = 0
        closes = 0
        total_pnl = 0.0
        
        last_toxic_ts = 0
        
        for i in range(20, len(m1_candles) - 15):
            c = m1_candles[i]
            ts = int(c["start"])
            o = float(c["open"]); cl = float(c["close"]); v = float(c.get("volume", 0))
            
            # Update Toxicity
            avg_v = sum(vols[i-20:i]) / 20
            if v > 5.0 * avg_v and (cl - o) / o < -0.01:
                last_toxic_ts = ts
                
            # Entry Signal (RSI proxy or simple dip)
            # RSI(3) < 30
            # (Simple RSI check for speed)
            closes_hist = vols[i-5:i] # wait, this is volume.
            closes_p = [float(cand["close"]) for cand in m1_candles[i-5:i]]
            # ... simple RSI calculation
            rsi = 50.0
            if len(closes_p) >= 4:
                d = [closes_p[j]-closes_p[j-1] for j in range(1, len(closes_p))]
                g = sum([x for x in d if x > 0]); lo = sum([-x for x in d if x < 0])
                if lo > 0: rsi = 100 - 100/(1+g/lo)
            
            if rsi <= 30:
                # Are we in the wait window?
                if ts - last_toxic_ts <= wait_window * 60:
                    continue # Waiting for toxicity to decay
                
                # Entry!
                ep = float(m1_candles[i+1]["open"])
                # Exit next bar
                xp = float(m1_candles[i+1]["close"])
                
                pnl = (xp - ep) / ep * 100 - 0.80 # fees
                total_pnl += pnl
                closes += 1
                if pnl > 0: wins += 1

        wr = wins / max(1, closes) * 100
        print(f"Wait Window={wait_window:2d}m | Closes={closes:4d} | WR={wr:4.1f}% | Net PnL={total_pnl:8.2f}%")

if __name__ == "__main__":
    main()
