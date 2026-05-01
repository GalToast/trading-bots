import json
import time
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

# Products for this system
LEADER = "RAVE-USD"
FOLLOWERS = ["MASK-USD", "FARTCOIN-USD", "DASH-USD", "ALEPH-USD"]
VOL_SPIKE_COINS = ["RAVE-USD", "BAL-USD", "FARTCOIN-USD"]

PRODUCTS = list(set([LEADER] + FOLLOWERS + VOL_SPIKE_COINS))

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
        except Exception:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_vol_sma(history, period=10):
    if len(history) < period:
        return sum(c["volume"] for c in history) / max(1, len(history))
    return sum(c["volume"] for c in history[-period:]) / period

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for Long-Only Grid Search...")
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

    # Base signals over the 72h period (so we don't recalculate for every parameter grid)
    print("Pre-calculating base signals...")
    history = {p: [] for p in PRODUCTS}
    
    # tick_data = [(t, tick, signals_for_this_tick)]
    timeline_data = []
    
    for t in all_times:
        tick = time_lookup.get(t, {})
        for pid, c in tick.items():
            history[pid].append(c)
            if len(history[pid]) > 20:
                history[pid].pop(0)
                
        rave_c = tick.get(LEADER)
        rave_surge = False
        if rave_c and rave_c["close"] > rave_c["open"] * 1.015:
            rave_surge = True

        signals = []
        for pid, c in tick.items():
            if len(history[pid]) < 10: continue
            
            if rave_surge and pid in FOLLOWERS:
                signals.append({"pid": pid, "type": "follower", "c": c})
                
            if pid in VOL_SPIKE_COINS:
                vol_sma = compute_vol_sma(history[pid][:-1], period=10)
                if vol_sma > 0 and c["volume"] > 2.0 * vol_sma and c["close"] > c["open"]:
                    signals.append({"pid": pid, "type": "vol_spike", "c": c})
                    
        # only append if there's signals to process for the NEXT tick, wait, signals are generated on this tick, traded immediately
        timeline_data.append((t, tick, signals))

    print("\n--- LONG-ONLY GRID SEARCH: Optimal Target & Stop per Product ---")
    optimal_params = {}
    
    for pid in PRODUCTS:
        best_net = -999.0
        best_params = None
        
        # Grid search: Take Profit (1% to 6%) and Stop Loss (1% to 4%) and Hold Bars (2, 4, 8)
        for tp_pct in [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0]:
            for sl_pct in [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]:
                for max_hold in [2, 3, 5, 8]:
                    
                    cash = 1000.0 # start with enough cash to not worry about hitting 0 for single-coin backtest
                    closes = 0
                    wins = 0
                    position = None
                    quote = 24.0
                    
                    for t, tick, signals in timeline_data:
                        # 1. Process exit
                        if position:
                            c = tick.get(pid)
                            if c:
                                h = c["high"]
                                l = c["low"]
                                ep = position["entry"]
                                tp = position["target"]
                                sp = position["stop"]
                                units = position["quote"] / ep
                                
                                position["hold_bars"] += 1
                                
                                if h >= tp:
                                    cash += position["quote"] + ((tp - ep) * units) - (position["quote"] * FEE_RATE) - (tp * units * FEE_RATE)
                                    closes += 1; wins += 1
                                    position = None
                                elif l <= sp:
                                    cash += position["quote"] + ((sp - ep) * units) - (position["quote"] * FEE_RATE) - (sp * units * FEE_RATE)
                                    closes += 1
                                    position = None
                                elif position["hold_bars"] >= max_hold:
                                    exit_p = c["close"]
                                    cash += position["quote"] + ((exit_p - ep) * units) - (position["quote"] * FEE_RATE) - (exit_p * units * FEE_RATE)
                                    closes += 1
                                    if exit_p > ep: wins += 1
                                    position = None
                                    
                        # 2. Process entry
                        if position is None:
                            for s in signals:
                                if s["pid"] == pid:
                                    ep = s["c"]["close"]
                                    tp = ep * (1 + tp_pct / 100.0)
                                    sp = ep * (1 - sl_pct / 100.0)
                                    position = {
                                        "entry": ep,
                                        "target": tp,
                                        "stop": sp,
                                        "quote": quote,
                                        "hold_bars": 0
                                    }
                                    cash -= quote
                                    break # Only take one signal per tick per coin
                    
                    if position:
                        cash += position["quote"]
                        
                    net = cash - 1000.0
                    if net > best_net and closes > 2:
                        best_net = net
                        best_params = (tp_pct, sl_pct, max_hold, closes, wins)

        if best_params and best_net > 0:
            tp_pct, sl_pct, max_hold, cl, w = best_params
            optimal_params[pid] = {"t": tp_pct, "s": sl_pct, "h": max_hold}
            wr = w / cl * 100 if cl > 0 else 0
            print(f"{pid}: Best Net=${best_net:.2f} | TP={tp_pct}% SL={sl_pct}% Hold={max_hold} | Closes={cl} WR={wr:.1f}%")
        else:
            print(f"{pid}: No profitable config found.")

if __name__ == "__main__":
    main()
