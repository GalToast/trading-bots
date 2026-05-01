#!/usr/bin/env python3
"""
SHORT-SQUEEZE SNIPER (Predatory Pump Hunter)
Logic:
1. Identify institutional Sell Icebergs.
2. Wait for the 'Gulp' (exhaustion).
3. Confirm with Kraken BTC Lead.
4. Entry: Market Buy when Iceberg is overrun.
5. Exit: 5% Target or RSI(4) > 95.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import math
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from predatory_logic_engine import PredatoryLogicEngine

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "short_squeeze_sniper_state.json"
EVENT_PATH = ROOT / "reports" / "short_squeeze_sniper_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

class ShortSqueezeSniper:
    def __init__(self, starting_cash=324.0):
        self.cash = starting_cash
        self.position = None # {"ep": ..., "quote": ..., "hold": 0}
        
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 0.0
        
        self.predator = PredatoryLogicEngine(PRODUCT)
        self.last_candle_time = 0

    def process_tick(self, client, current_price, current_ask_size, tick_volume, btc_pumping):
        events = []
        
        # 1. Management (Exits)
        if self.position:
            self.position["hold"] += 1
            # Check for 5% Target
            if current_price >= self.position["ep"] * 1.05 or self.position["hold"] >= 15:
                exit_p = current_price
                units = self.position["quote"] / self.position["ep"]
                # 25bps fee tier assumed
                pnl = (exit_p - self.position["ep"]) * units - (self.position["quote"] * 0.0025) - (exit_p * units * 0.0025)
                self.cash += (units * exit_p) - (exit_p * units * 0.0025)
                self.realized_net += pnl; self.closes += 1
                self.total_volume += self.position["quote"] + (exit_p * units)
                events.append({"ts_utc": utc_now_iso(), "action": "squeeze_close", "net": round(pnl, 4)})
                self.position = None

        # 2. Deployment (The Sniper Shot)
        if self.position is None and self.cash >= 100.0 and btc_pumping:
            # ICEBERG OVERRUN CHECK
            if self.predator.detect_iceberg_overrun(current_price, current_ask_size, tick_volume):
                # THE SQUEEZE IS ON!
                tq = self.cash * 0.95
                bf = tq * 0.0025
                self.position = {"ep": current_price, "quote": tq, "hold": 0}
                self.cash -= (tq + bf)
                events.append({"ts_utc": utc_now_iso(), "action": "squeeze_open", "price": current_price})
                print(f"[{utc_now_iso()}] 🌋 SQUEEZE SNIPER FIRED! Entry at {current_price}")
        
        return events

def main():
    client = CoinbaseAdvancedClient(); engine = ShortSqueezeSniper()
    print(f"🚀 SHORT-SQUEEZE SNIPER: Hunting Capitulation on {PRODUCT}.")
    
    while True:
        try:
            # 1. Fetch live data
            ticker = client.get_product(PRODUCT)
            price = float(ticker.get("price", 0))
            
            resp = client.best_bid_ask([PRODUCT])
            book = resp["pricebooks"][0]
            ask_s = float(book["asks"][0]["size"])
            
            # Fetch BTC for Warp Gate
            kr_btc = engine.predator.get_kraken_btc()
            btc_pumping = False
            if kr_btc and engine.predator.last_kraken_btc:
                if kr_btc - engine.predator.last_kraken_btc >= 5.0: btc_pumping = True
            
            # Process
            events = engine.process_tick(client, price, ask_s, 5000, btc_pumping) # volume dummy
            for ev in events: append_jsonl(EVENT_PATH, ev)
            
            print(f"  HB price=${price:.4f} cash=${engine.cash:.2f} net=${engine.realized_net:.2f} btc_pump={btc_pumping}", end="\r")
            time.sleep(2) # High frequency predatory scan
        except Exception as e: print(f"  EXC: {e}")

if __name__ == "__main__": main()
