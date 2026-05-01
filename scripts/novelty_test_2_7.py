import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"

def main():
    client = CoinbaseAdvancedClient()
    print(f"🚀 NOVELTY TEST #2 & #7: VELOCITY DECAY & AGGRESSOR IMBALANCE on {PRODUCT}...")
    
    last_book = None
    
    # Track stats
    decay_events = 0
    decay_followed_by_drop = 0
    
    aggressor_buy_vol = 0.0
    aggressor_sell_vol = 0.0
    
    start_time = time.time()
    
    try:
        while time.time() - start_time < 300: # 5 minute live sample
            try:
                resp = client.best_bid_ask([PRODUCT])
                book = resp["pricebooks"][0]
                bid_p = float(book["bids"][0]["price"])
                bid_s = float(book["bids"][0]["size"])
                ask_p = float(book["asks"][0]["price"])
                ask_s = float(book["asks"][0]["size"])
                
                if last_book:
                    # 1. TEST #2: Velocity Decay (Walls shrinking without price move)
                    if bid_p == last_book["bid_p"] and bid_s < last_book["bid_s"]:
                        # Buy wall shrunk. Was it a trade or a pull?
                        # We proxy 'trade' by checking if ticker volume changed (hard in L1)
                        # Let's just track 'Size Decay' as a signal.
                        decay_events += 1
                        # (We'd need longer tracking to see if it leads to a drop)
                        pass
                    
                    # 2. TEST #7: Aggressor Imbalance Proxy
                    # If Ask size decreases at same price -> Likely a Market Buy (Aggressor Buy)
                    if ask_p == last_book["ask_p"] and ask_s < last_book["ask_s"]:
                        delta = last_book["ask_s"] - ask_s
                        aggressor_buy_vol += delta * ask_p
                    
                    # If Bid size decreases at same price -> Likely a Market Sell (Aggressor Sell)
                    if bid_p == last_book["bid_p"] and bid_s < last_book["bid_s"]:
                        delta = last_book["bid_s"] - bid_s
                        aggressor_sell_vol += delta * bid_p

                last_book = {"bid_p": bid_p, "bid_s": bid_s, "ask_p": ask_p, "ask_s": ask_s}
                
                print(f"  Aggressor Imbalance: B={aggressor_buy_vol:.2f} S={aggressor_sell_vol:.2f}", end="\r")
                
            except: pass
            time.sleep(2) # High frequency
            
    except KeyboardInterrupt:
        pass

    print("\n\n--- 5-MINUTE LIVE SAMPLE RESULTS ---")
    print(f"Total Aggressor BUY Volume (Proxy): ${aggressor_buy_vol:.2f}")
    print(f"Total Aggressor SELL Volume (Proxy): ${aggressor_sell_vol:.2f}")
    imb = (aggressor_buy_vol - aggressor_sell_vol) / (aggressor_buy_vol + aggressor_sell_vol + 1)
    print(f"Aggressor Imbalance: {imb:.4f}")

if __name__ == "__main__":
    main()
