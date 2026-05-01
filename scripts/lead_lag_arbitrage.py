import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

LEADER = "ETH-USD"
LAGGER = "AVAX-USD"
PRODUCTS = [LEADER, LAGGER]

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

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

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h data for {LEADER} -> {LAGGER} Lead-Lag Test...")
    product_candles = {}
    for pid in PRODUCTS:
        product_candles[pid] = fetch_candles(client, pid, start, now)

    # Sync
    all_times = sorted(list(set(int(c["start"]) for pid in product_candles for c in product_candles[pid])))
    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            t = int(c["start"])
            time_lookup.setdefault(t, {})[pid] = {"open": float(c["open"]), "close": float(c["close"]), "high": float(c["high"]), "low": float(c["low"])}

    cash = 1000.0
    quote = 100.0 # larger trades for larger mid-cap
    closes = 0
    wins = 0
    
    leader_history = []

    print("\n--- SIMULATING LEAD-LAG momentum ---")
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        
        # 1. Process Exit (Always exit after 1 bar)
        # We simulate buying at the open of bar T and selling at the close of bar T.
        # But we only signal if bar T-1 was a lead move.
        
        l_c = tick.get(LEADER)
        if l_c:
            ret = (l_c["close"] - l_c["open"]) / l_c["open"]
            
            # SIGNAL from current bar (will trade on NEXT bar)
            if ret >= 0.005: # ETH up > 0.5%
                # Buy LAGGER at the START of the NEXT bar? 
                # No, we assume we see the close of ETH and buy LAGGER immediately.
                # In this backfill, if ETH at time T moved, we buy LAGGER at time T+5.
                pass
        
        # Correct logic:
        # Loop T:
        # Check if ETH moved in bar T-1.
        # If yes, buy AVAX at open of bar T, sell at close of bar T.
        
        prev_t = t - 300
        if prev_t in time_lookup and LEADER in time_lookup[prev_t]:
            prev_eth = time_lookup[prev_t][LEADER]
            eth_move = (prev_eth["close"] - prev_eth["open"]) / prev_eth["open"]
            
            if eth_move >= 0.002: # ETH up > 0.2% in PREVIOUS bar
                if LAGGER in tick:
                    avax = tick[LAGGER]
                    ep = avax["open"]
                    xp = avax["close"]
                    pnl = (xp - ep) / ep * quote - (2 * quote * FEE_RATE)
                    cash += pnl
                    closes += 1
                    if xp > ep: wins += 1
                    # print(f"TRADE: ETH moved {eth_move*100:.2f}% -> AVAX { (xp-ep)/ep*100:.2f}% PnL=${pnl:.2f}")

    print(f"\nRESULTS FOR {LEADER} -> {LAGGER}:")
    print(f"Net Profit: ${cash-1000:.2f} ({(cash-1000)/10:.2f}%)")
    print(f"Closes: {closes} | WR={wins/max(1, closes)*100:.1f}%")

if __name__ == "__main__":
    main()
