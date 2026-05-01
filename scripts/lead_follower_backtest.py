import json
import time
import sys
import os
import math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

LEADER = "RAVE-USD"
FOLLOWER = "LRDS-USD"
PRODUCTS = [LEADER, FOLLOWER]

def fetch_candles(client, pid, start, end):
    chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="ONE_MINUTE")
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

    print(f"🚀 LEAD-FOLLOWER AUDIT: Testing RAVE -> LRDS Lag...")
    leader_candles = fetch_candles(client, LEADER, start, now)
    follower_candles = fetch_candles(client, FOLLOWER, start, now)
    
    follower_lookup = {int(c["start"]): c for c in follower_candles}
    all_times = sorted(list(set(int(c["start"]) for c in leader_candles)))

    for mode in ["Baseline (LRDS RSI MR)", "Lead-Follower Sniper (RAVE Lead)"]:
        cash = 48.0; pos = None; closes = 0; wins = 0; history_leader = []; history_follower = []
        
        for t in all_times:
            if t not in follower_lookup: continue
            lc = [c for c in leader_candles if int(c["start"]) == t][0]
            fc = follower_lookup[t]; cl_l = float(lc["close"]); cl_f = float(fc["close"])
            history_leader.append(cl_l); history_follower.append(cl_f)
            if len(history_leader) > 20: history_leader.pop(0)
            if len(history_follower) > 20: history_follower.pop(0)
            
            if pos:
                pos["hold"] += 1; fh = float(fc["high"]); fcl = float(fc["close"])
                if fh >= pos["tp"]:
                    pnl = (pos["tp"] - pos["ep"]) / pos["ep"] * pos["quote"] - (2 * pos["quote"] * 0.0025)
                    cash += pos["quote"] + pnl; closes += 1; wins += 1; pos = None
                elif pos["hold"] >= 5:
                    pnl = (fcl - pos["ep"]) / pos["ep"] * pos["quote"] - (2 * pos["quote"] * 0.0025)
                    cash += pos["quote"] + pnl; closes += 1
                    if fcl > pos["ep"]: wins += 1
                    pos = None
            
            if pos is None and cash >= 10.0:
                if mode == "Baseline (LRDS RSI MR)":
                    rsi_f = compute_rsi(history_follower[:-1], 3)
                    if rsi_f <= 30:
                        ep = float(fc["open"]); tq = cash * 0.95
                        pos = {"ep": ep, "tp": ep * 1.02, "quote": tq, "hold": 0}; cash -= tq
                else:
                    rsi_l = compute_rsi(history_leader[:-1], 3)
                    if rsi_l <= 30:
                        if float(lc["close"]) > float(lc["open"]):
                            ep = float(fc["open"]); tq = cash * 0.95
                            pos = {"ep": ep, "tp": ep * 1.02, "quote": tq, "hold": 0}; cash -= tq

        net = cash - 48.0; wr = wins / max(1, closes) * 100
        print(f"\n{mode}: Net Profit: ${net:.2f} ({(net/48)*100:.1f}%) | WR: {wr:.1f}% | Closes: {closes}")

if __name__ == "__main__":
    main()
