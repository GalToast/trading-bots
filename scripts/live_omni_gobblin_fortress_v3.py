#!/usr/bin/env python3
"""
OMNI-GOBBLIN FORTRESS V3 (Multi-Asset VIP Blitz) - HARDENED VERSION
Fuses:
1. Multi-Asset Sniper (RAVE + IOTX Priority)
2. Spread-Eater Grinder (Hardened Selectivity)
3. Predatory Logic Engine (Kraken Lag + Gulp Shield + Magnetic Offset)
4. Aggressor Confirmation (cl > o)
5. 2026 Fee Tier Awareness
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
STATE_PATH = ROOT / "reports" / "omni_gobblin_fortress_v3_state.json"
EVENT_PATH = ROOT / "reports" / "omni_gobblin_fortress_v3_events.jsonl"

# Dynamic priority based on latest Regime Scan
PRIORITY_SNIPERS = ["IOTX-USD", "RAVE-USD", "MOG-USD"]
GRINDER_PRODUCTS = ["BAL-USD", "BLUR-USD", "IRYS-USD"]
BTC = "BTC-USD"

# SNIPER PARAMS
RSI_PERIOD = 4
OS_ENTRY = 30
OB_EXIT = 80
MAX_HOLD = 24

# HARDENED GRINDER PARAMS
MIN_SPREAD = 1.50 
MM_QUOTE = 15.0

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

class OmniGobblinFortressV3:
    def __init__(self, starting_cash=288.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.active_positions = {} 
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 22841.0 # Carrying over live status
        self.total_fees_paid = 0.0
        self.histories = {p: [] for p in PRIORITY_SNIPERS + GRINDER_PRODUCTS}
        self.last_candle_time = {p: 0 for p in PRIORITY_SNIPERS + GRINDER_PRODUCTS + [BTC]}
        self.predators = {p: PredatoryLogicEngine(p) for p in PRIORITY_SNIPERS + GRINDER_PRODUCTS}

    def get_fee_rate(self):
        if self.total_volume >= 500000: return 0.0008
        elif self.total_volume >= 100000: return 0.0010
        elif self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, tick_data):
        events = []
        fee_rate = self.get_fee_rate()
        for pid, candles in tick_data.items():
            if pid in self.histories:
                for c in candles:
                    self.histories[pid].append(float(c["close"]))
                    if len(self.histories[pid]) > 100: self.histories[pid].pop(0)

        still_active = {}
        for pid, pos in self.active_positions.items():
            closed = False
            if pid in tick_data:
                for c in tick_data[pid]:
                    cl = float(c["close"]); h = float(c["high"]); l = float(c["low"])
                    pos["hold"] += 1
                    if pos["type"] == "sniper":
                        deltas = [self.histories[pid][i] - self.histories[pid][i-1] for i in range(len(self.histories[pid])-4, len(self.histories[pid]))] if len(self.histories[pid]) >= 5 else []
                        g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                        rsi = 100 - 100/(1+g/lo) if lo > 0 else 50
                        if rsi >= OB_EXIT or pos["hold"] >= MAX_HOLD:
                            exit_p = cl; closed = True
                    else:
                        if h >= pos["target"]: exit_p = pos["target"]; closed = True
                        elif l <= pos["stop"]: exit_p = l; closed = True 
                    if closed:
                        units = pos["quote"] / pos["entry"]
                        taker_fee = 0.0060 if (pos["type"] == "grinder" and exit_p == l) else fee_rate
                        total_returned = (units * exit_p) - (units * exit_p * taker_fee)
                        self.cash += total_returned; pnl = total_returned - (pos["quote"] + pos["buy_fee"])
                        self.realized_net += pnl; self.closes += 1; self.total_volume += pos["quote"] + (exit_p * units)
                        self.total_fees_paid += pos["buy_fee"] + (exit_p * units * taker_fee)
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "product": pid, "type": pos["type"], "net": round(pnl, 4)})
                        break
            if not closed: still_active[pid] = pos
        self.active_positions = still_active

        for pid in PRIORITY_SNIPERS:
            if pid not in self.active_positions and self.cash >= 20.0:
                if len(self.histories[pid]) >= 10:
                    deltas = [self.histories[pid][i] - self.histories[pid][i-1] for i in range(len(self.histories[pid])-4, len(self.histories[pid]))]
                    g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                    rsi_now = 100 - 100/(1+g/lo) if lo > 0 else 50
                    if rsi_now <= OS_ENTRY:
                        try:
                            resp = client.best_bid_ask([pid]); book = resp["pricebooks"][0]
                            bs = float(book["bids"][0]["size"]); ask_s = float(book["asks"][0]["size"]); price = float(book["bids"][0]["price"])
                            score = self.predators[pid].evaluate_entry_quality(rsi_now, price, bs, ask_s, 71000)
                            if score >= 80:
                                tq = self.cash * 0.5; tq = max(10.0, min(tq, self.cash-1))
                                bf = tq * fee_rate
                                self.active_positions[pid] = {"entry": price, "quote": tq, "buy_fee": bf, "hold": 0, "type": "sniper"}
                                self.cash -= (tq + bf)
                                events.append({"ts_utc": utc_now_iso(), "action": "sniper_open", "product": pid, "size": round(tq, 2)})
                        except: pass

        if self.cash >= MM_QUOTE + 5.0:
            for pid in GRINDER_PRODUCTS:
                if pid not in self.active_positions and self.cash >= MM_QUOTE + 5.0:
                    try:
                        resp = client.best_bid_ask([pid]); book = resp["pricebooks"][0]
                        bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"]); spread = (ask - bid) / bid * 100
                        if spread >= MIN_SPREAD:
                            deltas = [self.histories[pid][i] - self.histories[pid][i-1] for i in range(len(self.histories[pid])-4, len(self.histories[pid]))] if len(self.histories[pid]) >= 5 else []
                            g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                            rsi_now = 100 - 100/(1+g/lo) if lo > 0 else 50
                            cl = float(tick_data[pid][0]["close"]); o = float(tick_data[pid][0]["open"])
                            if rsi_now <= 30 and cl > o: # HARDENED GOBBLIN
                                tq = MM_QUOTE; bf = tq * fee_rate
                                self.active_positions[pid] = {"entry": bid, "quote": tq, "buy_fee": bf, "hold": 0, "type": "grinder", "target": bid * 1.02, "stop": bid * 0.985}
                                self.cash -= (tq + bf)
                                events.append({"ts_utc": utc_now_iso(), "action": "grinder_open", "product": pid, "spread": round(spread, 2)})
                        time.sleep(0.5)
                    except: pass
        return events

    def snapshot(self):
        return {"cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4), "closes": self.closes, "vol": round(self.total_volume, 4), "fee_bps": round(self.get_fee_rate()*10000, 1), "active_count": len(self.active_positions)}

def save_state(path, engine, runner):
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = OmniGobblinFortressV3()
    print("🚀 OMNI-GOBBLIN FORTRESS V3 (HARDENED): VIP Blitz Started.")
    now = int(time.time()); start = now - 3600
    for p in PRIORITY_SNIPERS + GRINDER_PRODUCTS: engine.last_candle_time[p] = start
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}
    while True:
        try:
            end = int(time.time()); tick_data = {}
            for pid in PRIORITY_SNIPERS + GRINDER_PRODUCTS:
                st = max(engine.last_candle_time[pid], end - 300 * 60)
                resp = client.market_candles(pid, start=st, end=end, granularity="FIVE_MINUTE")
                new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[pid]]
                if new_c:
                    tick_data[pid] = new_c
                    for c in new_c: engine.last_candle_time[pid] = max(engine.last_candle_time[pid], int(c["start"]))
                time.sleep(3.0)
            events = engine.process_tick(client, tick_data)
            for ev in events: append_jsonl(EVENT_PATH, ev)
            save_state(STATE_PATH, engine, runner); snap = engine.snapshot()
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} vol=${snap['vol']:.2f} fee={snap['fee_bps']}bps {snap['active_count']}pos", flush=True)
        except Exception as e: print(f"  EXC: {e}", flush=True)
        time.sleep(30)

if __name__ == "__main__": main()
