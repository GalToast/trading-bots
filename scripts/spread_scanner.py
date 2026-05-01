import json
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCTS = [
    "MOG-USD", "A8-USD", "RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD",
    "IRYS-USD", "CFG-USD", "BOBBOB-USD", "DASH-USD", "FARTCOIN-USD",
    "COMP-USD", "MON-USD", "ZEC-USD", "VVV-USD", "ALGO-USD",
    "ARB-USD", "ETH-USD", "STORJ-USD", "SNX-USD", "AVAX-USD",
]

def main():
    client = CoinbaseAdvancedClient()
    print("Scanning for High-Spread Liquidity Gaps...")
    
    opportunities = []
    for pid in PRODUCTS:
        try:
            resp = client.best_bid_ask([pid])
            book = resp["pricebooks"][0]
            bid = float(book["bids"][0]["price"])
            ask = float(book["asks"][0]["price"])
            
            spread_pct = (ask - bid) / bid * 100
            
            if spread_pct >= 0.40: # Maker Fee floor
                opportunities.append({"pid": pid, "spread": spread_pct, "bid": bid, "ask": ask})
                print(f"  {pid}: Spread={spread_pct:.3f}% (Profit Zone!)")
            else:
                # print(f"  {pid}: Spread={spread_pct:.3f}% (Too tight)")
                pass
            
            time.sleep(0.2)
        except: pass

    opportunities.sort(key=lambda x: x["spread"], reverse=True)
    print("\n--- SPREAD-EATER LEADERBOARD ---")
    for o in opportunities:
        print(f"{o['pid']} | Spread={o['spread']:.3f}% | Bid={o['bid']} | Ask={o['ask']}")

if __name__ == "__main__":
    main()
