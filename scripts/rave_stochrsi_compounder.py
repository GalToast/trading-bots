import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

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

def compute_rsi(closes, period=4):
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

def compute_stoch_rsi(rsi_history, period=3):
    if len(rsi_history) < period: return 0.5
    low_rsi = min(rsi_history[-period:])
    high_rsi = max(rsi_history[-period:])
    if high_rsi == low_rsi: return 0.5
    return (rsi_history[-1] - low_rsi) / (high_rsi - low_rsi)

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h data for {PRODUCT} StochRSI Compounder...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    
    # NEW KING PARAMS from @qwen-trading
    TP_PCT = 25.0
    SL_PCT = 3.0
    RSI_PERIOD = 4
    STOCH_PERIOD = 3
    STOCH_OS = 0.05
    
    for mode in ["Qwen King (Fixed $48)", "Gemini God (95% Compound)"]:
        cash = 48.0
        pos = None
        closes = 0
        wins = 0
        total_volume = 0.0
        
        rsi_history = []
        closes_list = []
        
        for i in range(len(rave_candles)):
            c = rave_candles[i]
            h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            closes_list.append(cl)
            
            if len(closes_list) >= RSI_PERIOD + 1:
                rsi = compute_rsi(closes_list, RSI_PERIOD)
                rsi_history.append(rsi)
            
            if len(rsi_history) > 50: rsi_history.pop(0)
            
            # Process Exit
            if pos:
                pos["hold"] += 1
                exit_p = None
                if h >= pos["tp"]:
                    exit_p = pos["tp"]; wins += 1; closed = True
                elif l <= pos["sl"]:
                    exit_p = pos["sl"]; closed = True
                elif pos["hold"] >= 24:
                    exit_p = cl; closed = True
                    if exit_p > pos["ep"]: wins += 1
                else:
                    closed = False
                
                if closed:
                    units = pos["quote"] / pos["ep"]
                    pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * 0.0040) - (exit_p * units * 0.0040)
                    cash += pos["quote"] + pnl
                    total_volume += pos["quote"] + (exit_p * units)
                    closes += 1
                    pos = None
            
            # Process Entry
            if pos is None and cash >= 10.0:
                if len(rsi_history) >= STOCH_PERIOD + 1:
                    rsi_now = rsi_history[-1]
                    stoch_rsi = compute_stoch_rsi(rsi_history, STOCH_PERIOD)
                    
                    if rsi_now <= 30 and stoch_rsi <= STOCH_OS:
                        ep = float(c["open"])
                        tq = 48.0 if mode == "Qwen King (Fixed $48)" else cash * 0.95
                        if tq > cash: tq = cash
                        
                        if tq >= 10.0:
                            pos = {
                                "pid": PRODUCT, "ep": ep, "quote": tq, "hold": 0,
                                "tp": ep * (1 + TP_PCT / 100.0),
                                "sl": ep * (1 - SL_PCT / 100.0)
                            }
                            cash -= tq

        if pos: cash += pos["quote"]
        net = cash - 48.0
        wr = wins/max(1, closes)*100
        print(f"\n{mode}:")
        print(f"  Net Profit: ${net:.2f} ({net/48*100:.1f}%)")
        print(f"  Closes: {closes} | WR={wr:.1f}%")
        print(f"  Total Volume: ${total_volume:.2f}")

if __name__ == "__main__":
    main()
