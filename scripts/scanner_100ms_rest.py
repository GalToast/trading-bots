#!/usr/bin/env python3
"""Titan 10.9: 100ms REST Pulse Scanner.

High-frequency polling for lead-indicators across liquid Goldilocks assets.
Detects acceleration events and signals the Playbook Bot via a simple file-gate.
"""
import argparse
import time
import json
from pathlib import Path
from kraken_spot_client import KrakenSpotClient

TARGETS = ["ALGOUSD", "L3USD", "RENDERUSD", "NEARUSD", "SOLUSD"]
GATE_PATH = Path("reports/cache/scanner_100ms_gate.json")

def main():
    client = KrakenSpotClient()
    print("--- 100ms PULSE SCANNER ACTIVE ---")
    
    # Store previous prices for acceleration detection
    prev_prices = {pid: 0.0 for pid in TARGETS}
    
    while True:
        try:
            start = time.time()
            tickers = client.ticker(TARGETS)
            
            for pid, data in tickers.items():
                price = float(data["c"][0])
                if prev_prices[pid] > 0:
                    move = (price / prev_prices[pid] - 1.0) * 10000
                    if abs(move) > 10.0: # 10bps accel
                        print(f"[{time.time()}] ACCEL DETECTED: {pid} moved {move:.1f} bps!")
                        # Signal the Playbook Bot
                        GATE_PATH.write_text(json.dumps({"product_id": pid, "accel_bps": move}))
                prev_prices[pid] = price
                
            elapsed = time.time() - start
            # Sleep remainder of 100ms window
            time.sleep(max(0, 0.1 - elapsed))
        except Exception as e:
            print(f"Scanner error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
