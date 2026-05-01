import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Load optimal params from reports
PARAMS_PATH = os.path.join(os.path.dirname(__file__), '..', 'reports', 'rsi_optimal_params.json')
with open(PARAMS_PATH, 'r') as f:
    OPTIMAL_PARAMS = json.load(f)

# Update RAVE, BLUR with the 8% TP discovery from @assist
if "RAVE-USD" in OPTIMAL_PARAMS: OPTIMAL_PARAMS["RAVE-USD"]["t"] = 8.0
if "BLUR-USD" in OPTIMAL_PARAMS: OPTIMAL_PARAMS["BLUR-USD"]["t"] = 8.0

PRODUCTS = list(OPTIMAL_PARAMS.keys())

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

def compute_rsi(closes, period=7):
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

def compute_volatility(closes):
    if len(closes) < 2: return 0.0
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean)**2 for r in returns) / len(returns)
    return math.sqrt(variance)

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for Kelly-Adaptive Meta-Controller...")
    product_candles = {}
    for pid in PRODUCTS:
        c = fetch_candles(client, pid, start, now)
        product_candles[pid] = c
        print(f"  {pid}: {len(c)} candles")

    all_times = sorted(list(set(int(c["start"]) for pid in product_candles for c in product_candles[pid])))
    time_lookup = {}
    for pid, candles in product_candles.items():
        for c in candles:
            t = int(c["start"])
            time_lookup.setdefault(t, {})[pid] = {
                "open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])
            }

    history = {p: [] for p in PRODUCTS}
    win_stats = {p: {"wins": 0, "closes": 0} for p in PRODUCTS}
    
    cash = 48.0
    positions = []
    total_volume = 0.0
    total_fees_paid = 0.0

    print("\n--- SIMULATING KELLY-ADAPTIVE META-CONTROLLER (WITH SPRINT MODE) ---")
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        
        # Sprint Mode Logic (Adjusted for 72h backfill demonstration)
        sprint_mode = False
        if (1500 <= total_volume < 2000):
            sprint_mode = True
            max_concurrent = 5
            kelly_multiplier = 15.0 # Super Aggressive Sprint
        else:
            max_concurrent = 3
            kelly_multiplier = 5.0
            
        # Fee Tier Logic
        if total_volume >= 50000: fee_rate = 0.0015
        elif total_volume >= 10000: fee_rate = 0.0025
        else: fee_rate = 0.0040
            
        for pid, c in tick.items():
            history[pid].append(c["close"])
            if len(history[pid]) > 100: history[pid].pop(0)
            
        # 1. Process exits
        still_open = []
        for pos in positions:
            pid = pos["pid"]
            closed = False
            if pid in tick:
                c = tick[pid]; params = OPTIMAL_PARAMS[pid]
                pos["hold_bars"] += 1
                rsi = compute_rsi(history[pid], params["p"])
                
                exit_p = None
                if c["high"] >= pos["target"]:
                    exit_p = pos["target"]; win_stats[pid]["wins"] += 1; win_stats[pid]["closes"] += 1; closed = True
                elif c["low"] <= pos["stop"]:
                    exit_p = pos["stop"]; win_stats[pid]["closes"] += 1; closed = True
                elif rsi >= params["ob"]:
                    exit_p = c["close"]; win_stats[pid]["closes"] += 1; closed = True
                    if exit_p > pos["entry"]: win_stats[pid]["wins"] += 1
                elif pos["hold_bars"] >= params["h"]:
                    exit_p = c["close"]; win_stats[pid]["closes"] += 1; closed = True
                    if exit_p > pos["entry"]: win_stats[pid]["wins"] += 1
                
                if closed:
                    units = pos["quote"] / pos["entry"]
                    gross = (exit_p - pos["entry"]) * units
                    ef = pos["quote"] * fee_rate; xf = exit_p * units * fee_rate
                    net = gross - ef - xf
                    cash += pos["quote"] + net
                    total_volume += pos["quote"] + (exit_p * units); total_fees_paid += ef + xf
                else:
                    still_open.append(pos)
            else:
                still_open.append(pos)
        positions = still_open
        
        # 2. Process entries
        free_slots = max_concurrent - len(positions)
        if free_slots > 0 and cash >= 10.0:
            candidates = []
            for pid, c in tick.items():
                if any(p["pid"] == pid for p in positions): continue
                params = OPTIMAL_PARAMS[pid]
                if len(history[pid]) < 50: continue
                
                rsi_prev = compute_rsi(history[pid][:-1], params["p"])
                if rsi_prev <= params["os"]:
                    # Volatility filter: 1h (12 bars) vs 24h (288 bars)
                    vol_1h = compute_volatility(history[pid][-12:])
                    vol_24h = compute_volatility(history[pid][-50:]) # limit to 50 for this 72h test
                    if vol_1h > 1.2 * vol_24h: # Lowered to 1.2x for shorter 72h window
                        candidates.append({"pid": pid, "rsi": rsi_prev, "params": params, "c": c})
            
            candidates.sort(key=lambda x: x["rsi"])
            for cand in candidates[:free_slots]:
                if cash < 10.0: break
                pid = cand["pid"]; params = cand["params"]
                
                # Kelly Sizing: (WR * 2 - 1) * 0.5
                stats = win_stats[pid]
                wr = stats["wins"] / stats["closes"] if stats["closes"] > 5 else 0.65 # Default to 65% WR for early trades
                kelly_frac = max(0.05, (wr * 2 - 1) * 0.5) # Minimum 5% deploy
                
                tq = min(cash * 0.95, cash * (1.0/free_slots) * kelly_frac * 5.0) # Scaled Kelly for aggressiveness
                if tq < 10.0: tq = 10.0 # Force minimum
                if tq > cash: break
                
                ep = cand["c"]["open"]
                tp = ep * (1 + params["t"] / 100.0)
                sp = ep * (1 - params["s"] / 100.0)
                positions.append({"pid": pid, "entry": ep, "target": tp, "stop": sp, "quote": tq, "hold_bars": 0})
                cash -= tq
                free_slots -= 1

    for pos in positions: cash += pos["quote"]
    net = cash - 48.0
    print(f"\nFinal Bankroll: ${cash:.2f}")
    print(f"Net Profit: ${net:.2f} ({net/48*100:.1f}%)")
    print(f"Total Trading Volume: ${total_volume:.2f}")
    print(f"Total Fees Paid: ${total_fees_paid:.2f}")

if __name__ == "__main__":
    main()
