import json
import time
import sys
import os
import math
import random
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
            continue
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=3):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period; avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 FINAL BOSS STRESS TEST on {PRODUCT} (7 Days)...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    if not m1_candles:
        print("No data.")
        return

    FEE_RATE = 0.0010 # 10bps armor
    
    for mode in ["Verified Baseline", "Predatory Boss (5% Target)"]:
        cash = 48.0
        realized_net = 0.0
        total_volume = 0.0
        pending_entry = None
        position = None
        history = []
        
        for i in range(20, len(m1_candles) - 1):
            c = m1_candles[i]; next_c = m1_candles[i+1]
            o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            nh = float(next_c["high"]); nl = float(next_c["low"])
            history.append(cl)
            if len(history) > 50: history.pop(0)
            
            if pending_entry and position is None:
                if nl <= pending_entry:
                    # FILL confirmed
                    tp = pending_entry * (1.25 if mode == "Verified Baseline" else 1.05)
                    position = {"ep": pending_entry, "tp": tp, "hold": 0}
                    cash -= (10.0 * (1 + FEE_RATE))
                    pending_entry = None
                else: pending_entry = None

            if position:
                position["hold"] += 1
                if nh >= position["tp"] or position["hold"] >= 24:
                    exit_p = position["tp"] if nh >= position["tp"] else float(next_c["close"])
                    units = 10.0 / position["ep"]
                    total_returned = (units * exit_p) * (1 - FEE_RATE)
                    cash += total_returned
                    pnl = total_returned - (10.0 * (1 + FEE_RATE))
                    realized_net += pnl; total_volume += 20.0; position = None

            if position is None and pending_entry is None and cash >= 15.0:
                rsi = compute_rsi(history, 3)
                if rsi <= 30:
                    if mode == "Verified Baseline":
                        pending_entry = cl
                    else:
                        if cl > o: # Aggressor Confirm
                            mag = round(cl * 20) / 20.0
                            if abs(cl - mag) / mag <= 0.005:
                                pending_entry = mag + 0.0001

        net = cash - 48.0
        print(f"\n{mode}: Net Profit: ${net:.2f} | Vol: ${total_volume:.0f}")

if __name__ == "__main__":
    main()
