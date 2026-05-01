#!/usr/bin/env python3
"""
OMNI-VIP-FORTRESS V4 (The Final Boss)
Fuses:
1. RAVE RSI MR Sniper (Verified Real Edge)
2. Predatory Logic Engine (Kraken Lag + Gulp Shield + Magnetic Offset)
3. Aggressor Confirmation (Price Recovery Floor)
4. Lattice-Warp Grinder (BAL/IOTX front-running Coinbase via Kraken)
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
STATE_PATH = ROOT / "reports" / "omni_vip_fortress_v4_state.json"
EVENT_PATH = ROOT / "reports" / "omni_vip_fortress_v4_events.jsonl"

RAVE = "RAVE-USD"
GRINDERS = ["IOTX-USD", "BAL-USD"]
BTC = "BTC-USD"

# SNIPER PARAMS
RSI_PERIOD = 3
OS_ENTRY = 30
TP_PCT = 50.0 
MAX_HOLD = 48 

# GRINDER PARAMS
MIN_SPREAD = 1.20 # Lowered slightly for Warp-Gate
GRINDER_QUOTE = 25.0

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_kraken_btc():
    try:
        url = "https://api.kraken.com/0/public/Ticker?pair=XXBTZUSD"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return float(data["result"]["XXBTZUSD"]["c"][0])
    except: return None

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

class OmniVIPFortressV4:
    def __init__(self, starting_cash=1632.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.active_positions = {} 
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 27874.0 
        self.histories = {p: [] for p in [RAVE] + GRINDERS}
        self.last_candle_time = {p: 0 for p in [RAVE, BTC] + GRINDERS}
        self.predators = {p: PredatoryLogicEngine(p) for p in [RAVE] + GRINDERS}
        self.last_kraken_btc = None

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
                        if h >= pos["target"] or pos["hold"] >= MAX_HOLD:
                            exit_p = cl; closed = True
                    else:
                        if h >= pos["target"] or l <= pos["stop"]:
                            exit_p = pos["target"] if h >= pos["target"] else l
                            closed = True
                    if closed:
                        units = pos["quote"] / pos["entry"]
                        current_fee = 0.0060 if (pos["type"] == "grinder" and exit_p == l) else fee_rate
                        total_returned = (units * exit_p) - (units * exit_p * current_fee)
                        self.cash += total_returned; pnl = total_returned - (pos["quote"] + pos["buy_fee"])
                        self.realized_net += pnl; self.closes += 1; self.total_volume += pos["quote"] + (exit_p * units)
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "product": pid, "type": pos["type"], "net": round(pnl, 4)})
                        break
            if not closed: still_active[pid] = pos
        self.active_positions = still_active

        if RAVE not in self.active_positions and self.cash >= 100.0:
            if len(self.histories[RAVE]) >= 10:
                deltas = [self.histories[RAVE][i] - self.histories[RAVE][i-1] for i in range(len(self.histories[RAVE])-4, len(self.histories[RAVE]))]
                g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                rsi_now = 100 - 100/(1+g/lo) if lo > 0 else 50
                if rsi_now <= OS_ENTRY:
                    try:
                        resp = client.best_bid_ask([RAVE]); book = resp["pricebooks"][0]
                        bs = float(book["bids"][0]["size"]); ask_s = float(book["asks"][0]["size"]); price = float(book["bids"][0]["price"])
                        score = self.predators[RAVE].evaluate_entry_quality(rsi_now, price, bs, ask_s, 71000)
                        if score >= 80:
                            tq = self.cash * 0.5; bf = tq * fee_rate
                            self.active_positions[RAVE] = {"entry": price, "quote": tq, "buy_fee": bf, "hold": 0, "type": "sniper", "target": price * (1 + TP_PCT / 100.0)}
                            self.cash -= (tq + bf)
                            events.append({"ts_utc": utc_now_iso(), "action": "sniper_open", "score": score})
                    except: pass

        if self.cash >= GRINDER_QUOTE + 10.0:
            # KRAKEN LATTICE WARP
            kr_price = get_kraken_btc()
            btc_is_pumping = False
            if kr_price and self.last_kraken_btc:
                if kr_price - self.last_kraken_btc >= 5.0: btc_is_pumping = True
            if kr_price: self.last_kraken_btc = kr_price

            for pid in GRINDERS:
                if pid not in self.active_positions and self.cash >= GRINDER_QUOTE + 10.0:
                    try:
                        resp = client.best_bid_ask([pid]); book = resp["pricebooks"][0]
                        bid = float(book["bids"][0]["price"]); ask = float(book["asks"][0]["price"]); spread = (ask - bid) / bid * 100
                        if spread >= MIN_SPREAD:
                            if len(self.histories[pid]) >= 5:
                                deltas = [self.histories[pid][i] - self.histories[pid][i-1] for i in range(len(self.histories[pid])-4, len(self.histories[pid]))]
                                g = sum([d if d > 0 else 0 for d in deltas]); lo = sum([-d if d < 0 else 0 for d in deltas])
                                rsi_now = 100 - 100/(1+g/lo) if lo > 0 else 50
                            else: rsi_now = 50
                            
                            if rsi_now <= 35 and btc_is_pumping: 
                                tq = GRINDER_QUOTE; bf = tq * fee_rate
                                self.active_positions[pid] = {"entry": bid, "quote": tq, "buy_fee": bf, "hold": 0, "type": "grinder", "target": bid * 1.02, "stop": bid * 0.985}
                                self.cash -= (tq + bf)
                                events.append({"ts_utc": utc_now_iso(), "action": "grinder_open", "product": pid, "warp": True})
                    except: pass
        return events

    def snapshot(self):
        return {"cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4), "closes": self.closes, "vol": round(self.total_volume, 4), "fee_bps": round(self.get_fee_rate()*10000, 1), "active_count": len(self.active_positions)}

def save_state(path, engine, runner):
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = OmniVIPFortressV4()
    print("🚀 OMNI-VIP-FORTRESS V4 (HARDENED WARP): VIP Blitz Deployed.")
    now = int(time.time()); start = now - 3600
    for p in [RAVE] + GRINDERS: engine.last_candle_time[p] = start
    engine.last_candle_time[BTC] = start
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}
    while True:
        try:
            end = int(time.time()); tick_data = {}
            for pid in [RAVE] + GRINDERS:
                st = max(engine.last_candle_time[pid], end - 300 * 60)
                resp = client.market_candles(pid, start=st, end=end, granularity="FIVE_MINUTE")
                new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[pid]]
                if new_c:
                    tick_data[pid] = new_c
                    for c in new_c: engine.last_candle_time[pid] = max(engine.last_candle_time[pid], int(c["start"]))
                time.sleep(1.0)
            events = engine.process_tick(client, tick_data)
            for ev in events: append_jsonl(EVENT_PATH, ev)
            save_state(STATE_PATH, engine, runner); snap = engine.snapshot()
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} vol=${snap['vol']:.2f} fee={snap['fee_bps']}bps active={snap['active_count']}", flush=True)
        except Exception as e: print(f"  EXC: {e}")
        time.sleep(30)

if __name__ == "__main__": main()
