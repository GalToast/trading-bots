import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "MOG-USD"

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
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

def compute_volatility(closes):
    if len(closes) < 2: return 0.0
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean)**2 for r in returns) / len(returns)
    return math.sqrt(variance)

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 PREDATORY SCAN on {PRODUCT}...")
    try:
        m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
        if not m1_candles:
            print("No data for MOG-USD.")
            return

        closes = [float(c["close"]) for c in m1_candles]
        vol = compute_volatility(closes)
        
        print(f"  Volatility (M1): {vol*100:.2f}%")
        
        # Test Magnetic 
        magnetic_touches = 0
        total_magnetic = 0
        for c in m1_candles:
            o = float(c["open"]); l = float(c["low"]); h = float(c["high"])
            # MOG is 0.000003 region. Magnetic levels are different.
            # Let's check power-of-ten magnets or significant decimal steps.
            # Round to 7 decimals for MOG
            pass # Skipping magnetic for meme-precision for now
            
        # Test Spread
        resp = client.best_bid_ask([PRODUCT])
        book = resp["pricebooks"][0]
        bid = float(book["bids"][0]["price"])
        ask = float(book["asks"][0]["price"])
        spread = (ask - bid) / bid * 100
        print(f"  Live Spread: {spread:.3f}%")
        
        # Test Wick Recovery
        ops = 0; success = 0
        for i in range(len(m1_candles)):
            o = float(m1_candles[i]["open"]); l = float(m1_candles[i]["low"]); cl = float(m1_candles[i]["close"])
            if (o - l) / o >= 0.015: # 1.5% wick
                ops += 1
                if cl >= o: success += 1
        
        print(f"  Wick Recoveries (>1.5% drop): {success}/{ops} ({success/max(1, ops)*100:.1f}%)")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
