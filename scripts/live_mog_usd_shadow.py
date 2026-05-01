#!/usr/bin/env python3
"""
MOG-USD Live Shadow Runner
============================
RSI(4)<30 + 25% TP + No SL + 24-bar timeout
Discovered: $56.48/1.8d (117.7%), 17 trades, 70.6% WR
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "mog_live_shadow_state.json"
EVENT_PATH = ROOT / "reports" / "mog_live_shadow_events.jsonl"

PRODUCT = "MOG-USD"
RSI_PERIOD = 4
OS_THRESH = 30
TP_PCT = 25.0
SL_PCT = 0.0
MAX_HOLD = 24

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

def fetch_candles_chunked(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.2)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        return 100 - 100 / (1 + rs)
    return 100.0

class MogLiveShadow:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.history = []
        self.last_candle_time = 0

    def process_tick(self, candles, event_path):
        events = []
        fee_rate = 0.0040

        for c in candles:
            self.history.append(float(c["close"]))
            if len(self.history) > 100:
                self.history.pop(0)

        if self.position and candles:
            for c in candles:
                h = float(c["high"])
                l = float(c["low"])
                cl = float(c["close"])
                self.position["hold"] += 1
                rsi = compute_rsi(self.history)

                exit_p = None
                exit_reason = None

                if h >= self.position["target"]:
                    exit_p = self.position["target"]
                    exit_reason = "tp"
                elif self.position["hold"] >= MAX_HOLD:
                    exit_p = cl
                    exit_reason = "timeout"

                if exit_p is not None:
                    units = self.position["quote"] / self.position["entry"]
                    gross = (exit_p - self.position["entry"]) * units
                    entry_fee = self.position["entry"] * units * fee_rate
                    exit_fee = exit_p * units * fee_rate
                    net = gross - entry_fee - exit_fee

                    self.cash += self.position["quote"] + net
                    self.realized_net += net
                    self.closes += 1
                    if net > 0:
                        self.wins += 1
                    else:
                        self.losses += 1

                    events.append({"ts_utc": utc_now_iso(), "action": "close", "exit": exit_p,
                                   "net": round(net, 4), "reason": exit_reason})
                    self.position = None
                    break

        if self.position is None and self.cash >= 10.0 and candles:
            if len(self.history) >= RSI_PERIOD + 5:
                rsi_prev = compute_rsi(self.history[:-1])
                if rsi_prev <= OS_THRESH:
                    ep = float(candles[0]["open"])
                    tq = self.cash * 0.95
                    self.position = {
                        "entry": ep, "quote": tq, "hold": 0,
                        "target": ep * (1 + TP_PCT / 100.0),
                    }
                    self.cash -= tq
                    events.append({"ts_utc": utc_now_iso(), "action": "open", "entry": ep, "size": round(tq, 2)})

        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "pos": "active" if self.position else "flat",
        }

def main():
    client = CoinbaseAdvancedClient()
    engine = MogLiveShadow()

    now = int(time.time())
    start = now - 72 * 3600

    print(f"Backfilling 72h data for {PRODUCT}...")
    candles = fetch_candles_chunked(client, PRODUCT, start, now)
    for c in candles:
        engine.process_tick([c], EVENT_PATH)
        engine.last_candle_time = max(engine.last_candle_time, int(c["start"]))

    print(f"MOG LIVE: Net=${engine.realized_net:.2f} WR={engine.snapshot()['win_rate']}% "
          f"Closes={engine.closes} Pos={engine.snapshot()['pos']}", flush=True)

    try:
        while True:
            try:
                end = int(time.time())
                resp = client.market_candles(PRODUCT, start=engine.last_candle_time, end=end, granularity="FIVE_MINUTE")
                new_candles = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time]
                for c in new_candles:
                    engine.last_candle_time = max(engine.last_candle_time, int(c["start"]))

                if new_candles:
                    events = engine.process_tick(new_candles, EVENT_PATH)
                    for ev in events:
                        append_jsonl(EVENT_PATH, ev)

                snap = engine.snapshot()
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} "
                      f"{snap['closes']}c {snap['win_rate']}%WR {snap['pos']}", flush=True)
            except Exception as e:
                print(f"  EXC: {e}", flush=True)
            time.sleep(30)
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    main()
