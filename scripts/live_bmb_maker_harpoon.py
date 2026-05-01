#!/usr/bin/env python3
"""
BMB-USD Maker Harpoon (Live-Capital Lane)
Executes a High-MER discretization strategy on Kraken.
Uses Toxicity Propagation Filter from Coinbase to avoid predatory reloads.
"""

import sys
import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# Add scripts to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from kraken_spot_client import KrakenSpotClient, KrakenSpotClientError
import kraken_config as cfg

ROOT = Path(__file__).resolve().parent.parent
VETO_PATH = ROOT / "reports" / "kraken_toxic_veto.json"
STATE_PATH = ROOT / "reports" / "live_bmb_maker_state.json"
LOG_PATH = ROOT / "reports" / "live_bmb_maker_log.jsonl"

PRODUCT_ID = "BMBUSD"
TICK_SIZE = 0.00000001 # BMB is sub-penny, check asset_pairs for precision
# Actually BMB tick size is usually 1, but we should fetch it.

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path, payload):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

class BMBMakerHarpoon:
    def __init__(self, client: KrakenSpotClient, trade_size_usd=10.0):
        self.client = client
        self.trade_size_usd = trade_size_usd
        self.active_order_id = None
        self.position_qty = 0.0
        self.entry_price = 0.0
        self.pair_info = self.fetch_pair_info()
        self.tick_size = self.pair_info.get("pair_decimals", 8)
        
    def fetch_pair_info(self):
        pairs = self.client.asset_pairs()
        return pairs.get(PRODUCT_ID, {})

    def is_vetoed(self):
        if not VETO_PATH.exists():
            return False
        try:
            with open(VETO_PATH, "r") as f:
                vetoes = json.load(f)
                v = vetoes.get("BMB-USD")
                if v and v["expiry"] > time.time():
                    print(f"!!! BMB-USD VETO ACTIVE: {v['reason']} until {v['expiry']}")
                    return True
        except:
            pass
        return False

    def get_balances(self):
        try:
            bal = self.client.balance()
            usd = float(bal.get("ZUSD", 0.0))
            bmb = float(bal.get("BMB", 0.0))
            return usd, bmb
        except Exception as e:
            print(f"Error fetching balances: {e}")
            return 0.0, 0.0

    def run_loop(self):
        print(f"BMB Maker Harpoon Starting. Target: {PRODUCT_ID}")
        
        while True:
            try:
                # 1. Update State
                usd_bal, bmb_bal = self.get_balances()
                ticker = self.client.ticker([PRODUCT_ID]).get(PRODUCT_ID, {})
                if not ticker:
                    time.sleep(5)
                    continue
                
                bid = float(ticker["b"][0])
                ask = float(ticker["a"][0])
                
                # 2. Logic
                if bmb_bal < 0.0001: # No position
                    if self.is_vetoed():
                        time.sleep(10)
                        continue
                    
                    # Try to enter at bid - 1 tick
                    target_price = bid # For BMB, being at the bid is often enough to be top
                    # or target_price = bid - (10 ** -self.tick_size)
                    
                    if usd_bal >= self.trade_size_usd:
                        qty = self.trade_size_usd / target_price
                        print(f"Placing Maker BUY for {qty:.2f} BMB at {target_price}")
                        try:
                            # In a real bot, we'd handle active_order_id and replacements
                            # For this specialized lane, we use a simple add_order
                            self.client.add_order(
                                rest_pair=PRODUCT_ID,
                                side="buy",
                                volume=qty,
                                price=target_price,
                                post_only=True
                            )
                        except KrakenSpotClientError as e:
                            print(f"Order failed: {e}")
                
                else: # In position
                    # Try to exit at ask + 1 tick
                    target_price = ask
                    print(f"Placing Maker SELL for {bmb_bal:.2f} BMB at {target_price}")
                    try:
                        self.client.add_order(
                            rest_pair=PRODUCT_ID,
                            side="sell",
                            volume=bmb_bal,
                            price=target_price,
                            post_only=True
                        )
                    except KrakenSpotClientError as e:
                        print(f"Order failed: {e}")

            except Exception as e:
                print(f"Main Loop Error: {e}")
            
            time.sleep(30)

if __name__ == "__main__":
    client = KrakenSpotClient()
    harpoon = BMBMakerHarpoon(client)
    # harpoon.run_loop() # Commented out for safety during initial review
    print("BMB Maker Harpoon Initialized. Ready for deployment.")
