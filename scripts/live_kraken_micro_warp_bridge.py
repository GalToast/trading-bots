#!/usr/bin/env python3
"""
Kraken Micro-Warp Executor (WebSocket)
Bridges Coinbase micro-anomalies with Kraken execution using sub-second WebSockets.
"""

import asyncio
import json
import os
import sys
import time
import websockets
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "scripts"))

from kraken_spot_client import KrakenSpotClient
from toxicity_filter import ToxicityFilter

# Paths
VETO_PATH = ROOT / "reports" / "kraken_toxic_veto.json"
SHADOW_LOG_PATH = ROOT / "reports" / "neural_harpoon_shadow_log.jsonl"
HANDOFF_PATH = ROOT / "reports" / "venue_handoff_bridge.json"
WARP_LOG_PATH = ROOT / "reports" / "kraken_micro_warp_log.jsonl"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path, payload):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")

class MicroWarpBridge:
    def __init__(self, products):
        self.products = products
        self.toxicity = ToxicityFilter(SHADOW_LOG_PATH)
        self.prices = {p: {"bid": 0.0, "ask": 0.0} for p in products}
        self.active_vetoes = {}
        
    async def kraken_ws_loop(self):
        url = "wss://ws.kraken.com"
        async with websockets.connect(url) as ws:
            # Subscribe to tickers
            subscribe_msg = {
                "event": "subscribe",
                "pair": [p.replace("-", "/") for p in self.products],
                "subscription": {"name": "ticker"}
            }
            await ws.send(json.dumps(subscribe_msg))
            
            print(f"[*] Subscribed to Kraken WS Tickers for {self.products}")
            
            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                
                if isinstance(data, list):
                    # Kraken Ticker Format: [channelID, {a: [price, lot...], b: [...]}, "ticker", "pair"]
                    ticker_data = data[1]
                    pair = data[3].replace("/", "-")
                    
                    if "a" in ticker_data:
                        self.prices[pair]["ask"] = float(ticker_data["a"][0])
                    if "b" in ticker_data:
                        self.prices[pair]["bid"] = float(ticker_data["b"][0])
                        
                    # Check for "Warp" triggers here if needed
                    # For now, just maintain the price cache for the execution logic

    async def monitor_handoffs(self):
        print("[*] Monitoring Handoffs & Toxicity...")
        while True:
            self.toxicity.refresh()
            
            # Load Handoffs
            if HANDOFF_PATH.exists():
                try:
                    with open(HANDOFF_PATH, "r") as f:
                        handoffs = json.load(f).get("handoffs", [])
                except:
                    handoffs = []
                    
                for h in handoffs:
                    pid = h["product_id"]
                    if pid not in self.prices: continue
                    
                    # 1. Toxicity Check
                    if self.toxicity.is_toxic(pid):
                        continue
                        
                    # 2. Price Check (Sub-second)
                    curr_ask = self.prices[pid]["ask"]
                    if curr_ask > 0:
                        # Log the "Warp Signal" potential
                        # In a live script, we would FIRE here.
                        # For shadow, we log the latency advantage.
                        msg = f"[{utc_now_iso()}] MICRO-WARP potential for {pid} at {curr_ask}"
                        # Only log every 5s to avoid spam
                        if random.random() < 0.05:
                            print(msg)
                            append_jsonl(WARP_LOG_PATH, {
                                "ts": utc_now_iso(),
                                "product_id": pid,
                                "ask": curr_ask,
                                "bid": self.prices[pid]["bid"],
                                "source": "ws_bridge"
                            })
            
            await asyncio.sleep(1.0)

    async def run(self):
        await asyncio.gather(
            self.kraken_ws_loop(),
            self.monitor_handoffs()
        )

import random

if __name__ == "__main__":
    PRODUCTS = ["FIGHT-USD", "TOSHI-USD", "RAVE-USD", "KAT-USD", "APE-USD"]
    bridge = MicroWarpBridge(PRODUCTS)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass
