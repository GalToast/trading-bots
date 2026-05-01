import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Products for this confluence test
LEADER = "RAVE-USD"
FOLLOWER = "MASK-USD"
PRODUCTS = [LEADER, FOLLOWER]

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

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

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for RAVE-MASK Confluence Test...")
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
            time_lookup.setdefault(t, {})[pid] = {"open": float(c["open"]), "close": float(c["close"]), "high": float(c["high"]), "low": float(c["low"])}

    history = {p: [] for p in PRODUCTS}
    
    # Isolated MASK (Baseline)
    mask_baseline_cash = 1000.0
    mask_confluence_cash = 1000.0
    quote = 24.0
    
    # Performance trackers
    baseline_stats = {"closes": 0, "wins": 0}
    confluence_stats = {"closes": 0, "wins": 0}
    
    baseline_pos = None
    confluence_pos = None

    print("\n--- SIMULATING CONFLUENCE: MASK RSI + RAVE BOUNCE ---")
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        for pid, c in tick.items():
            history[pid].append(c["close"])
            if len(history[pid]) > 20: history[pid].pop(0)
            
        if len(history[FOLLOWER]) < 10 or len(history[LEADER]) < 10: continue
        
        # 1. Process Baseline Exits
        if baseline_pos:
            c = tick.get(FOLLOWER)
            if c:
                baseline_pos["hold"] += 1
                if c["high"] >= baseline_pos["tp"]:
                    pnl = (baseline_pos["tp"] - baseline_pos["ep"]) / baseline_pos["ep"] * quote - (2 * quote * FEE_RATE)
                    mask_baseline_cash += quote + pnl
                    baseline_stats["closes"] += 1; baseline_stats["wins"] += 1; baseline_pos = None
                elif c["low"] <= baseline_pos["sl"]:
                    pnl = (baseline_pos["sl"] - baseline_pos["ep"]) / baseline_pos["ep"] * quote - (2 * quote * FEE_RATE)
                    mask_baseline_cash += quote + pnl
                    baseline_stats["closes"] += 1; baseline_pos = None
                elif baseline_pos["hold"] >= 12:
                    pnl = (c["close"] - baseline_pos["ep"]) / baseline_pos["ep"] * quote - (2 * quote * FEE_RATE)
                    mask_baseline_cash += quote + pnl
                    baseline_stats["closes"] += 1
                    if c["close"] > baseline_pos["ep"]: baseline_stats["wins"] += 1
                    baseline_pos = None

        # 2. Process Confluence Exits
        if confluence_pos:
            c = tick.get(FOLLOWER)
            if c:
                confluence_pos["hold"] += 1
                if c["high"] >= confluence_pos["tp"]:
                    pnl = (confluence_pos["tp"] - confluence_pos["ep"]) / confluence_pos["ep"] * quote - (2 * quote * FEE_RATE)
                    mask_confluence_cash += quote + pnl
                    confluence_stats["closes"] += 1; confluence_stats["wins"] += 1; confluence_pos = None
                elif c["low"] <= confluence_pos["sl"]:
                    pnl = (confluence_pos["sl"] - confluence_pos["ep"]) / confluence_pos["ep"] * quote - (2 * quote * FEE_RATE)
                    mask_confluence_cash += quote + pnl
                    confluence_stats["closes"] += 1; confluence_pos = None
                elif confluence_pos["hold"] >= 12:
                    pnl = (c["close"] - confluence_pos["ep"]) / confluence_pos["ep"] * quote - (2 * quote * FEE_RATE)
                    mask_confluence_cash += quote + pnl
                    confluence_stats["closes"] += 1
                    if c["close"] > confluence_pos["ep"]: confluence_stats["wins"] += 1
                    confluence_pos = None

        # 3. Signals
        rsi_mask = compute_rsi(history[FOLLOWER], 7)
        rsi_leader = compute_rsi(history[LEADER], 7)
        rsi_leader_prev = compute_rsi(history[LEADER][:-1], 7)
        
        # Leader bounce check: RSI is increasing OR price is above previous close
        leader_bounced = (rsi_leader > rsi_leader_prev)
        
        # 4. Process Baseline Entry
        if baseline_pos is None:
            if rsi_mask <= 30:
                ep = tick[FOLLOWER]["open"]
                baseline_pos = {"ep": ep, "tp": ep * 1.03, "sl": ep * 0.98, "hold": 0}
                mask_baseline_cash -= quote
                
        # 5. Process Confluence Entry
        if confluence_pos is None:
            if rsi_mask <= 30 and leader_bounced:
                ep = tick[FOLLOWER]["open"]
                confluence_pos = {"ep": ep, "tp": ep * 1.03, "sl": ep * 0.98, "hold": 0}
                mask_confluence_cash -= quote

    print(f"\nRESULTS FOR MASK-USD (72h):")
    print(f"Baseline:   Net=${mask_baseline_cash-1000:.2f} | Closes={baseline_stats['closes']} | WR={baseline_stats['wins']/max(1, baseline_stats['closes'])*100:.1f}%")
    print(f"Confluence: Net=${mask_confluence_cash-1000:.2f} | Closes={confluence_stats['closes']} | WR={confluence_stats['wins']/max(1, confluence_stats['closes'])*100:.1f}%")

if __name__ == "__main__":
    main()
