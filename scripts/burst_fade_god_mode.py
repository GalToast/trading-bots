import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCTS = ["CHECK-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "CFG-USD", "COMP-USD", "DASH-USD", "BASED1-USD", "AVT-USD", "BOBBOB-USD"]

PRODUCT_PARAMS = {
    "CHECK-USD": {"bt": 2.0, "t": 1.0, "s": 0.1},
    "BAL-USD": {"bt": 2.0, "t": 0.6, "s": 0.1},
    "BLUR-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "ALEPH-USD": {"bt": 1.0, "t": 0.8, "s": 0.2},
    "CFG-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "COMP-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "DASH-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "BASED1-USD": {"bt": 2.0, "t": 1.0, "s": 0.1},
    "AVT-USD": {"bt": 1.0, "t": 0.8, "s": 0.1},
    "BOBBOB-USD": {"bt": 2.0, "t": 0.6, "s": 0.1},
}

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

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data...")
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
            time_lookup[t][pid] = c

    cash = 48.0
    positions = []
    max_concurrent = 1
    closes = 0
    wins = 0
    total_volume = 0.0
    total_fees_paid = 0.0

    print("\n--- SIMULATING GOD MODE ---")
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        
        # Calculate current fee tier based on trailing volume
        if total_volume >= 50000:
            fee_rate = 0.0015 # 15 bps
        elif total_volume >= 10000:
            fee_rate = 0.0025 # 25 bps
        else:
            fee_rate = 0.0040 # 40 bps
            
        # Process exits
        still_open = []
        for pos in positions:
            pid = pos["pid"]
            if pid in tick:
                c = tick[pid]
                h = float(c["high"])
                l = float(c["low"])
                ep = pos["entry"]
                tp = pos["target"]
                sp = pos["stop"]
                tq = pos["quote"]
                units = tq / ep
                
                closed = False
                if l <= tp:
                    gross = (ep - tp) * units
                    ef = tq * fee_rate
                    xf = tp * units * fee_rate
                    net = gross - ef - xf
                    cash += tq + net
                    closes += 1; wins += 1
                    total_volume += tq + (tp * units)
                    total_fees_paid += ef + xf
                    closed = True
                elif h >= sp:
                    gross = (ep - sp) * units
                    ef = tq * fee_rate
                    xf = sp * units * fee_rate
                    net = gross - ef - xf
                    cash += tq + net
                    closes += 1
                    total_volume += tq + (sp * units)
                    total_fees_paid += ef + xf
                    closed = True
                
                if not closed:
                    still_open.append(pos)
            else:
                still_open.append(pos)
                
        positions = still_open
        
        # Process entries
        free_slots = max_concurrent - len(positions)
        if free_slots > 0 and cash >= 10.0:
            candidates = []
            for pid, c in tick.items():
                params = PRODUCT_PARAMS.get(pid)
                if not params: continue
                # We can't enter if we already hold this coin
                if any(p["pid"] == pid for p in positions):
                    continue
                    
                o = float(c["open"])
                h = float(c["high"])
                l = float(c["low"])
                close = float(c["close"])
                mid = (o + close) / 2 if (o + close) > 0 else 1
                rp = (h - l) / mid * 100
                
                if rp >= params["bt"]:
                    candidates.append({"pid": pid, "rp": rp, "c": c, "params": params})
            
            # Sort by biggest burst
            candidates.sort(key=lambda x: x["rp"], reverse=True)
            
            for cand in candidates[:free_slots]:
                if cash < 10.0: break
                
                pid = cand["pid"]
                c = cand["c"]
                params = cand["params"]
                rp = cand["rp"]
                
                alloc_fraction = 1.0 / free_slots
                if rp >= params["bt"] * 1.5:
                    alloc_fraction = min(1.0, alloc_fraction * 1.5)
                
                tq = min(cash * 0.95, cash * alloc_fraction * 0.95)
                if tq < 10.0: continue
                
                burst_high = float(c["high"])
                # Laddering
                ep = burst_high * 1.005
                tp = ep * (1 - rp / 100 * params["t"])
                sp = ep * (1 + rp / 100 * params["s"])
                
                positions.append({"pid": pid, "entry": ep, "target": tp, "stop": sp, "quote": tq, "rp": rp})
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
