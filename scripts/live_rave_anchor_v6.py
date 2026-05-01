#!/usr/bin/env python3
"""
OMNI-VIP-FORTRESS V6 (The RAVE Anchor)
Logic: 
1. RAVE Only (The structural survivor)
2. RSI(3) < 30 (Verified crater entry)
3. 25% Take Profit (Optimal alpha space)
4. No Stop Loss (Mean reversion physics)
5. Session Gate (12, 19, 6, 0 UTC Death Zones blocked)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "omni_vip_fortress_v6_state.json"
EVENT_PATH = ROOT / "reports" / "omni_vip_fortress_v6_events.jsonl"

PRODUCT = "RAVE-USD"

# VERIFIED PARAMS
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

class RaveAnchorV6:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None
        self.realized_net = 0.0
        self.closes = 0
        self.total_volume = 0.0
        self.history = []
        self.last_candle_time = 0

    def get_fee_rate(self):
        return 0.0025

    def process_tick(self, candles):
        events = []
        fee_rate = self.get_fee_rate()
        
        if candles:
            for c in candles:
                self.history.append(float(c["close"]))
                if len(self.history) > 100: self.history.pop(0)

        # 1. Exit Logic
        if self.position:
            if candles:
                for c in candles:
                    cl = float(c["close"]); h = float(c["high"])
                    self.position["hold"] += 1
                    if h >= self.position["target"] or self.position["hold"] >= MAX_HOLD:
                        exit_p = self.position["target"] if h >= self.position["target"] else cl
                        units = self.position["quote"] / self.position["entry"]
                        total_returned = (units * exit_p) * (1 - fee_rate)
                        self.cash += total_returned
                        pnl = total_returned - (self.position["quote"] * (1 + fee_rate))
                        self.realized_net += pnl; self.closes += 1
                        self.total_volume += self.position["quote"] + (units * exit_p)
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "net": round(pnl, 4)})
                        self.position = None
                        break

        # 2. Entry Logic
        if self.position is None and self.cash >= 10.0:
            dt = datetime.now(timezone.utc)
            if dt.hour in [12, 19, 6, 0]: return events
            
            if len(self.history) >= 10:
                rsi_now = compute_rsi(self.history)
                if rsi_now <= OS_ENTRY:
                    if candles:
                        ep = float(candles[0]["open"])
                        tq = self.cash * 0.95
                        tp_price = ep * (1 + TP_PCT / 100.0)
                        self.position = {"entry": ep, "quote": tq, "hold": 0, "target": tp_price}
                        self.cash -= (tq * (1 + fee_rate))
                        events.append({"ts_utc": utc_now_iso(), "action": "open", "size": round(tq, 2), "price": ep, "tp": tp_price})
        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "vol": round(self.total_volume, 4),
            "pos": "active" if self.position else "flat",
            "entry": self.position["entry"] if self.position else None,
            "tp": self.position["target"] if self.position else None
        }

def save_state(path, engine, runner):
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = RaveAnchorV6()
    print("🚀 RAVE ANCHOR V6: Live Portfolio Anchor Deployed.")
    
    engine.last_candle_time = int(time.time()) - 3600
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}
    while True:
        try:
            end = int(time.time())
            resp = client.market_candles(PRODUCT, start=max(engine.last_candle_time, end-300*60), end=end, granularity="FIVE_MINUTE")
            new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time]
            for c in new_c: engine.last_candle_time = max(engine.last_candle_time, int(c["start"]))
            
            events = engine.process_tick(new_c)
            for ev in events: append_jsonl(EVENT_PATH, ev)
            
            save_state(STATE_PATH, engine, runner)
            snap = engine.snapshot()
            pos_str = f"active (entry={snap['entry']:.4f} tp={snap['tp']:.4f})" if snap['pos'] == "active" else "flat"
            print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} {pos_str}", flush=True)
        except Exception as e: print(f"  EXC: {e}")
        time.sleep(30)

if __name__ == "__main__": main()
