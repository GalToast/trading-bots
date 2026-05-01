import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Optimal Coins from @qwen-main and @assist
TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
BTC = "BTC-USD"
PRODUCTS = [BTC] + TOP_5

# Per-coin TP findings from @assist (Multiplier sweeps)
# RAVE can handle 15%, but we use 10% as baseline champion
PRODUCT_PARAMS = {
    "RAVE-USD": {"tp": 10.0, "sl": 2.5},
    "BLUR-USD": {"tp": 8.0, "sl": 2.5},
    "BAL-USD": {"tp": 6.0, "sl": 2.5},
    "ALEPH-USD": {"tp": 6.0, "sl": 2.5},
    "IOTX-USD": {"tp": 6.0, "sl": 2.5},
}

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

def compute_rsi(closes, period=7):
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

def compute_bb(closes, period=20, std_dev=2):
    if len(closes) < period: return 50.0, 0.0 # mid, lower
    sma = sum(closes[-period:]) / period
    variance = sum((x - sma) ** 2 for x in closes[-period:]) / period
    std = math.sqrt(variance)
    return sma, sma - (std_dev * std)

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for Lattice-Aware BB+RSI Rotation...")
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_m1_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    
    product_candles = {}
    for pid in TOP_5:
        c = fetch_candles(client, pid, start, now, granularity="FIVE_MINUTE")
        product_candles[pid] = c
        print(f"  {pid}: {len(c)} candles")

    all_times = sorted(list(set(int(c["start"]) for pid in product_candles for c in product_candles[pid])))
    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            t = int(c["start"])
            time_lookup.setdefault(t, {})[pid] = {"open": float(c["open"]), "close": float(c["close"]), "high": float(c["high"]), "low": float(c["low"])}

    history = {p: [] for p in TOP_5}
    btc_closes = []
    
    cash = 48.0
    position = None
    total_volume = 0.0
    closes_count = 0
    wins = 0

    print("\n--- SIMULATING LATTICE-AWARE BB+RSI ROTATION ---")
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        
        # BTC M1 Momentum Gate
        btc_gate = True
        p_t = t - 60
        p_t3 = t - 180
        if p_t in btc_m1_lookup and p_t3 in btc_m1_lookup:
            mom = (btc_m1_lookup[p_t] - btc_m1_lookup[p_t3]) / btc_m1_lookup[p_t3]
            if mom < -0.001: btc_gate = False
        
        # 1. Exit Logic
        if position:
            pid = position["pid"]
            if pid in tick:
                c = tick[pid]
                position["hold"] += 1
                
                # Fixed Exits per @qwen-main + per-coin TP
                rsi = compute_rsi(history[pid], 7)
                
                exit_p = None
                if c["high"] >= position["tp"]:
                    exit_p = position["tp"]; wins += 1; closes_count += 1; closed = True
                elif c["low"] <= position["sl"]:
                    exit_p = position["sl"]; closes_count += 1; closed = True
                elif rsi >= 70: 
                    exit_p = c["close"]; closes_count += 1; closed = True
                    if exit_p > position["ep"]: wins += 1
                elif position["hold"] >= 12:
                    exit_p = c["close"]; closes_count += 1; closed = True
                    if exit_p > position["ep"]: wins += 1
                else:
                    closed = False
                
                if closed:
                    units = position["quote"] / position["ep"]
                    gross = (exit_p - position["ep"]) * units
                    ef = position["quote"] * FEE_RATE; xf = exit_p * units * FEE_RATE
                    net = gross - ef - xf
                    cash += position["quote"] + net
                    total_volume += position["quote"] + (exit_p * units)
                    position = None

        # Update History
        for pid, c in tick.items():
            history[pid].append(c["close"])
            if len(history[pid]) > 100: history[pid].pop(0)

        # 2. Entry Logic (Rotation)
        if position is None and cash >= 10.0 and btc_gate:
            candidates = []
            for pid, c in tick.items():
                if len(history[pid]) < 20: continue
                
                rsi_prev = compute_rsi(history[pid][:-1], 7)
                mid, lower = compute_bb(history[pid][:-1], 20, 2)
                
                # BB + RSI CONFLUENCE
                if rsi_prev <= 30 and history[pid][-1] <= lower:
                    candidates.append({"pid": pid, "rsi": rsi_prev, "c": c})
            
            if candidates:
                # Pick the most oversold
                candidates.sort(key=lambda x: x["rsi"])
                best = candidates[0]
                pid = best["pid"]
                params = PRODUCT_PARAMS.get(pid, {"tp": 10.0, "sl": 2.5})
                
                tq = cash * 0.95 # Full rotation compounding
                ep = best["c"]["open"]
                position = {
                    "pid": pid, "ep": ep, "quote": tq, "hold": 0,
                    "tp": ep * (1 + params["tp"] / 100.0),
                    "sl": ep * (1 - params["sl"] / 100.0)
                }
                cash -= tq

    if position: cash += position["quote"]
    print(f"\nFINAL CHAMPION RESULTS (72h):")
    print(f"Bankroll: ${cash:.2f} | Net=${cash-48:.2f} (+{(cash-48)/48*100:.1f}%)")
    print(f"Closes: {closes_count} | WR={wins/max(1, closes_count)*100:.1f}%")
    print(f"Volume: ${total_volume:.2f}")

if __name__ == "__main__":
    main()
