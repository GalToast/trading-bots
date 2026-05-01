#!/usr/bin/env python3
"""
OMNI-VIP-FORTRESS V5 (The Final Boss)
Asset: RAVE-USD (The only verified survivor)
Logic: 
1. RSI(3) < 30 (Micro-momentum crater)
2. 25% Take Profit (Alpha Space)
3. No Stop Loss (Structural mean reversion)
4. Session Gate (Avoid high-drawdown UTC hours)
5. Aggressor Confirm (cl > o - Buyer recovery floor)
6. Magnetic Offset (1-tick above $0.05 walls)
Sizing: 95% Geometric Compounding from Shared Pool.
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
STATE_PATH = ROOT / "reports" / "omni_vip_fortress_v5_state.json"
EVENT_PATH = ROOT / "reports" / "omni_vip_fortress_v5_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

# FINAL BOSS PARAMS
RSI_PERIOD = 3
OS_ENTRY = 30
TP_PCT = 25.0
MAX_HOLD = 48 # 4 hours

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

class OmniVIPFortressV5:
    def __init__(self, starting_cash=1632.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None # {"ep": ..., "tp": ..., "quote": ..., "hold": 0}
        
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 27874.0 # Live carrying volume
        self.total_fees = 0.0
        
        self.history = []
        self.last_candle_time = {PRODUCT: 0, BTC: 0}
        self.predator = PredatoryLogicEngine(PRODUCT)

    def get_fee_rate(self):
        if self.total_volume >= 500000: return 0.0008
        elif self.total_volume >= 100000: return 0.0010
        elif self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, tick_candles, btc_tick):
        events = []
        fee_rate = self.get_fee_rate()
        
        if tick_candles:
            for c in tick_candles:
                self.history.append(float(c["close"]))
                if len(self.history) > 100: self.history.pop(0)

        # 1. Exit Logic
        if self.position:
            if tick_candles:
                for c in tick_candles:
                    cl = float(c["close"]); h = float(c["high"])
                    self.position["hold"] += 1
                    
                    if h >= self.position["tp"] or self.position["hold"] >= MAX_HOLD:
                        exit_p = self.position["tp"] if h >= self.position["tp"] else cl
                        units = self.position["quote"] / self.position["ep"]
                        
                        total_returned = (units * exit_p) - (units * exit_p * fee_rate)
                        self.cash += total_returned
                        pnl = total_returned - (self.position["quote"] + self.position["buy_fee"])
                        
                        self.realized_net += pnl; self.closes += 1
                        self.total_volume += self.position["quote"] + (exit_p * units)
                        self.total_fees += self.position["buy_fee"] + (exit_p * units * fee_rate)
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "net": round(pnl, 4)})
                        self.position = None
                        break

        # 2. Entry Logic
        if self.position is None and self.cash >= 10.0:
            # SESSION GATE
            dt = datetime.now(timezone.utc)
            if dt.hour in [12, 19, 6, 0]: return events
            
            if len(self.history) >= 10:
                # RSI check
                deltas = [self.history[i] - self.history[i-1] for i in range(len(self.history)-4, len(self.history))]
                g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                rsi_now = 100 - 100/(1+g/lo) if lo > 0 else 50
                
                if rsi_now <= OS_ENTRY:
                    # HYBRID CONFIRM LOGIC
                    # If Extreme RSI (< 25), enter immediately
                    # If Standard RSI (25-30), require Aggressor Confirm (cl > o)
                    is_extreme = (rsi_now < 25.0)
                    is_recovering = tick_candles and (float(tick_candles[0]["close"]) > float(tick_candles[0]["open"]))
                    
                    if is_extreme or is_recovering:
                        try:
                            resp = client.best_bid_ask([PRODUCT]); book = resp["pricebooks"][0]
                            bs = float(book["bids"][0]["size"]); ask_s = float(book["asks"][0]["size"]); price = float(book["bids"][0]["price"])
                            
                            # PREDATORY CHECK
                            score = self.predator.evaluate_entry_quality(rsi_now, price, bs, ask_s, 71000)
                            if score >= 80:
                                ep = price
                                # MAGNETIC OFFSET
                                is_mag, level = self.predator.check_magnetic_proximity(ep)
                                if is_mag: ep = level + 0.0001
                                
                                tq = self.cash * 0.95
                                bf = tq * fee_rate
                                self.position = {"ep": ep, "quote": tq, "buy_fee": bf, "hold": 0, "target": ep * (1 + TP_PCT / 100.0)}
                                self.cash -= (tq + bf)
                                events.append({"ts_utc": utc_now_iso(), "action": "open", "score": score, "extreme": is_extreme})
                        except: pass
        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "vol": round(self.total_volume, 4), "fee_bps": round(self.get_fee_rate()*10000, 1),
            "pos": "active" if self.position else "flat"
        }

def save_state(path, engine, runner):
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = OmniVIPFortressV5()
    print("🚀 OMNI-VIP-FORTRESS V5 (THE FINAL BOSS): Live Deployment.")
    
    now = int(time.time()); start = now - 3600
    engine.last_candle_time[PRODUCT] = start; engine.last_candle_time[BTC] = start
    
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}
    while True:
        try:
            end = int(time.time())
            # Fetch updates
            resp = client.market_candles(PRODUCT, start=max(engine.last_candle_time[PRODUCT], end-300*60), end=end, granularity="FIVE_MINUTE")
            new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[PRODUCT]]
            for c in new_c: engine.last_candle_time[PRODUCT] = max(engine.last_candle_time[PRODUCT], int(c["start"]))
            
            events = engine.process_tick(client, new_c, [])
            for ev in events: append_jsonl(EVENT_PATH, ev)
            
            save_state(STATE_PATH, engine, runner)
            snap = engine.snapshot()
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} vol=${snap['vol']:.2f} {snap['pos']}", flush=True)
        except Exception as e: print(f"  EXC: {e}")
        time.sleep(30)

if __name__ == "__main__": main()
