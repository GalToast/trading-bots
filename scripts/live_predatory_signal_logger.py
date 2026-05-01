#!/usr/bin/env python3
"""
LANE 4: PREDATORY SIGNAL LOGGER
Unifies all leading indicator studies into a single verifiable stream.
Logs: Kraken-Lag, Iceberg-Gulp, Magnetic-Walls, and Aggressor-Imbalance.
Output: reports/predatory_signals.jsonl
"""
import json
import time
import sys
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from predatory_logic_engine import PredatoryLogicEngine

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_LOG = ROOT / "reports" / "predatory_signals.jsonl"

PRODUCTS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD", "IRYS-USD"]
BTC = "BTC-USD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

def main():
    client = CoinbaseAdvancedClient()
    # Initialize predatory engines per product
    predators = {p: PredatoryLogicEngine(p) for p in PRODUCTS}
    
    print(f"🚀 LANE 4: Unified Signal Logger started for {len(PRODUCTS)} products.")
    
    while True:
        try:
            timestamp = time.time()
            ts_iso = utc_now_iso()
            
            # 1. Fetch Kraken BTC (Lattice Warp)
            kraken_price = predators[PRODUCTS[0]].get_kraken_btc() # using one instance to fetch
            
            # 2. Fetch Coinbase BTC
            cb_btc_ticker = client.get_product(BTC)
            cb_btc_price = float(cb_btc_ticker.get("price", 0))
            
            for pid in PRODUCTS:
                # 3. Fetch Book (Imbalance & Gulp)
                try:
                    resp = client.best_bid_ask([pid])
                    book = resp["pricebooks"][0]
                    bid_p = float(book["bids"][0]["price"])
                    bid_s = float(book["bids"][0]["size"])
                    ask_p = float(book["asks"][0]["price"])
                    ask_s = float(book["asks"][0]["size"])
                    
                    # Compute Logic Score
                    # (Dummy ATR/Swing for now)
                    score = predators[pid].evaluate_entry_quality(30, bid_p, bid_s, ask_s, cb_btc_price)
                    
                    # 4. Check for GULPS
                    bid_gulp, ask_gulp = predators[pid].check_gulp_active(bid_s, ask_s)
                    
                    # 5. Check for MAGNETIC
                    is_mag, mag_level = predators[pid].check_magnetic_proximity(bid_p)
                    
                    # Log if anything interesting is happening
                    if score >= 50 or bid_gulp or ask_gulp or is_mag:
                        signal = {
                            "ts_utc": ts_iso,
                            "product_id": pid,
                            "price": bid_p,
                            "logic_score": score,
                            "bid_gulp": bid_gulp,
                            "ask_gulp": ask_gulp,
                            "magnetic_wall": mag_level if is_mag else None,
                            "kraken_btc": kraken_price,
                            "coinbase_btc": cb_btc_price
                        }
                        append_jsonl(SIGNAL_LOG, signal)
                        
                except: pass
                time.sleep(0.2) # Avoid rate limit within loop
                
            time.sleep(2.0) # 2s sample rate
            
        except Exception as e:
            print(f"Logger Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
