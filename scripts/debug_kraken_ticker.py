#!/usr/bin/env python3
import sys
from pathlib import Path

SCRIPTS = Path("scripts")
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient

def main():
    client = KrakenSpotClient()
    try:
        # Try different possible names
        targets = ["KEYUSD", "KEY/USD", "L3USD", "L3/USD", "HONEYUSD", "HONEY/USD"]
        for target in targets:
            print(f"Fetching ticker for {target}...")
            try:
                res = client.ticker([target])
                print(f"  Result: {res}")
            except Exception as e:
                print(f"  Error: {e}")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
