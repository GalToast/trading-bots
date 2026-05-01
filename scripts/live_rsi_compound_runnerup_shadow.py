#!/usr/bin/env python3
"""
RSI Compound God Mode Live Shadow — Runner-Up Set
Products: BAL, BLUR, ALEPH, IOTX, IRYS, DASH
Non-overlapping with the primary Top 5 (RAVE/BAL/BLUR/ALEPH/IOTX).
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
STATE_PATH = ROOT / "reports" / "rsi_compound_runnerup_state.json"
EVENT_PATH = ROOT / "reports" / "rsi_compound_runnerup_events.jsonl"

# Load optimal params
PARAMS_PATH = ROOT / "reports" / "rsi_optimal_params.json"
with open(PARAMS_PATH, 'r') as f:
    OPTIMAL_PARAMS = json.load(f)

# Runner-Up set (non-overlapping with primary Top 5)
RUNNER_UP = ["BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD", "IRYS-USD", "DASH-USD"]
PRODUCTS = [p for p in RUNNER_UP if p in OPTIMAL_PARAMS]

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
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=7):
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

class RSICompoundShadow:
    def __init__(self, starting_cash=48.0, max_concurrent=1):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions = []
        self.max_concurrent = max_concurrent
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        self.history = {p: [] for p in PRODUCTS}
        self.last_candle_time = {}

    def get_fee_rate(self):
        if self.total_volume >= 50000:
            return 0.0015
        elif self.total_volume >= 10000:
            return 0.0025
        else:
            return 0.0040

    def process_tick(self, all_candles_by_pid, event_path):
        events = []
        fee_rate = self.get_fee_rate()

        # Update histories
        for pid, candles in all_candles_by_pid.items():
            if pid in self.history:
                for c in candles:
                    self.history[pid].append(float(c["close"]))
                    if len(self.history[pid]) > 50:
                        self.history[pid].pop(0)

        # 1. Exits
        still_open = []
        for pos in self.positions:
            pid = pos["pid"]
            closed = False
            if pid in all_candles_by_pid:
                params = OPTIMAL_PARAMS[pid]
                for c in all_candles_by_pid[pid]:
                    h = float(c["high"])
                    l = float(c["low"])
                    cl = float(c["close"])
                    ep = pos["entry"]
                    tp = pos["target"]
                    sp = pos["stop"]
                    tq = pos["quote"]
                    units = tq / ep

                    pos["hold_bars"] += 1
                    rsi = compute_rsi(self.history[pid], params["p"])

                    if h >= tp:
                        gross = (tp - ep) * units
                        ef = tq * fee_rate; xf = tp * units * fee_rate
                        net = gross - ef - xf
                        self.cash += tq + net; self.realized_net += net
                        self.closes += 1; self.wins += 1
                        self.total_volume += tq + (tp * units); self.total_fees_paid += ef + xf
                        events.append({"ts_utc": utc_now_iso(), "action": "close_target", "product": pid, "entry": ep, "exit": tp, "net": round(net, 4), "fees": round(ef+xf, 4), "size": round(tq, 2)})
                        closed = True; break
                    elif l <= sp:
                        gross = (sp - ep) * units
                        ef = tq * fee_rate; xf = sp * units * fee_rate
                        net = gross - ef - xf
                        self.cash += tq + net; self.realized_net += net
                        self.closes += 1; self.losses += 1
                        self.total_volume += tq + (sp * units); self.total_fees_paid += ef + xf
                        events.append({"ts_utc": utc_now_iso(), "action": "close_stop", "product": pid, "entry": ep, "exit": sp, "net": round(net, 4), "fees": round(ef+xf, 4), "size": round(tq, 2)})
                        closed = True; break
                    elif rsi >= params["ob"]:
                        gross = (cl - ep) * units
                        ef = tq * fee_rate; xf = cl * units * fee_rate
                        net = gross - ef - xf
                        self.cash += tq + net; self.realized_net += net
                        self.closes += 1
                        if cl > ep: self.wins += 1
                        self.total_volume += tq + (cl * units); self.total_fees_paid += ef + xf
                        events.append({"ts_utc": utc_now_iso(), "action": "close_rsi", "product": pid, "entry": ep, "exit": cl, "net": round(net, 4), "fees": round(ef+xf, 4), "size": round(tq, 2)})
                        closed = True; break
                    elif pos["hold_bars"] >= params["h"]:
                        gross = (cl - ep) * units
                        ef = tq * fee_rate; xf = cl * units * fee_rate
                        net = gross - ef - xf
                        self.cash += tq + net; self.realized_net += net
                        self.closes += 1
                        if cl > ep: self.wins += 1
                        self.total_volume += tq + (cl * units); self.total_fees_paid += ef + xf
                        events.append({"ts_utc": utc_now_iso(), "action": "close_timeout", "product": pid, "entry": ep, "exit": cl, "net": round(net, 4), "fees": round(ef+xf, 4), "size": round(tq, 2)})
                        closed = True; break
            if not closed: still_open.append(pos)
        self.positions = still_open

        # 2. Entries
        free_slots = self.max_concurrent - len(self.positions)
        if free_slots > 0 and self.cash >= 10.0:
            candidates = []
            for pid in PRODUCTS:
                if any(p["pid"] == pid for p in self.positions): continue
                if len(self.history[pid]) < 20: continue
                params = OPTIMAL_PARAMS[pid]
                rsi_prev = compute_rsi(self.history[pid][:-1], params["p"])
                if rsi_prev <= params["os"]:
                    candidates.append({"pid": pid, "rsi": rsi_prev, "params": params})
            candidates.sort(key=lambda x: x["rsi"])
            for cand in candidates[:free_slots]:
                if self.cash < 10.0: break
                pid = cand["pid"]; params = cand["params"]
                tq = self.cash * 0.95
                if pid in all_candles_by_pid:
                    ep = float(all_candles_by_pid[pid][0]["open"])
                    tp = ep * (1 + params["t"] / 100.0)
                    sp = ep * (1 - params["s"] / 100.0)
                    self.positions.append({"pid": pid, "entry": ep, "target": tp, "stop": sp, "quote": tq, "hold_bars": 0})
                    self.cash -= tq
                    events.append({"ts_utc": utc_now_iso(), "action": "open_rsi", "product": pid, "entry": ep, "target": round(tp, 6), "stop": round(sp, 6), "rsi_entry": round(cand["rsi"], 2), "size": round(tq, 2)})
                    free_slots -= 1
        return events

    def snapshot(self):
        return {
            "starting_cash": self.starting_cash, "cash": round(self.cash, 4), "realized_net_usd": round(self.realized_net, 4),
            "closes": self.closes, "wins": self.wins, "losses": self.losses, "total_fees": round(self.total_fees_paid, 4),
            "total_volume": round(self.total_volume, 4), "fee_rate_bps": round(self.get_fee_rate() * 10000, 1),
            "positions": self.positions, "win_rate": round(self.wins / max(1, self.closes) * 100, 2)
        }

def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    engine = RSICompoundShadow(starting_cash=args.starting_cash)
    runner = {
        "pid": os.getpid(), "script": Path(__file__).name, "started_at": utc_now_iso(),
        "poll_seconds": args.poll_seconds, "heartbeat_at": None,
        "last_successful_run_at": None, "consecutive_exceptions": 0
    }

    # Backfill 72h
    now = int(time.time()); start = now - 72 * 3600
    print("Backfilling 72h data...", flush=True)
    all_product_candles = {}
    for pid in PRODUCTS:
        c = fetch_candles_chunked(client, pid, start, now)
        all_product_candles[pid] = c
        engine.last_candle_time[pid] = 0
        time.sleep(0.2)

    all_times = sorted(list(set(int(c["start"]) for pid in all_product_candles for c in all_product_candles[pid])))
    time_lookup = {}
    for pid, candles in all_product_candles.items():
        for c in candles:
            t = int(c["start"])
            time_lookup.setdefault(t, {}).setdefault(pid, []).append(c)
            engine.last_candle_time[pid] = max(engine.last_candle_time.get(pid, 0), t)

    for t in all_times:
        engine.process_tick(time_lookup.get(t, {}), EVENT_PATH)

    save_state(STATE_PATH, engine, runner)
    snap = engine.snapshot()
    print(f"Live started. Net=${snap['realized_net_usd']:.2f} Closes={snap['closes']} WR={snap['win_rate']}% Tier={snap['fee_rate_bps']}bps", flush=True)

    try:
        while True:
            try:
                end = int(time.time()); tick_candles = {}
                for pid in PRODUCTS:
                    st = engine.last_candle_time.get(pid, end - 3600)
                    resp = client.market_candles(pid, start=st, end=end, granularity="FIVE_MINUTE")
                    new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time.get(pid, 0)]
                    if new_c:
                        tick_candles[pid] = new_c
                        for c in new_c: engine.last_candle_time[pid] = max(engine.last_candle_time.get(pid, 0), int(c["start"]))
                if tick_candles:
                    events = engine.process_tick(tick_candles, EVENT_PATH)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                runner["heartbeat_at"] = utc_now_iso(); runner["consecutive_exceptions"] = 0
                save_state(STATE_PATH, engine, runner); snap = engine.snapshot()
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} {snap['closes']}c {snap['win_rate']}%WR {len(snap['positions'])}pos", flush=True)
            except Exception as e:
                runner["consecutive_exceptions"] += 1; print(f"  EXC: {e}", flush=True)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    main()
