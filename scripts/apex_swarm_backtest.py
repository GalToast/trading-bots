import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
BTC = "BTC-USD"
PRODUCTS = [BTC] + TOP_5

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "ONE_MINUTE": chunk_sec = 300 * 60
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
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def compute_volatility(closes):
    if len(closes) < 2: return 0.0
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean)**2 for r in returns) / len(returns)
    return math.sqrt(variance)

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for Apex Predator Swarm...")
    product_candles = {}
    for pid in PRODUCTS:
        c = fetch_candles(client, pid, start, now)
        product_candles[pid] = c
        print(f"  {pid}: {len(c)} candles")

    all_times = sorted(list(set(int(c["start"]) for pid in product_candles for c in product_candles[pid])))
    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            t = int(c["start"])
            time_lookup.setdefault(t, {})[pid] = {"open": float(c["open"]), "close": float(c["close"]), "high": float(c["high"]), "low": float(c["low"])}

    history = {p: [] for p in TOP_5}
    
    # Modes:
    # 1. Apex RAVE (Single Asset)
    # 2. Apex Swarm (5 Assets, Shared Bankroll, Dynamic Sizing)
    
    for mode in ["Apex RAVE Only", "Apex Predator Swarm (Top 5)"]:
        cash = 48.0
        positions = []
        max_concurrent = 1 if "RAVE" in mode else 3
        closes_count = 0
        wins = 0
        total_volume = 0.0
        
        for t in all_times:
            tick = time_lookup.get(t, {})
            
            # 1. Process Exits
            still_open = []
            for pos in positions:
                pid = pos["pid"]
                closed = False
                if pid in tick:
                    c = tick[pid]
                    pos["hold"] += 1
                    rsi = compute_rsi(history[pid], 4)
                    
                    exit_p = None
                    if rsi >= 95 or pos["hold"] >= 4:
                        exit_p = c["close"]
                        units = pos["quote"] / pos["ep"]
                        pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * 0.0040) - (exit_p * units * 0.0040)
                        cash += pos["quote"] + pnl
                        total_volume += pos["quote"] + (exit_p * units)
                        closes_count += 1
                        if exit_p > pos["ep"]: wins += 1
                        closed = True
                if not closed:
                    still_open.append(pos)
            positions = still_open

            # Update History
            for pid, c in tick.items():
                if pid in history:
                    history[pid].append(c["close"])
                    if len(history[pid]) > 50: history[pid].pop(0)

            # 2. Process Entry
            target_pids = ["RAVE-USD"] if "RAVE" in mode else TOP_5
            free_slots = max_concurrent - len(positions)
            
            if free_slots > 0 and cash >= 10.0:
                candidates = []
                for pid in target_pids:
                    if pid not in tick: continue
                    if any(p["pid"] == pid for p in positions): continue
                    if len(history[pid]) < 20: continue
                    
                    vol = compute_volatility(history[pid][-20:])
                    if vol < 0.015: continue # Regime Gate
                    
                    rsi_now = compute_rsi(history[pid], 4)
                    if rsi_now <= 45:
                        candidates.append({"pid": pid, "rsi": rsi_now, "c": tick[pid]})
                
                candidates.sort(key=lambda x: x["rsi"])
                for cand in candidates[:free_slots]:
                    if cash < 10.0: break
                    pid = cand["pid"]
                    
                    tq = cash / free_slots * 0.95
                    if tq < 10.0: tq = 10.0
                    if tq > cash: break
                    
                    ep = cand["c"]["open"]
                    positions.append({"pid": pid, "ep": ep, "quote": tq, "hold": 0})
                    cash -= tq
                    free_slots -= 1

        if positions:
            for p in positions: cash += p["quote"]
            
        net = cash - 48.0
        wr = wins/max(1, closes_count)*100
        print(f"\n{mode}:")
        print(f"  Net Profit: ${net:.2f} ({net/48*100:.1f}%)")
        print(f"  Closes: {closes_count} | WR={wr:.1f}%")
        print(f"  Total Volume: ${total_volume:.2f}")

if __name__ == "__main__":
    main()
