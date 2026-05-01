#!/usr/bin/env python3
"""Kraken Pocket Executor: Venue-Guided Execution.

Monitor Coinbase for the Foundry's positive pockets (RSI-4, etc.) 
but execute on Kraken to capture the 0bps maker fee edge.
"""
import argparse
import json
import sys
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
POCKET_BOARD = ROOT / "reports" / "coinbase_spot_foundry_pocket_board.json"
STATE_PATH = ROOT / "reports" / "kraken_pocket_executor_state.json"
EVENT_PATH = ROOT / "reports" / "kraken_pocket_executor_events.jsonl"
TOXICITY_LOG = ROOT / "reports" / "neural_harpoon_shadow_log.jsonl"

# Clients
from coinbase_advanced_client import CoinbaseAdvancedClient
from kraken_spot_client import KrakenSpotClient
from live_coinbase_spot_machinegun_shadow import fetch_coinbase_ticks
from candle_cache_service import load_candles

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

class ToxicityFilter:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.toxic_pids = {} # pid -> last_seen_ts

    def refresh(self):
        if not self.log_path.exists():
            return
        
        try:
            with open(self.log_path, "r") as f:
                lines = f.readlines()
                for line in lines[-100:]: # Check last 100 events
                    data = json.loads(line)
                    pid = data.get("product_id")
                    if data.get("harpoon_action") == "SHADOW_SHORT":
                        self.toxic_pids[pid] = data.get("ts_utc")
        except Exception as e:
            print(f"Error refreshing toxicity filter: {e}")

    def is_toxic(self, pid: str) -> bool:
        # If seen in last 30 minutes, consider toxic
        last_seen = self.toxic_pids.get(pid)
        if not last_seen:
            return False
        
        try:
            ts = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            return age < 1800 # 30 minutes
        except:
            return False

class KrakenPocketExecutor:
    def __init__(self, starting_cash=100.0, deploy_pct=0.8, fee_bps=0.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.deploy_pct = deploy_pct
        self.fee_bps = fee_bps # Default to 0bps for Maker
        
        self.position = None
        self.trades_executed = 0
        self.trades_won = 0
        self.total_net = 0.0
        self.total_fees = 0.0
        
        self.candles = {}
        self.toxicity = ToxicityFilter(TOXICITY_LOG)

    def update_price(self, product_id: str, price: float):
        if self.position and self.position["product_id"] == product_id:
            if price > self.position["highest_price"]:
                self.position["highest_price"] = price
            
            # Unrealized calc
            self.position["current_price"] = price
            net_pct = ((price / self.position["entry_price"]) - 1.0) * 100.0
            self.position["net_pct"] = net_pct

    def check_pockets(self, pockets, ticks):
        self.toxicity.refresh()
        for p in pockets:
            pid = p["product_id"]
            if self.toxicity.is_toxic(pid):
                continue
                
            # Check RSI(4) trigger (mocked logic from pocket board)
            # In real use, this would use self.candles[pid]
            # For now, we use the ticker as a proxy if trigger is price-based
            pass 
        return None
    def state(self):
        return {
            "cash": self.cash,
            "starting_cash": self.starting_cash,
            "position": self.position,
            "trades_executed": self.trades_executed,
            "trades_won": self.trades_won,
            "total_net": self.total_net,
            "total_fees": self.total_fees
        }

    def open_position(self, pocket, entry_price, bid_price):
        # We model entry at the BID (Maker)
        cost = self.cash * self.deploy_pct
        fee_usd = cost * (self.fee_bps / 10000.0)
        quantity = (cost - fee_usd) / entry_price
        
        self.position = {
            "product_id": pocket["product_id"],
            "entry_price": entry_price,
            "quantity": quantity,
            "cost_usd": cost,
            "opened_at": utc_now_iso(),
            "highest_price": entry_price,
            "trail_pct": 2.5, # Default 2.5% trail
            "entry_fee": fee_usd
        }
        self.cash -= cost
        self.trades_executed += 1
        
        append_jsonl(EVENT_PATH, {
            "time": utc_now_iso(),
            "event": "kraken_pocket_entry",
            "product_id": pocket["product_id"],
            "price": entry_price,
            "cost": cost
        })

def load_pockets():
    if not POCKET_BOARD.exists():
        return []
    with open(POCKET_BOARD) as f:
        board = json.load(f)
    return [r for r in board.get("rows", []) if to_float(r.get("pocket_score")) > 0]

def main():
    parser = argparse.ArgumentParser(description="Kraken Pocket Executor (Venue-Guided)")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    
    print("=" * 80)
    print("KRAKEN POCKET EXECUTOR - Venue-Guided Harpoon")
    print("=" * 80)
    
    pockets = load_pockets()
    if not pockets:
        print("No positive pockets found. Exiting.")
        return
        
    executor = KrakenPocketExecutor(starting_cash=args.starting_cash)
    cb_client = CoinbaseAdvancedClient()
    kr_client = KrakenSpotClient()
    
    pocket_products = list(set(p["product_id"] for p in pockets))
    
    try:
        while True:
            # Radar: Fetch Coinbase Ticks
            cb_ticks = fetch_coinbase_ticks(cb_client, pocket_products)
            if not cb_ticks:
                time.sleep(args.poll_seconds)
                continue
                
            # Update Price & Check Signals (Logic would go here)
            # ...
            
            save_json(STATE_PATH, {"updated_at": utc_now_iso(), "executor": executor.state()})
            
            if args.once: break
            time.sleep(args.poll_seconds)
            
    except KeyboardInterrupt:
        print("Stopping...")

if __name__ == "__main__":
    main()
