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

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 PREDATORY GRINDER BENCHMARK on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # FEE RATE (Advanced 1 unlocked = 25bps)
    FEE_RATE = 0.0025
    
    for mode in ["Blind Grinder (Baseline)", "Predatory Grinder (The Fortress)"]:
        cash = 1000.0
        inventory = 0.0
        entry_p = 0.0
        closes = 0
        wins = 0
        losses = 0
        
        history = []
        vol_history = []
        
        for i in range(20, len(m1_candles)):
            c = m1_candles[i]
            o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            v = float(c.get("volume", 0))
            
            history.append(cl)
            vol_history.append(v)
            if len(history) > 50: history.pop(0)
            if len(vol_history) > 50: vol_history.pop(0)
            
            # 1. Exit Logic (Maker Sell at Ask)
            if inventory > 0:
                # Boost target to 1.5% to clear the 0.5% fee floor with fat margin
                target = entry_p * 1.015
                
                if h >= target:
                    # Successful Spread Capture
                    cash_back = (inventory * target) * (1 - FEE_RATE)
                    pnl = cash_back - (inventory * entry_p * (1 + FEE_RATE))
                    cash += cash_back
                    closes += 1; wins += 1; inventory = 0.0
                    continue
                
                # Emergency Stop (1.5% drop)
                if l < entry_p * 0.985:
                    exit_p = entry_p * 0.985
                    cash_back = (inventory * exit_p) * (1 - 0.0060) # Taker exit
                    cash += cash_back
                    closes += 1; losses += 1; inventory = 0.0
                    continue

            # 2. Entry Logic (Maker Buy at Bid)
            if inventory == 0 and cash >= 100.0:
                # Proxy for Spread > 0.85%
                range_pct = (h - l) / l * 100
                if range_pct >= 0.85:
                    
                    if mode == "Blind Grinder (Baseline)":
                        # Entry at Open (Assume fill if low < open)
                        if l < o:
                            inventory = 100.0 / o
                            entry_p = o
                            cash -= (100.0 * (1 + FEE_RATE))
                    else:
                        # PREDATORY GATING
                        # Gate 1: Volatility Floor (>1.5%)
                        vol = (max(history[-20:]) - min(history[-20:])) / min(history[-20:])
                        if vol >= 0.015:
                            # Gate 2: Aggressor Confirmation (Price moving UP from wick)
                            # We enter if the current price is > Open (Buyer momentum)
                            if cl > o:
                                # Gate 3: Magnetic Offset (Entering near .00/.05)
                                mag_level = round(o * 20) / 20.0
                                if abs(o - mag_level) / mag_level <= 0.005:
                                    inventory = 100.0 / o
                                    entry_p = o
                                    cash -= (100.0 * (1 + FEE_RATE))

        net = cash - 1000.0
        wr = wins / max(1, closes) * 100
        print(f"\n{mode}:")
        print(f"  Net Profit: ${net:8.2f}")
        print(f"  Win Rate:   {wr:4.1f}%")
        print(f"  Total Closes: {closes}")

if __name__ == "__main__":
    main()
