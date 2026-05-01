#!/usr/bin/env python3
"""
Multi-Coin Momentum Runner — Live deployment of all profitable momentum combos.

Runs 6 coins simultaneously with per-coin optimized params:
- RAVE:   lb=10, TP=10%, SL=10%  (highest throughput)
- CFG:    lb=50, TP=15%, SL=0%   (best risk-adjusted)
- BAL:    lb=50, TP=10%, SL=3%   (steady)
- IOTX:   lb=25, TP=10%, SL=0%   (moderate)
- ALEPH:  lb=50, TP=15%, SL=5%   (quality)
- BLUR:   lb=25, TP=12%, SL=7%   (supplemental)

Architecture:
- Single process, N coin lanes
- Shared bankroll (starting $48)
- Max 1 position per coin, unlimited concurrent across coins
- Min $10 per position
- State saved every cycle, events logged to JSONL

Usage:
    python scripts/multi_coin_momentum_runner.py
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
STATE_PATH = ROOT / "reports" / "multi_coin_momentum_state.json"
EVENT_PATH = ROOT / "reports" / "multi_coin_momentum_events.jsonl"

# CONSOLIDATED config — merged from both verifications (qwen-trading + qwen-trading-bots)
# NOM params from @qwen-trading-bots (MH=12, SL=8% — tighter, 16.7% DD)
# All other params from validated backfills
# IMPROVEMENT: Added min_breakout_pct to reduce false breakouts (Qwen proposal)
COIN_CONFIGS = [
    {"coin": "RAVE-USD",  "strategy": "momentum", "lookback": 15, "tp_pct": 0.10, "sl_pct": 0.00, "max_hold": 36, "min_breakout_pct": 0.005},
    {"coin": "MOG-USD",   "strategy": "rsi_mr",   "rsi_period": 4, "os_thresh": 45, "tp_pct": 0.075, "sl_pct": 0.005, "max_hold": 48},
    {"coin": "NOM-USD",   "strategy": "momentum", "lookback": 30, "tp_pct": 0.08, "sl_pct": 0.08, "max_hold": 12, "min_breakout_pct": 0.005},
    {"coin": "GHST-USD",  "strategy": "momentum", "lookback": 20, "tp_pct": 0.15, "sl_pct": 0.03, "max_hold": 24, "min_breakout_pct": 0.005},
    {"coin": "TRU-USD",   "strategy": "momentum", "lookback": 10, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 24, "min_breakout_pct": 0.005},
    {"coin": "A8-USD",    "strategy": "momentum", "lookback": 10, "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48, "min_breakout_pct": 0.005},
    {"coin": "SUP-USD",   "strategy": "momentum", "lookback": 10, "tp_pct": 0.10, "sl_pct": 0.05, "max_hold": 24, "min_breakout_pct": 0.005},
    {"coin": "IOTX-USD",  "strategy": "momentum", "lookback": 20, "tp_pct": 0.05, "sl_pct": 0.03, "max_hold": 24, "min_breakout_pct": 0.005},
    {"coin": "CFG-USD",   "strategy": "momentum", "lookback": 50, "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48, "min_breakout_pct": 0.005},
    {"coin": "BAL-USD",   "strategy": "momentum", "lookback": 50, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 36, "min_breakout_pct": 0.005},
]

MIN_CASH_PER_POSITION = 10.0
DEPLOY_FRACTION = 0.95  # Deploy 95% of available cash per trade
FETCH_LOOKBACK_MINUTES = 120  # Fetch 2h of candles for history


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    """Fetch candles with chunking."""
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


def get_fee_rate(total_volume):
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


def compute_rsi(closes: list[float], period: int = 3) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


class CoinLane:
    """Manages one coin's strategy (Momentum or RSI MR)."""

    def __init__(self, cfg):
        self.coin = cfg["coin"]
        self.strategy = cfg["strategy"]
        self.lookback = cfg.get("lookback", 10)
        self.rsi_period = cfg.get("rsi_period", 3)
        self.os_thresh = cfg.get("os_thresh", 30)
        self.tp_pct = cfg["tp_pct"]
        self.sl_pct = cfg["sl_pct"]
        self.max_hold = cfg["max_hold"]
        self.min_breakout_pct = cfg.get("min_breakout_pct", 0.0)  # Minimum breakout % above recent high
        self.position = None
        self.history = []  # close prices
        self.candle_history = []  # full candles
        self.last_candle_time = 0
        self.signals = 0
        self.closes = 0
        self.wins = 0
        self.losses = 0

    def process_candles(self, candles, cash, total_volume, *, backfill=False):
        """Process new candles, check for entries/exits. Returns list of events.
        
        If backfill=True, only builds history and processes exits — no new entries.
        """
        events = []
        fee_rate = get_fee_rate(total_volume)

        for candle in candles:
            ts = int(candle["start"])
            close = float(candle["close"])
            high = float(candle["high"])
            low = float(candle["low"])
            open_price = float(candle["open"])

            self.history.append(close)
            self.candle_history.append(candle)
            if len(self.history) > 500:
                self.history = self.history[-500:]
            self.last_candle_time = ts

            # EXIT
            if self.position:
                self.position["hold"] += 1
                exit_price = None
                exit_reason = None

                if high >= self.position["tp"]:
                    exit_price = self.position["tp"]
                    exit_reason = "tp"
                elif self.sl_pct > 0 and low <= self.position["sl"]:
                    exit_price = self.position["sl"]
                    exit_reason = "stop"
                elif self.position["hold"] >= self.max_hold:
                    exit_price = close
                    exit_reason = "timeout"

                if exit_price is not None:
                    units = self.position["units"]
                    gross = (exit_price - self.position["ep"]) * units
                    entry_fee = self.position["entry_fee"]
                    exit_fee = exit_price * units * fee_rate
                    net = gross - entry_fee - exit_fee

                    cash += self.position["q"] + net
                    self.closes += 1
                    if net > 0:
                        self.wins += 1
                    else:
                        self.losses += 1

                    event = {
                        "ts_utc": utc_now_iso(),
                        "coin": self.coin,
                        "action": "close",
                        "exit_price": round(exit_price, 6),
                        "entry_price": self.position["ep"],
                        "net": round(net, 4),
                        "reason": exit_reason,
                        "hold_bars": self.position["hold"],
                        "fees": round(entry_fee + exit_fee, 4),
                    }
                    events.append(event)
                    self.position = None

            # ENTRY (skip during backfill — just build history)
            if not backfill and self.position is None and cash >= MIN_CASH_PER_POSITION:
                signal_fired = False
                
                if self.strategy == "momentum":
                    if len(self.candle_history) > self.lookback + 1:
                        recent_high = max(float(c["high"]) for c in self.candle_history[-(self.lookback+1):-1])
                        breakout_pct = (high - recent_high) / recent_high if recent_high > 0 else 0
                        
                        # IMPROVEMENT: Require minimum breakout above recent high
                        # Reduces false breakouts and whipsaw entries
                        # Default 0.5% — price must exceed high by at least this margin
                        if high > recent_high and breakout_pct >= self.min_breakout_pct:
                            signal_fired = True
                
                elif self.strategy == "rsi_mr":
                    if len(self.history) > self.rsi_period + 1:
                        rsi_val = compute_rsi(self.history[:-1], self.rsi_period)
                        if rsi_val <= self.os_thresh:
                            signal_fired = True
                
                if signal_fired:
                    self.signals += 1

                    deploy = cash * DEPLOY_FRACTION
                    entry_price = open_price

                    # Guard against zero or invalid prices
                    if entry_price <= 0:
                        continue

                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / entry_price

                    tp = entry_price * (1 + self.tp_pct)
                    sl = entry_price * (1 - self.sl_pct) if self.sl_pct > 0 else 0

                    cash -= deploy
                    self.position = {
                        "ep": entry_price,
                        "q": deploy,
                        "units": units,
                        "tp": tp,
                        "sl": sl,
                        "hold": 0,
                        "entry_fee": entry_fee,
                    }

                    event = {
                        "ts_utc": utc_now_iso(),
                        "coin": self.coin,
                        "strategy": self.strategy,
                        "action": "open",
                        "entry_price": round(entry_price, 6),
                        "tp": round(tp, 6),
                        "sl": round(sl, 6),
                        "deploy": round(deploy, 4),
                        "entry_bar_start": ts,
                    }
                    if self.strategy == "momentum":
                        event["lookback"] = self.lookback
                        event["recent_high"] = round(recent_high, 6)
                    elif self.strategy == "rsi_mr":
                        event["rsi_period"] = self.rsi_period
                        event["os_thresh"] = self.os_thresh
                        event["rsi_val_at_entry"] = round(rsi_val, 2)
                        
                    events.append(event)

        return events, cash

    def snapshot(self):
        wr = self.wins / max(1, self.closes) * 100
        return {
            "signals": self.signals,
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(wr, 1),
            "position": "active" if self.position else "flat",
            "position_entry": round(self.position["ep"], 6) if self.position else None,
            "position_hold": self.position["hold"] if self.position else None,
        }


def main():
    client = CoinbaseAdvancedClient()

    # Initialize lanes
    lanes = {}
    for cfg in COIN_CONFIGS:
        lane = CoinLane(cfg)
        lanes[cfg["coin"]] = lane

    cash = 48.0
    starting_cash = cash
    total_volume = 0.0
    total_fees = 0.0
    cycle = 0

    # Backfill: fetch initial candles for all coins
    now = int(time.time())
    start = now - FETCH_LOOKBACK_MINUTES * 60
    print(f"=" * 70, flush=True)
    print(f"  MULTI-COIN UNIFIED RUNNER — LIVE", flush=True)
    print(f"  Coins: {', '.join(c['coin'] for c in COIN_CONFIGS)}", flush=True)
    print(f"  Starting cash: ${starting_cash:.2f}", flush=True)
    print(f"=" * 70, flush=True)

    print(f"\nBackfilling {FETCH_LOOKBACK_MINUTES}min of history (building history, no trades)...", flush=True)
    for cfg in COIN_CONFIGS:
        coin = cfg["coin"]
        candles = fetch_candles(client, coin, start, now)
        if candles:
            events, cash = lanes[coin].process_candles(candles, cash, total_volume, backfill=True)
            print(f"  {coin}: {len(candles)} candles, {len(events)} exits", flush=True)

    # Log initial state
    initial_event = {
        "ts_utc": utc_now_iso(),
        "action": "runner_start",
        "cash": round(cash, 4),
        "coins": [c["coin"] for c in COIN_CONFIGS],
    }
    append_jsonl(EVENT_PATH, initial_event)

    print(f"\nLIVE STARTED: cash=${cash:.2f}", flush=True)

    # Live loop
    try:
        while True:
            cycle += 1
            try:
                now = int(time.time())

                # Fetch new candles for all coins
                all_events = []
                for cfg in COIN_CONFIGS:
                    coin = cfg["coin"]
                    lane = lanes[coin]
                    start_fetch = lane.last_candle_time or (now - 600)

                    try:
                        candles = fetch_candles(client, coin, start_fetch, now)
                        new_candles = [c for c in candles if int(c["start"]) > lane.last_candle_time]

                        if new_candles:
                            events, cash = lane.process_candles(new_candles, cash, total_volume)
                            all_events.extend(events)

                    except Exception as e:
                        print(f"  EXC fetching {coin}: {e}", flush=True)

                # Log events
                for evt in all_events:
                    append_jsonl(EVENT_PATH, evt)
                    if evt["action"] == "close":
                        total_volume += evt.get("fees", 0)  # approximate

                # Calculate total equity (cash + all position values)
                position_value = sum(
                    lane.position["q"] for lane in lanes.values() if lane.position
                )
                total_equity = cash + position_value
                total_pnl = total_equity - starting_cash
                return_pct = total_pnl / starting_cash * 100

                # Save state
                state = {
                    "updated_at": utc_now_iso(),
                    "cycle": cycle,
                    "cash": round(cash, 4),
                    "position_value": round(position_value, 4),
                    "total_equity": round(total_equity, 4),
                    "total_pnl": round(total_pnl, 4),
                    "return_pct": round(return_pct, 1),
                    "coins": {
                        coin: lane.snapshot() for coin, lane in lanes.items()
                    },
                }
                STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

                # Print heartbeat
                active_positions = sum(1 for l in lanes.values() if l.position)
                total_signals = sum(l.signals for l in lanes.values())
                total_closes = sum(l.closes for l in lanes.values())
                total_wins = sum(l.wins for l in lanes.values())
                wr = total_wins / max(1, total_closes) * 100

                print(
                    f"HB#{cycle}: equity=${total_equity:.2f} pnl=${total_pnl:.2f} "
                    f"({return_pct:+.1f}%) | cash=${cash:.2f} | "
                    f"pos={active_positions}/{len(lanes)} | "
                    f"signals={total_signals} closes={total_closes} wr={wr:.1f}%",
                    flush=True
                )

            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"EXC in cycle {cycle}: {e}", flush=True)
                traceback.print_exc()

            time.sleep(30)  # Check every 30 seconds

    except KeyboardInterrupt:
        print(f"\nShutting down after {cycle} cycles...", flush=True)
        # Final state save
        position_value = sum(lane.position["q"] for lane in lanes.values() if lane.position)
        final_event = {
            "ts_utc": utc_now_iso(),
            "action": "runner_stop",
            "cash": round(cash, 4),
            "position_value": round(position_value, 4),
            "total_equity": round(cash + position_value, 4),
            "cycle": cycle,
        }
        append_jsonl(EVENT_PATH, final_event)
        print("State saved. Done. 🎯", flush=True)


if __name__ == "__main__":
    main()
