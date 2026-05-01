#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from kraken_spot_client import KrakenSpotClient, to_float

REPORTS = ROOT / "reports"
DEFAULT_STATE_PATH = REPORTS / "kraken_maker_fee_shadow_state.json"
DEFAULT_EVENT_PATH = REPORTS / "kraken_maker_fee_shadow_events.jsonl"
FRONTIER_BOARD_PATH = REPORTS / "kraken_spot_frontier_strategy_board.json"

@dataclass
class MakerPosition:
    product_id: str
    entry_price: float
    quantity: float
    cost_usd: float
    opened_at: str
    highest_bid: float
    trail_pct: float
    status: str = "open"
    mode: str = "MAKER" # Always MAKER for this sim

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

class KrakenMakerFeeShadowSim:
    def __init__(self, starting_cash: float = 1000.0, max_positions: int = 10):
        self.cash = starting_cash
        self.max_positions = max_positions
        self.positions: dict[str, MakerPosition] = {}
        self.client = KrakenSpotClient()
        self.poll_count = 0
        self.total_net = 0.0
        self.total_fees = 0.0

    def run_poll(self, products: list[str]):
        self.poll_count += 1
        print(f"--- MAKER SIM POLL {self.poll_count} | Cash: ${self.cash:.2f} | Open: {len(self.positions)} ---")
        
        # 1. Fetch Tickers
        rest_pairs = [p.replace("-", "") for p in products]
        try:
            tickers = self.client.ticker(rest_pairs)
        except Exception as e:
            print(f"Ticker error: {e}")
            return

        # 2. Update existing positions
        for pid, pos in list(self.positions.items()):
            clean_pid = pid.replace("-", "")
            data = None
            for k, v in tickers.items():
                if clean_pid in k:
                    data = v
                    break
            
            if not data: continue
            
            bid = to_float(data.get("b", [0])[0])
            ask = to_float(data.get("a", [0])[0])
            
            if bid > pos.highest_bid:
                pos.highest_bid = bid
            
            # Exit Logic (Maker Exit at ASK)
            # In a real sim, we'd check if the ask was HIT.
            # For this shadow sim, we assume if price moves significantly, we get filled.
            # Simplified: Exit if target reached or trail hit.
            
            drawdown = (pos.highest_bid - bid) / pos.highest_bid if pos.highest_bid > 0 else 0
            if drawdown >= pos.trail_pct:
                # Close at ASK (Maker)
                self.close_position(pos, ask, "trail_hit")
                continue

        # 3. Entry Logic (Maker Entry at BID)
        if len(self.positions) < self.max_positions:
            frontier = load_json(FRONTIER_BOARD_PATH)
            candidates = frontier.get("rows", [])
            
            for row in candidates:
                pid = str(row["product_id"])
                if pid in self.positions: continue
                
                # Check if it's in our target products for this sim
                if pid not in products: continue
                
                # ML Gate (Relaxed for Sim)
                tail_prob = to_float(row.get("tail_prob"))
                if tail_prob >= 0.55:
                    clean_pid = pid.replace("-", "")
                    data = None
                    for k, v in tickers.items():
                        if clean_pid in k:
                            data = v
                            break
                    
                    if not data: continue
                    
                    bid = to_float(data.get("b", [0])[0])
                    if bid <= 0: continue
                    
                    self.open_position(pid, bid)
                    if len(self.positions) >= self.max_positions:
                        break

    def open_position(self, product_id: str, price: float):
        cost = 100.0 # Standard unit
        if cost > self.cash: return
        
        # Maker fee = 0bps (Post-Only)
        fee = 0.0
        quantity = cost / price
        
        pos = MakerPosition(
            product_id=product_id,
            entry_price=price,
            quantity=quantity,
            cost_usd=cost,
            opened_at=utc_now_iso(),
            highest_bid=price,
            trail_pct=0.02
        )
        self.positions[product_id] = pos
        self.cash -= cost
        
        event = {
            "time": utc_now_iso(),
            "event": "maker_entry",
            "product_id": product_id,
            "price": price,
            "cost": cost,
            "fee": fee
        }
        append_jsonl(DEFAULT_EVENT_PATH, event)
        print(f"  MAKER OPEN: {product_id} at {price:.8f}")

    def close_position(self, pos: MakerPosition, price: float, reason: str):
        # Maker fee = 0bps
        proceeds = pos.quantity * price
        net = proceeds - pos.cost_usd
        
        self.cash += proceeds
        self.total_net += net
        del self.positions[pos.product_id]
        
        event = {
            "time": utc_now_iso(),
            "event": "maker_exit",
            "product_id": pos.product_id,
            "price": price,
            "net_pnl": net,
            "pnl_pct": (net / pos.cost_usd) * 100,
            "reason": reason
        }
        append_jsonl(DEFAULT_EVENT_PATH, event)
        print(f"  MAKER CLOSE: {pos.product_id} at {price:.8f} | PnL: {event['pnl_pct']:.2f}%")

def main():
    # Target Products from Geometric Sibling Board
    PRODUCTS = ["HONEY-USD", "CQT-USD", "STEP-USD", "ACA-USD", "SHAPE-USD", "DUCK-USD", "GST-USD", "HIPPO-USD"]
    
    sim = KrakenMakerFeeShadowSim()
    
    try:
        while True:
            sim.run_poll(PRODUCTS)
            time.sleep(30)
    except KeyboardInterrupt:
        print("Stopping sim...")

if __name__ == "__main__":
    main()
