#!/usr/bin/env python3
"""
Explicit Validate-Only Probe for Kraken
Satisfies the `post_only_validate_order_not_recorded` blocker on the live-readiness board.
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from kraken_spot_client import KrakenSpotClient
from live_penetration_lattice_shadow import append_jsonl

def main():
    client = KrakenSpotClient()
    
    # We use APE-USD or KAT-USD, something small to validate.
    pid = "HOUSE-USD"
    rest_pair = "HOUSEUSD" # Assuming this maps directly
    
    # Fetch current price to place a safe limit order (deep bid)
    try:
        ticker = client.ticker([rest_pair])
        if not ticker:
            print(f"Failed to fetch ticker for {rest_pair}")
            return
            
        data = ticker.get(list(ticker.keys())[0])
        bid = float(data['b'][0])
        
        # Safe limit price (10% below current bid)
        safe_bid = bid * 0.90
        
        # Calculate volume for $10
        volume = 10.0 / safe_bid
        
        print(f"Executing validate-only probe for {pid} at {safe_bid:.6f} (vol: {volume:.2f})")
        
        resp = client.add_order(
            rest_pair=rest_pair,
            side="buy",
            order_type="limit",
            volume=volume,
            price=round(safe_bid, 6),
            post_only=True,
            validate=True # THIS IS THE CRITICAL FLAG
        )
        print(f"Kraken Response: {json.dumps(resp)}")
        
        # Record the telemetry to satisfy the board
        event_path = Path("reports/kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_ab_events.jsonl")
        
        append_jsonl(event_path, {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "action": "kraken_validate_order",
            "product_id": pid,
            "reason": "live_readiness_probe",
            "validate_only": True,
            "post_only": True,
            "response": resp
        })
        
        print(f"Telemetry recorded to {event_path}")
        
    except Exception as e:
        print(f"Probe failed: {e}")

if __name__ == "__main__":
    main()
