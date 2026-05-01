import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]

def fetch_candles(client, pid, start, end):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="FIVE_MINUTE")
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

def compute_atr(candles, period=14):
    if len(candles) < period + 1: return 0.0
    tr_list = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i-1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for ATR-Target analysis...")
    product_candles = {}
    for pid in TOP_5:
        product_candles[pid] = fetch_candles(client, pid, start, now)

    for pid in TOP_5:
        candles = product_candles[pid]
        best_net = -999.0
        best_params = None
        
        for atr_mult in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
            cash = 1000.0
            quote = 24.0
            pos = None
            closes = 0
            wins = 0
            
            for i in range(20, len(candles)):
                c = candles[i]
                cl = float(c["close"])
                h = float(c["high"])
                l = float(c["low"])
                
                if pos:
                    pos["hold"] += 1
                    if h >= pos["tp"]:
                        pnl = (pos["tp"] - pos["ep"]) / pos["ep"] * quote - (2 * quote * 0.0040)
                        cash += quote + pnl; closes += 1; wins += 1; pos = None
                    elif l <= pos["sl"]:
                        pnl = (pos["sl"] - pos["ep"]) / pos["ep"] * quote - (2 * quote * 0.0040)
                        cash += quote + pnl; closes += 1; pos = None
                    elif pos["hold"] >= 12:
                        pnl = (cl - pos["ep"]) / pos["ep"] * quote - (2 * quote * 0.0040)
                        cash += quote + pnl; closes += 1
                        if cl > pos["ep"]: wins += 1
                        pos = None
                
                if pos is None:
                    closes_hist = [float(cand["close"]) for cand in candles[i-10:i]]
                    rsi = 50.0
                    if len(closes_hist) >= 8:
                        deltas = [closes_hist[j] - closes_hist[j-1] for j in range(1, len(closes_hist))]
                        gains = [d if d > 0 else 0 for d in deltas]; losses = [-d if d < 0 else 0 for d in deltas]
                        avg_g = sum(gains)/7; avg_l = sum(losses)/7
                        if avg_l > 0: rsi = 100 - 100 / (1 + avg_g/avg_l)
                    
                    if rsi <= 30:
                        atr = compute_atr(candles[i-15:i], 14)
                        if atr > 0:
                            ep = float(c["open"])
                            tp = ep + (atr * atr_mult)
                            sl = ep - (atr * 1.5)
                            pos = {"ep": ep, "tp": tp, "sl": sl, "hold": 0}
                            cash -= quote
            
            net = cash - 1000.0
            if net > best_net:
                best_net = net
                best_params = (atr_mult, closes, wins)
        
        if best_params:
            m, cl, w = best_params
            print(f"{pid}: Best ATR Mult={m:.1f} | Net=${best_net:.2f} | WR={w/max(1, cl)*100:.1f}%")

if __name__ == "__main__":
    main()
