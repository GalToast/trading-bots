import json
import time
import sys
import os
import math
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
    start = now - 72 * 3600 # 72 hours

    print(f"🚀 STRUCTURAL SNIPER BACKTEST on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    for mode in ["Pure RSI(3)", "Structural Sniper (Gated)"]:
        cash = 48.0; pos = None; closes = 0; wins = 0; history = []; vol_history = []
        for i in range(20, len(m1_candles)):
            c = m1_candles[i]; cl = float(c["close"]); h = float(c["high"]); l = float(c["low"]); o = float(c["open"]); v = float(c.get("volume", 0))
            history.append(cl)
            vol_history.append(v)
            if len(history) > 50: history.pop(0)
            if len(vol_history) > 50: vol_history.pop(0)
            
            if pos:
                pos["hold"] += 1; rsi = compute_rsi(history, 3)
                if rsi >= 80 or cl >= pos["tp"] or pos["hold"] >= 24:
                    exit_p = cl; units = pos["quote"] / pos["ep"]
                    pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * 0.0040) - (exit_p * units * 0.0040)
                    cash += pos["quote"] + pnl; closes += 1
                    if exit_p > pos["ep"]: wins += 1
                    pos = None
            
            if pos is None and cash >= 10.0:
                rsi_prev = compute_rsi(history[:-1], 3)
                if rsi_prev <= 30:
                    if mode == "Pure RSI(3)":
                        ep = o; tq = cash * 0.95; pos = {"ep": ep, "tp": ep * 1.25, "quote": tq, "hold": 0}; cash -= tq
                    else:
                        if cl > o:
                            avg_vol = sum(vol_history[-10:]) / 10
                            if v < 3.0 * avg_vol:
                                ep = cl; tq = cash * 0.95; pos = {"ep": ep, "tp": ep * 1.25, "quote": tq, "hold": 0}; cash -= tq

        net = cash - 48.0
        wr = wins / max(1, closes) * 100
        print(f"\n{mode}: Net Profit: ${net:.2f} ({(net/48)*100:.1f}%) | Closes: {closes} | WR={wr:.1f}%")

if __name__ == "__main__":
    main()
