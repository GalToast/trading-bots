import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
BTC = "BTC-USD"

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "FIFTEEN_MINUTE": chunk_sec = 300 * 15 * 60
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
    now = int(time.time()); start = now - 11 * 24 * 3600

    print("Fetching 11d data for GOBBLIN SWARM Backtest...")
    m5_data = {}
    m15_data = {}
    for pid in TOP_5:
        m5_data[pid] = fetch_candles(client, pid, start, now, "FIVE_MINUTE")
        m15_data[pid] = fetch_candles(client, pid, start, now, "FIFTEEN_MINUTE")

    all_times = sorted(list(set(int(c["start"]) for pid in m5_data for c in m5_data[pid])))
    time_lookup_m5 = {}
    for pid, candles in m5_data.items():
        for c in candles:
            time_lookup_m5.setdefault(int(c["start"]), {})[pid] = c

    time_lookup_m15 = {}
    for pid, candles in m15_data.items():
        for c in candles:
            time_lookup_m15.setdefault(int(c["start"]), {})[pid] = c

    for mode in ["RAVE Only (Main Ceiling)", "GOBBLIN SWARM (Top 5 + M15 Gate)"]:
        cash = 48.0; positions = []; max_concurrent = 1 if "Only" in mode else 3
        closes_count = 0; wins = 0; total_volume = 0.0
        histories_m5 = {p: [] for p in TOP_5}; histories_m15 = {p: [] for p in TOP_5}
        
        for t in all_times:
            if t in time_lookup_m5:
                for pid, c in time_lookup_m5[t].items():
                    histories_m5[pid].append(float(c["close"]))
                    if len(histories_m5[pid]) > 50: histories_m5[pid].pop(0)
            m15_t = (t // 900) * 900
            if m15_t in time_lookup_m15:
                for pid, c in time_lookup_m15[m15_t].items():
                    if not histories_m15[pid] or histories_m15[pid][-1] != float(c["close"]):
                        histories_m15[pid].append(float(c["close"]))
                        if len(histories_m15[pid]) > 50: histories_m15[pid].pop(0)

            still_open = []
            for pos in positions:
                pid = pos["pid"]
                if t in time_lookup_m5 and pid in time_lookup_m5[t]:
                    c = time_lookup_m5[t][pid]; pos["hold"] += 1; rsi = compute_rsi(histories_m5[pid], 3)
                    if rsi >= 80 or pos["hold"] >= 24:
                        exit_p = float(c["close"]); units = pos["quote"] / pos["ep"]
                        pnl = (exit_p - pos["ep"]) * units - (pos["quote"] * 0.0040) - (exit_p * units * 0.0040)
                        cash += pos["quote"] + pnl; total_volume += pos["quote"] + (exit_p * units)
                        closes_count += 1
                        if exit_p > pos["ep"]: wins += 1
                        continue
                still_open.append(pos)
            positions = still_open

            target_pids = ["RAVE-USD"] if "Only" in mode else TOP_5
            free_slots = max_concurrent - len(positions)
            if free_slots > 0 and cash >= 10.0:
                candidates = []
                for pid in target_pids:
                    if t not in time_lookup_m5 or pid not in time_lookup_m5[t]: continue
                    if any(p["pid"] == pid for p in positions): continue
                    if len(histories_m5[pid]) < 5: continue
                    rsi_prev = compute_rsi(histories_m5[pid][:-1], 3)
                    if rsi_prev <= 30:
                        if len(histories_m15[pid]) < 4: continue
                        m15_range = (max(histories_m15[pid][-4:]) - min(histories_m15[pid][-4:])) / min(histories_m15[pid][-4:])
                        if m15_range <= 0.05:
                            candidates.append({"pid": pid, "rsi": rsi_prev, "c": time_lookup_m5[t][pid]})
                if candidates:
                    candidates.sort(key=lambda x: x["rsi"])
                    for cand in candidates[:free_slots]:
                        if cash < 10.0: break
                        pid = cand["pid"]; tq = cash / free_slots * 0.95
                        if tq < 10.0: tq = 10.0
                        if tq > cash: break
                        ep = float(cand["c"]["open"])
                        positions.append({"pid": pid, "ep": ep, "quote": tq, "hold": 0})
                        cash -= tq; free_slots -= 1

        if positions:
            for p in positions: cash += p["quote"]
        net = cash - 48.0
        wr = wins/max(1, closes_count)*100
        print(f"\n{mode}: Net=${net:.2f} ({net/48*100:.1f}%) | Closes={closes_count} | WR={wr:.1f}%")

if __name__ == "__main__":
    main()
