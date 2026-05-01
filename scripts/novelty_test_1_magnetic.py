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

    print(f"🚀 NOVELTY TEST #1: MAGNETIC ROUND NUMBERS on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    touches = 0
    near_misses = 0
    total_events = 0
    
    for c in m1_candles:
        h = float(c["high"]); l = float(c["low"]); cl = float(c["close"]); o = float(c["open"])
        for i in range(100, 300, 5): 
            level = i / 100.0
            if abs(o - level) / level <= 0.0025:
                total_events += 1
                if l <= level <= h: touches += 1
                else: near_misses += 1

    print("\n--- RESULTS ---")
    print(f"Total Magnetic Events (Price near .00/.05): {total_events}")
    print(f"Successful Touches (The Magnet Pulled): {touches} ({touches/max(1, total_events)*100:.1f}%)")
    print(f"Near Misses: {near_misses}")
    
    cash = 48.0; pos = None; closes = 0; wins = 0; history = []
    for i in range(20, len(m1_candles)):
        c = m1_candles[i]; o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
        history.append(cl)
        if len(history) > 20: history.pop(0)
        
        if pos:
            pos["hold"] += 1
            if h >= pos["tp"]:
                pnl = (pos["tp"] - pos["ep"]) / pos["ep"] * 24.0 - (2 * 24.0 * 0.0040)
                cash += 24.0 + pnl; closes += 1; wins += 1; pos = None
            elif pos["hold"] >= 15:
                pnl = (cl - pos["ep"]) / pos["ep"] * 24.0 - (2 * 24.0 * 0.0040)
                cash += 24.0 + pnl; closes += 1
                if cl > pos["ep"]: wins += 1
                pos = None
        
        if pos is None and cash >= 24.0:
            magnetic_level = round(o * 20) / 20.0
            if abs(o - magnetic_level) / magnetic_level <= 0.005:
                ep = magnetic_level + 0.0001
                if l <= ep:
                    pos = {"ep": ep, "tp": ep * 1.02, "hold": 0}
                    cash -= 24.0

    print(f"\nMAGNETIC SCALPER NET: ${cash-48:.2f} | WR={wins/max(1, closes)*100:.1f}%")

if __name__ == "__main__":
    main()
