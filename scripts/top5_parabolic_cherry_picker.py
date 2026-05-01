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

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
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

def compute_stoch_rsi(rsi_history, period=5):
    if len(rsi_history) < period: return 0.5
    low_rsi = min(rsi_history[-period:])
    high_rsi = max(rsi_history[-period:])
    if high_rsi == low_rsi: return 0.5
    return (rsi_history[-1] - low_rsi) / (high_rsi - low_rsi)

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for Top 5 Cherry Picker...")
    product_candles = {}
    for pid in TOP_5:
        product_candles[pid] = fetch_candles(client, pid, start, now)

    all_times = sorted(list(set(int(c["start"]) for pid in product_candles for c in product_candles[pid])))
    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            t = int(c["start"])
            time_lookup.setdefault(t, {})[pid] = {"open": float(c["open"]), "close": float(c["close"]), "high": float(c["high"]), "low": float(c["low"])}

    # Simulation Params
    TP_PCT = 25.0
    SL_PCT = 3.0
    RSI_PERIOD = 4
    STOCH_PERIOD = 5
    
    # Modes:
    # 1. Crown Jewel (RAVE only, fixed)
    # 2. TOP 5 CHERRY PICKER (1 concurrent, 95% compound, Stoch-RSI filter)
    
    for mode in ["Crown Jewel (RAVE)", "Top 5 Parabolic Cherry-Picker"]:
        cash = 48.0
        pos = None
        closes = 0
        wins = 0
        total_volume = 0.0
        
        histories = {p: [] for p in TOP_5}
        rsi_histories = {p: [] for p in TOP_5}
        
        for t in all_times:
            tick = time_lookup.get(t, {})
            
            # Update histories
            for pid, c in tick.items():
                histories[pid].append(c["close"])
                if len(histories[pid]) >= RSI_PERIOD + 1:
                    rsi = compute_rsi(histories[pid], RSI_PERIOD)
                    rsi_histories[pid].append(rsi)
                if len(histories[pid]) > 50: histories[pid].pop(0)
                if len(rsi_histories[pid]) > 50: rsi_histories[pid].pop(0)
            
            # 1. Exit Logic
            if pos:
                pid = pos["pid"]
                if pid in tick:
                    c = tick[pid]
                    pos["hold"] += 1
                    exit_p = None
                    if c["high"] >= pos["tp"]:
                        exit_p = pos["tp"]; wins += 1; closed = True
                    elif c["low"] <= pos["sl"]:
                        exit_p = pos["sl"]; closed = True
                    elif pos["hold"] >= 24:
                        exit_p = c["close"]; closed = True
                        if exit_p > pos["ep"]: wins += 1
                    else:
                        closed = False
                    
                    if closed:
                        units = pos["quote"] / pos["ep"]
                        pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * 0.0040) - (exit_p * units * 0.0040)
                        cash += pos["quote"] + pnl
                        total_volume += pos["quote"] + (exit_p * units)
                        closes += 1
                        pos = None

            # 2. Entry Logic
            if pos is None and cash >= 10.0:
                candidates = []
                target_pids = ["RAVE-USD"] if mode == "Crown Jewel (RAVE)" else TOP_5
                
                for pid in target_pids:
                    if pid not in tick: continue
                    if len(rsi_histories[pid]) < STOCH_PERIOD + 1: continue
                    
                    rsi_now = rsi_histories[pid][-1]
                    stoch_rsi = compute_stoch_rsi(rsi_histories[pid], STOCH_PERIOD)
                    
                    signal = False
                    if mode == "Crown Jewel (RAVE)":
                        if rsi_now <= 30: signal = True
                    else:
                        # STOCH-RSI Filter
                        if rsi_now <= 30 and stoch_rsi <= 0.1: signal = True
                    
                    if signal:
                        candidates.append({"pid": pid, "rsi": rsi_now, "stoch": stoch_rsi, "c": tick[pid]})
                
                if candidates:
                    # Pick deepest RSI
                    candidates.sort(key=lambda x: x["rsi"])
                    best = candidates[0]
                    
                    ep = best["c"]["open"]
                    tq = 48.0 if mode == "Crown Jewel (RAVE)" else cash * 0.95
                    if tq > cash: tq = cash
                    
                    if tq >= 10.0:
                        pos = {
                            "pid": best["pid"], "ep": ep, "quote": tq, "hold": 0,
                            "tp": ep * (1 + TP_PCT / 100.0),
                            "sl": ep * (1 - SL_PCT / 100.0)
                        }
                        cash -= tq

        if pos: cash += pos["quote"]
        net = cash - 48.0
        wr = wins/max(1, closes)*100
        print(f"\n{mode}:")
        print(f"  Net Profit: ${net:.2f} ({net/48*100:.1f}%)")
        print(f"  Closes: {closes} | WR={wr:.1f}%")
        print(f"  Total Volume: ${total_volume:.2f}")

if __name__ == "__main__":
    main()
