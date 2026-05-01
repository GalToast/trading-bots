#!/usr/bin/env python3
"""
THE SUPREME GOD KILLER (Long-Only)
Asset: RAVE-USD
Logic: StochRSI(4,3) < 0.05 + RSI(4) < 30 + Order Book Imbalance Filter.
Exit: RSI(4) > 80 or 24-bar timeout. No Stop Loss.
Sizing: 95% Geometric Compounding.
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
STATE_PATH = ROOT / "reports" / "supreme_god_killer_state.json"
EVENT_PATH = ROOT / "reports" / "supreme_god_killer_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def save_state(path: Path, engine, runner: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def bounded_start(start_ts: int, end_ts: int, granularity_seconds: int, *, max_candles: int = 300) -> int:
    if end_ts <= 0:
        return start_ts
    latest_allowed = end_ts - granularity_seconds * max_candles
    return max(int(start_ts), int(latest_allowed))


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
    start_ts = bounded_start(cursor, end_ts, granularity_seconds, max_candles=max_candles)
    return start_ts, end_ts, cursor

def compute_rsi(closes, period=4):
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

def compute_stoch_rsi(rsi_history, period=3):
    if len(rsi_history) < period: return 0.5
    low_rsi = min(rsi_history[-period:])
    high_rsi = max(rsi_history[-period:])
    if high_rsi == low_rsi: return 0.5
    return (rsi_history[-1] - low_rsi) / (high_rsi - low_rsi)

class SupremeGodKiller:
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
        self.rsi_history = []
        self.btc_history = []
        self.last_candle_time = {PRODUCT: 0, BTC: 0}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, m5_candles, btc_candles, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        
        if m5_candles:
            for c in m5_candles:
                cl = float(c["close"])
                self.history.append(cl)
                if len(self.history) >= 5:
                    rsi = compute_rsi(self.history, 4)
                    self.rsi_history.append(rsi)
                if len(self.history) > 100: self.history.pop(0)
                if len(self.rsi_history) > 100: self.rsi_history.pop(0)
        
        if btc_candles:
            for c in btc_candles:
                if c:
                    self.btc_history.append(float(c["close"]))
                    if len(self.btc_history) > 100: self.btc_history.pop(0)

        # 1. Exit
        if self.position:
            if m5_candles:
                for c in m5_candles:
                    cl = float(c["close"])
                    self.position["hold"] += 1
                    rsi = compute_rsi(self.history, 4)
                    
                    exit_p = None
                    if rsi >= 80: exit_p = cl; closed = True
                    elif self.position["hold"] >= 24: exit_p = cl; closed = True
                    else: closed = False
                    
                    if closed:
                        units = self.position["quote"] / self.position["entry"]
                        gross = (exit_p - self.position["entry"]) * units
                        ef = self.position["quote"] * fee_rate; xf = exit_p * units * fee_rate
                        net = gross - ef - xf
                        self.cash += self.position["quote"] + net
                        self.realized_net += net
                        self.closes += 1
                        if exit_p > self.position["entry"]: self.wins += 1
                        self.total_volume += self.position["quote"] + (exit_p * units)
                        self.total_fees_paid += ef + xf
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "net": round(net, 4)})
                        self.position = None
                        break

        # 2. Entry
        if self.position is None and self.cash >= 10.0:
            if len(self.rsi_history) >= 4:
                rsi_now = self.rsi_history[-1]
                stoch_rsi = compute_stoch_rsi(self.rsi_history, 3)
                
                if rsi_now <= 30 and stoch_rsi <= 0.05:
                    # ORDER BOOK FILTER
                    try:
                        resp = client.best_bid_ask([PRODUCT])
                        book = resp["pricebooks"][0]
                        bid_size = sum(float(b["size"]) for b in book["bids"])
                        ask_size = sum(float(a["size"]) for a in book["asks"])
                        imbalance = (bid_size - ask_size) / (bid_size + ask_size)
                        
                        if imbalance > 0.2: # POSITIVE PRESSURE
                            ep = float(m5_candles[0]["open"]) if m5_candles else 0.0
                            if ep == 0: return events
                            
                            tq = self.cash * 0.95
                            self.position = {"pid": PRODUCT, "entry": ep, "quote": tq, "hold": 0}
                            self.cash -= tq
                            events.append({"ts_utc": utc_now_iso(), "action": "open", "imbalance": round(imbalance, 2)})
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
        }

def main():
    client = CoinbaseAdvancedClient(); engine = SupremeGodKiller()
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
    print(f"Backfilling 72h data for {PRODUCT}...", flush=True)
    try:
        rave_m5 = fetch_candles_chunked(client, PRODUCT, start, now, event_logger=event_logger)
        
        for c in rave_m5:
            t = int(c["start"])
            # Backfill uses OHLC logic only (no book data)
            engine.process_tick(client, [c], [], EVENT_PATH)
            engine.last_candle_time[PRODUCT] = max(engine.last_candle_time[PRODUCT], t)
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
                last_time = int(engine.last_candle_time.get(PRODUCT, 0) or 0)
                start_ts, end_ts, filter_after = live_poll_window(last_time, now_ts, 300)
                if end_ts <= 0 or start_ts <= 0 or start_ts >= end_ts:
                    runner["heartbeat_at"] = utc_now_iso()
                    runner["last_successful_run_at"] = runner["heartbeat_at"]
                    runner["consecutive_exceptions"] = 0
                    save_state(STATE_PATH, engine, runner)
                    time.sleep(30)
                    continue
                if last_time != filter_after:
                    engine.last_candle_time[PRODUCT] = filter_after
                    append_jsonl(
                        EVENT_PATH,
                        {
                            "ts_utc": utc_now_iso(),
                            "action": "cursor_realign",
                            "product_id": PRODUCT,
                            "prior_last_candle_time": last_time,
                            "realigned_last_candle_time": filter_after,
                            "poll_end": end_ts,
                        },
                    )
                rave_tick = fetch_live_candles(
                    client,
                    PRODUCT,
                    start=start_ts,
                    end=end_ts,
                    granularity="FIVE_MINUTE",
                    filter_after=filter_after,
                    event_logger=event_logger,
                )
                for c in rave_tick: engine.last_candle_time[PRODUCT] = max(engine.last_candle_time[PRODUCT], int(c["start"]))
                
                if rave_tick:
                    events = engine.process_tick(client, rave_tick, [], EVENT_PATH)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                save_state(STATE_PATH, engine, runner)
                snap = engine.snapshot()
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} {snap['closes']}c {snap['win_rate']}%WR", flush=True)
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
