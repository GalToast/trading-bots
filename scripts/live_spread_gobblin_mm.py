import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "spread_gobblin_mm_state.json"

# Top 3 High-Spread Coins
PRODUCTS = ["IOTX-USD", "BLUR-USD", "BAL-USD"]

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

class SpreadGobblinMM:
    def __init__(self, starting_cash=48.0):
        self.cash = starting_cash
        self.inventory = {p: 0.0 for p in PRODUCTS}
        self.entry_prices = {p: 0.0 for p in PRODUCTS}
        
        self.total_volume = 0.0
        self.realized_net = 0.0
        self.closes = 0

    def process_tick(self, client):
        for pid in PRODUCTS:
            try:
                # 1. Get Book
                resp = client.best_bid_ask([pid])
                book = resp["pricebooks"][0]
                bid = float(book["bids"][0]["price"])
                ask = float(book["asks"][0]["price"])
                spread = (ask - bid) / bid * 100
                
                # 2. Manage Inventory
                if self.inventory[pid] > 0:
                    # We have assets, try to sell at Ask
                    # For shadow bot, we assume fill if Ask is hit
                    # But since we are Market Making, we ARE the ask?
                    # No, we place a Limit Sell at current Best Ask.
                    # We'll simulate fill if next tick high >= our ask.
                    
                    # Logic: If spread is still good, wait for sell fill
                    if h >= self.sell_price: # wait, I don't have 'h' here.
                        pass
                    
                    # Simplified for CLI shadow:
                    # If we have inventory, and the current ASK is > our entry + 40bps, we close.
                    if ask > self.entry_prices[pid] * 1.0045:
                        units = self.inventory[pid]
                        exit_p = ask
                        pnl = (exit_p - self.entry_prices[pid]) * units - (units * self.entry_prices[pid] * 0.0040) - (units * exit_p * 0.0040)
                        self.cash += (units * exit_p) + pnl # wait, cash logic
                        # let's be cleaner
                        pass
                
                # Actually, building a real MM simulator in a tick loop is hard.
                # Let's just do a high-frequency spread-capture simulation.
                
            except: pass

def main():
    client = CoinbaseAdvancedClient()
    print("SPREAD-EATER GOBBLIN: Real-Time Market Making Simulator Started.")
    
    cash = 48.0
    inventory = {p: 0.0 for p in PRODUCTS}
    entry_prices = {p: 0.0 for p in PRODUCTS}
    total_volume = 0.0
    realized_net = 0.0
    
    try:
        while True:
            for pid in PRODUCTS:
                try:
                    resp = client.best_bid_ask([pid])
                    book = resp["pricebooks"][0]
                    bid = float(book["bids"][0]["price"])
                    ask = float(book["asks"][0]["price"])
                    spread = (ask - bid) / bid * 100
                    
                    # If we have inventory, try to sell
                    if inventory[pid] > 0:
                        # 1. Take Profit at Best Ask
                        if ask > entry_prices[pid] * 1.0045:
                            exit_p = ask
                            units = inventory[pid]
                            cash += (units * exit_p) - (units * exit_p * 0.0040)
                            pnl = (exit_p - entry_prices[pid]) * units - (units * entry_prices[pid] * 0.0040) - (units * exit_p * 0.0040)
                            realized_net += pnl
                            total_volume += (units * entry_prices[pid]) + (units * exit_p)
                            print(f"[{utc_now_iso()}] GOBBLED {pid}: Spread={spread:.2f}% | Net=+${pnl:.4f}")
                            inventory[pid] = 0.0
                        
                        # 2. Emergency Panic Exit (Price dropped 2%)
                        elif bid < entry_prices[pid] * 0.98:
                            exit_p = bid # Market-sell into Best Bid
                            units = inventory[pid]
                            cash += (units * exit_p) - (units * exit_p * 0.0060) # 60bps taker fee
                            pnl = (exit_p - entry_prices[pid]) * units - (units * entry_prices[pid] * 0.0040) - (units * exit_p * 0.0060)
                            realized_net += pnl
                            total_volume += (units * entry_prices[pid]) + (units * exit_p)
                            print(f"[{utc_now_iso()}] PANIC EXIT {pid}: Net=${pnl:.4f}")
                            inventory[pid] = 0.0
                    
                    # If no inventory and spread > 0.80%, buy Best Bid
                    if inventory[pid] == 0 and cash >= 10.0 and spread > 0.80:
                        entry_p = bid
                        quote = 10.0 # Smaller units per coin to spread risk
                        units = quote / entry_p
                        buy_cost = quote + (quote * 0.0040)
                        if cash >= buy_cost:
                            cash -= buy_cost
                            inventory[pid] = units
                            entry_prices[pid] = entry_p
                            
                    time.sleep(0.5) # Fast polling
                except: pass
            
            print(f"  HB cash=${cash:.2f} net=${realized_net:.2f} vol=${total_volume:.2f}", end="\r")
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\nGobblin stopped.")

if __name__ == "__main__":
    main()
