#!/usr/bin/env python3
"""
APEX SWARM GOBBLIN SHADOW (M1 Volume Engine) - STEALTH VERSION
Logic: High-frequency volume generation to shatter fee tiers.
Strategy: RSI(4) < 30 on M1, 25% TP, RSI(4) > 80 exit, 24-bar hold.
Confluence: BTC Momentum Gate + Regime Awareness.
Goal: Generate $50,000 volume to unlock 15bps fee tier.
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
STATE_PATH = ROOT / "reports" / "apex_swarm_gobblin_state.json"
EVENT_PATH = ROOT / "reports" / "apex_swarm_gobblin_events.jsonl"
REGIME_PATH = ROOT / "reports" / "live_regime_monitor.json"

PRODUCTS = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD", 
            "IRYS-USD", "CFG-USD", "DASH-USD", "COMP-USD", "MON-USD"]
BTC = "BTC-USD"

# GOBBLIN PARAMETERS (Crown Jewel Micro-Scalp optimized)
RSI_PERIOD = 4
OS_ENTRY = 30
OB_EXIT = 80
TP_PCT = 25.0
MAX_HOLD = 24 

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

def fetch_candles_chunked(client, pid, start, end, granularity="ONE_MINUTE"):
    chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.5) 
        except:
            time.sleep(2.0)
            continue
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

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

class ApexSwarmGobblin:
    def __init__(self, starting_cash=48.0, max_concurrent=5):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions = []
        self.max_concurrent = max_concurrent
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        self.histories = {p: [] for p in PRODUCTS}
        self.btc_history = []
        self.last_candle_time = {p: 0 for p in PRODUCTS + [BTC]}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, tick_data, btc_tick, regime_info, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        for pid, candles in tick_data.items():
            if pid in self.histories:
                for c in candles:
                    self.histories[pid].append(float(c["close"]))
                    if len(self.histories[pid]) > 100: self.histories[pid].pop(0)
        if btc_tick:
            for c in btc_tick:
                if c:
                    self.btc_history.append(float(c["close"]))
                    if len(self.btc_history) > 100: self.btc_history.pop(0)

        # 1. Process Exits
        still_open = []
        for pos in self.positions:
            pid = pos["pid"]; closed = False
            if pid in tick_data:
                for c in tick_data[pid]:
                    cl = float(c["close"]); h = float(c["high"]); l = float(c["low"])
                    pos["hold"] += 1
                    
                    rsi = compute_rsi(self.histories[pid])
                    is_rave = (pid == "RAVE-USD")
                    
                    should_exit = False
                    if h >= pos["tp"]:
                        exit_p = pos["tp"]; self.wins += 1; should_exit = True
                    elif not is_rave and l <= pos["sl"]:
                        exit_p = pos["sl"]; should_exit = True
                    elif rsi >= OB_EXIT:
                        exit_p = cl; should_exit = True
                        if cl > pos["entry"]: self.wins += 1
                    elif pos["hold"] >= MAX_HOLD:
                        exit_p = cl; should_exit = True
                        if cl > pos["entry"]: self.wins += 1
                    
                    if should_exit:
                        units = pos["quote"] / pos["entry"]
                        pnl = (exit_p - pos["entry"]) * units - (pos["quote"] * fee_rate) - (exit_p * units * fee_rate)
                        self.cash += pos["quote"] + pnl; self.realized_net += pnl; self.closes += 1
                        self.total_volume += pos["quote"] + (exit_p * units); self.total_fees_paid += (pos["quote"] + exit_p * units) * fee_rate
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "product": pid, "net": round(pnl, 4)})
                        closed = True; break
            if not closed: still_open.append(pos)
        self.positions = still_open

        # 2. Entries
        btc_gate = True
        if len(self.btc_history) >= 3:
            mom = (self.btc_history[-1] - self.btc_history[-3]) / self.btc_history[-3]
            if mom < -0.001: btc_gate = False
        regime_gate = (regime_info.get("primary_regime") != "DEAD") if regime_info else True

        free_slots = self.max_concurrent - len(self.positions)
        if free_slots > 0 and self.cash >= 10.0 and btc_gate and regime_gate:
            candidates = []
            for pid in PRODUCTS:
                if any(p["pid"] == pid for p in self.positions): continue
                if len(self.histories[pid]) < 10: continue
                rsi_prev = compute_rsi(self.histories[pid][:-1])
                if rsi_prev <= OS_ENTRY:
                    candidates.append({"pid": pid, "rsi": rsi_prev})
            candidates.sort(key=lambda x: x["rsi"])
            for cand in candidates[:free_slots]:
                if self.cash < 10.0: break
                pid = cand["pid"]
                if pid in tick_data:
                    ep = float(tick_data[pid][0]["open"])
                    tq = self.cash / free_slots * 0.95
                    if tq < 10.0: tq = 10.0
                    if tq > self.cash: break
                    tp = ep * (1 + TP_PCT / 100.0); sl = ep * 0.97
                    self.positions.append({"pid": pid, "entry": ep, "quote": tq, "hold": 0, "tp": tp, "sl": sl})
                    self.cash -= tq
                    events.append({"ts_utc": utc_now_iso(), "action": "open", "product": pid, "size": round(tq, 2)})
                    free_slots -= 1
        return events

    def snapshot(self):
        return {"cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4), "closes": self.closes, "vol": round(self.total_volume, 4), "pos": len(self.positions)}

def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = ApexSwarmGobblin()
    print("APEX SWARM GOBBLIN: High-Frequency Volume Engine (Stealth Mode).")
    now = int(time.time()); start = now - 72 * 3600
    print("Backfilling 72h...")
    try:
        btc_m1 = fetch_candles_chunked(client, BTC, start, now, "ONE_MINUTE")
        btc_lookup = {int(c["start"]): c for c in btc_m1}
        for pid in PRODUCTS:
            cands = fetch_candles_chunked(client, pid, start, now, "ONE_MINUTE")
            for c in cands:
                t = int(c["start"])
                engine.process_tick({pid: [c]}, [btc_lookup.get(t)], None, EVENT_PATH)
                engine.last_candle_time[pid] = max(engine.last_candle_time[pid], t)
                engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], t)
            time.sleep(1.0)
    except: pass

    print(f"Live ready. Vol=${engine.total_volume:.2f}")
    runner = {"pid": os.getpid(), "started_at": utc_now_iso()}
    while True:
        try:
            end = int(time.time()); regime_info = {}
            if REGIME_PATH.exists():
                try: regime_info = json.loads(REGIME_PATH.read_text())
                except: pass
            st_btc = engine.last_candle_time.get(BTC, end - 3600)
            if st_btc == 0: st_btc = end - 3600
            btc_tick = [c for c in client.market_candles(BTC, start=st_btc, end=end, granularity="ONE_MINUTE").get("candles", []) if int(c["start"]) > engine.last_candle_time[BTC]]
            for c in btc_tick: engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], int(c["start"]))
            tick_data = {}
            for pid in PRODUCTS:
                st_pid = engine.last_candle_time.get(pid, end - 3600)
                if st_pid == 0: st_pid = end - 3600
                resp = client.market_candles(pid, start=st_pid, end=end, granularity="ONE_MINUTE")
                new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[pid]]
                if new_c:
                    tick_data[pid] = new_c
                    for c in new_c: engine.last_candle_time[pid] = max(engine.last_candle_time[pid], int(c["start"]))
                time.sleep(1.0) 
            if tick_data or btc_tick:
                events = engine.process_tick(tick_data, btc_tick, regime_info, EVENT_PATH)
                for ev in events: append_jsonl(EVENT_PATH, ev)
            save_state(STATE_PATH, engine, runner)
            snap = engine.snapshot(); print(f"  HB cash=${snap['cash']:.2f} vol=${snap['vol']:.2f} {snap['pos']}pos", flush=True)
        except Exception as e: print(f"  EXC: {e}", flush=True)
        time.sleep(60)

if __name__ == "__main__": main()
