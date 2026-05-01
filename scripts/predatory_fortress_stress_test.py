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
            time.sleep(0.5)
            continue
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=4):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 HARDENED FORTRESS STRESS TEST on {PRODUCT} (7 Days)...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    if not m1_candles:
        print("No data.")
        return

    # Scenarios
    for fill_prob in [1.0, 0.5, 0.25]:
        for latency_mins in [0, 1]:
            
            cash = 48.0
            total_volume = 0.0
            realized_net = 0.0
            position = None
            history = []
            
            for i in range(20, len(m1_candles)):
                c = m1_candles[i]
                o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                history.append(cl)
                if len(history) > 100: history.pop(0)
                
                # Assume we already hit 25bps tier
                fee_rate = 0.0025
                
                # 1. Exit Logic
                if position:
                    position["hold"] += 1
                    exit_p = None
                    closed = False
                    
                    if position["type"] == "sniper":
                        rsi = compute_rsi(history, 4)
                        if h >= position["entry"] * 1.25 or rsi >= 80 or position["hold"] >= 24:
                            exit_p = cl
                            if random.random() <= fill_prob: closed = True
                    else:
                        # HARDENED GRINDER: 2.0% Target
                        target = position["entry"] * 1.02
                        if h >= target:
                            exit_p = target
                            if random.random() <= fill_prob: closed = True
                        elif l < position["entry"] * 0.985: # 1.5% SL
                            exit_p = position["entry"] * 0.985
                            closed = True
                    
                    if closed:
                        units = position["quote"] / position["entry"]
                        total_returned = (units * exit_p) - (units * exit_p * fee_rate)
                        cash += total_returned
                        pnl = total_returned - (position["quote"] * (1 + fee_rate))
                        realized_net += pnl; total_volume += position["quote"] + (units * exit_p)
                        position = None
                        continue

                # 2. Entry Logic
                if position is None and cash >= 15.0:
                    obs_idx = i - latency_mins
                    obs_c = m1_candles[obs_idx]
                    obs_cl = float(obs_c["close"])
                    rsi_prev = compute_rsi(history[:-(1+latency_mins)], 4) if len(history) > 1+latency_mins else 50.0
                    
                    # SNIPER PRIORITY
                    if rsi_prev <= 30:
                        # Magnetic Check
                        magnetic_level = round(obs_cl * 20) / 20.0
                        if abs(obs_cl - magnetic_level) / magnetic_level <= 0.0025:
                            if random.random() <= fill_prob:
                                ep = magnetic_level + 0.0001
                                tq = cash * 0.95
                                position = {"entry": ep, "quote": tq, "type": "sniper", "hold": 0}
                                cash -= (tq * (1 + fee_rate))
                                continue
                    
                    # HARDENED GRINDER
                    obs_range = (float(obs_c["high"]) - float(obs_c["low"])) / float(obs_c["low"]) * 100
                    # Must be RSI < 30 and Wide Range
                    if obs_range >= 1.5 and rsi_prev <= 30:
                        if obs_cl > float(obs_c["open"]):
                            if random.random() <= fill_prob:
                                ep = obs_cl
                                tq = 15.0 # Hardened quote
                                position = {"entry": ep, "quote": tq, "type": "grinder", "hold": 0}
                                cash -= (tq * (1 + fee_rate))

            net = cash - 48.0
            print(f"Fill={fill_prob:4.2f} | Lag={latency_mins}m | Net=${net:8.2f} | Vol=${total_volume:10.0f}")

if __name__ == "__main__":
    main()
