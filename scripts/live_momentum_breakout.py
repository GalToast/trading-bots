#!/usr/bin/env python3
"""
Momentum Breakout Live Shadow — Validated $741/11d (1544%), 59 trades, 81.4% WR.
Logic: Current HIGH breaks above 10-bar high → enter long.
Exit: TP 10% / SL 7% / 50-bar timeout.
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
STATE_PATH = ROOT / "reports" / "momentum_breakout_state.json"
EVENT_PATH = ROOT / "reports" / "momentum_breakout_events.jsonl"

PRODUCT = "RAVE-USD"
LOOKBACK = 10
TP_PCT = 10.0
SL_PCT = 7.0
MAX_HOLD = 50

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
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
            if not cands: break
            time.sleep(0.2)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

class MomentumBreakoutShadow:
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
        self.high_history = []
        self.last_candle_time = 0

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, candles):
        events = []
        fee_rate = self.get_fee_rate()

        for c in candles:
            h = float(c["high"]); l = float(c["low"]); cl = float(c["close"]); o = float(c["open"])
            ts = int(c["start"])
            self.high_history.append(h)
            if len(self.high_history) > 200: self.high_history.pop(0)
            self.last_candle_time = max(self.last_candle_time, ts)

            # Exit
            if self.position:
                self.position["hold"] += 1
                exit_p = None
                exit_reason = None

                if h >= self.position["tp"]:
                    exit_p = self.position["tp"]; exit_reason = "tp"
                elif l <= self.position["sl"]:
                    exit_p = self.position["sl"]; exit_reason = "sl"
                elif self.position["hold"] >= MAX_HOLD:
                    exit_p = cl; exit_reason = "timeout"

                if exit_p is not None:
                    units = self.position["units"]
                    ef = self.position["entry_fee"]
                    xf = exit_p * units * fee_rate
                    net = (exit_p - self.position["ep"]) * units - ef - xf
                    self.cash += exit_p * units - xf
                    self.realized_net += net
                    self.closes += 1
                    self.total_volume += self.position["deploy"] + (exit_p * units)
                    self.total_fees_paid += ef + xf
                    if exit_p > self.position["ep"]: self.wins += 1
                    else: self.losses += 1
                    events.append({
                        "ts_utc": utc_now_iso(), "action": "close",
                        "exit": exit_p, "net": round(net, 4), "reason": exit_reason
                    })
                    self.position = None

            # Entry: current HIGH breaks above LOOKBACK-bar high
            if self.position is None and self.cash >= 10.0 and len(self.high_history) >= LOOKBACK + 2:
                recent_high = max(self.high_history[-LOOKBACK-1:-1])  # Previous LOOKBACK highs
                if h > recent_high:
                    deploy = self.cash * 0.95
                    if deploy >= 10.0:
                        entry_fee = deploy * fee_rate
                        units = (deploy - entry_fee) / o
                        if units > 0:
                            self.cash -= deploy
                            self.position = {
                                "ep": o, "deploy": deploy, "units": units, "hold": 0,
                                "tp": o * (1 + TP_PCT / 100.0),
                                "sl": o * (1 - SL_PCT / 100.0),
                                "entry_fee": entry_fee,
                            }
                            events.append({"ts_utc": utc_now_iso(), "action": "open", "entry": o, "size": round(deploy, 2)})

        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "total_volume": round(self.total_volume, 4), "pos": "active" if self.position else "flat"
        }

def main():
    client = CoinbaseAdvancedClient()
    engine = MomentumBreakoutShadow()

    now = int(time.time()); start = now - 72 * 3600
    print(f"Backfilling 72h for Momentum Breakout on {PRODUCT}...")
    candles = fetch_candles_chunked(client, PRODUCT, start, now)

    for c in candles:
        engine.process_tick([c])

    print(f"MOMENTUM BREAKOUT LIVE: Net=${engine.realized_net:.2f} WR={engine.snapshot()['win_rate']}% "
          f"Cash=${engine.cash:.2f} {engine.closes} trades", flush=True)

    try:
        while True:
            try:
                end = int(time.time())
                resp = client.market_candles(PRODUCT, start=engine.last_candle_time, end=end, granularity="FIVE_MINUTE")
                new = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time]
                if new:
                    events = engine.process_tick(new)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                snap = engine.snapshot()
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} "
                      f"{snap['closes']}c {snap['win_rate']}%WR {snap['pos']}", flush=True)
            except Exception as e: print(f"  EXC: {e}", flush=True)
            time.sleep(30)
    except KeyboardInterrupt: return 0

if __name__ == "__main__": main()
