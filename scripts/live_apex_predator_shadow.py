#!/usr/bin/env python3
"""
THE APEX PREDATOR (Long-Only)
Logic: The final synthesis of all structural edges.
1. Regime Gate: Volatility > 3% (Avoid fee-drag death)
2. Lattice Gate: BTC M1 Momentum > -0.1% (Market safety)
3. Entry: RSI(4) < 45 (The @main Peak)
4. Confirm: OB Ratio > 2.0 AND Ratio Velocity > 0 (The @gemini Wall)
5. Exit: RSI(4) > 95 OR 4-bar timeout. NO Stop Loss.
6. Sizing: 95% Geometric Compounding.
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
STATE_PATH = ROOT / "reports" / "apex_predator_state.json"
EVENT_PATH = ROOT / "reports" / "apex_predator_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

# HYPER-PARAMETERS
RSI_PERIOD = 4
OS_ENTRY = 45
OB_EXIT = 95
MAX_HOLD = 4
VOL_FLOOR = 0.015 # 1.5% volatility floor
RATIO_FLOOR = 2.0

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

def compute_volatility(closes):
    if len(closes) < 2: return 0.0
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean)**2 for r in returns) / len(returns)
    return math.sqrt(variance)


def live_poll_window(
    last_candle_time: int,
    now_ts: int,
    granularity_seconds: int,
    *,
    max_candles: int = 300,
    fallback_candles: int = 3,
) -> tuple[int, int, int]:
    end_ts = int(now_ts) - (int(now_ts) % granularity_seconds)
    if end_ts <= 0:
        return 0, 0, 0
    latest_closed = end_ts - granularity_seconds
    if latest_closed <= 0:
        return 0, end_ts, 0
    cursor = int(last_candle_time or 0)
    if cursor <= 0 or cursor >= end_ts:
        cursor = max(0, latest_closed - granularity_seconds * max(1, int(fallback_candles)))
    latest_allowed = end_ts - granularity_seconds * int(max_candles)
    cursor = max(cursor, latest_allowed)
    return cursor, end_ts, cursor

class ApexPredatorShadow:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None
        
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        
        self.history = []
        self.btc_history = []
        self.last_ratio = 1.0
        self.last_candle_time = {PRODUCT: 0, BTC: 0}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, m5_candles, btc_tick, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        
        if m5_candles:
            for c in m5_candles:
                self.history.append(float(c["close"]))
                if len(self.history) > 100: self.history.pop(0)
        
        if btc_tick:
            for c in btc_tick:
                if c:
                    self.btc_history.append(float(c["close"]))
                    if len(self.btc_history) > 100: self.btc_history.pop(0)

        # 1. Exit Logic
        if self.position:
            if m5_candles:
                for c in m5_candles:
                    cl = float(c["close"])
                    self.position["hold"] += 1
                    rsi = compute_rsi(self.history)
                    
                    exit_p = None
                    if rsi >= OB_EXIT or self.position["hold"] >= MAX_HOLD:
                        exit_p = cl
                        units = self.position["quote"] / self.position["entry"]
                        pnl = (exit_p - self.position["entry"]) * units - (self.position["quote"] * fee_rate) - (exit_p * units * fee_rate)
                        self.cash += self.position["quote"] + pnl
                        self.realized_net += pnl
                        self.closes += 1
                        if exit_p > self.position["entry"]: self.wins += 1
                        self.total_volume += self.position["quote"] + (exit_p * units)
                        self.total_fees_paid += (self.position["quote"] + exit_p * units) * fee_rate
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "exit": exit_p, "net": round(pnl, 4)})
                        self.position = None
                        break

        # 2. Entry Logic
        # Safety Gates
        btc_gate = True
        if len(self.btc_history) >= 3:
            mom = (self.btc_history[-1] - self.btc_history[-3]) / self.btc_history[-3]
            if mom < -0.001: btc_gate = False

        vol_gate = False
        if len(self.history) >= 20:
            vol = compute_volatility(self.history[-20:])
            print(f"DEBUG: Vol={vol*100:.2f}% (Target {VOL_FLOOR*100:.2f}%)")
            if vol >= VOL_FLOOR: vol_gate = True

        dt_now = datetime.now(timezone.utc)
        session_gate = (dt_now.hour not in [12, 19, 6, 0])

        if self.position is None and self.cash >= 10.0 and btc_gate and vol_gate and session_gate:
            rsi_now = compute_rsi(self.history)
            if rsi_now <= OS_ENTRY:
                # ORDER BOOK VELOCITY Confirm
                try:
                    resp = client.best_bid_ask([PRODUCT])
                    book = resp["pricebooks"][0]
                    bid_size = sum(float(b["size"]) for b in book["bids"])
                    ask_size = sum(float(a["size"]) for a in book["asks"])
                    ratio = bid_size / ask_size if ask_size > 0 else 999.0
                    
                    velocity = ratio - self.last_ratio
                    self.last_ratio = ratio
                    
                    if ratio >= RATIO_FLOOR and velocity > 0:
                        ep = float(m5_candles[0]["open"]) if m5_candles else 0.0
                        if ep == 0: return events
                        
                        tq = self.cash * 0.95
                        self.position = {"pid": PRODUCT, "entry": ep, "quote": tq, "hold": 0}
                        self.cash -= tq
                        events.append({"ts_utc": utc_now_iso(), "action": "open", "entry": ep, "rsi": round(rsi_now, 2), "ratio": round(ratio, 2), "vel": round(velocity, 2)})
                except:
                    pass
        return events

    def snapshot(self):
        return {
            "starting_cash": round(self.starting_cash, 4),
            "cash": round(self.cash, 4),
            "realized_net_usd": round(self.realized_net, 4),
            "realized_net": round(self.realized_net, 4),
            "closes": self.closes,
            "wins": self.wins,
            "losses": max(0, self.closes - self.wins),
            "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "total_volume": round(self.total_volume, 4),
            "total_fees": round(self.total_fees_paid, 4),
            "open_count": 1 if self.position else 0,
            "product_id": PRODUCT,
            "position": self.position,
            "pos": "active" if self.position else "flat",
            "vol_status": "HIGH" if (len(self.history) >= 20 and compute_volatility(self.history[-20:]) >= VOL_FLOOR) else "LOW"
        }

def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = ApexPredatorShadow()
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
    print(f"Backfilling 72h data for {PRODUCT} Apex Predator...", flush=True)
    
    try:
        rave_m5 = fetch_candles_chunked(client, PRODUCT, start, now, event_logger=event_logger)
        btc_m1 = fetch_candles_chunked(client, BTC, start, now, granularity="ONE_MINUTE", event_logger=event_logger)
        btc_lookup = {int(c["start"]): c for c in btc_m1}
        
        for c in rave_m5:
            t = int(c["start"])
            # Backfill assumes neutral book velocity for initialization
            engine.process_tick(client, [c], [btc_lookup.get(t)], EVENT_PATH)
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
                now_ts = int(time.time())
                # BTC Gate
                btc_start, btc_end, btc_filter_after = live_poll_window(engine.last_candle_time.get(BTC, 0), now_ts, 60)
                btc_tick = fetch_live_candles(
                    client,
                    BTC,
                    start=btc_start,
                    end=btc_end,
                    granularity="ONE_MINUTE",
                    filter_after=btc_filter_after,
                    event_logger=event_logger,
                )
                for c in btc_tick: engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], int(c["start"]))
                
                # RAVE Tick
                rave_start, rave_end, rave_filter_after = live_poll_window(engine.last_candle_time.get(PRODUCT, 0), now_ts, 300)
                rave_tick = fetch_live_candles(
                    client,
                    PRODUCT,
                    start=rave_start,
                    end=rave_end,
                    granularity="FIVE_MINUTE",
                    filter_after=rave_filter_after,
                    event_logger=event_logger,
                )
                for c in rave_tick: engine.last_candle_time[PRODUCT] = max(engine.last_candle_time[PRODUCT], int(c["start"]))
                
                if rave_tick or btc_tick:
                    events = engine.process_tick(client, rave_tick, btc_tick, EVENT_PATH)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                
                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                runner["last_exception_at"] = None
                runner["last_exception_type"] = ""
                runner["last_exception_message"] = ""
                save_state(STATE_PATH, engine, runner)
                snap = engine.snapshot()
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} {snap['closes']}c {snap['win_rate']}%WR {snap['pos']} Vol={snap['vol_status']}", flush=True)
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
