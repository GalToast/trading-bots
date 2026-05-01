#!/usr/bin/env python3
"""
RAVE RSI MR Live Shadow — Proper state management and event logging.
No ghost PIDs. Every fill logged. State saved every cycle.

Strategy: RSI(3) < 30 → buy, TP 25%, no SL, 48-bar max hold, Session Gate.
Shadow benchmark: $183/30d realistic, $321/30d with Session Gate.
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
from regime_detection import regime_score

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "rave_rsi_mr_live_v2_state.json"
EVENT_PATH = ROOT / "reports" / "rave_rsi_mr_live_v2_events.jsonl"

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"
RSI_PERIOD = 3
OS_THRESH = 30
TP_PCT = 25.0
MAX_HOLD = 48
SESSION_DEAD_HOURS = {0, 6, 12, 19}

# Regime gate thresholds
REGIME_SKIP = 40      # Score < 40 → skip entry (choppy)
REGIME_HALF = 70      # Score 40-70 → half size
# Score >= 70 → full size
CANDLE_BUFFER_SIZE = 50  # Full candles to keep for regime scoring

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
        except:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0

def get_fee_rate(total_volume):
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040

class RaveRsiMrLive:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None  # {ep, q, hold, tp, entry_fee}
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_volume = 0.0
        self.total_fees = 0.0
        self.history = []  # close prices only (for RSI)
        self.candle_history = []  # full candles (for regime detection)
        self.btc_candle_history = []  # BTC candles (for regime detection)
        self.last_candle_time = 0
        self.session_filtered = 0
        self.regime_filtered = 0
        self.regime_score_last = 0
        self.rsi_signals = 0
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
                "regime_filtered": self.regime_filtered,
                "regime_score_last": self.regime_score_last,
                "rsi_signals": self.rsi_signals,
                "execution_phase_counts": self.execution_phase_counts,
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
        previous_candle_start = self.last_candle_time or None
        previous_candle_close = self.history[-1] if self.history else None

        self.history.append(close)
        if len(self.history) > 500:
            self.history = self.history[-500:]

        # Track full candles for regime detection
        self.candle_history.append({
            "start": ts, "open": candle_open, "high": high,
            "low": low, "close": close, "volume": float(candle.get("volume", 0))
        })
        if len(self.candle_history) > CANDLE_BUFFER_SIZE:
            self.candle_history = self.candle_history[-CANDLE_BUFFER_SIZE:]

        # Track BTC candles for regime correlation
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
                    "bar_start": ts,
                    "bar_close": round(close, 6),
                }
                append_jsonl(event_path, event)
                self.position = None

        # ENTRY
        if self.position is None and self.cash >= 10.0 and session_open and len(self.history) >= RSI_PERIOD + 2:
            rsi_val = compute_rsi(self.history[:-1], RSI_PERIOD)

            if rsi_val <= OS_THRESH:
                self.rsi_signals += 1

                # Session gate check
                if not session_open:
                    self.session_filtered += 1
                    return

                # --- REGIME GATE ---
                r_score = 0
                deploy_fraction = 1.0
                if len(self.candle_history) >= 20:
                    try:
                        r_result = regime_score(
                            self.candle_history, self.btc_candle_history
                        )
                        r_score = r_result.get("score", 0)
                    except Exception:
                        r_score = 50  # Default to medium on error
                    self.regime_score_last = r_score

                    if r_score < REGIME_SKIP:
                        self.regime_filtered += 1
                        return  # Skip choppy regime entirely
                    elif r_score < REGIME_HALF:
                        deploy_fraction = 0.5  # Half size in medium regime
                # If < 20 candles in buffer, skip regime check (startup grace)

                deploy = self.cash * deploy_fraction
                entry_fee = deploy * fr
                units = (deploy - entry_fee) / candle_open
                tp = candle_open * (1 + TP_PCT / 100.0)

                self.cash -= deploy
                self.position = {
                    "ep": candle_open,
                    "q": deploy,
                    "hold": 0,
                    "tp": tp,
                    "units": units,
                    "entry_fee": entry_fee,
                }

                event = {
                    "ts_utc": utc_now_iso(),
                    "action": "open",
                    "phase": phase,
                    "entry_price": candle_open,
                    "tp": round(tp, 6),
                    "deploy": round(deploy, 4),
                    "deploy_fraction": deploy_fraction,
                    "regime_score": r_score,
                    "rsi_at_entry": round(rsi_val, 2),
                    "fee_rate": fr,
                    "entry_bar_start": ts,
                    "entry_bar_open": round(candle_open, 6),
                    "entry_bar_close": round(close, 6),
                    "signal_bar_start": previous_candle_start,
                    "signal_price": round(previous_candle_close, 6) if previous_candle_close is not None else None,
                    "signal_to_entry_gap_bps": round(((candle_open - previous_candle_close) / previous_candle_close) * 10000.0, 1)
                    if previous_candle_close not in (None, 0.0)
                    else None,
                }
                append_jsonl(event_path, event)

    def snapshot(self):
        wr = self.wins / max(1, self.closes) * 100
        net_pnl = self.cash + (self.position["q"] if self.position else 0) - self.starting_cash
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
            "rsi_signals": self.rsi_signals,
            "session_filtered": self.session_filtered,
            "regime_filtered": self.regime_filtered,
            "regime_score_last": self.regime_score_last,
            "position": "active" if self.position else "flat",
        }


def main():
    client = CoinbaseAdvancedClient()
    engine = RaveRsiMrLive()

    # Backfill 72h
    now = int(time.time())
    start = now - 72 * 3600
    print(f"Backfilling 72h M5 candles for {PRODUCT}...", flush=True)

    candles = fetch_candles_chunked(client, PRODUCT, start, now)
    btc_candles = fetch_candles_chunked(client, BTC, start, now)
    btc_lookup = {int(c["start"]): float(c["close"]) for c in btc_candles}

    # Build BTC candle lookup for passing full candle objects
    btc_by_ts = {int(c["start"]): c for c in btc_candles}

    for c in candles:
        ts = int(c["start"])
        btc_close = btc_lookup.get(ts, 0)
        btc_c = btc_by_ts.get(ts)
        engine.process_candle(c, btc_close, EVENT_PATH, phase="startup_backfill",
                              btc_candle=btc_c)

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
                    engine.process_candle(c, btc_close, EVENT_PATH, phase="live_forward",
                                          btc_candle=btc_c)

                snap = engine.snapshot()
                engine.last_heartbeat = utc_now_iso()
                engine.save_state(STATE_PATH)

                print(f"HB: cash=${snap['cash']:.2f} pnl=${snap['total_pnl']:.2f} "
                      f"closes={snap['closes']} wr={snap['win_rate']}% "
                      f"pos={snap['position']} signals={snap['rsi_signals']} "
                      f"sess_filt={snap['session_filtered']} regime_filt={snap['regime_filtered']} "
                      f"regime={snap['regime_score_last']}", flush=True)

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
