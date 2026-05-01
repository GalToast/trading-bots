#!/usr/bin/env python3
"""
OMNI-GOBBLIN FORTRESS V2 (The 10bps VIP Blitz)
Fuses:
1. RSI MR Sniper (Alpha Anchor)
2. Spread-Eater Grinder (Volume & Fee Hacking)
3. Predatory Logic Engine (Kraken Lag + Gulp Shield + Magnetic Offset)
4. 2026 Fee Tier Awareness ($1M = 0.08% Maker Armor)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import math
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from predatory_logic_engine import PredatoryLogicEngine

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "omni_gobblin_fortress_v2_state.json"
EVENT_PATH = ROOT / "reports" / "omni_gobblin_fortress_v2_events.jsonl"

RAVE = "RAVE-USD"
MM_PRODUCTS = ["IOTX-USD", "BAL-USD", "BLUR-USD", "IRYS-USD"]
BTC = "BTC-USD"

# SNIPER PARAMS (Micro-Scalp optimized)
RSI_PERIOD = 4
OS_ENTRY = 30
OB_EXIT = 80
MAX_HOLD = 24

# GRINDER PARAMS
MIN_SPREAD = 0.85
MM_QUOTE = 50.0 # Aggressive for $1M goal

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

class OmniGobblinFortressV2:
    def __init__(self, starting_cash=288.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        
        self.sniper_pos = None
        self.mm_inventory = {p: None for p in MM_PRODUCTS}
        
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        
        self.history = {p: [] for p in [RAVE] + MM_PRODUCTS}
        self.last_candle_time = {p: 0 for p in [RAVE, BTC] + MM_PRODUCTS}
        
        # Predatory Engines per product
        self.predators = {p: PredatoryLogicEngine(p) for p in [RAVE] + MM_PRODUCTS}

    def get_fee_rate(self):
        # 2026 Advanced Tiers
        if self.total_volume >= 500000: return 0.0008 # 8 bps VIP
        elif self.total_volume >= 100000: return 0.0010 # 10 bps
        elif self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, tick_data, btc_tick):
        events = []
        fee_rate = self.get_fee_rate()
        
        # Update histories
        for pid, candles in tick_data.items():
            if pid in self.history:
                for c in candles:
                    self.history[pid].append(float(c["close"]))
                    if len(self.history[pid]) > 100: self.history[pid].pop(0)

        # 1. Exit Logic (Sniper)
        if self.sniper_pos:
            if RAVE in tick_data:
                for c in tick_data[RAVE]:
                    cl = float(c["close"]); h = float(c["high"])
                    self.sniper_pos["hold"] += 1
                    
                    # Update RSI
                    deltas = [self.history[RAVE][i] - self.history[RAVE][i-1] for i in range(len(self.history[RAVE])-4, len(self.history[RAVE]))] if len(self.history[RAVE]) >= 5 else []
                    g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                    rsi = 100 - 100/(1+g/lo) if lo > 0 else 50
                    
                    if rsi >= OB_EXIT or self.sniper_pos["hold"] >= MAX_HOLD:
                        exit_p = cl
                        units = self.sniper_pos["quote"] / self.sniper_pos["entry"]
                        total_returned = (units * exit_p) - (units * exit_p * fee_rate)
                        self.cash += total_returned
                        pnl = total_returned - (self.sniper_pos["quote"] + self.sniper_pos["buy_fee"])
                        self.realized_net += pnl; self.closes += 1
                        self.total_volume += self.sniper_pos["quote"] + (exit_p * units)
                        self.total_fees_paid += self.sniper_pos["buy_fee"] + (exit_p * units * fee_rate)
                        events.append({"ts_utc": utc_now_iso(), "action": "sniper_close", "net": round(pnl, 4)})
                        self.sniper_pos = None; break

        # 2. Exit Logic (Grinder MM)
        for pid in MM_PRODUCTS:
            if self.mm_inventory[pid]:
                try:
                    resp = client.best_bid_ask([pid])
                    book = resp["pricebooks"][0]
                    bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"])
                    
                    inv = self.mm_inventory[pid]
                    # Take Profit at Ask
                    if ask > inv["entry"] * 1.0045:
                        exit_p = ask; units = inv["quote"] / inv["entry"]
                        total_returned = (units * exit_p) - (units * exit_p * fee_rate)
                        self.cash += total_returned
                        pnl = total_returned - (inv["quote"] + inv["buy_fee"])
                        self.realized_net += pnl; self.closes += 1
                        self.total_volume += inv["quote"] + (exit_p * units)
                        self.total_fees_paid += inv["buy_fee"] + (exit_p * units * fee_rate)
                        events.append({"ts_utc": utc_now_iso(), "action": "grinder_close", "product": pid, "net": round(pnl, 4)})
                        self.mm_inventory[pid] = None
                    # Panic
                    elif bid < inv["entry"] * 0.98:
                        exit_p = bid; units = inv["quote"] / inv["entry"]
                        total_returned = (units * exit_p) - (units * exit_p * 0.0060)
                        self.cash += total_returned
                        pnl = total_returned - (inv["quote"] + inv["buy_fee"])
                        self.realized_net += pnl; self.closes += 1
                        self.total_volume += inv["quote"] + (exit_p * units)
                        self.total_fees_paid += inv["buy_fee"] + (exit_p * units * 0.0060)
                        events.append({"ts_utc": utc_now_iso(), "action": "grinder_panic", "product": pid, "net": round(pnl, 4)})
                        self.mm_inventory[pid] = None
                except: pass

        # 3. Entry Logic (Sniper - PRIORITY)
        if self.sniper_pos is None and self.cash >= 10.0:
            if len(self.history[RAVE]) >= 10:
                # RSI check
                deltas = [self.history[RAVE][i] - self.history[RAVE][i-1] for i in range(len(self.history[RAVE])-4, len(self.history[RAVE]))]
                g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                rsi_now = 100 - 100/(1+g/lo) if lo > 0 else 50
                
                if rsi_now <= OS_ENTRY:
                    # PREDATORY LOGIC EVAL
                    try:
                        resp = client.best_bid_ask([RAVE])
                        book = resp["pricebooks"][0]
                        bs = float(book["bids"][0]["size"]); ask_s = float(book["asks"][0]["size"])
                        price = float(book["bids"][0]["price"])
                        
                        score = self.predators[RAVE].evaluate_entry_quality(rsi_now, price, bs, ask_s, 71000) # cb_btc dummy
                        if score >= 80:
                            ep = price
                            # Magnetic Offset
                            is_mag, level = self.predators[RAVE].check_magnetic_proximity(ep)
                            if is_mag: ep = level + 0.0001
                            
                            tq = self.cash * 0.95
                            bf = tq * fee_rate
                            self.sniper_pos = {"pid": RAVE, "entry": ep, "quote": tq, "buy_fee": bf, "hold": 0, "target": ep * 1.25}
                            self.cash -= (tq + bf)
                            events.append({"ts_utc": utc_now_iso(), "action": "sniper_open", "score": score, "mag": is_mag})
                            return events
                    except: pass

        # 4. Entry Logic (Grinder MM)
        if self.cash >= MM_QUOTE + 5.0:
            for pid in MM_PRODUCTS:
                if self.mm_inventory[pid] is None and self.cash >= MM_QUOTE + 5.0:
                    try:
                        resp = client.best_bid_ask([pid])
                        book = resp["pricebooks"][0]
                        bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"])
                        spread = (ask - bid) / bid * 100
                        if spread >= MIN_SPREAD:
                            tq = MM_QUOTE
                            bf = tq * fee_rate
                            self.mm_inventory[pid] = {"entry": bid, "quote": tq, "buy_fee": bf}
                            self.cash -= (tq + bf)
                            events.append({"ts_utc": utc_now_iso(), "action": "grinder_open", "product": pid, "spread": round(spread, 2)})
                    except: pass
        
        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "vol": round(self.total_volume, 4), "fee_bps": round(self.get_fee_rate()*10000, 1),
            "sniper": "active" if self.sniper_pos else "flat",
            "grinders": len([p for p in self.mm_inventory if self.mm_inventory[p]])
        }

def save_state(path, engine, runner):
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = OmniGobblinFortressV2()
    print("🚀 OMNI-GOBBLIN FORTRESS V2: Starting VIP Blitz.")
    
    now = int(time.time()); start = now - 3600
    engine.last_candle_time[RAVE] = start; engine.last_candle_time[BTC] = start
    for p in MM_PRODUCTS: engine.last_candle_time[p] = start
    
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}
    while True:
        try:
            end = int(time.time())
            # Fetch updates
            tick_data = {}
            for pid in [RAVE] + MM_PRODUCTS:
                st = max(engine.last_candle_time[pid], end - 300 * 60)
                resp = client.market_candles(pid, start=st, end=end, granularity="FIVE_MINUTE")
                new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[pid]]
                if new_c:
                    tick_data[pid] = new_c
                    for c in new_c: engine.last_candle_time[pid] = max(engine.last_candle_time[pid], int(c["start"]))
                time.sleep(0.5)
            
            events = engine.process_tick(client, tick_data, [])
            for ev in events: append_jsonl(EVENT_PATH, ev)
            
            save_state(STATE_PATH, engine, runner)
            snap = engine.snapshot()
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} vol=${snap['vol']:.2f} fee={snap['fee_bps']}bps {snap['sniper']}/{snap['grinders']}gr", flush=True)
        except Exception as e: print(f"  EXC: {e}", flush=True)
        time.sleep(30)

if __name__ == "__main__": main()
