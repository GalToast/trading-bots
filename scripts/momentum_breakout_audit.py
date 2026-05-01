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
    start = now - 11 * 24 * 3600 # 11 days per qwen-trading

    print(f"🚀 AUDITING MOMENTUM BREAKOUT (11 Days) on {PRODUCT}...")
    candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # PARAMETERS (from @qwen-trading champion config)
    # LB10 + TP10% + SL7% + H50
    LOOKBACK = 10
    TP = 0.10
    SL = 0.07
    HOLD = 50
    FEE = 0.0040 # 40bps
    
    cash = 48.0
    pos = None
    closes = 0
    wins = 0
    
    # Track high-water mark for lookback
    highs = [float(c["high"]) for c in candles]
    closes_list = [float(c["close"]) for c in candles]
    
    for i in range(20, len(candles)):
        c = candles[i]
        curr_h = float(c["high"])
        curr_l = float(c["low"])
        curr_c = float(c["close"])
        
        # 1. Exit Logic
        if pos:
            pos["hold"] += 1
            exit_p = None
            if curr_h >= pos["tp_price"]:
                exit_p = pos["tp_price"]; wins += 1; closed = True
            elif curr_l <= pos["sl_price"]:
                exit_p = pos["sl_price"]; closed = True
            elif pos["hold"] >= HOLD:
                exit_p = curr_c; closed = True
                if exit_p > pos["entry"]: wins += 1
            else:
                closed = False
            
            if closed:
                units = pos["quote"] / pos["entry"]
                pnl = (exit_p - pos["entry"]) * units - (pos["quote"] * FEE) - (exit_p * units * FEE)
                cash += pos["quote"] + pnl
                closes += 1
                pos = None
        
        # 2. Entry Logic
        if pos is None and cash >= 10.0:
            # Check if current high breaks previous 10-bar high
            lookback_high = max(highs[i-LOOKBACK:i])
            if curr_h > lookback_high:
                # ENTRY: We assume we fill at the MOMENT the high is broken? 
                # NO. In a real market, if high is broken, we buy at market.
                # Market price will be >= lookback_high.
                ep = lookback_high * 1.001 # Assume 10bps slippage on breakout
                tq = cash * 0.95
                pos = {
                    "entry": ep,
                    "tp_price": ep * (1 + TP),
                    "sl_price": ep * (1 - SL),
                    "quote": tq,
                    "hold": 0
                }
                cash -= tq

    net = cash - 48.0
    wr = wins / max(1, closes) * 100
    print(f"\nAUDIT RESULTS:")
    print(f"Net Profit: ${net:.2f} ({(net/48)*100:.1f}%)")
    print(f"Closes: {closes} | WR={wr:.1f}%")
    
    # 3. CRITICAL STRESS TEST: Toxic Breakout (Fakeouts)
    # What if we only fill if the breakout holds for 1 bar?
    print("\n--- STRESS TEST: 1-Bar Confirmation ---")
    cash = 48.0; pos = None; closes = 0; wins = 0
    for i in range(20, len(candles)):
        c = candles[i]
        curr_h = float(c["high"]); curr_l = float(c["low"]); curr_c = float(c["close"])
        if pos:
            pos["hold"] += 1; exit_p = None
            if curr_h >= pos["tp_price"]: exit_p = pos["tp_price"]; wins += 1; closed = True
            elif curr_l <= pos["sl_price"]: exit_p = pos["sl_price"]; closed = True
            elif pos["hold"] >= HOLD: exit_p = curr_c; closed = True; 
            if exit_p > pos["entry"]: wins += 1
            if closed:
                units = pos["quote"] / pos["entry"]
                pnl = (exit_p - pos["entry"]) * units - (pos["quote"] * FEE) - (exit_p * units * FEE)
                cash += pos["quote"] + pnl; closes += 1; pos = None
        if pos is None and cash >= 10.0:
            lookback_high = max(highs[i-LOOKBACK:i])
            # Only enter if PREVIOUS candle closed ABOVE the breakout high (Confirmation)
            if float(candles[i-1]["close"]) > lookback_high:
                ep = float(c["open"])
                tq = cash * 0.95
                pos = {"entry": ep, "tp_price": ep * (1 + TP), "sl_price": ep * (1 - SL), "quote": tq, "hold": 0}
                cash -= tq
    print(f"Net Profit (Confirmed): ${cash-48:.2f} | WR={wins/max(1, closes)*100:.1f}%")

if __name__ == "__main__":
    main()
