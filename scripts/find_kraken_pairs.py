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
        pairs = client.asset_pairs()
        print(f"Total Kraken Pairs: {len(pairs)}")
        
        targets = ["AI3", "TRAC", "RENDER", "SOL", "NEAR", "FET"]
        for target in targets:
            print(f"\nLooking for {target} pairs:")
            found = False
            for rest_pair, info in pairs.items():
                base = info.get("base", "")
                quote = info.get("quote", "")
                wsname = info.get("wsname", "")
                if target in wsname:
                    print(f"  - {wsname} (Base: {base}, Quote: {quote})")
                    found = True
            if not found:
                print(f"  - None found")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()

