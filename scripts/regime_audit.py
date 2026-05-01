import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"

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

def compute_volatility(closes):
    if len(closes) < 2: return 0.0
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean)**2 for r in returns) / len(returns)
    return math.sqrt(variance)

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 14 * 24 * 3600 # 14 days

    print(f"Fetching 14d data for {PRODUCT} Regime Audit...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    
    # Analyze in 72h windows
    window_sec = 72 * 3600
    
    print("\n--- REGIME AUDIT (RAVE-USD) ---")
    print(f"{ 'Window':20s} | { 'Vol':8s} | { 'Volume':10s} | { 'Returns':10s}")
    
    for i in range(0, 14, 3):
        ws = now - (i + 3) * 24 * 3600
        we = now - i * 24 * 3600
        
        window_c = [c for c in rave_candles if ws <= int(c["start"]) < we]
        if not window_c: continue
        
        closes = [float(c["close"]) for c in window_c]
        vols = [float(c.get("volume", 0)) for c in window_c]
        
        vol = compute_volatility(closes)
        total_v = sum(vols)
        # 72h return
        ret = (closes[-1] - closes[0]) / closes[0] * 100
        
        label = f"Day {i+3}-{i} ago"
        if i == 0: label = "LAST 72H"
        
        print(f"{label:20s} | {vol*100:7.2f}% | {total_v:10.0f} | {ret:9.2f}%")

if __name__ == "__main__":
    main()
