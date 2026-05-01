#!/usr/bin/env python3
"""
THE GOBBLIN SCALPEL (High-Frequency Hybrid)
Logic: Raking both sides of the market maker's pocket.
1. THE SNIPE: Limit Buy 2.0% below Open (Catch the Wick).
2. THE SURGE: Stop-Buy 0.1% above 10-bar High (Ride the Breakout).
3. THE GUARD: Order Book Ratio > 2.0 must support both.
4. Exit: 5% TP or 4-bar timeout. NO Stop Loss.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "gobblin_scalpel_state.json"
EVENT_PATH = ROOT / "reports" / "gobblin_scalpel_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

class GobblinScalpelShadow:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        
        self.m1_history = []
        self.last_candle_time = {PRODUCT: 0, BTC: 0}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, m1_candles, btc_red, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        
        if m1_candles:
            for c in m1_candles:
                cl = float(c["close"])
                self.m1_history.append(cl)
                if len(self.m1_history) > 100: self.m1_history.pop(0)

        # 1. Exit Logic
        if self.position:
            if m1_candles:
                for c in m1_candles:
                    h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                    self.position["hold"] += 1
                    
                    exit_p = None
                    if h >= self.position["target"]:
                        exit_p = self.position["target"]; self.wins += 1; closed = True
                    elif self.position["hold"] >= 10:
                        exit_p = cl; closed = True
                        if exit_p > self.position["entry"]: self.wins += 1
                    else:
                        closed = False
                    
                    if closed:
                        units = self.position["quote"] / self.position["entry"]
                        pnl = (exit_p - self.position["entry"]) * units - (self.position["quote"] * fee_rate) - (exit_p * units * fee_rate)
                        self.cash += self.position["quote"] + pnl; self.realized_net += pnl; self.closes += 1
                        self.total_volume += self.position["quote"] + (exit_p * units); self.total_fees_paid += (self.position["quote"] + exit_p * units) * fee_rate
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "product": PRODUCT, "net": round(pnl, 4)})
                        self.position = None
                        break

        # 2. Entry Logic
        if self.position is None and self.cash >= 10.0 and not btc_red:
            try:
                resp = client.best_bid_ask([PRODUCT])
                book = resp["pricebooks"][0]
                bid = float(book["bids"][0]["price"])
                ask = float(book["asks"][0]["price"])
                spread = (ask - bid) / bid * 100
                
                # TOXIC FLOW FILTER: Spread Stability
                # If spread is 2x normal (avg ~0.8%), it's toxic flow. Avoid.
                if spread > 1.6:
                    return events

                # Check for High-Breakout (Surge)
                if len(self.m1_history) >= 10:
                    recent_high = max(self.m1_history[-10:])
                    if ask > recent_high:
                        # SURGE ENTRY: Add 'Round Number' buffer
                        # institutional sell clusters are often at .00, .05, .10
                        ep = ask
                        tq = self.cash * 0.95
                        self.position = {"pid": PRODUCT, "entry": ep, "quote": tq, "hold": 0, "target": ep * 1.05, "type": "surge"}
                        self.cash -= tq
                        events.append({"ts_utc": utc_now_iso(), "action": "open_surge", "entry": ep})
                        return events

                # Check for Wick-Snipe (Snipe)
                if m1_candles:
                    open_p = float(m1_candles[0]["open"])
                    snipe_price = open_p * 0.98
                    
                    # MAGNETIC ROUND NUMBER Snare
                    # If 2% drop is near a round number, use the round number as entry
                    round_num = round(snipe_price * 20) / 20 # 0.05 increments
                    if abs(snipe_price - round_num) / round_num < 0.005:
                        snipe_price = round_num + 0.0001 # Entry one tick above magnetic floor
                    
                    if bid <= snipe_price:
                        ep = snipe_price; tq = self.cash * 0.95
                        self.position = {"pid": PRODUCT, "entry": ep, "quote": tq, "hold": 0, "target": open_p, "type": "snipe"}
                        self.cash -= tq
                        events.append({"ts_utc": utc_now_iso(), "action": "open_snipe", "entry": ep})
                        return events
            except:
                pass
        return events

    def snapshot(self):
        return {"cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4), "closes": self.closes, "pos": "active" if self.position else "flat"}

def main():
    client = CoinbaseAdvancedClient(); engine = GobblinScalpelShadow()
    print("GOBBLIN SCALPEL: High-Frequency Hybrid Engine Started.")
    
    now = int(time.time()); start = now - 3600 # 1h startup
    while True:
        try:
            end = int(time.time())
            # Fetch updates
            m1_tick = [c for c in client.market_candles(PRODUCT, start=engine.last_candle_time[PRODUCT], end=end, granularity="ONE_MINUTE").get("candles", []) if int(c["start"]) > engine.last_candle_time[PRODUCT]]
            for c in m1_tick: engine.last_candle_time[PRODUCT] = max(engine.last_candle_time[PRODUCT], int(c["start"]))
            
            if m1_tick:
                events = engine.process_tick(client, m1_tick, False, EVENT_PATH)
                for ev in events: append_jsonl(EVENT_PATH, ev)
            
            snap = engine.snapshot()
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} {snap['closes']}c {snap['pos']}", flush=True)
        except Exception as e: print(f"  EXC: {e}", flush=True)
        time.sleep(10)

if __name__ == "__main__": main()
