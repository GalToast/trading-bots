#!/usr/bin/env python3
"""Titan 10.0 Inter-Exchange Bridge (Kraken Maker -> Coinbase Taker).

Hedges Kraken maker fills by triggering immediate Coinbase taker orders.
Essential for sector-dislocation strategies where we want to capture
relative strength differences across exchanges.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from coinbase_advanced_client import CoinbaseAdvancedClient

# Mapping Kraken product IDs to Coinbase product IDs (or sector proxies)
SYMBOL_MAP = {
    # Direct Mappings
    "TRAC-USD": "TRAC-USD",
    "SOL-USD": "SOL-USD",
    "GLMR-USD": "GLMR-USD",
    "DOT-USD": "DOT-USD",
    "AKT-USD": "AKT-USD",
    "FET-USD": "FET-USD",
    "RENDER-USD": "RENDER-USD",
    "TAO-USD": "TAO-USD",
    "PYTH-USD": "PYTH-USD",
    "JUP-USD": "JUP-USD",
    "PENGU-USD": "PENGU-USD",
    "SPX-USD": "SPX-USD",
    "EDU-USD": "EDU-USD",
    "EDU-EUR": "EDU-USD",
    "BLUR-USD": "BLUR-USD",
    "ENS-USD": "ENS-USD",
    "KSM-USD": "KSM-USD",
    "KEY-USD": "KEY-USD",
    "KEY-EUR": "KEY-USD",
    "L3-USD": "L3-USD",
    "CHEX-USD": "CHEX-USD",
    "CHEX-EUR": "CHEX-USD",
    "HOUSE-USD": "SPX-USD",     # Meme-Vol Proxy
    "HOUSE-EUR": "SPX-USD",
    "BERT-USD": "SPX-USD",
    "BERT-EUR": "SPX-USD",
    "GOAT-EUR": "GOAT-USD",
    "ALICE-EUR": "ALICE-USD",
    "CHIP-EUR": "SPX-USD",      # Proxy
    "DOG-EUR": "DOGE-USD",      # Proxy
    "HONEY-USD": "SOL-USD",     # SOL-Eco Proxy (Audit Fix: Ensure this is correctly mapped)
    "HONEY-EUR": "SOL-USD",
    
    # Sector Proxies (Kraken exclusive -> Coinbase Proxy)
    "AI3-USD": "RENDER-USD",      # AI-Compute Sector Proxy
    "KOBAN-USD": "PENGU-USD",     # Meme-Vol Proxy
    "ANLOG-USD": "NEAR-USD",      # DePIN/Infra Proxy
    "PLANCK-USD": "FET-USD",      # AI Sector Proxy
    "SHAPE-USD": "SOL-USD",       # SOL-Eco Proxy
    "CQT-USD": "GLMR-USD",        # Polkadot/Infra Proxy
}

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def log_bridge_event(payload: dict[str, Any], event_path: Path):
    payload["bridge_ts_utc"] = utc_now_iso()
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with open(event_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")

def send_coinbase_order(client: CoinbaseAdvancedClient, product_id: str, side: str, quote_usd: float) -> dict[str, Any]:
    """Helper to send market orders with retry and side-specific sizing logic."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            client_oid = f"bridge-{int(time.time()*1000)}-{product_id}"
            
            # ADVERSARIAL FIX: For SELLs, try to use base_size if possible, 
            # though market orders on Coinbase Advanced often prefer quote_size for BUYs
            # and base_size for SELLs. To keep it simple, we'll try quote_size first
            # but log the attempt.
            
            if side == "SELL":
                # If we had ticker data here, we'd convert quote_usd to base_size.
                # For now, we'll use quote_size and handle the potential rejection.
                res = client.create_market_order(
                    product_id=product_id,
                    side="SELL",
                    quote_size=quote_usd,
                    client_order_id=client_oid
                )
            else:
                res = client.create_market_order(
                    product_id=product_id,
                    side="BUY",
                    quote_size=quote_usd,
                    client_order_id=client_oid
                )
                
            if "order_id" in res:
                return res
            else:
                print(f"[{utc_now_iso()}] Order Error Attempt {attempt+1}: {res}")
                
        except Exception as e:
            print(f"[{utc_now_iso()}] Order Exception Attempt {attempt+1}: {e}")
            
        time.sleep(1)
    return {"error": "Failed after max retries"}

def run_bridge(args: argparse.Namespace):
    cb_client = CoinbaseAdvancedClient()
    
    print(f"--- TITAN 10.0 INTER-EXCHANGE BRIDGE ACTIVE (HARDENED) ---")
    print(f"Monitoring Kraken Events: {args.kraken_event_path}")
    print(f"Bridge Journal: {args.bridge_event_path}")
    
    if args.validate_only:
        print("MODE: VALIDATE ONLY (SIMULATION)")
    else:
        if not cb_client.has_auth():
            print("CRITICAL: Coinbase API credentials not found. Use --validate-only for simulation.")
            return
        print("MODE: LIVE HEDGING ENABLED")

    active_hedges = {} # Map Kraken product_id -> {coinbase_product, size, order_id}

    file_pos = 0
    if not args.from_beginning and args.kraken_event_path.exists():
        file_pos = args.kraken_event_path.stat().st_size
    
    print(f"Starting scan from position {file_pos}...")

    while True:
        if not args.kraken_event_path.exists():
            print(f"Waiting for log file to appear: {args.kraken_event_path}")
            time.sleep(5)
            continue
        
        # ADVERSARIAL FIX: Log Rotation Detection
        curr_size = args.kraken_event_path.stat().st_size
        if curr_size < file_pos:
            print(f"[{utc_now_iso()}] Log rotation/truncation detected. Resetting pointer from {file_pos} to 0.")
            file_pos = 0

        try:
            with open(args.kraken_event_path, "r", encoding="utf-8") as f:
                f.seek(file_pos)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    
                    try:
                        event = json.loads(line)
                        action = event.get("action")
                        product_id = event.get("product_id")
                        
                        if action in ["open_maker_shadow", "open_maker_live"]:
                            if product_id in SYMBOL_MAP:
                                target_symbol = SYMBOL_MAP[product_id]
                                qty = event.get("quantity", 0.0)
                                cost_usd = event.get("cost_usd", 0.0) or event.get("planned_quote_usd", 0.0) or 10.0
                                
                                print(f"[{utc_now_iso()}] KRAKEN FILL: {product_id} | Qty: {qty} | Cost: ${cost_usd:.2f}")
                                print(f"[{utc_now_iso()}] BRIDGE TRIGGER: Hedging on Coinbase with {target_symbol}")
                                
                                if not args.validate_only:
                                    res = send_coinbase_order(cb_client, target_symbol, "SELL", cost_usd)
                                    order_id = res.get("order_id", "UNKNOWN")
                                    
                                    active_hedges[product_id] = {
                                        "target": target_symbol,
                                        "size": cost_usd,
                                        "order_id": order_id
                                    }
                                    
                                    log_bridge_event({
                                        "action": "bridge_hedge_sent",
                                        "kraken_product": product_id,
                                        "coinbase_product": target_symbol,
                                        "side": "SELL",
                                        "notional_usd": cost_usd,
                                        "order_id": order_id,
                                        "status": "sent" if "order_id" in res else "failed",
                                        "error": res.get("error") or res.get("message")
                                    }, args.bridge_event_path)
                                else:
                                    log_bridge_event({
                                        "action": "bridge_hedge_simulated",
                                        "kraken_product": product_id,
                                        "coinbase_product": target_symbol,
                                        "notional_usd": cost_usd,
                                        "side": "SELL"
                                    }, args.bridge_event_path)

                        elif action in ["close_maker_shadow", "close_maker_live"]:
                            if product_id in SYMBOL_MAP:
                                target_symbol = SYMBOL_MAP[product_id]
                                print(f"[{utc_now_iso()}] KRAKEN CLOSE: {product_id}")
                                print(f"[{utc_now_iso()}] BRIDGE UNWIND: Closing Coinbase hedge on {target_symbol}")
                                
                                if not args.validate_only:
                                    prev_hedge = active_hedges.pop(product_id, None)
                                    unwind_size = prev_hedge["size"] if prev_hedge else event.get("cost_usd", 0.0) or 10.0
                                    
                                    res = send_coinbase_order(cb_client, target_symbol, "BUY", unwind_size)
                                    order_id = res.get("order_id", "UNKNOWN")
                                    
                                    log_bridge_event({
                                        "action": "bridge_unwind_sent",
                                        "kraken_product": product_id,
                                        "coinbase_product": target_symbol,
                                        "side": "BUY",
                                        "notional_usd": unwind_size,
                                        "order_id": order_id,
                                        "status": "sent" if "order_id" in res else "failed",
                                        "error": res.get("error") or res.get("message")
                                    }, args.bridge_event_path)
                                else:
                                    log_bridge_event({
                                        "action": "bridge_unwind_simulated",
                                        "kraken_product": product_id,
                                        "coinbase_product": target_symbol,
                                        "side": "BUY"
                                    }, args.bridge_event_path)

                    except json.JSONDecodeError:
                        continue
                
                file_pos = f.tell()
        except Exception as e:
            print(f"ERROR: Log monitor encountered error: {e}")
            time.sleep(5)
            
        time.sleep(args.poll_seconds)

def main():
    parser = argparse.ArgumentParser(description="Titan 10.0 Bridge")
    parser.add_argument("--kraken-event-path", type=Path, 
                        default=ROOT / "reports" / "kraken_spot_maker_machinegun_parallel_ratio50_taker_guard_live_exec_dds25_fixed_texas_safe_epoch1_ab_events.jsonl",
                        help="Path to Kraken event log")
    parser.add_argument("--bridge-event-path", type=Path, default=ROOT / "reports" / "titan10_bridge_events.jsonl")
    parser.add_argument("--validate-only", action="store_true", help="Run in simulation mode")
    parser.add_argument("--from-beginning", action="store_true", help="Start scanning from beginning of log")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="Polling interval")
    
    args = parser.parse_args()
    
    if not args.kraken_event_path.exists():
        fallback = ROOT / "reports" / "kraken_spot_maker_machinegun_shadow_events.jsonl"
        if fallback.exists():
            args.kraken_event_path = fallback
            print(f"Defaulting to fallback log: {args.kraken_event_path}")

    run_bridge(args)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBridge stopping...")
        sys.exit(0)
