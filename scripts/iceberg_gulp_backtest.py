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

    print(f"🚀 PREDATORY GOBBLIN: The 'Iceberg Gulp' Strategy on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # We define a 'Gulp' as a 1-minute bar with massive volume but 0 price movement.
    # This indicates a hidden seller (Iceberg) being exhausted.
    # Entry: Buy at the close of the Gulp bar.
    # Exit: Sell at 1.0% profit or next bar close.
    
    FEE_RATE = 0.0025 # 25bps
    
    for vol_threshold in [5.0, 10.0]: # x average volume
        
        cash = 1000.0
        closes = 0
        wins = 0
        
        # Rolling average volume
        vol_history = []
        
        for i in range(20, len(m1_candles) - 1):
            window = m1_candles[i-20:i]
            avg_vol = sum(float(c.get("volume", 0)) for c in window) / 20
            
            c = m1_candles[i]
            cv = float(c.get("volume", 0))
            co = float(c["open"]); cc = float(c["close"]); ch = float(c["high"]); cl = float(c["low"])
            
            # GULP DETECTION: Massive volume, small range (relative to volume)
            range_pct = (ch - cl) / cl * 100
            if avg_vol > 0 and cv >= vol_threshold * avg_vol and range_pct < 0.5:
                # ICEBERG BEING EATEN!
                # Enter at the close of this bar
                ep = cc
                
                # Check next bar for the snap-back
                next_c = m1_candles[i+1]
                nh = float(next_c["high"]); nl = float(next_c["low"]); nc = float(next_c["close"])
                
                # Exit at 1% target or next close
                target = ep * 1.01
                if nh >= target:
                    exit_p = target
                else:
                    exit_p = nc
                
                units = 100.0 / ep
                total_returned = (units * exit_p) * (1 - FEE_RATE)
                pnl = total_returned - (100.0 * (1 + FEE_RATE))
                
                cash += pnl
                closes += 1
                if exit_p > ep: wins += 1

        net = cash - 1000.0
        wr = wins / max(1, closes) * 100
        print(f"Vol_Thresh={vol_threshold:4.1f}x | Net=${net:8.2f} | Closes={closes:4d} | WR={wr:4.1f}%")

if __name__ == "__main__":
    main()
