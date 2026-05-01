#!/usr/bin/env python3
"""
Lattice-Aware + Multi-Timeframe RSI Compound Live Shadow
Combines:
1. Top 5 High-Edge RSI Assets
2. Single-Position Geometric Compounding (Kelly-Adaptive)
3. BTC M1 Momentum Safety Gate
4. Session Gate (Time-of-Day filtering)
5. Multi-Timeframe Confluence (M5 RSI < 30 & M15 RSI < 40)
6. Dynamic Fee Tier Modeling
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
from coinbase_advanced_client import CoinbaseAdvancedClient, CoinbaseAdvancedClientError

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "rsi_compound_god_mode_state.json"
EVENT_PATH = ROOT / "reports" / "rsi_compound_god_mode_events.jsonl"

# Load optimal params
PARAMS_PATH = ROOT / "reports" / "rsi_optimal_params.json"
with open(PARAMS_PATH, 'r') as f:
    OPTIMAL_PARAMS = json.load(f)

if "RAVE-USD" in OPTIMAL_PARAMS: OPTIMAL_PARAMS["RAVE-USD"]["t"] = 8.0
if "BLUR-USD" in OPTIMAL_PARAMS: OPTIMAL_PARAMS["BLUR-USD"]["t"] = 8.0

TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]
PRODUCTS = [p for p in TOP_5 if p in OPTIMAL_PARAMS]
BTC = "BTC-USD"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def is_rate_limited_error(exc: Exception) -> bool:
    return "HTTP 429" in str(exc or "")


def safe_market_candles(client, pid, *, start, end, granularity, retries=4, base_delay=1.0):
    delay = max(0.2, float(base_delay))
    for attempt in range(max(1, int(retries))):
        try:
            return client.market_candles(pid, start=start, end=end, granularity=granularity)
        except CoinbaseAdvancedClientError as exc:
            if not is_rate_limited_error(exc):
                raise
            if attempt == int(retries) - 1:
                return None
            time.sleep(delay)
            delay = min(delay * 2.0, 15.0)
    return None


def fetch_candles_chunked(client, pid, start, end, granularity="FIVE_MINUTE", event_path=None):
    chunk_sec = 300 * 5 * 60
    if granularity == "ONE_MINUTE": chunk_sec = 300 * 60
    elif granularity == "FIFTEEN_MINUTE": chunk_sec = 300 * 15 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = safe_market_candles(client, pid, start=cs, end=ce, granularity=granularity, retries=4, base_delay=1.0)
            if resp is None:
                if event_path:
                    append_jsonl(
                        event_path,
                        {
                            "ts_utc": utc_now_iso(),
                            "action": "rate_limit_skip_chunk",
                            "product": pid,
                            "granularity": granularity,
                            "start": int(cs),
                            "end": int(ce),
                        },
                    )
                cs = ce
                continue
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.2) # Avoid rate limit
        except Exception:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=7):
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


def save_state(path: Path, engine, runner: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

class MultiTimeframeRSICompoundShadow:
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
        
        self.history_m5 = {p: [] for p in PRODUCTS}
        self.history_m15 = {p: [] for p in PRODUCTS}
        self.btc_history = []
        self.last_candle_time = {p: {"M5": 0, "M15": 0} for p in PRODUCTS}
        self.last_candle_time[BTC] = 0

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, m5_tick, m15_tick, btc_tick, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        
        # Update histories
        for pid, candles in m5_tick.items():
            if pid in self.history_m5:
                for c in candles:
                    self.history_m5[pid].append(float(c["close"]))
                    if len(self.history_m5[pid]) > 100: self.history_m5[pid].pop(0)
        
        for pid, candles in m15_tick.items():
            if pid in self.history_m15:
                for c in candles:
                    self.history_m15[pid].append(float(c["close"]))
                    if len(self.history_m15[pid]) > 100: self.history_m15[pid].pop(0)
        
        if btc_tick:
            for c in btc_tick:
                if c is not None:
                    self.btc_history.append(float(c["close"]))
                    if len(self.btc_history) > 100: self.btc_history.pop(0)

        # 1. Exits
        still_open = []
        for pos in self.positions:
            pid = pos["pid"]; closed = False
            if pid in m5_tick:
                params = OPTIMAL_PARAMS[pid]
                for c in m5_tick[pid]:
                    h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                    pos["hold_bars"] += 1
                    rsi = compute_rsi(self.history_m5[pid], params["p"])
                    
                    exit_p = None
                    if h >= pos["target"]:
                        exit_p = pos["target"]; self.wins += 1; closed = True
                    elif l <= pos["stop"]:
                        exit_p = pos["stop"]; self.losses += 1; closed = True
                    elif rsi >= params["ob"] or pos["hold_bars"] >= params["h"]:
                        exit_p = cl; closed = True
                        if cl > pos["entry"]: self.wins += 1
                        else: self.losses += 1
                    
                    if closed:
                        units = pos["quote"] / pos["entry"]
                        gross = (exit_p - pos["entry"]) * units
                        ef = pos["quote"] * fee_rate; xf = exit_p * units * fee_rate
                        net = gross - ef - xf
                        self.cash += pos["quote"] + net; self.realized_net += net
                        self.closes += 1; self.total_volume += pos["quote"] + (exit_p * units); self.total_fees_paid += ef + xf
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "product": pid, "net": round(net, 4)})
                        break
            if not closed: still_open.append(pos)
        self.positions = still_open

        # 2. Entries
        btc_gate = True
        if len(self.btc_history) >= 3:
            mom = (self.btc_history[-1] - self.btc_history[-3]) / self.btc_history[-3]
            if mom < -0.001: btc_gate = False

        # Session Gate
        dt_now = datetime.now(timezone.utc)
        hour_now = dt_now.hour
        session_gate = (hour_now not in [12, 19, 6, 0])

        free_slots = self.max_concurrent - len(self.positions)
        if free_slots > 0 and self.cash >= 10.0 and btc_gate and session_gate:
            candidates = []
            for pid in PRODUCTS:
                if any(p["pid"] == pid for p in self.positions): continue
                if len(self.history_m5[pid]) < 50: continue
                if len(self.history_m15[pid]) < 10: continue # M15 Confluence
                
                params = OPTIMAL_PARAMS[pid]
                rsi5 = compute_rsi(self.history_m5[pid][:-1], params["p"])
                rsi15 = compute_rsi(self.history_m15[pid], 7) # Standard 7-period M15
                
                # M15 RSI Gate: Must be < 40
                if rsi5 <= params["os"] and rsi15 <= 40:
                    vol_1h = compute_volatility(self.history_m5[pid][-12:])
                    vol_24h = compute_volatility(self.history_m5[pid][-50:])
                    if vol_1h > 1.2 * vol_24h:
                        candidates.append({"pid": pid, "rsi": rsi5, "params": params})
            
            candidates.sort(key=lambda x: x["rsi"])
            for cand in candidates[:free_slots]:
                if self.cash < 10.0: break
                pid = cand["pid"]; params = cand["params"]
                wr = self.wins / max(1, self.closes) if self.closes > 5 else 0.65
                kelly = max(0.05, (wr * 2 - 1) * 0.5)
                tq = min(self.cash * 0.95, self.cash * kelly * 5.0)
                if tq < 10.0: tq = 10.0
                if tq > self.cash: break
                
                if pid in m5_tick:
                    ep = float(m5_tick[pid][0]["open"])
                    self.positions.append({
                        "pid": pid, "entry": ep, "quote": tq, "hold_bars": 0,
                        "target": ep * (1 + params["t"] / 100.0),
                        "stop": ep * (1 - params["s"] / 100.0)
                    })
                    self.cash -= tq
                    events.append({"ts_utc": utc_now_iso(), "action": "open", "product": pid, "size": round(tq, 2)})
                    free_slots -= 1
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
            "fee_rate_bps": round(self.get_fee_rate() * 10000, 1),
            "open_count": len(self.positions),
            "positions": self.positions,
            "products": PRODUCTS,
        }

def main():
    client = CoinbaseAdvancedClient(); engine = MultiTimeframeRSICompoundShadow()
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
    print("Backfilling 72h data...")
    try:
        btc_m1 = fetch_candles_chunked(client, BTC, start, now, granularity="ONE_MINUTE", event_path=EVENT_PATH)
        btc_lookup = {int(c["start"]): c for c in btc_m1}
        
        m15_hist_lookup = {}
        for pid in PRODUCTS:
            print(f"  Fetching {pid} M15...")
            m15 = fetch_candles_chunked(client, pid, start, now, granularity="FIFTEEN_MINUTE", event_path=EVENT_PATH)
            m15_hist_lookup[pid] = {int(c["start"]): c for c in m15}
            time.sleep(0.5)

        for pid in PRODUCTS:
            print(f"  Backfilling {pid} M5...")
            cands = fetch_candles_chunked(client, pid, start, now, event_path=EVENT_PATH)
            for c in cands:
                t = int(c["start"])
                m15_ts = (t // 900) * 900 - 900
                m15_c = m15_hist_lookup[pid].get(m15_ts)
                engine.process_tick({pid: [c]}, {pid: [m15_c] if m15_c else []}, [btc_lookup.get(t)], EVENT_PATH)
                engine.last_candle_time[pid]["M5"] = max(engine.last_candle_time[pid]["M5"], t)
                engine.last_candle_time[pid]["M15"] = max(engine.last_candle_time[pid]["M15"], m15_ts)
                engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], t)
    except Exception as e:
        runner["consecutive_exceptions"] += 1
        runner["last_exception_at"] = utc_now_iso()
        runner["last_exception_type"] = type(e).__name__
        runner["last_exception_message"] = str(e)
        save_state(STATE_PATH, engine, runner)
        print(f"Backfill error: {e}", flush=True)

    print(f"Live started. Net=${engine.realized_net:.2f} WR={engine.snapshot()['win_rate']}%  ")
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(STATE_PATH, engine, runner)
    try:
        while True:
            try:
                end = int(time.time()); m5_tick = {}; m15_tick = {}; btc_tick = []
                # Fetch BTC M1
                resp = safe_market_candles(client, BTC, start=engine.last_candle_time[BTC], end=end, granularity="ONE_MINUTE", retries=4, base_delay=1.0)
                if resp is not None:
                    btc_tick = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[BTC]]
                for c in btc_tick: engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], int(c["start"]))
                # Fetch M15s
                for pid in PRODUCTS:
                    resp = safe_market_candles(client, pid, start=engine.last_candle_time[pid]["M15"], end=end, granularity="FIFTEEN_MINUTE", retries=4, base_delay=1.0)
                    if resp is None:
                        append_jsonl(EVENT_PATH, {"ts_utc": utc_now_iso(), "action": "rate_limit_skip_live_fetch", "product": pid, "granularity": "FIFTEEN_MINUTE"})
                        time.sleep(0.2)
                        continue
                    new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[pid]["M15"]]
                    if new_c:
                        m15_tick[pid] = new_c
                        for c in new_c: engine.last_candle_time[pid]["M15"] = max(engine.last_candle_time[pid]["M15"], int(c["start"]))
                    time.sleep(0.2)
                # Fetch M5s
                for pid in PRODUCTS:
                    resp = safe_market_candles(client, pid, start=engine.last_candle_time[pid]["M5"], end=end, granularity="FIVE_MINUTE", retries=4, base_delay=1.0)
                    if resp is None:
                        append_jsonl(EVENT_PATH, {"ts_utc": utc_now_iso(), "action": "rate_limit_skip_live_fetch", "product": pid, "granularity": "FIVE_MINUTE"})
                        time.sleep(0.2)
                        continue
                    new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time[pid]["M5"]]
                    if new_c:
                        m5_tick[pid] = new_c
                        for c in new_c: engine.last_candle_time[pid]["M5"] = max(engine.last_candle_time[pid]["M5"], int(c["start"]))
                    time.sleep(0.2)
                if m5_tick or m15_tick or btc_tick:
                    events = engine.process_tick(m5_tick, m15_tick, btc_tick, EVENT_PATH)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                runner["last_exception_at"] = None
                runner["last_exception_type"] = ""
                runner["last_exception_message"] = ""
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
