#!/usr/bin/env python3
"""Titan 10.4 Dual-Exchange Sync Tape Generator.

Polls Kraken and Coinbase simultaneously for a set of target products
and saves them to a single synced tape for lead-lag and arbitrage backtesting.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient
from coinbase_advanced_client import CoinbaseAdvancedClient

# Goldilocks + Anchors for Lead-Lag analysis
TARGETS = {
    "BTC/USD": "BTC-USD",
    "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD",
    "RENDER/USD": "RENDER-USD",
    "NEAR/USD": "NEAR-USD",
    "L3/USD": "L3-USD",
    "TRAC/USD": "TRAC-USD",
}

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-path", default="reports/cache/dual_exchange_synced_tape.jsonl")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--duration-seconds", type=int, default=300)
    args = parser.parse_args()

    krk_client = KrakenSpotClient()
    cb_client = CoinbaseAdvancedClient()
    output_file = Path(args.output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"--- DUAL-EXCHANGE SYNC TAPE GENERATOR ACTIVE ---")
    print(f"Targeting: {list(TARGETS.keys())}")
    print(f"Saving to: {args.output_path}")

    start_time = time.time()
    count = 0

    while time.time() - start_time < args.duration_seconds:
        try:
            print(f"[{utc_now_iso()}] Polling exchanges...")
            # Simultaneous Poll
            krk_tickers = krk_client.ticker(list(TARGETS.keys()))
            print(f"[{utc_now_iso()}] Kraken poll done ({len(krk_tickers)} tickers).")
            
            cb_res = cb_client.best_bid_ask(list(TARGETS.values()))
            pb_list = cb_res.get("pricebooks", [])
            print(f"[{utc_now_iso()}] Coinbase poll done ({len(pb_list)} products).")
            
            ts = time.time()
            ts_iso = utc_now_iso()
            
            # Process Coinbase
            cb_data = {}
            for item in pb_list:
                pid = item.get("product_id")
                bids = item.get("bids", [])
                asks = item.get("asks", [])
                if bids and asks:
                    cb_data[pid] = {
                        "bid": float(bids[0]["price"]),
                        "ask": float(asks[0]["price"]),
                        "bid_size": float(bids[0]["size"]),
                        "ask_size": float(asks[0]["size"])
                    }
            
            # Combine into a single synced row
            payload = {
                "ts": ts,
                "ts_iso": ts_iso,
                "exchanges": {
                    "kraken": {},
                    "coinbase": {}
                }
            }
            
            for krk_pid, cb_pid in TARGETS.items():
                k_tick = krk_tickers.get(krk_pid)
                c_tick = cb_data.get(cb_pid)
                
                if k_tick and c_tick:
                    payload["exchanges"]["kraken"][krk_pid] = {
                        "bid": float(k_tick["b"][0]),
                        "ask": float(k_tick["a"][0]),
                        "bid_size": float(k_tick["b"][2]),
                        "ask_size": float(k_tick["a"][2])
                    }
                    payload["exchanges"]["coinbase"][cb_pid] = c_tick
            
            if payload["exchanges"]["kraken"]:
                with open(output_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(payload) + "\n")
                count += 1
                if count % 10 == 0:
                    print(f"Captured {count} synced ticks...")

        except Exception as e:
            print(f"Error capturing ticks: {e}")
            
        time.sleep(args.poll_seconds)

    print(f"DONE! Captured {count} synced ticks.")

if __name__ == "__main__":
    main()
