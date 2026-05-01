import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

LEADER = "BAL-USD"
LAGGER = "IOTX-USD"

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

    print(f"🚀 INTER-ASSET ARBITRAGE: Using {LEADER} to rake {LAGGER}'s pockets...")
    leader_candles = fetch_candles(client, LEADER, start, now, "ONE_MINUTE")
    lagger_candles = fetch_candles(client, LAGGER, start, now, "ONE_MINUTE")
    
    lagger_lookup = {int(c["start"]): c for c in lagger_candles}
    
    FEE_RATE = 0.0025 # 25bps
    
    cash = 1000.0
    closes = 0
    wins = 0
    
    for i in range(1, len(leader_candles)):
        c = leader_candles[i]
        ts = int(c["start"])
        
        # Check if LEADER moved UP > 0.5% in 1 minute
        o = float(c["open"]); cl = float(c["close"])
        if (cl - o) / o >= 0.005:
            # LEADER SURGED!
            # Act on LAGGER in the SAME minute (assuming we saw the first 10s of the move)
            if ts in lagger_lookup:
                lc = lagger_lookup[ts]
                lo = float(lc["open"]); lh = float(lc["high"]); lcl = float(lc["close"])
                
                # We assume we can still get the 'Open' price or close to it
                ep = lo
                # Exit at the close of the same minute (High frequency scalp)
                exit_p = lcl
                
                pnl = (exit_p - ep) / ep * 100.0 - 0.50 # 50bps round trip
                cash += (pnl / 100.0) * 100.0 # $100 trade
                closes += 1
                if pnl > 0: wins += 1

    print("\n--- RESULTS ---")
    print(f"Total Arbitrage Ops: {closes}")
    print(f"Wins: {wins} | WR: {wins/max(1, closes)*100:.1f}%")
    print(f"Net Profit: ${cash-1000:.2f}")

if __name__ == "__main__":
    main()
