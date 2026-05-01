import json
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

def main():
    client = CoinbaseAdvancedClient()
    products = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
    
    print("Fetching Best Bid/Ask for Top 5...")
    resp = client.best_bid_ask(products)
    print(json.dumps(resp, indent=2))

if __name__ == "__main__":
    main()
