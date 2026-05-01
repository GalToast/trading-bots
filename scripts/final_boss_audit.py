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

    print(f"🚀 FINAL BOSS AUDIT: Hardening the RAVE Edge on {PRODUCT}...")
    m1_candles = fetch_candles(client, PRODUCT, start, now, "ONE_MINUTE")
    
    # PARAMETERS from @qwen-trading
    RSI_PERIOD = 3; OS_ENTRY = 30; TP = 0.25; FEE = 0.0025
    
    for mode in ["Verified Baseline", "Hardened Sniper (Final Boss)"]:
        cash = 48.0; pos = None; closes = 0; wins = 0; drawdowns = []; peak_cash = 48.0; history = []
        for i in range(20, len(m1_candles)):
            c = m1_candles[i]; o = float(c["open"]); h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            ts = int(c["start"]); history.append(cl)
            if len(history) > 100: history.pop(0)
            
            if pos:
                pos["hold"] += 1
                if h >= pos["tp_price"] or pos["hold"] >= 48:
                    exit_p = pos["tp_price"] if h >= pos["tp_price"] else cl
                    units = pos["quote"] / pos["entry"]
                    pnl = (exit_p - pos["entry"]) * units - (pos["quote"] * FEE) - (exit_p * units * FEE)
                    cash += pos["quote"] + pnl; closes += 1
                    if exit_p > pos["entry"]: wins += 1
                    pos = None
                    if cash > peak_cash: peak_cash = cash
                    drawdowns.append((peak_cash - cash) / peak_cash * 100)
            
            if pos is None and cash >= 10.0:
                rsi_prev = compute_rsi(history[:-1], RSI_PERIOD)
                if rsi_prev <= OS_ENTRY:
                    if mode == "Verified Baseline":
                        ep = o; tq = cash * 0.95
                        pos = {"entry": ep, "tp_price": ep * (1 + TP), "quote": tq, "hold": 0}; cash -= tq
                    else:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                        if dt.hour in [12, 19, 6, 0]: continue
                        mag_level = round(o * 20) / 20.0
                        if abs(o - mag_level) / mag_level <= 0.0025:
                            ep = mag_level + 0.0001
                            if l <= ep:
                                tq = cash * 0.95
                                pos = {"entry": ep, "tp_price": ep * (1 + TP), "quote": tq, "hold": 0}; cash -= tq

        net = cash - 48.0; wr = wins / max(1, closes) * 100; max_dd = max(drawdowns) if drawdowns else 0
        print(f"\n{mode}: Net Profit: ${net:.2f} ({(net/48)*100:.1f}%) | Win Rate: {wr:.1f}% | Closes: {closes} | Max DD: {max_dd:.1f}%")

if __name__ == "__main__":
    main()
