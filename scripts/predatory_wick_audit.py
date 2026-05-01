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

    print(f"🚀 PREDATORY WICK AUDIT: Raking the Market Maker's Pockets on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # PARAMETERS
    WICK_DEPTH = 0.02 # 2% drop
    FEE = 0.0040 # 40bps
    
    for mode in ["Standard Wick-Sniper", "Predatory Wick-Snare (The Trap)"]:
        cash = 48.0; pos = None; closes = 0; wins = 0
        for i in range(len(m1_candles)):
            c = m1_candles[i]; o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            if pos:
                pos["hold"] += 1
                if h >= pos["tp"]:
                    units = pos["quote"] / pos["ep"]
                    pnl = (pos["tp"] - pos["ep"]) * units - (pos["quote"] * FEE) - (pos["tp"] * units * FEE)
                    cash += pos["quote"] + pnl; closes += 1; wins += 1; pos = None
                elif pos["hold"] >= 10:
                    units = pos["quote"] / pos["ep"]
                    pnl = (cl - pos["ep"]) * units - (pos["quote"] * FEE) - (cl * units * FEE)
                    cash += pos["quote"] + pnl; closes += 1
                    if cl > pos["ep"]: wins += 1
                    pos = None
            if pos is None and cash >= 10.0:
                if mode == "Standard Wick-Sniper":
                    limit_price = o * (1 - WICK_DEPTH)
                    if l <= limit_price:
                        import random
                        if random.random() <= 0.5:
                            tq = cash * 0.95; pos = {"ep": limit_price, "tp": o, "quote": tq, "hold": 0}; cash -= tq
                else:
                    prev = m1_candles[i-1] if i > 0 else None
                    if prev:
                        po = float(prev["open"]); pl = float(prev["low"]); pcl = float(prev["close"])
                        wick_size = (po - pl) / po
                        if wick_size >= WICK_DEPTH and pcl > pl:
                            ep = o; tq = cash * 0.95; pos = {"ep": ep, "tp": po, "quote": tq, "hold": 0}; cash -= tq

        net = cash - 48.0
        wr = wins / max(1, closes) * 100
        print(f"\n{mode}: Net Profit: ${net:.2f} ({(net/48)*100:.1f}%) | Closes: {closes} | WR={wr:.1f}%")

if __name__ == "__main__":
    main()
