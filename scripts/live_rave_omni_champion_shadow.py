#!/usr/bin/env python3
"""
RAVE Omni-Champion Live Shadow
Logic: @qwen-main's Ultimate parameters + Gemini Geometric Compounding.
Config: RSI(4) < 45, 20% TP, 2.75% SL, RSI > 95 Exit, 4-bar timeout.
Includes: BTC M1 Gate + Session Gate.
Sizing: 95% Compound.
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
from coinbase_rate_limit import fetch_candles_chunked, fetch_live_candles

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "rave_omni_champion_state.json"
EVENT_PATH = ROOT / "reports" / "rave_omni_champion_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

# ULTIMATE PARAMETERS
RSI_PERIOD = 4
OS_ENTRY = 45
OB_EXIT = 95
TP_PCT = 20.0
SL_PCT = 2.75
MAX_HOLD = 4

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
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

class RaveOmniChampionShadow:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        self.history = []
        self.btc_history = []
        self.last_candle_time = {PRODUCT: 0, BTC: 0}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, m5_candles, btc_candles, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        
        if m5_candles:
            for c in m5_candles:
                self.history.append(float(c["close"]))
                if len(self.history) > 100: self.history.pop(0)
        
        if btc_candles:
            for c in btc_candles:
                if c:
                    self.btc_history.append(float(c["close"]))
                    if len(self.btc_history) > 100: self.btc_history.pop(0)

        # 1. Exit Logic
        if self.position:
            if m5_candles:
                for c in m5_candles:
                    h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                    self.position["hold"] += 1
                    rsi = compute_rsi(self.history)
                    
                    exit_p = None
                    if h >= self.position["target"]:
                        exit_p = self.position["target"]; self.wins += 1; closed = True
                    elif l <= self.position["stop"]:
                        exit_p = self.position["stop"]; self.losses += 1; closed = True
                    elif rsi >= OB_EXIT or self.position["hold"] >= MAX_HOLD:
                        exit_p = cl; closed = True
                        if cl > self.position["entry"]: self.wins += 1
                        else: self.losses += 1
                    else:
                        closed = False
                    
                    if closed:
                        units = self.position["quote"] / self.position["entry"]
                        pnl = (exit_p - self.position["entry"]) * units - (self.position["quote"] * fee_rate) - (exit_p * units * fee_rate)
                        self.cash += self.position["quote"] + pnl
                        self.realized_net += pnl
                        self.closes += 1
                        self.total_volume += self.position["quote"] + (exit_p * units)
                        self.total_fees_paid += (self.position["quote"] + exit_p * units) * fee_rate
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "exit": exit_p, "net": round(pnl, 4)})
                        self.position = None
                        break

        # 2. Entry Logic
        btc_gate = True
        if len(self.btc_history) >= 3:
            mom = (self.btc_history[-1] - self.btc_history[-3]) / self.btc_history[-3]
            if mom < -0.001: btc_gate = False

        dt_now = datetime.now(timezone.utc)
        session_gate = (dt_now.hour not in [12, 19, 6, 0])

        if self.position is None and self.cash >= 10.0 and btc_gate and session_gate:
            if len(self.history) >= 10:
                rsi_prev = compute_rsi(self.history[:-1])
                if rsi_prev <= OS_ENTRY and m5_candles:
                    ep = float(m5_candles[0]["open"])
                    tq = self.cash * 0.95
                    self.position = {
                        "pid": PRODUCT, "entry": ep, "quote": tq, "hold": 0,
                        "target": ep * (1 + TP_PCT / 100.0),
                        "stop": ep * (1 - SL_PCT / 100.0)
                    }
                    self.cash -= tq
                    events.append({"ts_utc": utc_now_iso(), "action": "open", "entry": ep, "size": round(tq, 2), "rsi": round(rsi_prev, 2)})
        
        return events

    def snapshot(self):
        return {
            "starting_cash": round(self.starting_cash, 4),
            "cash": round(self.cash, 4),
            "realized_net_usd": round(self.realized_net, 4),
            "realized_net": round(self.realized_net, 4),
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "total_volume": round(self.total_volume, 4),
            "total_fees": round(self.total_fees_paid, 4),
            "open_count": 1 if self.position else 0,
            "product_id": PRODUCT,
            "position": self.position,
            "pos": "active" if self.position else "flat",
        }

def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = RaveOmniChampionShadow()
    event_logger = lambda record: append_jsonl(EVENT_PATH, record)
    runner = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": utc_now_iso(),
        "poll_seconds": 30.0,
        "heartbeat_at": None,
        "last_successful_run_at": None,
        "consecutive_exceptions": 0,
        "last_exception_at": None,
        "last_exception_type": "",
        "last_exception_message": "",
    }
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(STATE_PATH, engine, runner)
    now = int(time.time()); start = now - 72 * 3600
    print(f"Backfilling 72h data for {PRODUCT} Omni-Champion...", flush=True)
    try:
        btc_m1 = fetch_candles_chunked(client, BTC, start, now, granularity="ONE_MINUTE", event_logger=event_logger)
        btc_lookup = {int(c["start"]): c for c in btc_m1}
        rave_m5 = fetch_candles_chunked(client, PRODUCT, start, now, event_logger=event_logger)
        
        for c in rave_m5:
            t = int(c["start"])
            engine.process_tick([c], [btc_lookup.get(t)], EVENT_PATH)
            engine.last_candle_time[PRODUCT] = max(engine.last_candle_time[PRODUCT], t)
            engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], t)
    except Exception as e:
        runner["consecutive_exceptions"] += 1
        runner["last_exception_at"] = utc_now_iso()
        runner["last_exception_type"] = type(e).__name__
        runner["last_exception_message"] = str(e)
        save_state(STATE_PATH, engine, runner)
        print(f"Backfill error: {e}", flush=True)

    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(STATE_PATH, engine, runner)
    print(f"Live started. Net=${engine.realized_net:.2f} WR={engine.snapshot()['win_rate']}%", flush=True)

    try:
        while True:
            try:
                end = int(time.time())
                # Fetch BTC M1
                st_btc = engine.last_candle_time.get(BTC, end - 3600)
                if st_btc == 0: st_btc = end - 3600
                btc_tick = fetch_live_candles(
                    client,
                    BTC,
                    start=st_btc,
                    end=end,
                    granularity="ONE_MINUTE",
                    filter_after=engine.last_candle_time[BTC],
                    event_logger=event_logger,
                )
                for c in btc_tick: engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], int(c["start"]))

                # Fetch RAVE M5
                st_rave = engine.last_candle_time.get(PRODUCT, end - 3600*4)
                if st_rave == 0: st_rave = end - 3600*4
                rave_tick = fetch_live_candles(
                    client,
                    PRODUCT,
                    start=st_rave,
                    end=end,
                    granularity="FIVE_MINUTE",
                    filter_after=engine.last_candle_time[PRODUCT],
                    event_logger=event_logger,
                )
                for c in rave_tick: engine.last_candle_time[PRODUCT] = max(engine.last_candle_time[PRODUCT], int(c["start"]))
                
                if rave_tick or btc_tick:
                    events = engine.process_tick(rave_tick, btc_tick, EVENT_PATH)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                save_state(STATE_PATH, engine, runner)
                snap = engine.snapshot()
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} {snap['closes']}c {snap['win_rate']}%WR {snap['pos']}", flush=True)
            except Exception as e:
                runner["consecutive_exceptions"] += 1
                runner["last_exception_at"] = utc_now_iso()
                runner["last_exception_type"] = type(e).__name__
                runner["last_exception_message"] = str(e)
                save_state(STATE_PATH, engine, runner)
                print(f"  EXC: {e}", flush=True)
            time.sleep(30)
    except KeyboardInterrupt:
        runner["heartbeat_at"] = utc_now_iso()
        save_state(STATE_PATH, engine, runner)
        return 0

if __name__ == "__main__": main()
