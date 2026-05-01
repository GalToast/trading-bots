import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "ONE_MINUTE": chunk_sec = 300 * 60
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

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print(f"Fetching 72h data for {PRODUCT} Parabolic Backtest...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    btc_m1 = fetch_candles(client, BTC, start, now, granularity="ONE_MINUTE")
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_m1}

    # Simulation Params
    TP_PCT = 25.0
    SL_PCT = 3.0
    RSI_PERIOD = 4
    
    # 1. FIXED SIZING (Baseline)
    # 2. GEOMETRIC COMPOUNDING (Double Target)
    
    for mode in ["Fixed ($48)", "Geometric (95% Compound)"]:
        cash = 48.0
        pos = None
        closes = 0
        wins = 0
        total_volume = 0.0
        
        history = []
        
        for i in range(len(rave_candles)):
            c = rave_candles[i]
            ts = int(c["start"])
            h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            
            history.append(cl)
            if len(history) > 50: history.pop(0)
            
            # BTC Gate
            btc_gate = True
            p_t = ts - 60; p_t3 = ts - 180
            if p_t in btc_lookup and p_t3 in btc_lookup:
                mom = (btc_lookup[p_t] - btc_lookup[p_t3]) / btc_lookup[p_t3]
                if mom < -0.001: btc_gate = False
            
            # Session Gate
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            session_gate = (hour not in [12, 19, 6, 0])
            
            # Fee Tier
            if total_volume >= 50000: fr = 0.0015
            elif total_volume >= 10000: fr = 0.0025
            else: fr = 0.0040
            
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
                    pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * fr) - (exit_p * units * fr)
                    cash += pos["quote"] + pnl
                    total_volume += pos["quote"] + (exit_p * units)
                    closes += 1
                    pos = None
            
            # Process Entry
            if pos is None and cash >= 10.0 and btc_gate and session_gate:
                if len(history) >= RSI_PERIOD + 2:
                    rsi_prev = compute_rsi(history[:-1], RSI_PERIOD)
                    if rsi_prev <= 30:
                        ep = float(c["open"])
                        tq = 48.0 if mode == "Fixed ($48)" else cash * 0.95
                        if tq > cash: tq = cash
                        if tq >= 10.0:
                            pos = {
                                "ep": ep, "quote": tq, "hold": 0,
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
