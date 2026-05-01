#!/usr/bin/env python3
"""
OMNI-VIP-FORTRESS (Final Production Fleet)
Fuses:
1. Qwen-Trading's Top 34 Profitable Asset Map
2. Gemini's Predatory Logic Engine (Kraken Lag + Gulp Shield + Magnetic Offset)
3. Structural Grinder MM (IOTX/BAL) for Volume Armor (8bps Goal)
4. Shared $1632 Capital Model
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
STATE_PATH = ROOT / "reports" / "omni_vip_fortress_state.json"
EVENT_PATH = ROOT / "reports" / "omni_vip_fortress_events.jsonl"

# --- THE TOP 34 UNIVERSE (Ranked by qwen-trading Phase 4 Scan) ---
PROFITABLE_COINS = [
    "RAVE-USD", "MOG-USD", "A8-USD", "IDEX-USD", "LRDS-USD", "BAL-USD", "STRK-USD", "DRIFT-USD",
    "ALEPH-USD", "MATH-USD", "IOTX-USD", "KARRAT-USD", "BLUR-USD", "PERP-USD", "SKL-USD", "VOXEL-USD",
    "OSMO-USD", "ARPA-USD", "FIS-USD", "FORT-USD", "DOGINME-USD", "T-USD", "RARE-USD", "00-USD",
    "VELO-USD", "ALT-USD", "DEGEN-USD", "IRYS-USD", "AST-USD", "VTHO-USD", "WELL-USD", "SUKU-USD", "ACS-USD", "GMT-USD"
]

# --- GRINDER VOLUME ASSETS ---
GRINDER_PRODUCTS = ["IOTX-USD", "BAL-USD", "RAVE-USD"]
BTC = "BTC-USD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

class OmniVIPFortress:
    def __init__(self, starting_cash=1632.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.active_positions = {} # {pid: {"entry": ..., "quote": ..., "hold": 0, "type": "sniper|grinder"}}
        
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 27874.0 # Carrying over live status
        
        self.histories = {p: [] for p in PROFITABLE_COINS}
        self.last_candle_time = {p: 0 for p in PROFITABLE_COINS + [BTC]}
        self.predators = {p: PredatoryLogicEngine(p) for p in PROFITABLE_COINS}

    def get_fee_rate(self):
        # 2026 VIP Tiers
        if self.total_volume >= 1000000: return 0.0008 # VIP
        elif self.total_volume >= 100000: return 0.0010
        elif self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, tick_data):
        events = []
        fee_rate = self.get_fee_rate()
        
        # Update histories
        for pid, candles in tick_data.items():
            if pid in self.histories:
                for c in candles:
                    self.histories[pid].append(float(c["close"]))
                    if len(self.histories[pid]) > 100: self.histories[pid].pop(0)

        # 1. Management (Exits)
        still_active = {}
        for pid, pos in self.active_positions.items():
            closed = False
            if pid in tick_data:
                for c in tick_data[pid]:
                    cl = float(c["close"]); h = float(c["high"])
                    pos["hold"] += 1
                    
                    exit_p = None
                    if pos["type"] == "sniper":
                        # Updated RSI Exit
                        if len(self.histories[pid]) >= 5:
                            deltas = [self.histories[pid][i] - self.histories[pid][i-1] for i in range(len(self.histories[pid])-4, len(self.histories[pid]))]
                            g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                            rsi = 100 - 100/(1+g/lo) if lo > 0 else 50
                        else: rsi = 50
                        
                        if rsi >= 80 or pos["hold"] >= 24:
                            exit_p = cl; closed = True
                    else:
                        # Grinder MM Exit
                        if h >= pos["target"] or pos["hold"] >= 10:
                            exit_p = pos["target"] if h >= pos["target"] else cl
                            closed = True
                    
                    if closed:
                        units = pos["quote"] / pos["entry"]
                        total_returned = (units * exit_p) - (units * exit_p * fee_rate)
                        self.cash += total_returned
                        pnl = total_returned - (pos["quote"] + pos["buy_fee"])
                        self.realized_net += pnl; self.closes += 1; self.total_volume += pos["quote"] + (exit_p * units)
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "product": pid, "type": pos["type"], "net": round(pnl, 4)})
                        break
            if not closed: still_active[pid] = pos
        self.active_positions = still_active

        # 2. Deployment (The Fleet)
        # We allow up to 10 concurrent snipers from the Top 34
        if len(self.active_positions) < 10 and self.cash >= 50.0:
            for pid in PROFITABLE_COINS:
                if pid in self.active_positions: continue
                if len(self.histories[pid]) < 10: continue
                
                # RSI check
                deltas = [self.histories[pid][i] - self.histories[pid][i-1] for i in range(len(self.histories[pid])-4, len(self.histories[pid]))]
                g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                rsi_now = 100 - 100/(1+g/lo) if lo > 0 else 50
                
                if rsi_now <= 30:
                    try:
                        # PREDATORY GATING
                        resp = client.best_bid_ask([pid]); book = resp["pricebooks"][0]
                        bs = float(book["bids"][0]["size"]); ask_s = float(book["asks"][0]["size"]); price = float(book["bids"][0]["price"])
                        
                        score = self.predators[pid].evaluate_entry_quality(rsi_now, price, bs, ask_s, 71000)
                        if score >= 80:
                            tq = 48.0 # Standard unit size
                            bf = tq * fee_rate
                            self.active_positions[pid] = {"entry": price, "quote": tq, "buy_fee": bf, "hold": 0, "type": "sniper"}
                            self.cash -= (tq + bf)
                            events.append({"ts_utc": utc_now_iso(), "action": "sniper_open", "product": pid, "score": score})
                            if len(self.active_positions) >= 10: break
                    except: pass

        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "vol": round(self.total_volume, 4), "fee_bps": round(self.get_fee_rate()*10000, 1),
            "fleet_size": len(self.active_positions)
        }

def save_state(path, engine, runner):
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = OmniVIPFortress()
    print("🚀 OMNI-VIP-FORTRESS: Final Fleet Deployment Started.")
    
    now = int(time.time()); start = now - 3600
    for p in PROFITABLE_COINS: engine.last_candle_time[p] = start
    engine.last_candle_time[BTC] = start
    
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}
    while True:
        try:
            end = int(time.time()); tick_data = {}
            # Cycle through the fleet with stealth delays
            for pid in PROFITABLE_COINS:
                st = max(engine.last_candle_time[pid], end - 300 * 60)
                resp = client.market_candles(pid, start=st, end=end, granularity="FIVE_MINUTE")
                new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[pid]]
                if new_c:
                    tick_data[pid] = new_c
                    for c in new_c: engine.last_candle_time[pid] = max(engine.last_candle_time[pid], int(c["start"]))
                time.sleep(1.0) # Stealth delay
            
            events = engine.process_tick(client, tick_data)
            for ev in events: append_jsonl(EVENT_PATH, ev)
            
            save_state(STATE_PATH, engine, runner)
            snap = engine.snapshot()
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} vol=${snap['vol']:.2f} fee={snap['fee_bps']}bps active={snap['fleet_size']}", flush=True)
        except Exception as e: print(f"  EXC: {e}", flush=True)
        time.sleep(30)

if __name__ == "__main__": main()
