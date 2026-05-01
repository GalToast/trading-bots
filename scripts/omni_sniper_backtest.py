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

def compute_volatility(closes):
    if len(closes) < 2: return 0.0
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean)**2 for r in returns) / len(returns)
    return math.sqrt(variance)

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time()); start = now - 72 * 3600

    print("Fetching 72h data for Omni-Asset Sniper Backtest...")
    product_candles = {}
    for pid in TOP_5:
        product_candles[pid] = fetch_candles(client, pid, start, now)

    all_times = sorted(list(set(int(c["start"]) for pid in product_candles for c in product_candles[pid])))
    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            ts = int(c["start"])
            if ts not in time_lookup: time_lookup[ts] = {}
            time_lookup[ts][pid] = {"open": float(c["open"]), "close": float(c["close"]), "high": float(c["high"]), "low": float(c["low"])}

    for mode in ["RAVE Only Sniper", "Top 5 Omni-Sniper"]:
        cash = 48.0; pos = None; closes = 0; wins = 0; histories = {p: [] for p in TOP_5}
        
        for t in all_times:
            tick = time_lookup.get(t, {})
            for pid, c in tick.items():
                histories[pid].append(c["close"])
                if len(histories[pid]) > 50: histories[pid].pop(0)
            
            if pos:
                pid = pos["pid"]
                if pid in tick:
                    c = tick[pid]; pos["hold"] += 1; rsi = compute_rsi(histories[pid], 4)
                    if rsi >= 95 or pos["hold"] >= 4:
                        cl = c["close"]
                        units = pos["quote"] / pos["ep"]
                        pnl = (cl - pos["ep"]) * units - (pos["quote"] * 0.0040) - (cl * units * 0.0040)
                        cash += pos["quote"] + pnl; closes += 1
                        if cl > pos["ep"]: wins += 1
                        pos = None
            
            if pos is None and cash >= 10.0:
                candidates = []
                targets = ["RAVE-USD"] if "RAVE" in mode else TOP_5
                for pid in targets:
                    if pid not in tick or len(histories[pid]) < 20: continue
                    vol = compute_volatility(histories[pid][-20:])
                    if vol < 0.015: continue
                    rsi_prev = compute_rsi(histories[pid][:-1], 4)
                    if rsi_prev <= 45:
                        candidates.append({"pid": pid, "rsi": rsi_prev, "c": tick[pid]})
                
                if candidates:
                    candidates.sort(key=lambda x: x["rsi"])
                    best = candidates[0]
                    tq = cash * 0.95; ep = best["c"]["open"]
                    pos = {"pid": best["pid"], "ep": ep, "quote": tq, "hold": 0}
                    cash -= tq

        if pos: cash += pos["quote"]
        net = cash - 48.0
        print(f"{mode}: Net=${net:.2f} ({net/48*100:.1f}%) | Closes={closes} | WR={wins/max(1, closes)*100:.1f}%")

if __name__ == "__main__":
    main()
