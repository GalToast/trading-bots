#!/usr/bin/env python3
"""
RAVE Book-Fortress Live Shadow
Logic: @qwen-main's Ultimate RSI(4) + Gemini Order Book Imbalance Filter.
Config: RSI(4) < 45 AND Bid/Ask Ratio > 2.0.
Exit: RSI(4) > 95 OR 24-bar timeout. NO Stop Loss.
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
STATE_PATH = ROOT / "reports" / "rave_book_fortress_state.json"
EVENT_PATH = ROOT / "reports" / "rave_book_fortress_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

# ULTIMATE PARAMETERS
RSI_PERIOD = 4
OS_ENTRY = 45
OB_EXIT = 95
MAX_HOLD = 24
BOOK_RATIO_THRESH = 2.0

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

class RaveBookFortressShadow:
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
        self.last_candle_time = {PRODUCT: 0, BTC: 0}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, m5_candles, btc_momentum_red, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        
        if m5_candles:
            for c in m5_candles:
                self.history.append(float(c["close"]))
                if len(self.history) > 100: self.history.pop(0)

        # 1. Exit Logic
        if self.position:
            if m5_candles:
                for c in m5_candles:
                    cl = float(c["close"])
                    self.position["hold"] += 1
                    rsi = compute_rsi(self.history)
                    
                    exit_p = None
                    # Exit on RSI Target or Timeout
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
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "exit": exit_p, "net": round(pnl, 4), "reason": "rsi_exit" if rsi >= OB_EXIT else "timeout"})
                        self.position = None
                        break

        # 2. Entry Logic
        dt_now = datetime.now(timezone.utc)
        session_gate = (dt_now.hour not in [12, 19, 6, 0])

        if self.position is None and self.cash >= 10.0 and not btc_momentum_red and session_gate:
            if len(self.history) >= 10:
                rsi_now = compute_rsi(self.history)
                if rsi_now <= OS_ENTRY:
                    # ORDER BOOK CONFLUENCE
                    try:
                        resp = client.best_bid_ask([PRODUCT])
                        book = resp["pricebooks"][0]
                        bid_size = sum(float(b["size"]) for b in book["bids"])
                        ask_size = sum(float(a["size"]) for a in book["asks"])
                        ratio = bid_size / ask_size if ask_size > 0 else 999.0
                        
                        if ratio >= BOOK_RATIO_THRESH:
                            ep = float(m5_candles[0]["open"]) if m5_candles else 0.0
                            if ep == 0: return events
                            
                            tq = self.cash * 0.95
                            self.position = {"pid": PRODUCT, "entry": ep, "quote": tq, "hold": 0}
                            self.cash -= tq
                            events.append({"ts_utc": utc_now_iso(), "action": "open", "entry": ep, "size": round(tq, 2), "rsi": round(rsi_now, 2), "book_ratio": round(ratio, 2)})
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

def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    client = CoinbaseAdvancedClient(); engine = RaveBookFortressShadow()
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
    print(f"Backfilling 72h data for {PRODUCT} Book-Fortress...", flush=True)
    
    try:
        rave_m5 = fetch_candles_chunked(client, PRODUCT, start, now, event_logger=event_logger)
        for c in rave_m5:
            t = int(c["start"])
            engine.process_tick(client, [c], False, EVENT_PATH)
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
                # BTC Gate
                btc_start, btc_end, _ = live_poll_window(engine.last_candle_time.get(BTC, 0), now_ts, 60)
                btc_cands = fetch_live_candles(
                    client,
                    BTC,
                    start=btc_start,
                    end=btc_end,
                    granularity="ONE_MINUTE",
                    filter_after=btc_start - 1,
                    event_logger=event_logger,
                )
                btc_red = False
                if len(btc_cands) >= 3:
                    btc_cands.sort(key=lambda x: int(x["start"]))
                    mom = (float(btc_cands[-1]["close"]) - float(btc_cands[-3]["close"])) / float(btc_cands[-3]["close"])
                    if mom < -0.001: btc_red = True
                
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
                
                if rave_tick:
                    events = engine.process_tick(client, rave_tick, btc_red, EVENT_PATH)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                
                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                runner["last_exception_at"] = None
                runner["last_exception_type"] = ""
                runner["last_exception_message"] = ""
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
