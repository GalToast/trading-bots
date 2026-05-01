#!/usr/bin/env python3
"""
THE APEX OMNI-CHAMPION (Long-Only)
Logic: The final, unbreakable ceiling of spot crypto.
1. Structure: M15 Range < 5% (Range-bound confirmation)
2. Energy: ATR% > 1.5% (Vol-gate)
3. Safety: BTC M1 Momentum > -0.1% + Session Gate
4. Entry: RSI(3) < 30 (Micro-momentum)
5. THE SNIPER: Order Book Bid/Ask Ratio > 2.0 AND growing (Ratio_Velocity > 0)
6. Exit: RSI(3) > 80 OR 25% TP OR 24-bar timeout. NO STOP LOSS.
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
STATE_PATH = ROOT / "reports" / "apex_omni_champion_v2_state.json"
EVENT_PATH = ROOT / "reports" / "apex_omni_champion_v2_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

# SUPREME PARAMETERS
RSI_PERIOD = 3
OS_ENTRY = 30
OB_EXIT = 80
TP_PCT = 25.0
MAX_HOLD = 24
VOL_FLOOR = 0.015
M15_RANGE_CEILING = 0.05

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

def compute_volatility(closes):
    if len(closes) < 2: return 0.0
    returns = [(closes[i] - closes[i-1])/closes[i-1] for i in range(1, len(closes))]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean)**2 for r in returns) / len(returns)
    return math.sqrt(variance)

class ApexOmniChampionV2:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        
        self.history_m5 = []
        self.history_m15 = []
        self.btc_history = []
        self.last_ratio = 1.0
        self.last_candle_time = {PRODUCT: {"M5": 0, "M15": 0}, BTC: 0}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, client, m5_tick, m15_tick, btc_tick, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        
        if m5_tick:
            for c in m5_tick:
                self.history_m5.append(float(c["close"]))
                if len(self.history_m5) > 100: self.history_m5.pop(0)
        if m15_tick:
            for c in m15_tick:
                self.history_m15.append(float(c["close"]))
                if len(self.history_m15) > 100: self.history_m15.pop(0)
        if btc_tick:
            for c in btc_tick:
                if c:
                    self.btc_history.append(float(c["close"]))
                    if len(self.btc_history) > 100: self.btc_history.pop(0)

        # 1. Exit Logic
        if self.position:
            if m5_tick:
                for c in m5_tick:
                    cl = float(c["close"])
                    self.position["hold"] += 1
                    rsi = compute_rsi(self.history_m5)
                    
                    exit_p = None
                    if cl >= self.position["target"] or rsi >= OB_EXIT or self.position["hold"] >= MAX_HOLD:
                        exit_p = cl
                        units = self.position["quote"] / self.position["entry"]
                        pnl = (exit_p - self.position["entry"]) * units - (self.position["quote"] * fee_rate) - (exit_p * units * fee_rate)
                        self.cash += self.position["quote"] + pnl; self.realized_net += pnl; self.closes += 1
                        if exit_p > self.position["entry"]: self.wins += 1
                        self.total_volume += self.position["quote"] + (exit_p * units); self.total_fees_paid += (self.position["quote"] + exit_p * units) * fee_rate
                        events.append({"ts_utc": utc_now_iso(), "action": "close", "exit": exit_p, "net": round(pnl, 4)})
                        self.position = None
                        break

        # 2. Entry Logic
        if self.position is None and self.cash >= 10.0:
            # GATES
            # BTC Gate
            btc_gate = True
            if len(self.btc_history) >= 3:
                mom = (self.btc_history[-1] - self.btc_history[-3]) / self.btc_history[-3]
                if mom < -0.001: btc_gate = False
            
            # M15 Ranging Gate
            m15_gate = False
            if len(self.history_m15) >= 4:
                m15_range = (max(self.history_m15[-4:]) - min(self.history_m15[-4:])) / min(self.history_m15[-4:])
                if m15_range <= M15_RANGE_CEILING: m15_gate = True
            
            # Vol Gate
            vol = compute_volatility(self.history_m5[-20:]) if len(self.history_m5) >= 20 else 0.0
            vol_gate = (vol >= VOL_FLOOR)
            
            # Session Gate
            dt_now = datetime.now(timezone.utc)
            session_gate = (dt_now.hour not in [12, 19, 6, 0])

            if btc_gate and m15_gate and vol_gate and session_gate:
                rsi_now = compute_rsi(self.history_m5)
                if rsi_now <= OS_ENTRY:
                    # THE SNIPER: Order Book Velocity Confirm
                    try:
                        resp = client.best_bid_ask([PRODUCT])
                        book = resp["pricebooks"][0]
                        bid_size = sum(float(b["size"]) for b in book["bids"])
                        ask_size = sum(float(a["size"]) for a in book["asks"])
                        ratio = bid_size / ask_size if ask_size > 0 else 999.0
                        
                        velocity = ratio - self.last_ratio
                        self.last_ratio = ratio
                        
                        if ratio >= 2.0 and velocity > 0:
                            ep = float(m5_tick[0]["open"]) if m5_tick else 0.0
                            if ep == 0: return events
                            
                            tq = self.cash * 0.95
                            self.position = {"pid": PRODUCT, "entry": ep, "quote": tq, "hold": 0, "target": ep * (1 + TP_PCT / 100.0)}
                            self.cash -= tq
                            events.append({"ts_utc": utc_now_iso(), "action": "open", "rsi": round(rsi_now, 2), "ratio": round(ratio, 2), "vel": round(velocity, 2)})
                    except:
                        pass
        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "pos": "active" if self.position else "flat"
        }

def main():
    client = CoinbaseAdvancedClient(); engine = ApexOmniChampionV2()
    event_logger = lambda record: append_jsonl(EVENT_PATH, record)
    now = int(time.time()); start = now - 72 * 3600
    print(f"Backfilling 72h data for {PRODUCT} Apex Omni-Champion V2...")
    
    # Combined Backfill
    try:
        rave_m5 = fetch_candles_chunked(client, PRODUCT, start, now, "FIVE_MINUTE", event_logger=event_logger)
        rave_m15 = fetch_candles_chunked(client, PRODUCT, start, now, "FIFTEEN_MINUTE", event_logger=event_logger)
        btc_m1 = fetch_candles_chunked(client, BTC, start, now, "ONE_MINUTE", event_logger=event_logger)
        
        m15_lookup = {int(c["start"]): c for c in rave_m15}
        btc_lookup = {int(c["start"]): c for c in btc_m1}
        
        for c in rave_m5:
            ts = int(c["start"])
            m15_ts = (ts // 900) * 900
            engine.process_tick(client, [c], [m15_lookup.get(m15_ts)], [btc_lookup.get(ts)], EVENT_PATH)
            engine.last_candle_time[PRODUCT]["M5"] = max(engine.last_candle_time[PRODUCT]["M5"], ts)
            engine.last_candle_time[PRODUCT]["M15"] = max(engine.last_candle_time[PRODUCT]["M15"], m15_ts)
            engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], ts)
    except Exception as e: print(f"Backfill error: {e}")

    print(f"Live started. Net=${engine.realized_net:.2f} WR={engine.snapshot()['win_rate']}%")
    runner = {"pid": os.getpid(), "script": Path(__file__).name, "started_at": utc_now_iso()}

    try:
        while True:
            try:
                end = int(time.time())
                # Fetch all updates
                btc_tick = fetch_live_candles(
                    client,
                    BTC,
                    start=engine.last_candle_time[BTC],
                    end=end,
                    granularity="ONE_MINUTE",
                    filter_after=engine.last_candle_time[BTC],
                    event_logger=event_logger,
                )
                for c in btc_tick: engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], int(c["start"]))
                
                m15_tick = fetch_live_candles(
                    client,
                    PRODUCT,
                    start=engine.last_candle_time[PRODUCT]["M15"],
                    end=end,
                    granularity="FIFTEEN_MINUTE",
                    filter_after=engine.last_candle_time[PRODUCT]["M15"],
                    event_logger=event_logger,
                )
                for c in m15_tick: engine.last_candle_time[PRODUCT]["M15"] = max(engine.last_candle_time[PRODUCT]["M15"], int(c["start"]))
                
                m5_tick = fetch_live_candles(
                    client,
                    PRODUCT,
                    start=engine.last_candle_time[PRODUCT]["M5"],
                    end=end,
                    granularity="FIVE_MINUTE",
                    filter_after=engine.last_candle_time[PRODUCT]["M5"],
                    event_logger=event_logger,
                )
                for c in m5_tick: engine.last_candle_time[PRODUCT]["M5"] = max(engine.last_candle_time[PRODUCT]["M5"], int(c["start"]))
                
                if m5_tick or m15_tick or btc_tick:
                    events = engine.process_tick(client, m5_tick, m15_tick, btc_tick, EVENT_PATH)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                
                snap = engine.snapshot()
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} {snap['closes']}c {snap['win_rate']}%WR {snap['pos']}", flush=True)
            except Exception as e: print(f"  EXC: {e}", flush=True)
            time.sleep(30)
    except KeyboardInterrupt: return 0

if __name__ == "__main__": main()
