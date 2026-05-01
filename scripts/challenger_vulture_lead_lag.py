#!/usr/bin/env python3
"""Titan 10.2 Challenger: 'The Vulture' (Kraken/Coinbase Lead-Lag).

Monitors price deltas between Kraken and Coinbase for liquid assets.
Wait for an idiosyncratic Kraken dump (>1% vs Coinbase global price),
then execute a 'Vulture Buy' on Kraken and hedge on Coinbase.
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

# Target liquid assets for Lead-Lag arb
TARGETS = {
    "SOL/USD": "SOL-USD",
    "RENDER/USD": "RENDER-USD",
    "NEAR/USD": "NEAR-USD",
    "FET/USD": "FET-USD",
    "TAO/USD": "TAO-USD",
    "BTC/USD": "BTC-USD",
    "ETH/USD": "ETH-USD"
}

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def run_vulture(args: argparse.Namespace):
    krk_client = KrakenSpotClient()
    cb_client = CoinbaseAdvancedClient()
    
    print(f"--- TITAN 10.2 CHALLENGER: THE VULTURE ACTIVE ---")
    print(f"Targeting: {list(TARGETS.keys())}")
    print(f"Threshold: {args.trigger_bps} bps delta")

    while True:
        try:
            # 1. Fetch Kraken Prices
            krk_tickers = krk_client.ticker(list(TARGETS.keys()))
            
            # 2. Fetch Coinbase Prices
            # best_bid_ask returns a list of bids/asks for products
            cb_res = cb_client.best_bid_ask(list(TARGETS.values()))
            cb_prices = {}
            for item in cb_res.get("price_book", []):
                pid = item.get("product_id")
                bids = item.get("bids", [])
                asks = item.get("asks", [])
                if bids and asks:
                    mid = (float(bids[0]["price"]) + float(asks[0]["price"])) / 2.0
                    cb_prices[pid] = mid

            # 3. Analyze Lead-Lag Delta
            for krk_pid, cb_pid in TARGETS.items():
                krk_data = krk_tickers.get(krk_pid)
                if not krk_data: continue
                
                krk_bid = float(krk_data["b"][0])
                krk_ask = float(krk_data["a"][0])
                krk_mid = (krk_bid + krk_ask) / 2.0
                
                cb_mid = cb_prices.get(cb_pid)
                if not cb_mid: continue
                
                # Delta calculation: (Kraken / Coinbase) - 1
                # Negative delta means Kraken is CHEAPER (The Vulture buy)
                delta_bps = (krk_mid / cb_mid - 1.0) * 10000.0
                
                if abs(delta_bps) > 10.0: # Only log if > 10bps difference
                     print(f"[{utc_now_iso()}] {cb_pid}: Delta {delta_bps:.1f} bps (K: {krk_mid:.4f} vs C: {cb_mid:.4f})")

                if delta_bps < -args.trigger_bps:
                    print(f"[{utc_now_iso()}] 🚨 VULTURE TRIGGER: {krk_pid} is {abs(delta_bps):.1f} bps BELOW Coinbase!")
                    print(f"ACTION: Buy Kraken Floor, Sell Coinbase Hedge.")
                    # TODO: Implement real execution here if not --validate-only
                
                elif delta_bps > args.trigger_bps:
                    print(f"[{utc_now_iso()}] 🚨 VULTURE TRIGGER: {krk_pid} is {delta_bps:.1f} bps ABOVE Coinbase!")
                    print(f"ACTION: Sell Kraken High, Buy Coinbase Hedge.")

        except Exception as e:
            print(f"Error: {e}")
            
        time.sleep(args.poll_seconds)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Titan 10.2 Vulture")
    parser.add_argument("--trigger-bps", type=float, default=50.0, help="Delta threshold to trigger action")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Polling interval")
    parser.add_argument("--validate-only", action="store_true", help="Run in simulation mode")
    
    args = parser.parse_args()
    run_vulture(args)
