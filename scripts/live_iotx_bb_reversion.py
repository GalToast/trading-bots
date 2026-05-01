#!/usr/bin/env python3
"""
IOTX BB Reversion Live Runner — Phase 1 of multi-coin expansion.

Strategy: Bollinger Band Reversion on IOTX-USD
- Entry: RSI(3) < 30 AND price within 3% of BB lower band
- TP: BB middle band (SMA20) — mean reversion target
- SL: 5% below entry
- Max hold: 24 bars (2 hours)
- Session gate: dead hours {0, 6, 12, 19 UTC}
- Compounding: full reinvestment

Backtest: 79.1% WR, $44/mo, 11.4% DD, RAR 3.86
"""
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "live_iotx_bb_reversion_state.json"
EVENT_PATH = ROOT / "reports" / "live_iotx_bb_reversion_events.jsonl"

PRODUCT = "IOTX-USD"
BTC = "BTC-USD"

# BB Reversion params
BB_PERIOD = 20
BB_STD_MULT = 2.0
RSI_PERIOD = 3
RSI_THRESH = 30
BB_PROXIMITY_PCT = 3.0  # Entry: price must be within 3% of BB lower band
SL_PCT = 5.0
MAX_HOLD = 24
SESSION_DEAD_HOURS = {0, 6, 12, 19}
CANDLE_BUFFER_SIZE = 50

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
            if not cands:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses_rsi = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses_rsi) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0

def compute_bb(closes, period=BB_PERIOD, std_mult=BB_STD_MULT):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    sma = sum(window) / period
    variance = sum((x - sma) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return sma, upper, lower

def get_fee_rate(total_volume):
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


class IotxBbReversionLive:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None  # {ep, q, hold, tp, sl, entry_fee}
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_volume = 0.0
        self.total_fees = 0.0
        self.history = []  # close prices
        self.candle_history = []
        self.btc_candle_history = []
        self.last_candle_time = 0
        self.session_filtered = 0
        self.bb_signals = 0
        self.execution_phase_counts = {"startup_backfill": 0, "live_forward": 0}
        self.started_at = utc_now_iso()
        self.last_heartbeat = utc_now_iso()

    def save_state(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": utc_now_iso(),
            "started_at": self.started_at,
            "state": {
                "cash": round(self.cash, 4),
                "starting_cash": self.starting_cash,
                "realized_net": round(self.realized_net, 4),
                "total_volume": round(self.total_volume, 4),
                "total_fees": round(self.total_fees, 4),
                "closes": self.closes,
                "wins": self.wins,
                "losses": self.losses,
                "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
                "position": self.position,
                "session_filtered": self.session_filtered,
                "bb_signals": self.bb_signals,
                "execution_phase_counts": dict(self.execution_phase_counts),
                "history_len": len(self.history),
                "candle_history_len": len(self.candle_history),
            },
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def process_candle(self, candle, btc_close, event_path: Path, *, phase: str,
                        btc_candle: dict | None = None):
        ts = int(candle["start"])
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        candle_open = float(candle["open"])
        previous_close = self.history[-1] if self.history else None

        self.history.append(close)
        if len(self.history) > 500:
            self.history = self.history[-500:]

        self.candle_history.append({
            "start": ts, "open": candle_open, "high": high,
            "low": low, "close": close, "volume": float(candle.get("volume", 0))
        })
        if len(self.candle_history) > CANDLE_BUFFER_SIZE:
            self.candle_history = self.candle_history[-CANDLE_BUFFER_SIZE:]

        if btc_candle is not None:
            self.btc_candle_history.append(btc_candle)
        elif btc_close > 0:
            self.btc_candle_history.append({
                "start": ts, "open": btc_close, "high": btc_close,
                "low": btc_close, "close": btc_close, "volume": 0
            })
        if len(self.btc_candle_history) > CANDLE_BUFFER_SIZE:
            self.btc_candle_history = self.btc_candle_history[-CANDLE_BUFFER_SIZE:]

        self.last_candle_time = ts
        self.execution_phase_counts[phase] = self.execution_phase_counts.get(phase, 0) + 1

        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in SESSION_DEAD_HOURS
        fr = get_fee_rate(self.total_volume)

        # EXIT
        if self.position:
            self.position["hold"] += 1
            exit_price = None
            exit_reason = None

            if high >= self.position["tp"]:
                exit_price = self.position["tp"]
                exit_reason = "tp"
            elif self.position["sl"] > 0 and low <= self.position["sl"]:
                exit_price = self.position["sl"]
                exit_reason = "sl"
            elif self.position["hold"] >= MAX_HOLD:
                exit_price = close
                exit_reason = "timeout"

            if exit_price is not None:
                units = self.position["units"]
                gross = (exit_price - self.position["ep"]) * units
                entry_fee = self.position["entry_fee"]
                exit_fee = exit_price * units * fr
                net = gross - entry_fee - exit_fee

                self.cash += self.position["q"] + net
                self.realized_net += net
                self.closes += 1
                self.total_volume += self.position["q"] + (exit_price * units)
                self.total_fees += entry_fee + exit_fee

                if net > 0:
                    self.wins += 1
                else:
                    self.losses += 1

                event = {
                    "ts_utc": utc_now_iso(),
                    "action": "close",
                    "phase": phase,
                    "exit_price": round(exit_price, 6),
                    "entry_price": self.position["ep"],
                    "net": round(net, 4),
                    "reason": exit_reason,
                    "hold_bars": self.position["hold"],
                    "fees": round(entry_fee + exit_fee, 4),
                    "tp": round(self.position["tp"], 6),
                    "sl": round(self.position.get("sl", 0), 6),
                }
                append_jsonl(event_path, event)
                self.position = None

        # ENTRY: BB Reversion signal
        if self.position is None and self.cash >= 10.0 and session_open:
            if len(self.history) >= BB_PERIOD + 2:
                sma, upper, lower = compute_bb(self.history, BB_PERIOD, BB_STD_MULT)
                rsi_val = compute_rsi(self.history[:-1], RSI_PERIOD)

                if sma is not None and lower is not None:
                    # Check: RSI oversold AND price near BB lower band
                    bb_proximity = (close - lower) / lower * 100 if lower > 0 else 999

                    if rsi_val <= RSI_THRESH and bb_proximity <= BB_PROXIMITY_PCT:
                        self.bb_signals += 1

                        deploy = self.cash
                        entry_fee = deploy * fr
                        units = (deploy - entry_fee) / candle_open
                        tp = sma  # Mean reversion to BB middle
                        sl = candle_open * (1 - SL_PCT / 100.0)

                        self.cash -= deploy
                        self.position = {
                            "ep": candle_open,
                            "q": deploy,
                            "hold": 0,
                            "tp": tp,
                            "sl": sl,
                            "units": units,
                            "entry_fee": entry_fee,
                        }

                        event = {
                            "ts_utc": utc_now_iso(),
                            "action": "open",
                            "phase": phase,
                            "entry_price": round(candle_open, 6),
                            "tp": round(tp, 6),
                            "sl": round(sl, 6),
                            "deploy": round(deploy, 4),
                            "rsi_at_entry": round(rsi_val, 2),
                            "bb_sma": round(sma, 6),
                            "bb_lower": round(lower, 6),
                            "bb_upper": round(upper, 6),
                            "bb_proximity_pct": round(bb_proximity, 2),
                            "fee_rate": fr,
                            "entry_bar_start": ts,
                            "entry_bar_close": round(close, 6),
                        }
                        append_jsonl(event_path, event)

    def snapshot(self):
        wr = self.wins / max(1, self.closes) * 100
        pos_value = self.position["q"] if self.position else 0
        net_pnl = self.cash + pos_value - self.starting_cash
        return {
            "cash": round(self.cash, 4),
            "realized_net": round(self.realized_net, 4),
            "total_pnl": round(net_pnl, 4),
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(wr, 2),
            "total_volume": round(self.total_volume, 4),
            "total_fees": round(self.total_fees, 4),
            "bb_signals": self.bb_signals,
            "session_filtered": self.session_filtered,
            "position": "active" if self.position else "flat",
        }


def main():
    client = CoinbaseAdvancedClient()
    engine = IotxBbReversionLive()

    # Backfill 72h
    now = int(time.time())
    start = now - 72 * 3600
    print(f"Backfilling 72h M5 candles for {PRODUCT}...", flush=True)

    candles = fetch_candles_chunked(client, PRODUCT, start, now)
    btc_candles = fetch_candles_chunked(client, BTC, start, now)
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_candles}
    btc_by_ts = {int(c["start"]): c for c in btc_candles}

    for c in candles:
        ts = int(c["start"])
        btc_close = btc_lookup.get(ts, 0)
        btc_c = btc_by_ts.get(ts)
        engine.process_candle(c, btc_close, EVENT_PATH, phase="startup_backfill", btc_candle=btc_c)

    snap = engine.snapshot()
    print(f"LIVE STARTED: cash=${snap['cash']:.2f} realized=${snap['realized_net']:.2f} "
          f"closes={snap['closes']} wr={snap['win_rate']}% pos={snap['position']}", flush=True)
    engine.save_state(STATE_PATH)

    # Live loop
    try:
        while True:
            try:
                end = int(time.time())
                if end <= engine.last_candle_time:
                    time.sleep(30)
                    continue

                resp = client.market_candles(PRODUCT, start=engine.last_candle_time, end=end, granularity="FIVE_MINUTE")
                new = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time]

                btc_resp = client.market_candles(BTC, start=engine.last_candle_time, end=end, granularity="FIVE_MINUTE")
                btc_new = btc_resp.get("candles", [])
                btc_close = float(btc_new[-1]["close"]) if btc_new else 0
                btc_by_ts_live = {int(c["start"]): c for c in btc_new}

                for c in new:
                    c_ts = int(c["start"])
                    btc_c = btc_by_ts_live.get(c_ts)
                    engine.process_candle(c, btc_close, EVENT_PATH, phase="live_forward", btc_candle=btc_c)

                snap = engine.snapshot()
                engine.last_heartbeat = utc_now_iso()
                engine.save_state(STATE_PATH)

                print(f"HB: cash=${snap['cash']:.2f} pnl=${snap['total_pnl']:.2f} "
                      f"closes={snap['closes']} wr={snap['win_rate']}% "
                      f"pos={snap['position']} signals={snap['bb_signals']} "
                      f"sess_filt={snap['session_filtered']}", flush=True)

            except Exception as e:
                print(f"EXC: {e}", flush=True)
                traceback.print_exc()
                engine.save_state(STATE_PATH)

            time.sleep(30)

    except KeyboardInterrupt:
        print("Shutting down...", flush=True)
        engine.save_state(STATE_PATH)
        return 0


if __name__ == "__main__":
    main()
