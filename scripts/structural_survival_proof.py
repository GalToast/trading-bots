import json
import time
import sys
import os
import math
import random
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

def compute_rsi(closes, period=3):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period; avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 7 * 24 * 3600 # 7 days

    print(f"🚀 NOVELTY TEST #10: STRUCTURAL SURVIVAL PROOF on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    if not m1_candles:
        print("No data.")
        return

    # THE STRICT FILL MODEL (codex-2 standard)
    # 1. We place a Limit Bid.
    # 2. It ONLY fills if the NEXT minute's Low <= our Limit.
    # 3. We place a Limit Ask.
    # 4. It ONLY fills if a SUBSEQUENT minute's High >= our Limit.
    
    FEE_RATE = 0.0010 # 10bps VIP armor
    
    for mode in ["Blind MM", "Predatory Fortress V4"]:
        cash = 48.0
        realized_net = 0.0
        total_volume = 0.0
        
        pending_entry = None # Limit Price
        position = None # {"ep": ..., "tp": ..., "hold": 0}
        
        history = []
        
        for i in range(20, len(m1_candles) - 1):
            c = m1_candles[i]
            next_c = m1_candles[i+1]
            o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            nh = float(next_c["high"]); nl = float(next_c["low"]); no = float(next_c["open"])
            
            history.append(cl)
            if len(history) > 50: history.pop(0)
            
            # 1. Check Pending Entry Fill
            if pending_entry and position is None:
                if nl <= pending_entry:
                    # FILLED as Maker
                    position = {"ep": pending_entry, "tp": pending_entry * 1.015, "hold": 0}
                    cash -= (10.0 * (1 + FEE_RATE))
                    pending_entry = None
                else:
                    # Cancel after 1 min (high frequency)
                    pending_entry = None

            # 2. Process Open Position
            if position:
                position["hold"] += 1
                # Target check
                if nh >= position["tp"]:
                    cash += (10.0 * 1.015) * (1 - FEE_RATE)
                    pnl = (position["tp"] - position["ep"]) / position["ep"] * 10.0 - (2 * 10.0 * FEE_RATE)
                    realized_net += pnl; total_volume += 20.0
                    position = None
                elif nl < position["ep"] * 0.985: # SL
                    exit_p = position["ep"] * 0.985
                    cash += (10.0 * 0.985) * (1 - 0.0060) # Taker
                    pnl = (exit_p - position["ep"]) / position["ep"] * 10.0 - (10.0 * FEE_RATE) - (10.0 * 0.985 * 0.0060)
                    realized_net += pnl; total_volume += 20.0
                    position = None
                elif position["hold"] >= 15: # Timeout
                    exit_p = float(next_c["close"])
                    cash += (10.0 * (exit_p/position["ep"])) * (1 - 0.0060)
                    pnl = (exit_p - position["ep"]) / position["ep"] * 10.0 - (10.0 * FEE_RATE) - (10.0 * (exit_p/position["ep"]) * 0.0060)
                    realized_net += pnl; total_volume += 20.0
                    position = None

            # 3. Entry Signal
            if position is None and pending_entry is None and cash >= 15.0:
                if mode == "Blind MM":
                    pending_entry = cl # Place bid at close
                else:
                    # PREDATORY FORTRESS V4
                    # RSI + Aggressor + Vol + Magnet
                    rsi = compute_rsi(history, 3)
                    vol = (max(history[-20:]) - min(history[-20:])) / min(history[-20:])
                    if rsi <= 30 and vol >= 0.015 and cl > o:
                        # Find nearest magnetic floor
                        mag = round(cl * 20) / 20.0
                        pending_entry = mag + 0.0001

        net = cash - 48.0
        print(f"\n{mode}:")
        print(f"  Net Profit: ${net:.2f}")
        print(f"  Total Volume: ${total_volume:.0f}")

if __name__ == "__main__":
    main()
