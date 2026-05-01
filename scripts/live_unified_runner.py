#!/usr/bin/env python3
"""
Unified Live Runner — RSI Mean Reversion + Momentum Breakout with Wick-Trap Filter.
Two independent edges on RAVE-USD, shared bankroll.

Edge 1: RSI Mean Reversion — RSI(3)<30, TP25%, no SL, no timeout
Edge 2: Momentum Breakout — HIGH breaks 5-bar high, TP10%, SL7%, H50, max breakout 1%

Only ONE position at a time (shared $48 bankroll). Whichever edge fires first gets the capital.
"""
import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "unified_runner_state.json"
EVENT_PATH = ROOT / "reports" / "unified_runner_events.jsonl"

PRODUCT = "RAVE-USD"

# Edge 1: RSI Mean Reversion
RSI_PERIOD = 3
RSI_OS = 30
RSI_TP = 25.0

# Edge 2: Momentum Breakout with Wick-Trap Filter
MB_LOOKBACK = 5
MB_TP = 10.0
MB_SL = 7.0
MB_MAX_HOLD = 50
MB_MAX_MAGNITUDE_PCT = 1.0  # Filter out breakouts >1% (wick-trap)

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

def fetch_candles_chunked(client, pid, start, end, granularity="ONE_MINUTE"):
    chunk_sec = 300 * 60
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
            time.sleep(0.15)
        except:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0

class UnifiedRunner:
    def __init__(self, starting_cash=48.0):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.position = None  # {"edge": "rsi_mr"|"mb", "ep": ..., "tp": ..., "sl": ..., "hold": 0, ...}
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.total_volume = 0.0
        self.total_fees = 0.0
        self.close_history = []
        self.high_history = []
        self.last_candle_time = 0
        self.rsi_mr_signals = 0
        self.mb_signals = 0
        self.mb_wick_traps_skipped = 0

    def get_fee_rate(self):
        if self.total_volume >= 50000: return 0.0015
        elif self.total_volume >= 10000: return 0.0025
        else: return 0.0040

    def process_tick(self, candles):
        events = []
        fee_rate = self.get_fee_rate()

        for c in candles:
            ts = int(c["start"])
            h = float(c["high"])
            l = float(c["low"])
            cl = float(c["close"])
            o = float(c["open"])

            self.close_history.append(cl)
            self.high_history.append(h)
            if len(self.close_history) > 200:
                self.close_history.pop(0)
                self.high_history.pop(0)
            self.last_candle_time = max(self.last_candle_time, ts)

            # EXIT
            if self.position:
                self.position["hold"] += 1
                exit_p = None
                exit_reason = None

                if h >= self.position["tp"]:
                    exit_p = self.position["tp"]; exit_reason = "tp"
                elif l <= self.position["sl"]:
                    exit_p = self.position["sl"]; exit_reason = "sl"
                elif self.position["hold"] >= self.position["max_hold"]:
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
                    self.total_fees += ef + xf
                    if exit_p > self.position["ep"]: self.wins += 1

                    events.append({
                        "ts_utc": utc_now_iso(), "action": "close",
                        "edge": self.position["edge"], "exit": exit_p,
                        "net": round(net, 4), "reason": exit_reason,
                    })
                    self.position = None

            # ENTRY — try both edges, pick whichever fires
            if self.position is None and self.cash >= 10.0:
                # Edge 1: RSI Mean Reversion
                if len(self.close_history) >= RSI_PERIOD + 2:
                    rsi = compute_rsi(self.close_history[:-1])
                    if rsi <= RSI_OS:
                        ep = o
                        deploy = self.cash * 0.95
                        entry_fee = deploy * fee_rate
                        units = (deploy - entry_fee) / ep
                        if units > 0:
                            self.cash -= deploy
                            self.position = {
                                "edge": "rsi_mr", "ep": ep, "deploy": deploy,
                                "units": units, "hold": 0,
                                "tp": ep * (1 + RSI_TP / 100.0),
                                "sl": 0,  # No SL for RSI MR
                                "max_hold": None,  # No timeout
                                "entry_fee": entry_fee,
                            }
                            self.rsi_mr_signals += 1
                            events.append({"ts_utc": utc_now_iso(), "action": "open",
                                           "edge": "rsi_mr", "entry": ep, "rsi": round(rsi, 1),
                                           "size": round(deploy, 2)})
                            continue  # Don't try MB if RSI MR fired

                # Edge 2: Momentum Breakout with Wick-Trap Filter
                if len(self.high_history) >= MB_LOOKBACK + 2:
                    recent_high = max(self.high_history[-MB_LOOKBACK-1:-1])
                    if h > recent_high:
                        breakout_magnitude = (h - recent_high) / recent_high * 100
                        if breakout_magnitude <= MB_MAX_MAGNITUDE_PCT:
                            # Wick-trap filter passed — enter mid-breakout
                            estimated_fill = recent_high + (h - recent_high) * 0.5
                            deploy = self.cash * 0.95
                            entry_fee = deploy * fee_rate
                            units = (deploy - entry_fee) / estimated_fill
                            if units > 0:
                                self.cash -= deploy
                                self.position = {
                                    "edge": "momentum_breakout", "ep": estimated_fill,
                                    "deploy": deploy, "units": units, "hold": 0,
                                    "tp": estimated_fill * (1 + MB_TP / 100.0),
                                    "sl": estimated_fill * (1 - MB_SL / 100.0),
                                    "max_hold": MB_MAX_HOLD,
                                    "entry_fee": entry_fee,
                                }
                                self.mb_signals += 1
                                events.append({"ts_utc": utc_now_iso(), "action": "open",
                                               "edge": "momentum_breakout", "entry": round(estimated_fill, 6),
                                               "breakout_magnitude": round(breakout_magnitude, 2),
                                               "size": round(deploy, 2)})
                        else:
                            self.mb_wick_traps_skipped += 1

        return events

    def snapshot(self):
        return {
            "cash": round(self.cash, 4), "realized_net": round(self.realized_net, 4),
            "closes": self.closes, "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "total_volume": round(self.total_volume, 4),
            "pos": self.position["edge"] if self.position else "flat",
            "rsi_mr_signals": self.rsi_mr_signals,
            "mb_signals": self.mb_signals,
            "mb_wick_traps_skipped": self.mb_wick_traps_skipped,
        }

def main():
    client = CoinbaseAdvancedClient()
    engine = UnifiedRunner()

    now = int(time.time()); start = now - 72 * 3600
    print(f"Backfilling 72h for Unified Runner on {PRODUCT}...")
    candles = fetch_candles_chunked(client, PRODUCT, start, now, "ONE_MINUTE")

    for c in candles:
        engine.process_tick([c])

    snap = engine.snapshot()
    print(f"UNIFIED RUNNER LIVE: Net=${snap['realized_net']:.2f} WR={snap['win_rate']}% "
          f"Cash=${snap['cash']:.2f} {snap['closes']} trades "
          f"RSI_MR={snap['rsi_mr_signals']} MB={snap['mb_signals']} WICK_SKIP={snap['mb_wick_traps_skipped']}",
          flush=True)

    try:
        while True:
            try:
                end = int(time.time())
                resp = client.market_candles(PRODUCT, start=engine.last_candle_time, end=end, granularity="ONE_MINUTE")
                new = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time]
                if new:
                    events = engine.process_tick(new)
                    for ev in events: append_jsonl(EVENT_PATH, ev)
                snap = engine.snapshot()
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net']:.2f} "
                      f"{snap['closes']}c {snap['win_rate']}%WR pos={snap['pos']} "
                      f"RSI={snap['rsi_mr_signals']} MB={snap['mb_signals']} SKIP={snap['mb_wick_traps_skipped']}",
                      flush=True)
            except Exception as e: print(f"  EXC: {e}", flush=True)
            time.sleep(30)
    except KeyboardInterrupt: return 0

if __name__ == "__main__": main()
