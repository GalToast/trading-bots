import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

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

    print(f"Fetching 72h data for {PRODUCT} Hyper-Pump Backtest...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    
    for mode in ["Apex Champion", "Apex Hyper-Pump"]:
        cash = 48.0; pos = None; closes = 0; wins = 0; history = []
        for i in range(len(rave_candles)):
            c = rave_candles[i]; cl = float(c["close"]); history.append(cl)
            if len(history) > 50: history.pop(0)
            vol = compute_volatility(history[-20:]) if len(history) >= 20 else 0.0
            
            if pos:
                pos["hold"] += 1; rsi = compute_rsi(history, 4)
                if rsi >= 95 or pos["hold"] >= 4:
                    units = pos["quote"] / pos["entry"]
                    pnl = (cl - pos["entry"]) * units - (pos["quote"] * 0.0040) - (cl * units * 0.0040)
                    cash += pos["quote"] + pnl; closes += 1
                    if cl > pos["entry"]: wins += 1
                    pos = None
            
            if pos is None and cash >= 10.0:
                rsi_prev = compute_rsi(history[:-1], 4) if len(history) >= 5 else 50.0
                if mode == "Apex Champion":
                    if vol >= 0.015 and rsi_prev <= 45:
                        ep = float(c["open"]); tq = cash * 0.95
                        pos = {"entry": ep, "quote": tq, "hold": 0}
                        cash -= tq
                else:
                    entry_thresh = 45
                    if vol >= 0.05: entry_thresh = 55
                    if vol >= 0.015 and rsi_prev <= entry_thresh:
                        ep = float(c["open"]); tq = cash * 0.45
                        if tq < 10.0: tq = 10.0
                        if tq <= cash:
                            pos = {"entry": ep, "quote": tq, "hold": 0}
                            cash -= tq

        if pos: cash += pos["quote"]
        net = cash - 48.0
        print(f"{mode}: Net=${net:.2f} ({net/48*100:.1f}%) | Closes={closes} | WR={wins/max(1, closes)*100:.1f}%")

if __name__ == "__main__":
    main()
