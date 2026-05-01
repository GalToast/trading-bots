import json
import time
import sys
import os
import math
import random
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

IOTX = "IOTX-USD"
BAL = "BAL-USD"
BTC = "BTC-USD"

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

def compute_rsi(closes, period=4):
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

    print(f"🕵️ LATTICE-WARP ADVERSARIAL AUDIT (IOTX & BAL)...")
    
    btc_m1 = fetch_candles(client, BTC, start, now, "ONE_MINUTE")
    btc_lookup = {int(c["start"]): c for c in btc_m1}
    
    FEE_RATE = 0.0025 # 25bps
    FILL_PROB = 0.50 # Realistic Competition
    LATENCY_BARS = 1 # Realistic 1-min data lag
    
    for product in [IOTX, BAL]:
        print(f"\n--- AUDITING {product} ---")
        candles = fetch_candles(client, product, start, now, "ONE_MINUTE")
        
        for mode in ["Blind Grinder", "Warp-Gated Grinder"]:
            cash = 1000.0
            inventory = 0.0
            entry_p = 0.0
            closes = 0
            wins = 0
            
            history = []
            
            for i in range(20, len(candles)):
                c = candles[i]
                ts = int(c["start"])
                o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                history.append(cl)
                
                # 1. Exit Logic
                if inventory > 0:
                    target = entry_p * 1.015 # 1.5% target
                    if h >= target:
                        if random.random() <= FILL_PROB:
                            cash += (inventory * target) * (1 - FEE_RATE)
                            closes += 1; wins += 1; inventory = 0.0
                            continue
                    if l < entry_p * 0.985: # 1.5% SL
                        exit_p = entry_p * 0.985
                        cash += (inventory * exit_p) * (1 - 0.0060)
                        closes += 1; inventory = 0.0
                        continue

                # 2. Entry Logic
                if inventory == 0 and cash >= 100.0:
                    # Lagged RSI
                    rsi_prev = compute_rsi(history[:-(1+LATENCY_BARS)], 4) if len(history) > 1+LATENCY_BARS else 50.0
                    
                    if rsi_prev <= 30:
                        
                        if mode == "Blind Grinder":
                            # Assumption: Fill if price touches bid
                            if l < o:
                                if random.random() <= FILL_PROB:
                                    inventory = 100.0 / o
                                    entry_p = o
                                    cash -= (100.0 * (1 + FEE_RATE))
                        else:
                            # WARP-GATED: 
                            # Only enter if BTC moved UP in the same window (proxy for Kraken lead)
                            if ts in btc_lookup:
                                bc = btc_lookup[ts]
                                btc_ret = (float(bc["close"]) - float(bc["open"])) / float(bc["open"])
                                
                                if btc_ret >= 0.0005: # BTC up > 0.05%
                                    if l < o:
                                        if random.random() <= FILL_PROB:
                                            inventory = 100.0 / o
                                            entry_p = o
                                            cash -= (100.0 * (1 + FEE_RATE))

            net = cash - 1000.0
            wr = wins / max(1, closes) * 100
            print(f"{mode:20s} | Net=${net:8.2f} | Closes={closes:4d} | WR={wr:4.1f}%")

if __name__ == "__main__":
    main()
