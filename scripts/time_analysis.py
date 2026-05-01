import json
import time
from datetime import datetime, timezone
import sys
import os

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
    start = now - 14 * 24 * 3600 # 14 days

    print("Fetching 14d data for Time-of-Day analysis...")
    product_candles = {}
    for pid in TOP_5:
        product_candles[pid] = fetch_candles(client, pid, start, now)

    hourly_pnl = {h: 0.0 for h in range(24)}
    hourly_trades = {h: 0 for h in range(24)}

    for pid in TOP_5:
        candles = product_candles[pid]
        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        
        pos = None
        for i in range(10, len(candles)):
            c = candles[i]
            ts = int(c["start"])
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            hour = dt.hour
            
            if pos:
                pos["hold"] += 1
                exit_p = None
                if highs[i] >= pos["tp"]:
                    exit_p = pos["tp"]
                elif lows[i] <= pos["sl"]:
                    exit_p = pos["sl"]
                elif pos["hold"] >= 12:
                    exit_p = closes[i]
                
                if exit_p:
                    pnl = (exit_p - pos["ep"]) / pos["ep"] * 24.0 - (2 * 24.0 * 0.0040)
                    hourly_pnl[pos["start_hour"]] += pnl
                    hourly_trades[pos["start_hour"]] += 1
                    pos = None
            
            if pos is None:
                rsi = compute_rsi(closes[i-8:i], 7)
                if rsi <= 30:
                    ep = float(c["open"])
                    tp = ep * 1.05
                    sl = ep * 0.97
                    pos = {"ep": ep, "tp": tp, "sl": sl, "hold": 0, "start_hour": hour}

    print("\n--- HOURLY PERFORMANCE (UTC) ---")
    for h in range(24):
        p = hourly_pnl[h]
        t = hourly_trades[h]
        avg = p / t if t > 0 else 0
        print(f"{h:02d}:00 | Net=${p:6.2f} | Trades={t:2d} | Avg=${avg:5.2f}")

if __name__ == "__main__":
    main()
