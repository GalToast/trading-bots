#!/usr/bin/env python3
"""
OMNI-GOBBLIN FORTRESS (Shared Bankroll Unified System)
Combines:
1. RSI Mean Reversion (RAVE-USD, The Sniper) - Priority Alpha
2. Spread-Eater Gobblin MM (IOTX, BAL, BLUR, The Grinder) - Volume & Fee-Hacking
3. Real-Time Volume Tracking & Fee-Tier Switching
4. Shared Bankroll Logic
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
STATE_PATH = ROOT / "reports" / "omni_gobblin_fortress_state.json"
EVENT_PATH = ROOT / "reports" / "omni_gobblin_fortress_events.jsonl"

RAVE = "RAVE-USD"
MM_PRODUCTS = ["IOTX-USD", "BAL-USD", "BLUR-USD"]
BTC = "BTC-USD"

# SNIPER PARAMS
RSI_PERIOD = 3
OS_ENTRY = 30
TP_PCT = 25.0

# GRINDER PARAMS
MIN_SPREAD = 0.85
MM_QUOTE = 10.0

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

def compute_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period; avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

class OmniGobblinFortress:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        
        self.sniper_pos = None # {"entry": ..., "target": ..., "quote": ..., "hold": 0}
        self.mm_inventory = {p: None for p in MM_PRODUCTS} # {p: {"entry": ..., "quote": ...}}
        
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        
        self.rave_history = []
        self.btc_history = []
        self.last_candle_time = {RAVE: 0, BTC: 0}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, rave_m5, btc_m1):
        events = []
        fee_rate = self.get_fee_rate()
        
        if rave_m5:
            for c in rave_m5:
                self.rave_history.append(float(c["close"]))
                if len(self.rave_history) > 50: self.rave_history.pop(0)
        
        if btc_m1:
            for c in btc_m1:
                if c:
                    self.btc_history.append(float(c["close"]))
                    if len(self.btc_history) > 100: self.btc_history.pop(0)

        # 1. Exit Logic (Sniper)
        if self.sniper_pos:
            if rave_m5:
                for c in rave_m5:
                    cl = float(c["close"]); h = float(c["high"])
                    self.sniper_pos["hold"] += 1
                    if h >= self.sniper_pos["target"] or self.sniper_pos["hold"] >= 288: # 24h timeout
                        exit_p = self.sniper_pos["target"] if h >= self.sniper_pos["target"] else cl
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
                        exit_p = ask
                        units = inv["quote"] / inv["entry"]
                        total_returned = (units * exit_p) - (units * exit_p * fee_rate)
                        self.cash += total_returned
                        pnl = total_returned - (inv["quote"] + inv["buy_fee"])
                        
                        self.realized_net += pnl; self.closes += 1
                        self.total_volume += inv["quote"] + (exit_p * units)
                        self.total_fees_paid += inv["buy_fee"] + (exit_p * units * fee_rate)
                        events.append({"ts_utc": utc_now_iso(), "action": "grinder_close", "product": pid, "net": round(pnl, 4)})
                        self.mm_inventory[pid] = None
                    # Panic Exit at 2%
                    elif bid < inv["entry"] * 0.98:
                        exit_p = bid
                        units = inv["quote"] / inv["entry"]
                        # Taker fee 60bps
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
        btc_gate = True
        if len(self.btc_history) >= 3:
            mom = (self.btc_history[-1] - self.btc_history[-3]) / self.btc_history[-3]
            if mom < -0.001: btc_gate = False

        if self.sniper_pos is None and self.cash >= 10.0 and btc_gate:
            if len(self.rave_history) >= 10:
                rsi_now = compute_rsi(self.rave_history)
                if rsi_now <= OS_ENTRY:
                    ep = float(rave_m5[0]["open"]) if rave_m5 else 0.0
                    if ep > 0:
                        tq = self.cash * 0.95
                        bf = tq * fee_rate
                        self.sniper_pos = {"pid": RAVE, "entry": ep, "quote": tq, "buy_fee": bf, "hold": 0, "target": ep * (1 + TP_PCT / 100.0)}
                        self.cash -= (tq + bf)
                        events.append({"ts_utc": utc_now_iso(), "action": "sniper_open", "size": round(tq, 2)})
                        return events

        # 4. Entry Logic (Grinder MM)
        if self.cash >= MM_QUOTE + 1.0:
            for pid in MM_PRODUCTS:
                if self.mm_inventory[pid] is None and self.cash >= MM_QUOTE + 1.0:
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
                            events.append({"ts_utc": utc_now_iso(), "action": "grinder_open", "product": pid, "size": tq})
                    except: pass
        
        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "vol": round(self.total_volume, 4), "fee_tier": round(self.get_fee_rate()*10000, 1),
            "sniper": "active" if self.sniper_pos else "flat",
            "grinder_count": len([p for p in self.mm_inventory if self.mm_inventory[p]])
        }

def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = OmniGobblinFortress()
    print("🚀 OMNI-GOBBLIN FORTRESS: Shared Bankroll Unified System Started.")
    
    # Backfill Logic (simplified)
    now = int(time.time()); start = now - 3600
    engine.last_candle_time[RAVE] = start; engine.last_candle_time[BTC] = start
    
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}
    while True:
        try:
            end = int(time.time())
            # Fetch updates
            st_btc = max(engine.last_candle_time[BTC], end - 300 * 60)
            btc_tick = [c for c in client.market_candles(BTC, start=st_btc, end=end, granularity="ONE_MINUTE").get("candles", []) if int(c["start"]) > engine.last_candle_time[BTC]]
            for c in btc_tick: engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], int(c["start"]))
            
            st_rave = max(engine.last_candle_time[RAVE], end - 300 * 300)
            rave_tick = [c for c in client.market_candles(RAVE, start=st_rave, end=end, granularity="FIVE_MINUTE").get("candles", []) if int(c["start"]) > engine.last_candle_time[RAVE]]
            for c in rave_tick: engine.last_candle_time[RAVE] = max(engine.last_candle_time[RAVE], int(c["start"]))
            
            events = engine.process_tick(client, rave_tick, btc_tick)
            for ev in events: append_jsonl(EVENT_PATH, ev)
            
            save_state(STATE_PATH, engine, runner)
            snap = engine.snapshot()
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} vol=${snap['vol']:.2f} {snap['sniper']}/{snap['grinder_count']}gr", flush=True)
        except Exception as e: print(f"  EXC: {e}", flush=True)
        time.sleep(30)

if __name__ == "__main__": main()
