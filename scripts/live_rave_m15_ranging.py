#!/usr/bin/env python3
"""
RAVE M15-Ranging Champion Live Shadow
Asset: RAVE-USD
Logic: RSI(3)<30 entry, 50%TP, NoSL, 48-bar hold, COMPOUND, M15_RANGING filter.
Includes: BTC M1 Momentum Gate + Session Gate + M15 Ranging Gate.

CHAMPION: $545.79/11 days (1137%), 24 trades, 87.5% WR, 13.1% DD.
Previous baseline: $377.27/11 days (786%), 25 trades, 84.0% WR, 19.6% DD.
M15 ranging filter improvement: +$168.52 (+45%), -6.5pp DD.
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from coinbase_rate_limit import fetch_candles_chunked, fetch_live_candles

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "rave_m15_ranging_state.json"
EVENT_PATH = ROOT / "reports" / "rave_m15_ranging_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

# Champion Parameters
RSI_PERIOD = 3
OS_THRESH = 30
TP_PCT = 50.0
MAX_HOLD = 48
M15_RANGE_THRESHOLD = 5.0  # M15 range% must be < 5% to trade

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

def check_m15_ranging(m15_candles, threshold=M15_RANGE_THRESHOLD):
    """Check if M15 is in a ranging regime (not trending)."""
    if len(m15_candles) < 10:
        return True  # Not enough data, allow trade
    recent = m15_candles[-10:]
    closes = [float(c["close"]) for c in recent]
    range_pct = (max(closes) - min(closes)) / min(closes) * 100
    return range_pct < threshold

class RaveM15RangingShadow:
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
        self.m15_history = []
        self.last_candle_time = {PRODUCT: 0, BTC: 0, f"{PRODUCT}_M15": 0}

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, m5_candles, btc_candles, m15_candles):
        events = []
        fee_rate = self.get_fee_rate()

        if m5_candles:
            for c in m5_candles:
                self.history.append(float(c["close"]))
                if len(self.history) > 500: self.history.pop(0)

        if btc_candles:
            for c in btc_candles:
                if c:
                    self.btc_history.append(float(c["close"]))
                    if len(self.btc_history) > 100: self.btc_history.pop(0)

        if m15_candles:
            for c in m15_candles:
                self.m15_history.append(c)
                if len(self.m15_history) > 100: self.m15_history.pop(0)

        # 1. Exit
        if self.position:
            if m5_candles:
                for c in m5_candles:
                    h = float(c["high"]); l = float(c["low"]); cl = float(c["close"])
                    self.position["hold"] += 1

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
                        ef = self.position["quote"] * fee_rate
                        xf = exit_p * units * fee_rate
                        net = gross - ef - xf
                        self.cash += self.position["quote"] + net
                        self.realized_net += net
                        self.closes += 1
                        self.total_volume += self.position["quote"] + (exit_p * units)
                        self.total_fees_paid += ef + xf
                        if exit_p > self.position["entry"]:
                            self.wins += 1
                        else:
                            self.losses += 1
                        events.append({
                            "ts_utc": utc_now_iso(), "action": "close",
                            "exit": exit_p, "net": round(net, 4), "reason": exit_reason
                        })
                        self.position = None
                        break

        # 2. Entry
        btc_gate = True
        if len(self.btc_history) >= 3:
            mom = (self.btc_history[-1] - self.btc_history[-3]) / self.btc_history[-3]
            if mom < -0.001: btc_gate = False

        dt_now = datetime.now(timezone.utc)
        session_gate = (dt_now.hour not in [12, 19, 6, 0])

        # M15 Ranging Gate
        m15_ranging = check_m15_ranging(self.m15_history)

        if self.position is None and self.cash >= 10.0 and btc_gate and session_gate and m15_ranging:
            if len(self.history) >= RSI_PERIOD + 2:
                rsi_prev = compute_rsi(self.history[:-1])
                if rsi_prev <= OS_THRESH and m5_candles:
                    ep = float(m5_candles[0]["open"])
                    tq = self.cash * 0.95  # COMPOUND
                    self.position = {
                        "pid": PRODUCT, "entry": ep, "quote": tq, "hold": 0,
                        "target": ep * (1 + TP_PCT / 100.0),
                    }
                    self.cash -= tq
                    events.append({"ts_utc": utc_now_iso(), "action": "open", "entry": ep, "size": round(tq, 2), "m15_ranging": True})

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

def main():
    client = CoinbaseAdvancedClient()
    engine = RaveM15RangingShadow()
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
    print(f"Backfilling 72h data for {PRODUCT} M15 Ranging Champion...")
    try:
        # Fetch M5, M15, and BTC M1
        rave_m5 = fetch_candles_chunked(client, PRODUCT, start, now, "FIVE_MINUTE", event_logger=event_logger)
        rave_m15 = fetch_candles_chunked(client, PRODUCT, start, now, "FIFTEEN_MINUTE", event_logger=event_logger)
        btc_m1 = fetch_candles_chunked(client, BTC, start, now, "ONE_MINUTE", event_logger=event_logger)
        
        btc_lookup = {int(c["start"]): c for c in btc_m1}

        # Build M15 lookup
        m15_lookup = {int(c["start"]): c for c in rave_m15}

        # Backfill: process M5 candles, find corresponding M15
        print(f"  RAVE M5: {len(rave_m5)}, M15: {len(rave_m15)}, BTC M1: {len(btc_m1)}")
        
        m15_buffer = []
        for c in rave_m5:
            t = int(c["start"])
            # Find matching M15 candle (nearest)
            m15_t = t - (t % 900)  # Round down to nearest 15 min
            m15_c = m15_lookup.get(m15_t)
            if m15_c:
                m15_buffer.append(m15_c)
            
            engine.process_tick([c], [btc_lookup.get(t)], m15_buffer if m15_buffer else None)
            engine.last_candle_time[PRODUCT] = max(engine.last_candle_time[PRODUCT], t)
            engine.last_candle_time[BTC] = max(engine.last_candle_time[BTC], t)
            if m15_c:
                engine.last_candle_time[f"{PRODUCT}_M15"] = max(engine.last_candle_time[f"{PRODUCT}_M15"], m15_t)
    except Exception as e:
        runner["consecutive_exceptions"] += 1
        runner["last_exception_at"] = utc_now_iso()
        runner["last_exception_type"] = type(e).__name__
        runner["last_exception_message"] = str(e)
        save_state(STATE_PATH, engine, runner)
        print(f"Backfill error: {e}", flush=True)

    print(f"M15 RANGING CHAMPION LIVE: Net=${engine.realized_net:.2f} WR={engine.snapshot()['win_rate']}% "
          f"Cash=${engine.cash:.2f} {engine.closes} trades", flush=True)
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(STATE_PATH, engine, runner)
    try:
        while True:
            try:
                now_ts = int(time.time())
                
                # Fetch new M5 candles
                rave_start, rave_end, rave_filter_after = live_poll_window(engine.last_candle_time[PRODUCT], now_ts, 300)
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
                
                # Fetch new M15 candles
                m15_start, m15_end, m15_filter_after = live_poll_window(engine.last_candle_time[f"{PRODUCT}_M15"], now_ts, 900)
                m15_tick = fetch_live_candles(
                    client,
                    PRODUCT,
                    start=m15_start,
                    end=m15_end,
                    granularity="FIFTEEN_MINUTE",
                    filter_after=m15_filter_after,
                    event_logger=event_logger,
                )
                for c in m15_tick: engine.last_candle_time[f"{PRODUCT}_M15"] = max(engine.last_candle_time[f"{PRODUCT}_M15"], int(c["start"]))
                
                # Fetch new BTC M1 candles
                btc_start, btc_end, btc_filter_after = live_poll_window(engine.last_candle_time[BTC], now_ts, 60)
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

                if rave_tick or btc_tick or m15_tick:
                    events = engine.process_tick(rave_tick, btc_tick, m15_tick if m15_tick else None)
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
