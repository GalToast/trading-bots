import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

BTC = "BTC-USD"
TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
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

    print("Fetching 72h data for Lattice-Aware Confluence...")
    
    # Fetch BTC M1
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    print(f"  {BTC} (M1): {len(btc_m1)} candles")
    
    # Fetch Top 5 M5
    product_candles = {}
    for pid in TOP_5:
        c = fetch_candles(client, pid, start, now, granularity="FIVE_MINUTE")
        product_candles[pid] = c
        print(f"  {pid} (M5): {len(c)} candles")

    # Sync BTC M1 to M5 timeline
    btc_m1_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}
    
    all_times = sorted(list(set(int(c["start"]) for pid in product_candles for c in product_candles[pid])))
    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            t = int(c["start"])
            time_lookup.setdefault(t, {})[pid] = {"open": float(c["open"]), "close": float(c["close"]), "high": float(c["high"]), "low": float(c["low"])}

    history = {p: [] for p in TOP_5}
    
    cash = 48.0
    positions = []
    max_concurrent = 3
    total_volume = 0.0
    total_fees_paid = 0.0
    closes_count = 0
    wins = 0

    print("\n--- SIMULATING LATTICE-AWARE RSI ---")
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        
        # Calculate BTC M1 Momentum Gate (3-min window ending just before t)
        btc_gate = True
        p_t = t - 60
        p_t3 = t - 180
        if p_t in btc_m1_lookup and p_t3 in btc_m1_lookup:
            mom = (btc_m1_lookup[p_t] - btc_m1_lookup[p_t3]) / btc_m1_lookup[p_t3]
            if mom < -0.001: 
                btc_gate = False
        
        # 1. Exits
        still_open = []
        for pos in positions:
            pid = pos["pid"]
            closed = False
            if pid in tick:
                c = tick[pid]
                pos["hold"] += 1
                if c["high"] >= pos["tp"]:
                    pnl = (pos["tp"] - pos["ep"]) / pos["ep"] * pos["quote"] - (2 * pos["quote"] * FEE_RATE)
                    cash += pos["quote"] + pnl; total_volume += 2 * pos["quote"]; total_fees_paid += 2 * pos["quote"] * FEE_RATE
                    closes_count += 1; wins += 1; closed = True
                elif c["low"] <= pos["sl"]:
                    pnl = (pos["sl"] - pos["ep"]) / pos["ep"] * pos["quote"] - (2 * pos["quote"] * FEE_RATE)
                    cash += pos["quote"] + pnl; total_volume += 2 * pos["quote"]; total_fees_paid += 2 * pos["quote"] * FEE_RATE
                    closes_count += 1; closed = True
                elif pos["hold"] >= 24:
                    pnl = (c["close"] - pos["ep"]) / pos["ep"] * pos["quote"] - (2 * pos["quote"] * FEE_RATE)
                    cash += pos["quote"] + pnl; total_volume += 2 * pos["quote"]; total_fees_paid += 2 * pos["quote"] * FEE_RATE
                    closes_count += 1
                    if c["close"] > pos["ep"]: wins += 1
                    closed = True
            if not closed: still_open.append(pos)
        positions = still_open

        # Update History
        for pid, c in tick.items():
            history[pid].append(c["close"])
            if len(history[pid]) > 100: history[pid].pop(0)

        # 2. Entries
        free_slots = max_concurrent - len(positions)
        if free_slots > 0 and cash >= 10.0 and btc_gate:
            candidates = []
            for pid, c in tick.items():
                if any(p["pid"] == pid for p in positions): continue
                if len(history[pid]) < 50: continue
                
                rsi_prev = compute_rsi(history[pid][:-1], 7)
                if rsi_prev <= 30:
                    vol_1h = compute_volatility(history[pid][-12:])
                    vol_24h = compute_volatility(history[pid][-50:])
                    if vol_1h > 1.2 * vol_24h:
                        candidates.append({"pid": pid, "rsi": rsi_prev, "c": c})
            
            candidates.sort(key=lambda x: x["rsi"])
            for cand in candidates[:free_slots]:
                if cash < 10.0: break
                pid = cand["pid"]
                tq = cash / free_slots * 0.5 
                if tq < 10.0: tq = 10.0
                if tq > cash: break
                
                ep = cand["c"]["open"]
                tp_pct = 8.0 if pid == "RAVE-USD" else 5.0
                positions.append({
                    "pid": pid, "ep": ep, "quote": tq, "hold": 0,
                    "tp": ep * (1 + tp_pct / 100.0),
                    "sl": ep * 0.97
                })
                cash -= tq
                free_slots -= 1

    for pos in positions: cash += pos["quote"]
    print(f"\nFINAL RESULTS (72h):")
    print(f"Bankroll: ${cash:.2f} | Net=${cash-48:.2f}")
    print(f"Closes: {closes_count} | WR={wins/max(1, closes_count)*100:.1f}%")
    print(f"Volume: ${total_volume:.2f} | Fees=${total_fees_paid:.2f}")

if __name__ == "__main__":
    main()
