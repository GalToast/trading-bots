import json
import time
from datetime import datetime, timezone
import sys
import os
import math

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
    start = now - 72 * 3600

    print(f"Fetching 72h M1 data for {PRODUCT} Tick-Native Grid Backtest...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # 1. M5-based Grid (Baseline)
    # 2. M1-based Grid (Micro-Scalping)
    
    for mode in ["M5 Grid (0.5%)", "M1 Micro-Grid (0.25%)"]:
        cash = 48.0
        grid_inventory = []
        closes = 0
        wins = 0
        
        spacing = 0.005 if "M5" in mode else 0.0025
        
        # We simulate the M5 grid using M1 data for more accuracy
        step = 5 if "M5" in mode else 1
        
        for i in range(0, len(m1_candles), step):
            # Aggregate bar if needed
            window = m1_candles[i:i+step]
            h = max([float(c["high"]) for c in window])
            l = min([float(c["low"]) for c in window])
            cl = float(window[-1]["close"])
            
            # 1. Process Exits
            still_holding = []
            for inv in grid_inventory:
                if h >= inv["ep"] * (1 + spacing):
                    pnl = (inv["ep"]*spacing) / inv["ep"] * inv["quote"] - (2 * inv["quote"] * 0.0040)
                    cash += inv["quote"] + pnl
                    closes += 1; wins += 1
                else:
                    still_holding.append(inv)
            grid_inventory = still_holding
            
            # 2. Process Entries
            if len(grid_inventory) < 5 and cash >= 10.0:
                buy_level = cl * (1 - spacing)
                if l <= buy_level:
                    grid_inventory.append({"ep": buy_level, "quote": 10.0})
                    cash -= 10.0

        for inv in grid_inventory: cash += inv["quote"]
        net = cash - 48.0
        print(f"\n{mode}: Net=${net:.2f} ({net/48*100:.1f}%) | Closes={closes} | WR={wins/max(1, closes)*100:.1f}%")

if __name__ == "__main__":
    main()
