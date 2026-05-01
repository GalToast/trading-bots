import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Full volatile universe from our previous studies
PRODUCTS = [
    "VIRTUAL-USD", "MOG-USD", "A8-USD", "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
    "IRYS-USD", "CFG-USD", "BOBBOB-USD", "DASH-USD", "FARTCOIN-USD",
]

def compute_volatility(closes):
    if len(closes) < 2: return 0.0
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean)**2 for r in returns) / len(returns)
    return math.sqrt(variance)

def main():
    client = CoinbaseAdvancedClient()
    print(f"Scanning {len(PRODUCTS)} coins for ACTIVE Regimes...")
    
    active_coins = []
    for pid in PRODUCTS:
        try:
            now = int(time.time())
            # Fetch 24h of M5 data
            resp = client.market_candles(pid, start=now-24*3600, end=now, granularity="FIVE_MINUTE")
            candles = resp.get("candles", [])
            if len(candles) < 50: 
                time.sleep(1.0)
                continue
            
            closes = [float(c["close"]) for c in candles]
            volatility = compute_volatility(closes)
            
            regime = "DEAD"
            if volatility >= 0.03: regime = "PUMP"
            elif volatility >= 0.015: regime = "ACTIVE"
            
            if regime != "DEAD":
                active_coins.append({"pid": pid, "regime": regime, "vol": round(volatility*100, 2)})
                print(f"  {pid}: {regime} ({volatility*100:.2f}%)")
            
            time.sleep(1.0) # Robust delay
        except Exception as e:
            print(f"Error {pid}: {e}")
            time.sleep(2.0)

    print("\n--- ACTIVE REGIME REPORT ---")
    if not active_coins:
        print("No coins currently in ACTIVE or PUMP regime.")
    else:
        for c in active_coins:
            print(f"{c['pid']} | {c['regime']} | Vol={c['vol']}%")

if __name__ == "__main__":
    main()
