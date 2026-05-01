import json
import time
from datetime import datetime, timezone
import sys
import os
import concurrent.futures

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

def optimize_product(pid, candles):
    closes_list = [float(c["close"]) for c in candles]
    highs_list = [float(c["high"]) for c in candles]
    lows_list = [float(c["low"]) for c in candles]
    
    # Pre-calculate RSI arrays for speed
    rsi_cache = {}
    for period in [5, 7, 9]:
        rsi_cache[period] = [50.0] * len(candles)
        for i in range(period + 1, len(candles)):
            rsi_cache[period][i] = compute_rsi(closes_list[i-period-1:i+1], period)
            
    best_net = -999.0
    best_params = None
    
    for rsi_period in [5, 7, 9]:
        rsi_series = rsi_cache[rsi_period]
        for over_s in [20, 25, 30]:
            for over_b in [70, 75, 80]:
                for tp_pct in [1.0, 2.0, 3.0, 4.0, 5.0]:
                    for sl_pct in [0.5, 1.0, 2.0, 3.0]:
                        for max_hold in [4, 8, 12, 24]:
                            
                            cash = 1000.0
                            quote = 24.0
                            wins = 0
                            trades = 0
                            pos = None
                            
                            for i in range(rsi_period + 1, len(candles)):
                                cl = closes_list[i]
                                h = highs_list[i]
                                l = lows_list[i]
                                rsi = rsi_series[i-1] # RSI generated from previous candle close
                                current_rsi = rsi_series[i] # RSI currently
                                
                                if pos:
                                    pos["hold"] += 1
                                    ep = pos["entry"]
                                    tp = pos["target"]
                                    sp = pos["stop"]
                                    units = pos["units"]
                                    
                                    closed = False
                                    if h >= tp:
                                        cash += quote + ((tp - ep) * units) - (quote * FEE_RATE) - (tp * units * FEE_RATE)
                                        trades += 1; wins += 1; pos = None; closed = True
                                    elif l <= sp:
                                        cash += quote + ((sp - ep) * units) - (quote * FEE_RATE) - (sp * units * FEE_RATE)
                                        trades += 1; pos = None; closed = True
                                    elif current_rsi >= over_b:
                                        cash += quote + ((cl - ep) * units) - (quote * FEE_RATE) - (cl * units * FEE_RATE)
                                        trades += 1
                                        if cl > ep: wins += 1
                                        pos = None; closed = True
                                    elif pos["hold"] >= max_hold:
                                        cash += quote + ((cl - ep) * units) - (quote * FEE_RATE) - (cl * units * FEE_RATE)
                                        trades += 1
                                        if cl > ep: wins += 1
                                        pos = None; closed = True
                                        
                                if pos is None:
                                    if rsi <= over_s:
                                        # Enter at open of this candle = close of previous
                                        ep = closes_list[i-1]
                                        units = quote / ep
                                        pos = {
                                            "entry": ep,
                                            "target": ep * (1 + tp_pct / 100.0),
                                            "stop": ep * (1 - sl_pct / 100.0),
                                            "units": units,
                                            "quote": quote,
                                            "hold": 0
                                        }
                                        cash -= quote
                                        
                            if pos: cash += pos["quote"]
                            net = cash - 1000.0
                            if net > best_net and trades >= 5:
                                best_net = net
                                best_params = (rsi_period, over_s, over_b, tp_pct, sl_pct, max_hold, trades, wins)
                                
    return pid, best_net, best_params

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching 72h data for RSI Grid Search...")
    product_candles = {}
    for pid in PRODUCTS:
        c = fetch_candles(client, pid, start, now)
        product_candles[pid] = c
        print(f"  {pid}: {len(c)} candles")

    print("\n--- LONG-ONLY RSI GRID SEARCH: Optimal Target & Stop per Product ---")
    optimal_params = {}
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = []
        for pid in PRODUCTS:
            if len(product_candles[pid]) < 100:
                print(f"{pid}: Not enough data")
                continue
            futures.append(executor.submit(optimize_product, pid, product_candles[pid]))
            
        for f in concurrent.futures.as_completed(futures):
            pid, best_net, best_params = f.result()
            if best_params and best_net > 0:
                rsi_period, over_s, over_b, tp_pct, sl_pct, max_hold, trades, wins = best_params
                wr = wins / trades * 100 if trades > 0 else 0
                optimal_params[pid] = {"p": rsi_period, "os": over_s, "ob": over_b, "t": tp_pct, "s": sl_pct, "h": max_hold}
                print(f"{pid}: Best Net=${best_net:.2f} | RSI({rsi_period}) OS={over_s} OB={over_b} TP={tp_pct}% SL={sl_pct}% Hold={max_hold} | Closes={trades} WR={wr:.1f}%")
            else:
                print(f"{pid}: No profitable config found.")
                
    # Save the params so we can build the compounder
    with open(os.path.join(os.path.dirname(__file__), '..', 'reports', 'rsi_optimal_params.json'), 'w') as f:
        json.dump(optimal_params, f, indent=2)

if __name__ == "__main__":
    main()
