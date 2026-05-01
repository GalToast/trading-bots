import json
import time
import sys
import os
import math
import random
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Top Assets from our benchmark
PRODUCTS = ["MOG-USD", "RAVE-USD", "IOTX-USD", "BAL-USD"]

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

    print(f"🚀 SWARM GOBBLIN STRESS TEST (7 Days)...")
    product_candles = {}
    for pid in PRODUCTS:
        print(f"  Fetching {pid}...")
        product_candles[pid] = fetch_candles(client, pid, start, now, "ONE_MINUTE")

    all_times = sorted(list(set(int(c["start"]) for pid in product_candles for c in product_candles[pid])))
    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            time_lookup.setdefault(int(c["start"]), {})[pid] = c

    # Scenario: 50% Fill Probability, 1-min Lag (Worst Case)
    FILL_PROB = 0.50
    LATENCY = 1
    
    cash = 48.0
    total_volume = 0.0
    realized_net = 0.0
    
    active_positions = {} # {pid: {"entry": ..., "quote": ..., "type": "sniper|grinder", "hold": 0}}
    histories = {p: [] for p in PRODUCTS}
    
    print("\n--- SIMULATING SWARM UNDER STRESS ---")
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        
        # Fee Tiers
        if total_volume >= 50000: fee_rate = 0.0015
        elif total_volume >= 10000: fee_rate = 0.0025
        else: fee_rate = 0.0040
        
        # Update History
        for pid, c in tick.items():
            histories[pid].append(float(c["close"]))
            if len(histories[pid]) > 100: histories[pid].pop(0)
            
        # 1. Management (Exits)
        still_active = {}
        for pid, pos in active_positions.items():
            closed = False
            if pid in tick:
                c = tick[pid]
                cl = float(c["close"]); h = float(c["high"]); l = float(c["low"])
                pos["hold"] += 1
                
                exit_p = None
                if pos["type"] == "sniper":
                    rsi = compute_rsi(histories[pid], 4)
                    if h >= pos["entry"] * 1.25 or rsi >= 80 or pos["hold"] >= 24:
                        exit_p = cl
                        if random.random() <= FILL_PROB: closed = True
                else:
                    # HARDENED GRINDER
                    target = pos["entry"] * 1.02
                    if h >= target:
                        exit_p = target
                        if random.random() <= FILL_PROB: closed = True
                    elif l < pos["entry"] * 0.985: # SL
                        exit_p = pos["entry"] * 0.985
                        closed = True
                
                if closed:
                    units = pos["quote"] / pos["entry"]
                    total_returned = (units * exit_p) - (units * exit_p * fee_rate)
                    cash += total_returned
                    pnl = total_returned - (pos["quote"] * (1 + fee_rate))
                    realized_net += pnl; total_volume += pos["quote"] + (units * exit_p)
                    continue
            still_active[pid] = pos
        active_positions = still_active

        # 2. Deployment
        if len(active_positions) < 3 and cash >= 15.0:
            # Random asset scan order
            random_pids = list(PRODUCTS)
            random.shuffle(random_pids)
            
            for pid in random_pids:
                if pid in active_positions: continue
                if pid not in tick: continue
                if len(histories[pid]) < 20: continue
                
                # Use Lagged RSI
                rsi_prev = compute_rsi(histories[pid][:-(1+LATENCY)], 4) if len(histories[pid]) > 1+LATENCY else 50.0
                
                # SNIPER PRIORITY
                if rsi_prev <= 30:
                    if random.random() <= FILL_PROB:
                        ep = float(tick[pid]["open"])
                        tq = cash * 0.33 # Divide bankroll
                        active_positions[pid] = {"entry": ep, "quote": tq, "type": "sniper", "hold": 0}
                        cash -= (tq * (1 + fee_rate))
                        break
                
                # HARDENED GRINDER
                obs_range = (float(tick[pid]["high"]) - float(tick[pid]["low"])) / float(tick[pid]["low"]) * 100
                # Must be very selective for Grinder
                if obs_range >= 2.0 and rsi_prev <= 30:
                    if float(tick[pid]["close"]) > float(tick[pid]["open"]):
                        if random.random() <= FILL_PROB:
                            ep = float(tick[pid]["open"])
                            tq = 15.0
                            active_positions[pid] = {"entry": ep, "quote": tq, "type": "grinder", "hold": 0}
                            cash -= (tq * (1 + fee_rate))
                            break

    for pid, pos in active_positions.items(): cash += pos["quote"]
    net = cash - 48.0
    print(f"\nSWARM STRESS TEST RESULTS:")
    print(f"Final Bankroll: ${cash:.2f}")
    print(f"Net Profit:     ${net:.2f}")
    print(f"Total Volume:   ${total_volume:.0f}")

if __name__ == "__main__":
    main()
