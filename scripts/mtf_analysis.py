import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "FIFTEEN_MINUTE": chunk_sec = 300 * 15 * 60
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

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for Multi-Timeframe RSI analysis...")
    m5_data = {}
    m15_data = {}
    for pid in TOP_5:
        m5_data[pid] = fetch_candles(client, pid, start, now, "FIVE_MINUTE")
        m15_data[pid] = fetch_candles(client, pid, start, now, "FIFTEEN_MINUTE")

    for pid in TOP_5:
        m5 = m5_data[pid]
        m15_lookup = {int(c["start"]): float(c["close"]) for c in m15_data[pid]}
        m15_starts = sorted(m15_lookup.keys())
        
        for mode in ["Baseline (M5 < 30)", "Multi-TF (M5 < 30 & M15 < 40)"]:
            cash = 1000.0; quote = 24.0; pos = None; closes = 0; wins = 0
            m5_closes = []
            
            for i in range(20, len(m5)):
                c = m5[i]; ts = int(c["start"]); cl = float(c["close"])
                m5_closes.append(cl)
                if len(m5_closes) > 20: m5_closes.pop(0)
                
                m15_ts = (ts // 900) * 900 - 900
                m15_hist = [m15_lookup[t] for t in m15_starts if t <= m15_ts]
                
                if pos:
                    pos["hold"] += 1; exit_p = None
                    if float(c["high"]) >= pos["tp"]: exit_p = pos["tp"]
                    elif float(c["low"]) <= pos["sl"]: exit_p = pos["sl"]
                    elif pos["hold"] >= 12: exit_p = cl
                    if exit_p:
                        pnl = (exit_p - pos["ep"]) / pos["ep"] * quote - (2 * quote * 0.0040)
                        cash += quote + pnl; closes += 1
                        if exit_p > pos["ep"]: wins += 1
                        pos = None
                
                if pos is None:
                    rsi5 = compute_rsi(m5_closes, 7); rsi15 = compute_rsi(m15_hist, 7)
                    signal = False
                    if mode == "Baseline (M5 < 30)":
                        if rsi5 <= 30: signal = True
                    else:
                        if rsi5 <= 30 and rsi15 <= 40: signal = True
                    if signal:
                        ep = float(c["open"])
                        pos = {"ep": ep, "tp": ep * 1.05, "sl": ep * 0.97, "hold": 0}
                        cash -= quote
            
            print(f"{pid} | {mode:30s} | Net=${cash-1000:6.2f} | Closes={closes:3d} | WR={wins/max(1, closes)*100:4.1f}%")

if __name__ == "__main__":
    main()
