import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Load optimal params
PARAMS_PATH = os.path.join(os.path.dirname(__file__), '..', 'reports', 'rsi_optimal_params.json')
with open(PARAMS_PATH, 'r') as f:
    OPTIMAL_PARAMS = json.load(f)

# Top 5 RSI assets by isolated backtest profit
TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
PRODUCTS = [p for p in TOP_5 if p in OPTIMAL_PARAMS]

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
        except Exception:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=7):
    if len(closes) < period + 1:
        return 50.0

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

    print("Fetching 72h data for RSI Compound God Mode...")
    product_candles = {}
    for pid in PRODUCTS:
        c = fetch_candles(client, pid, start, now)
        product_candles[pid] = c
        print(f"  {pid}: {len(c)} candles")

    all_times = set()
    for pid, candles in product_candles.items():
        for c in candles:
            all_times.add(int(c["start"]))
    all_times = sorted(list(all_times))

    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            t = int(c["start"])
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t][pid] = {
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume", 0)),
                "start": int(c["start"])
            }

    history = {p: [] for p in PRODUCTS}
    
    cash = 48.0
    positions = []
    max_concurrent = 1
    closes = 0
    wins = 0
    total_volume = 0.0
    total_fees_paid = 0.0

    print("\n--- SIMULATING LONG-ONLY RSI GOD MODE ---")
    
    print(f"Loaded params for {len(PRODUCTS)} products.")
    print(f"Total time steps: {len(all_times)}")
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        
        # Calculate current fee tier based on trailing volume
        if total_volume >= 50000:
            fee_rate = 0.0015 # 15 bps
        elif total_volume >= 10000:
            fee_rate = 0.0025 # 25 bps
        else:
            fee_rate = 0.0040 # 40 bps
            
        # Update history
        for pid, c in tick.items():
            history[pid].append(c["close"])
            if len(history[pid]) > 50: # Increased history for better RSI
                history[pid].pop(0)
            
        # 1. Process exits
        still_open = []
        for pos in positions:
            pid = pos["pid"]
            if pid in tick:
                c = tick[pid]
                h = c["high"]
                l = c["low"]
                cl = c["close"]
                ep = pos["entry"]
                tp = pos["target"]
                sp = pos["stop"]
                tq = pos["quote"]
                units = tq / ep
                
                pos["hold_bars"] += 1
                closed = False
                
                params = OPTIMAL_PARAMS[pid]
                if len(history[pid]) >= params["p"] + 1:
                    rsi = compute_rsi(history[pid], params["p"])
                else:
                    rsi = 50.0
                
                if h >= tp:
                    gross = (tp - ep) * units
                    ef = tq * fee_rate
                    xf = tp * units * fee_rate
                    net = gross - ef - xf
                    cash += tq + net
                    closes += 1; wins += 1
                    total_volume += tq + (tp * units)
                    total_fees_paid += ef + xf
                    closed = True
                elif l <= sp:
                    gross = (sp - ep) * units
                    ef = tq * fee_rate
                    xf = sp * units * fee_rate
                    net = gross - ef - xf
                    cash += tq + net
                    closes += 1
                    total_volume += tq + (sp * units)
                    total_fees_paid += ef + xf
                    closed = True
                elif rsi >= params["ob"]:
                    gross = (cl - ep) * units
                    ef = tq * fee_rate
                    xf = cl * units * fee_rate
                    net = gross - ef - xf
                    cash += tq + net
                    closes += 1
                    total_volume += tq + (cl * units)
                    total_fees_paid += ef + xf
                    if cl > ep: wins += 1
                    closed = True
                elif pos["hold_bars"] >= params["h"]:
                    gross = (cl - ep) * units
                    ef = tq * fee_rate
                    xf = cl * units * fee_rate
                    net = gross - ef - xf
                    cash += tq + net
                    closes += 1
                    total_volume += tq + (cl * units)
                    total_fees_paid += ef + xf
                    if cl > ep: wins += 1
                    closed = True
                
                if not closed:
                    still_open.append(pos)
            else:
                still_open.append(pos)
                
        positions = still_open
        
        # 2. Process entries
        free_slots = max_concurrent - len(positions)
        if free_slots > 0 and cash >= 10.0:
            candidates = []
            for pid, c in tick.items():
                if any(p["pid"] == pid for p in positions):
                    continue
                
                params = OPTIMAL_PARAMS.get(pid)
                if not params: continue
                
                if len(history[pid]) >= params["p"] + 2:
                    rsi_prev = compute_rsi(history[pid][:-1], params["p"])
                    # Enter if the PREVIOUS candle closed oversold
                    if rsi_prev <= params["os"]:
                        candidates.append({"pid": pid, "rsi": rsi_prev, "c": c, "params": params})
            
            # Sort by lowest RSI (deepest oversold)
            candidates.sort(key=lambda x: x["rsi"])
            
            for cand in candidates[:free_slots]:
                if cash < 10.0: break
                
                pid = cand["pid"]
                c = cand["c"]
                params = cand["params"]
                
                alloc_fraction = 1.0 / free_slots
                tq = min(cash * 0.95, cash * alloc_fraction * 0.95)
                if tq < 10.0: continue
                
                # Enter at open of current candle (simulating filling immediately after previous candle closed)
                ep = c["open"]
                tp = ep * (1 + params["t"] / 100.0)
                sp = ep * (1 - params["s"] / 100.0)
                
                positions.append({
                    "pid": pid,
                    "entry": ep,
                    "target": tp,
                    "stop": sp,
                    "quote": tq,
                    "hold_bars": 0
                })
                cash -= tq
                free_slots -= 1

    for pos in positions:
        cash += pos["quote"]
        
    wr = wins / closes * 100 if closes > 0 else 0
    net = cash - 48.0
    roi = net / 48.0 * 100
    
    print(f"\nFinal Bankroll: ${cash:.2f}")
    print(f"Net Profit: ${net:.2f} ({roi:.1f}%)")
    print(f"Closes: {closes} (Win Rate: {wr:.1f}%)")
    print(f"Total Trading Volume: ${total_volume:.2f}")
    print(f"Total Fees Paid: ${total_fees_paid:.2f}")
    
    if total_volume > 50000:
         print("-> Broke $50k Tier (15bps)!")
    elif total_volume > 10000:
         print("-> Broke $10k Tier (25bps)!")

if __name__ == "__main__":
    main()
