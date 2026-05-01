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

# Grid Search results:
# MASK-USD: TP=2.0% SL=3.0% Hold=8
# RAVE-USD: TP=6.0% SL=4.0% Hold=8
# Others didn't find profitable configs in this restricted search, fallback to 2% TP, 2% SL, 5 Hold.
OPTIMAL_PARAMS = {
    "MASK-USD": {"t": 2.0, "s": 3.0, "h": 8},
    "RAVE-USD": {"t": 6.0, "s": 4.0, "h": 8},
}

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
            # parse to useful format
            time_lookup[t][pid] = {
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume", 0)),
                "start": int(c["start"])
            }

    cash = 48.0
    quote = 24.0
    max_concurrent = 5
    positions = []
    closes = 0
    wins = 0
    total_fees = 0.0
    
    # Store history for SMA
    history = {p: [] for p in PRODUCTS}

    for t in all_times:
        tick = time_lookup.get(t, {})
        
        # 1. Process exits (timeout, tp, sl)
        still_open = []
        for pos in positions:
            pid = pos["pid"]
            if pid in tick:
                c = tick[pid]
                h = c["high"]
                l = c["low"]
                ep = pos["entry"]
                tp = pos["target"]
                sp = pos["stop"]
                units = pos["quote"] / ep
                
                pos["hold_bars"] += 1
                
                closed = False
                if h >= tp: # Long hit Target Profit
                    gross = (tp - ep) * units
                    ef = pos["quote"] * FEE_RATE
                    xf = tp * units * FEE_RATE
                    net = gross - ef - xf
                    cash += pos["quote"] + net
                    closes += 1; wins += 1; total_fees += ef + xf
                    closed = True
                elif l <= sp: # Long hit Stop Loss
                    gross = (sp - ep) * units
                    ef = pos["quote"] * FEE_RATE
                    xf = sp * units * FEE_RATE
                    net = gross - ef - xf
                    cash += pos["quote"] + net
                    closes += 1; total_fees += ef + xf
                    closed = True
                elif pos["hold_bars"] >= pos.get("max_hold", 3): # Time stop after max_hold bars
                    exit_p = c["close"]
                    gross = (exit_p - ep) * units
                    ef = pos["quote"] * FEE_RATE
                    xf = exit_p * units * FEE_RATE
                    net = gross - ef - xf
                    cash += pos["quote"] + net
                    closes += 1; total_fees += ef + xf
                    if exit_p > ep: wins += 1
                    closed = True
                
                if not closed:
                    still_open.append(pos)
            else:
                still_open.append(pos)
                
        positions = still_open

        # Update history
        for pid, c in tick.items():
            history[pid].append(c)
            if len(history[pid]) > 20:
                history[pid].pop(0)

        # 2. Evaluate signals based on the completed candle (we assume we can enter at the open of the next candle, but since we are iterating, we enter at the close of this candle)
        # Actually, if we use this candle's close to signal, we'd enter at this candle's close.
        
        rave_c = tick.get(LEADER)
        rave_surge = False
        if rave_c:
            # Did RAVE just surge > 1.5%?
            if rave_c["close"] > rave_c["open"] * 1.015:
                rave_surge = True

        signals = []
        for pid, c in tick.items():
            if len(history[pid]) < 10: continue
            
            # Leader-follower signal
            if rave_surge and pid in FOLLOWERS:
                signals.append({"pid": pid, "type": "follower", "c": c})
                
            # Volume spike signal
            if pid in VOL_SPIKE_COINS:
                vol_sma = compute_vol_sma(history[pid][:-1], period=10) # SMA up to previous bar
                if vol_sma > 0 and c["volume"] > 2.0 * vol_sma and c["close"] > c["open"]:
                    signals.append({"pid": pid, "type": "vol_spike", "c": c})
                    
        # 3. Enter trades
        free_slots = max_concurrent - len(positions)
        if free_slots > 0 and cash >= quote and signals:
            # Filter unique PIDs
            unique_signals = []
            seen = set([p["pid"] for p in positions])
            for s in signals:
                if s["pid"] not in seen:
                    unique_signals.append(s)
                    seen.add(s["pid"])
                    
            for s in unique_signals[:free_slots]:
                if cash < quote: break
                
                ep = s["c"]["close"]
                
                params = OPTIMAL_PARAMS.get(s["pid"])
                if params:
                    tp_pct = params["t"]
                    sl_pct = params["s"]
                    hold_bars = params["h"]
                else:
                    tp_pct = 2.0
                    sl_pct = 2.0
                    hold_bars = 5
                
                tp = ep * (1 + tp_pct / 100.0)
                sp = ep * (1 - sl_pct / 100.0)
                
                positions.append({
                    "pid": s["pid"],
                    "entry": ep,
                    "target": tp,
                    "stop": sp,
                    "quote": quote,
                    "hold_bars": 0,
                    "max_hold": hold_bars,
                    "type": s["type"]
                })
                cash -= quote

    for pos in positions:
        cash += pos["quote"]

    net = cash - 48.0
    wr = wins / closes * 100 if closes > 0 else 0
    print(f"\n--- LONG-ONLY SUPER SYSTEM RESULTS ---")
    print(f"Final Bankroll: ${cash:.2f}")
    print(f"Net Profit: ${net:.2f} ({net/48*100:.1f}%)")
    print(f"Closes: {closes} (Win Rate: {wr:.1f}%)")
    print(f"Total Fees: ${total_fees:.2f}")

if __name__ == "__main__":
    main()
