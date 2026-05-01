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
            continue
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 PREDATORY TRAP AUDIT on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    FEE_RATE = 0.0025
    
    # THE TRAP:
    # 1. Price is near a Magnetic Level ($0.05).
    # 2. RSI is oversold (< 30).
    # 3. We place a Limit Buy at exactly the Magnetic Level.
    # 4. We exit at a 1.5% bounce. 
    
    wins = 0
    losses = 0
    total_pnl_usd = 0.0
    
    for i in range(20, len(m1_candles)):
        c = m1_candles[i]
        o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
        
        # Magnetic Level
        mag = round(o * 20) / 20.0
        if abs(o - mag) / mag <= 0.005: # Within 0.5%
            # Entry Target
            ep = mag
            if l <= ep:
                # FILLED!
                # We check next 30 bars for recovery
                success = False
                for j in range(1, 31):
                    if i + j < len(m1_candles):
                        nc = m1_candles[i+j]
                        if float(nc["high"]) >= ep * 1.015:
                            success = True
                            break
                        if float(nc["low"]) < ep * 0.985: # 1.5% Stop
                            break
                
                # Accounting
                if success:
                    # Win: 1.5% gross - 0.5% fees = 1.0% net
                    total_pnl_usd += 1.0 # 1% of $100
                    wins += 1
                else:
                    # Loss: -1.5% gross - 0.8% fees = -2.3% net
                    total_pnl_usd -= 2.3
                    losses += 1

    print("\n--- RESULTS ---")
    print(f"Total Traps Sprung: {wins + losses}")
    print(f"Wins: {wins} | Losses: {losses} | WR: {wins/(wins+losses)*100:.1f}%")
    print(f"Net PnL (as % of quote): {total_pnl_usd:.2f}%")

if __name__ == "__main__":
    main()
