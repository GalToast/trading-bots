#!/usr/bin/env python3
"""
LATTICE-WARP GRINDER (The Resurrection)
Fuses:
1. Kraken-Lead Lattice Lag (2-4s edge)
2. Structural Market Making (Maker Bid / Maker Ask)
3. 25bps Fee Armor
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

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "lattice_warp_grinder_state.json"

PRODUCTS = ["IOTX-USD", "BAL-USD", "BLUR-USD"]
KRAKEN_BTC = "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_kraken_btc():
    try:
        with urllib.request.urlopen(KRAKEN_BTC, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return float(data["result"]["XXBTZUSD"]["c"][0])
    except: return None

class LatticeWarpGrinder:
    def __init__(self, starting_cash=324.0):
        self.cash = starting_cash
        self.inventory = {p: None for p in PRODUCTS} # {pid: {"ep": ..., "quote": ..., "buy_fee": ...}}
        
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 27874.0 # Continuing volume
        
        self.last_kraken_btc = None
        self.warp_velocity = 0.0

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client):
        fee_rate = self.get_fee_rate()
        
        # 1. Update Kraken Lattice
        kr_price = get_kraken_btc()
        if kr_price and self.last_kraken_btc:
            self.warp_velocity = kr_price - self.last_kraken_btc
        if kr_price: self.last_kraken_btc = kr_price

        # 2. Exit Logic
        for pid in PRODUCTS:
            if self.inventory[pid]:
                try:
                    resp = client.best_bid_ask([pid]); book = resp["pricebooks"][0]
                    bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"])
                    inv = self.inventory[pid]
                    
                    # Target: 0.6% profit (clears fees + 10bps)
                    if ask >= inv["ep"] * 1.0060:
                        exit_p = ask; units = inv["quote"] / inv["ep"]
                        total_returned = (units * exit_p) - (units * exit_p * fee_rate)
                        self.cash += total_returned
                        pnl = total_returned - (inv["quote"] + inv["buy_fee"])
                        self.realized_net += pnl; self.closes += 1; self.total_volume += inv["quote"] + (units * exit_p)
                        print(f"[{utc_now_iso()}] WARP-GOBBLED {pid} (Net=+${pnl:.4f})")
                        self.inventory[pid] = None
                    elif bid < inv["ep"] * 0.985: # Panic Stop
                        exit_p = bid; units = inv["quote"] / inv["ep"]
                        total_returned = (units * exit_p) - (units * exit_p * 0.0060)
                        self.cash += total_returned
                        pnl = total_returned - (inv["quote"] + inv["buy_fee"])
                        self.realized_net += pnl; self.closes += 1; self.total_volume += inv["quote"] + (units * exit_p)
                        self.inventory[pid] = None
                except: pass

        # 3. Entry Logic: THE LATTICE WARP
        # If Kraken BTC is surging, place Limit Bids on microcaps
        if self.warp_velocity >= 5.0 and self.cash >= 50.0:
            for pid in PRODUCTS:
                if self.inventory[pid] is None and self.cash >= 50.0:
                    try:
                        resp = client.best_bid_ask([pid]); book = resp["pricebooks"][0]
                        bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"])
                        spread = (ask - bid) / bid * 100
                        
                        if spread >= 0.85:
                            tq = 50.0; bf = tq * fee_rate
                            self.inventory[pid] = {"ep": bid, "quote": tq, "buy_fee": bf}
                            self.cash -= (tq + bf)
                            print(f"[{utc_now_iso()}] WARP-BID PLACED {pid} (Vel=${self.warp_velocity:.2f})")
                    except: pass

def main():
    client = CoinbaseAdvancedClient(); engine = LatticeWarpGrinder()
    print("🚀 LATTICE-WARP GRINDER: Real-Time Kraken Lag Arbitrage Started.")
    
    while True:
        try:
            engine.process_tick(client)
            # Save state
            payload = {"ts": utc_now_iso(), "net": round(engine.realized_net, 4), "vol": round(engine.total_volume, 4), "vel": round(engine.warp_velocity, 2)}
            STATE_PATH.write_text(json.dumps(payload, indent=2))
            
            print(f"  HB cash=${engine.cash:.2f} net=${engine.realized_net:.2f} vol=${engine.total_volume:.2f} vel=${engine.warp_velocity:.2f}", end="\r")
            time.sleep(2.0) # High frequency lattice check
        except Exception as e: print(f"  EXC: {e}")

if __name__ == "__main__": main()
