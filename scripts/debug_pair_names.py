#!/usr/bin/env python3
import sys
from pathlib import Path

SCRIPTS = Path("scripts")
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, parse_pair

def main():
    client = KrakenSpotClient()
    try:
        pairs = client.asset_pairs()
        for p_id in ["KEYUSD", "L3USD", "CHEXUSD"]:
            info = pairs.get(p_id)
            if info:
                pair = parse_pair(p_id, info)
                print(f"Product: {p_id}")
                print(f"  rest_pair: {pair.rest_pair}")
                print(f"  altname: {pair.altname}")
                print(f"  wsname: {pair.wsname}")
            else:
                print(f"Product {p_id} not found in asset_pairs")
                
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
