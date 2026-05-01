import json
import time
from datetime import datetime, timezone
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
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

    print(f"Fetching 72h data for {PRODUCT} Regime-Fluid Backtest...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    
    for mode in ["Apex Champion", "Regime-Fluid (Apex + Grid)"]:
        cash = 48.0
        position = None
        grid_inventory = []
        closes = 0
        wins = 0
        total_volume = 0.0
        history = []
        
        for i in range(len(rave_candles)):
            c = rave_candles[i]
            h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
            history.append(cl)
            if len(history) > 50: history.pop(0)
            
            vol = compute_volatility(history[-20:]) if len(history) >= 20 else 0.0
            
            if vol >= 0.015:
                if position and position["type"] == "apex":
                    position["hold"] += 1
                    rsi = 50.0
                    if len(history) >= 5:
                        deltas = [history[j] - history[j-1] for j in range(len(history)-4, len(history))]
                        g = sum([d if d > 0 else 0 for d in deltas])/4; lo = sum([-d if d < 0 else 0 for d in deltas])/4
                        if lo > 0: rsi = 100 - 100/(1+g/lo)
                    
                    if rsi >= 95 or position["hold"] >= 4:
                        units = position["quote"] / position["entry"]
                        pnl = (cl - position["entry"]) * units - (position["quote"] * 0.0040) - (cl * units * 0.0040)
                        cash += position["quote"] + pnl
                        closes += 1
                        if cl > position["entry"]: wins += 1
                        position = None
                
                if position is None and cash >= 10.0:
                    rsi_prev = 50.0
                    if len(history) >= 6:
                        deltas = [history[j] - history[j-1] for j in range(len(history)-5, len(history)-1)]
                        g = sum([d if d > 0 else 0 for d in deltas])/4; lo = sum([-d if d < 0 else 0 for d in deltas])/4
                        if lo > 0: rsi_prev = 100 - 100/(1+g/lo)
                    
                    if rsi_prev <= 45:
                        ep = float(c["open"])
                        tq = cash * 0.95
                        position = {"type": "apex", "entry": ep, "quote": tq, "hold": 0}
                        cash -= tq
            
            elif mode == "Regime-Fluid (Apex + Grid)":
                if position:
                    units = position["quote"] / position["entry"]
                    pnl = (cl - position["entry"]) * units - (position["quote"] * 0.0040) - (cl * units * 0.0040)
                    cash += position["quote"] + pnl
                    closes += 1
                    if cl > position["entry"]: wins += 1
                    position = None

                spacing = 0.01
                if not grid_inventory:
                    buy_level = cl * (1 - spacing)
                    if l <= buy_level and cash >= 10.0:
                        grid_inventory.append({"ep": buy_level, "quote": 10.0})
                        cash -= 10.0
                else:
                    still_holding = []
                    for inv in grid_inventory:
                        if h >= inv["ep"] * (1 + spacing):
                            pnl = (inv["ep"]*spacing) / inv["ep"] * inv["quote"] - (2 * inv["quote"] * 0.0040)
                            cash += inv["quote"] + pnl
                            closes += 1; wins += 1
                        else:
                            still_holding.append(inv)
                    grid_inventory = still_holding
                    if grid_inventory and len(grid_inventory) < 3 and cash >= 10.0:
                        next_buy = min([inv["ep"] for inv in grid_inventory]) * (1 - spacing)
                        if l <= next_buy:
                            grid_inventory.append({"ep": next_buy, "quote": 10.0})
                            cash -= 10.0

        if position: cash += position["quote"]
        for inv in grid_inventory: cash += inv["quote"]
        net = cash - 48.0
        print(f"\n{mode}: Net=${net:.2f} ({net/48*100:.1f}%) | Closes={closes} | WR={wins/max(1, closes)*100:.1f}%")

if __name__ == "__main__":
    main()
