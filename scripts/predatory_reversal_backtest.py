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
    start = now - 72 * 3600 # 72 hours

    print(f"🚀 PREDATORY REVERSAL AUDIT: Snaring the Spoofers on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    for mode in ["Standard RSI(3)<30", "Predatory Snare (1.5% Flush + Recovery)"]:
        cash = 48.0; pos = None; closes = 0; wins = 0; history = []
        for i in range(len(m1_candles)):
            c = m1_candles[i]; cl = float(c["close"]); h = float(c["high"]); l = float(c["low"]); o = float(c["open"])
            history.append(cl)
            if len(history) > 20: history.pop(0)
            
            if pos:
                pos["hold"] += 1
                if h >= pos["tp"]:
                    pnl = (pos["tp"] - pos["ep"]) / pos["ep"] * pos["quote"] - (2 * pos["quote"] * 0.0040)
                    cash += pos["quote"] + pnl; closes += 1; wins += 1; pos = None
                elif pos["hold"] >= 15:
                    pnl = (cl - pos["ep"]) / pos["ep"] * pos["quote"] - (2 * pos["quote"] * 0.0040)
                    cash += pos["quote"] + pnl; closes += 1
                    if cl > pos["ep"]: wins += 1
                    pos = None
            
            if pos is None and cash >= 10.0:
                if mode == "Standard RSI(3)<30":
                    rsi = 50.0
                    if len(history) >= 4:
                        deltas = [history[j] - history[j-1] for j in range(len(history)-3, len(history))]
                        g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                        if lo > 0: rsi = 100 - 100/(1+g/lo)
                    if rsi <= 30:
                        ep = o; tq = cash * 0.95; pos = {"ep": ep, "tp": ep * 1.05, "quote": tq, "hold": 0}; cash -= tq
                else:
                    if (o - l) / o >= 0.015:
                        ep = o * 0.985
                        # ENTRY confirmed
                        # EXIT at NEXT bar open
                        if i + 1 < len(m1_candles):
                            exit_p = float(m1_candles[i+1]["open"])
                            tq = cash * 0.95
                            pnl = (exit_p - ep) / ep * tq - (2 * tq * 0.0040)
                            cash += pnl; closes += 1
                            if exit_p > ep: wins += 1

        net = cash - 48.0
        wr = wins / max(1, closes) * 100
        print(f"\n{mode}: Net Profit: ${net:.2f} ({(net/48)*100:.1f}%) | Closes: {closes} | WR={wr:.1f}%")

if __name__ == "__main__":
    main()
