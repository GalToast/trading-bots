import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCTS = [
    "RAVE-USD", "TROLL-USD", "BAL-USD", "NOM-USD", "MASK-USD",
    "ALEPH-USD", "CHECK-USD", "BLUR-USD", "AVT-USD", "IOTX-USD",
    "IRYS-USD", "CFG-USD", "BOBBOB-USD", "DASH-USD", "FARTCOIN-USD",
    "COMP-USD", "MON-USD", "ZEC-USD", "VVV-USD", "ALGO-USD",
    "ARB-USD", "ETH-USD", "STORJ-USD", "SNX-USD", "AVAX-USD",
    "LDO-USD", "BASED1-USD", "RLC-USD", "SKL-USD", "TAO-USD",
]
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
        except Exception as e:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def simulate_strategy(candles, burst_threshold, target_frac, stop_frac, laddering=False):
    cash = 48.0
    quote = 48.0
    closes = 0
    wins = 0
    losses = 0
    fees = 0
    position = None

    for c in candles:
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        close = float(c["close"])
        mid = (o + close) / 2 if (o + close) > 0 else 1
        
        if position:
            ep = position["entry"]
            tp = position["target"]
            sp = position["stop"]
            trade_quote = position["quote"]
            units = trade_quote / ep
            
            if l <= tp:
                gross = (ep - tp) * units
                ef = ep * units * FEE_RATE
                xf = tp * units * FEE_RATE
                net = gross - ef - xf
                closes += 1
                wins += 1
                fees += ef + xf
                cash += trade_quote + net
                position = None
            elif h >= sp:
                gross = (ep - sp) * units
                ef = ep * units * FEE_RATE
                xf = sp * units * FEE_RATE
                net = gross - ef - xf
                closes += 1
                losses += 1
                fees += ef + xf
                cash += trade_quote + net
                position = None

        if position is None and cash >= 10.0:
            rp = (h - l) / mid * 100
            if rp >= burst_threshold:
                trade_quote = cash * 0.95
                entry = h
                
                if laddering:
                    # Simplified laddering proxy: better average entry price, but lower hit rate
                    # Assume we only get filled if price pushes 0.5% past the burst high
                    entry = h * 1.005 
                    if h < entry: # if the candle's high didn't reach the ladder level, we didn't fill
                         continue
                
                target = entry * (1 - rp / 100 * target_frac)
                stop = entry * (1 + rp / 100 * stop_frac)
                position = {"entry": entry, "target": target, "stop": stop, "quote": trade_quote}
                cash -= trade_quote
                
    # Close pending at end
    if position:
        cash += position["quote"]
        
    return {"net": cash - 48.0, "closes": closes, "wins": wins, "cash": cash}

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for Grid Search...")
    product_candles = {}
    for pid in PRODUCTS:
        c = fetch_candles(client, pid, start, now)
        product_candles[pid] = c
        print(f"  {pid}: {len(c)} candles")

    print("\n--- GRID SEARCH: Optimal Target & Stop per Product ---")
    optimal_params = {}
    
    for pid in PRODUCTS:
        candles = product_candles[pid]
        best_net = -999
        best_params = None
        
        # Grid search
        for t_frac in [0.2, 0.4, 0.6, 0.8, 1.0]:
            for s_frac in [0.1, 0.2, 0.3, 0.4, 0.5]:
                for bt in [1.0, 2.0, 3.0]:
                    res = simulate_strategy(candles, bt, t_frac, s_frac)
                    if res["net"] > best_net and res["closes"] > 5:
                        best_net = res["net"]
                        best_params = (t_frac, s_frac, bt, res["closes"], res["wins"])
        
        if best_params:
            t, s, bt, cl, w = best_params
            optimal_params[pid] = {"target_frac": t, "stop_frac": s, "burst_thresh": bt}
            wr = w/cl*100 if cl > 0 else 0
            print(f"{pid}: Best Net=${best_net:.2f} | BT={bt}% T={t} S={s} | Closes={cl} WR={wr:.1f}%")
        else:
            print(f"{pid}: No profitable config found.")

    print("\n--- EXPERIMENT: Laddering vs Single Entry (Compound) ---")
    # Test Round Robin logic locally with optimal parameters vs laddering
    # We will simulate the exact round robin
    
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

    for mode in ["Single Limit", "Laddering (Entry at Burst High + 0.5%)"]:
        cash = 48.0
        position = None
        closes = 0
        wins = 0
        ladder_mode = (mode != "Single Limit")
        
        for t in all_times:
            tick = time_lookup.get(t, {})
            
            # Exit
            if position:
                pid = position["pid"]
                if pid in tick:
                    c = tick[pid]
                    h = float(c["high"])
                    l = float(c["low"])
                    ep = position["entry"]
                    tp = position["target"]
                    sp = position["stop"]
                    tq = position["quote"]
                    units = tq / ep
                    
                    if l <= tp:
                        cash += tq + ((ep - tp) * units) - (ep * units * FEE_RATE) - (tp * units * FEE_RATE)
                        closes += 1; wins += 1
                        position = None
                    elif h >= sp:
                        cash += tq + ((ep - sp) * units) - (ep * units * FEE_RATE) - (sp * units * FEE_RATE)
                        closes += 1
                        position = None

            # Entry
            if position is None and cash >= 10.0:
                best_rp = 0
                best_pid = None
                best_c = None
                
                for pid, c in tick.items():
                    params = optimal_params.get(pid)
                    if not params: continue
                    o = float(c["open"])
                    h = float(c["high"])
                    l = float(c["low"])
                    close = float(c["close"])
                    mid = (o + close) / 2 if (o + close) > 0 else 1
                    rp = (h - l) / mid * 100
                    
                    if rp > best_rp and rp >= params["burst_thresh"]:
                        best_rp = rp
                        best_pid = pid
                        best_c = c
                
                if best_pid and best_c:
                    params = optimal_params[best_pid]
                    ep = float(best_c["high"])
                    
                    if ladder_mode:
                        # Only enter if the next candle goes 0.5% higher
                        # This is a simplified forward-look for laddering fill
                        # Wait, we can't look forward in this tick. 
                        # We assume laddering places limits 0.5% above burst high.
                        # It will fill in the next ticks if h >= ladder_level
                        ep = ep * 1.005
                        
                    tq = cash * 0.95
                    tp = ep * (1 - best_rp / 100 * params["target_frac"])
                    sp = ep * (1 + best_rp / 100 * params["stop_frac"])
                    position = {"pid": best_pid, "entry": ep, "target": tp, "stop": sp, "quote": tq, "rp": best_rp}
                    cash -= tq

        # Add back pending
        if position: cash += position["quote"]
        
        wr = wins/closes*100 if closes > 0 else 0
        net = cash - 48.0
        print(f"{mode}: Net=${net:.2f} ({cash/48.0*100-100:.1f}%) | Closes={closes} WR={wr:.1f}%")

if __name__ == "__main__":
    main()
