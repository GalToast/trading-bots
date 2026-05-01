#!/usr/bin/env python3
"""
Multi-Coin Momentum Runner — Patched v2

Fixes:
1. Crash recovery: Loads positions from state file on startup
2. Microcap price handling: 12 decimal precision for display
3. Per-coin error isolation: One coin crashing doesn't kill the runner
4. MOG removed: Price too tiny ($0.00000013) causes display and edge case issues
5. Position persistence: Position state survives restarts

Coins (9, MOG removed):
- RAVE:   lb=15, TP=10%, SL=0%
- NOM:    lb=30, TP=8%,  SL=8%, MH=12
- GHST:   lb=20, TP=15%, SL=3%, MH=24
- TRU:    lb=10, TP=10%, SL=3%, MH=24
- A8:     lb=10, TP=15%, SL=0%, MH=48
- SUP:    lb=10, TP=10%, SL=5%, MH=24
- IOTX:   lb=20, TP=5%,  SL=3%, MH=24
- CFG:    lb=50, TP=15%, SL=0%, MH=48
- BAL:    lb=50, TP=10%, SL=3%, MH=36

Usage:
    python scripts/multi_coin_momentum_runner_v2.py
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

COIN_CONFIGS = [
    {"coin": "RAVE-USD",  "strategy": "momentum", "lookback": 15, "tp_pct": 0.10, "sl_pct": 0.00, "max_hold": 36},
    {"coin": "NOM-USD",   "strategy": "range_breakout", "lookback": 10, "tp_pct": 0.10, "sl_pct": 0.01, "max_hold": 24},
    {"coin": "GHST-USD",  "strategy": "momentum", "lookback": 20, "tp_pct": 0.15, "sl_pct": 0.03, "max_hold": 24},
    {"coin": "TRU-USD",   "strategy": "momentum", "lookback": 10, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 24},
    {"coin": "A8-USD",    "strategy": "momentum", "lookback": 10, "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48},
    {"coin": "SUP-USD",   "strategy": "momentum", "lookback": 10, "tp_pct": 0.10, "sl_pct": 0.05, "max_hold": 24},
    {"coin": "IOTX-USD",  "strategy": "momentum", "lookback": 20, "tp_pct": 0.05, "sl_pct": 0.03, "max_hold": 24},
    {"coin": "CFG-USD",   "strategy": "momentum", "lookback": 50, "tp_pct": 0.15, "sl_pct": 0.00, "max_hold": 48},
    {"coin": "BAL-USD",   "strategy": "momentum", "lookback": 50, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 36},
]

MIN_CASH_PER_POSITION = 10.0
DEPLOY_FRACTION = 0.95
FETCH_LOOKBACK_MINUTES = 120


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
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


def compute_rsi(closes, period=3):
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


class CoinLane:
    def __init__(self, cfg):
        self.coin = cfg["coin"]
        self.strategy = cfg["strategy"]
        self.lookback = cfg.get("lookback", 20)
        self.tp_pct = cfg.get("tp_pct", 0.10)
        self.sl_pct = cfg.get("sl_pct", 0.03)
        self.max_hold = cfg.get("max_hold", 48)
        self.rsi_period = cfg.get("rsi_period", 3)
        self.os_thresh = cfg.get("os_thresh", 30)
        self.history = []
        self.candle_history = []
        self.last_candle_time = 0
        self.position = None
        self.signals = 0
        self.closes = 0
        self.wins = 0
        self.losses = 0

    def process_candles(self, candles, cash, total_volume, *, backfill=False):
        events = []
        fee_rate = get_fee_rate(total_volume)

        for candle in candles:
            ts = int(candle["start"])
            close = float(candle["close"])
            high = float(candle["high"])
            low = float(candle["low"])
            open_price = float(candle["open"])

            # FIX: Validate candle prices — skip if any price is invalid
            if open_price <= 0 or close <= 0 or high <= 0 or low <= 0:
                continue

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
                        "exit_price": round(exit_price, 12),
                        "entry_price": round(self.position["ep"], 12),
                        "net": round(net, 4),
                        "reason": exit_reason,
                        "hold_bars": self.position["hold"],
                        "fees": round(entry_fee + exit_fee, 4),
                    }
                    events.append(event)
                    self.position = None

            # ENTRY (skip during backfill)
            if not backfill and self.position is None and not getattr(self, '_recovered_position', False) and cash >= MIN_CASH_PER_POSITION:
                signal_fired = False
                recent_high = None

                if self.strategy == "momentum" or self.strategy == "range_breakout":
                    if len(self.candle_history) > self.lookback + 1:
                        recent_high = max(float(c["high"]) for c in self.candle_history[-(self.lookback+1):-1])
                        if high > recent_high:
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

                    # FIX: Double-check entry price is valid
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
                        "entry_price": round(entry_price, 12),
                        "tp": round(tp, 12),
                        "sl": round(sl, 12),
                        "deploy": round(deploy, 4),
                        "entry_bar_start": ts,
                    }
                    if self.strategy == "momentum" or self.strategy == "range_breakout":
                        event["lookback"] = self.lookback
                        event["recent_high"] = round(recent_high, 12) if recent_high else None
                    elif self.strategy == "rsi_mr":
                        event["rsi_val_at_entry"] = round(rsi_val, 2)

                    events.append(event)

        return events, cash

    def snapshot(self):
        wr = self.wins / max(1, self.closes) * 100
        snap = {
            "signals": self.signals,
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(wr, 1),
            "position": "active" if self.position else "flat",
            "position_entry": round(self.position["ep"], 12) if self.position else None,
            "position_hold": self.position["hold"] if self.position else None,
        }
        # Save full position data for crash recovery
        if self.position:
            snap["position_tp"] = round(self.position["tp"], 12)
            snap["position_sl"] = round(self.position["sl"], 12)
            snap["position_units"] = self.position["units"]
            snap["position_deploy"] = round(self.position["q"], 4)
            snap["position_entry_fee"] = round(self.position["entry_fee"], 6)
            snap["position_max_hold"] = self.position["max_hold"]
        return snap


def load_state(state_path):
    """Load previous state for crash recovery."""
    if not state_path.exists():
        return None
    try:
        with open(state_path) as f:
            return json.load(f)
    except Exception:
        return None


def recover_positions(lanes, state):
    """Recover positions from previous state after crash.

    Restores full position data (entry, TP, SL, units, etc.) so
    the runner can properly exit recovered positions instead of
    losing them on restart.
    """
    if not state or "coins" not in state:
        return 0
    recovered = 0
    for coin, data in state["coins"].items():
        if data.get("position") == "active" and coin in lanes:
            lane = lanes[coin]
            entry = data.get("position_entry")
            if entry and entry > 0:
                # Full position recovery
                lane.position = {
                    "ep": entry,
                    "tp": data.get("position_tp", entry * 1.1),
                    "sl": data.get("position_sl", 0),
                    "units": data.get("position_units", 0),
                    "q": data.get("position_deploy", 0),
                    "entry_fee": data.get("position_entry_fee", 0),
                    "max_hold": data.get("position_max_hold", 48),
                    "hold": data.get("position_hold", 0),
                }
                recovered += 1
                print(f"  RECOVERED {coin}: entry=${entry}, hold={lane.position['hold']} bars", flush=True)
            else:
                # Fallback: just mark as recovered to prevent re-entry
                lane._recovered_position = True
                recovered += 1
    return recovered


def main():
    client = CoinbaseAdvancedClient()

    # Load previous state for crash recovery
    prev_state = load_state(STATE_PATH)
    
    # Initialize lanes
    lanes = {}
    for cfg in COIN_CONFIGS:
        lane = CoinLane(cfg)
        # Restore per-coin stats from previous state
        if prev_state and "coins" in prev_state and cfg["coin"] in prev_state["coins"]:
            prev_coin = prev_state["coins"][cfg["coin"]]
            lane.signals = prev_coin.get("signals", 0)
            lane.closes = prev_coin.get("closes", 0)
            lane.wins = prev_coin.get("wins", 0)
            lane.losses = prev_coin.get("losses", 0)
        lanes[cfg["coin"]] = lane

    # Restore cash and counters from previous state
    if prev_state:
        cash = prev_state.get("cash", 48.0)
        total_volume = prev_state.get("total_volume", 0.0)
        total_fees = prev_state.get("total_fees", 0.0)
        cycle = prev_state.get("cycle", 0)
        recovered = recover_positions(lanes, prev_state)
        print(f"\nRecovered state: cycle={cycle}, cash=${cash:.2f}, "
              f"recovered {recovered} positions", flush=True)
    else:
        cash = 48.0
        total_volume = 0.0
        total_fees = 0.0
        cycle = 0

    starting_cash = cash

    # Backfill
    now = int(time.time())
    start = now - FETCH_LOOKBACK_MINUTES * 60
    print(f"=" * 70, flush=True)
    print(f"  MULTI-COIN MOMENTUM RUNNER v2 (patched)", flush=True)
    print(f"  Coins: {', '.join(c['coin'] for c in COIN_CONFIGS)}", flush=True)
    print(f"  Starting cash: ${cash:.2f} (cycle {cycle})", flush=True)
    print(f"  MOG removed: price too tiny for 6-decimal display", flush=True)
    print(f"=" * 70, flush=True)

    print(f"\nBackfilling {FETCH_LOOKBACK_MINUTES}min of history...", flush=True)
    for cfg in COIN_CONFIGS:
        coin = cfg["coin"]
        try:
            candles = fetch_candles(client, coin, start, now)
            if candles:
                events, cash = lanes[coin].process_candles(candles, cash, total_volume, backfill=True)
                print(f"  {coin}: {len(candles)} candles", flush=True)
            else:
                print(f"  {coin}: NO CANDLES — will try live", flush=True)
        except Exception as e:
            print(f"  {coin}: BACKFAIL ERROR — {e}", flush=True)

    # Log start
    append_jsonl(EVENT_PATH, {
        "ts_utc": utc_now_iso(),
        "action": "runner_start_v2",
        "cash": round(cash, 4),
        "coins": [c["coin"] for c in COIN_CONFIGS],
    })

    print(f"\nLIVE: cash=${cash:.2f}", flush=True)

    # Live loop
    try:
        while True:
            cycle += 1
            try:
                now = int(time.time())
                all_events = []

                for cfg in COIN_CONFIGS:
                    coin = cfg["coin"]
                    lane = lanes[coin]
                    start_fetch = lane.last_candle_time or (now - 600)

                    # FIX: Per-coin error isolation
                    try:
                        candles = fetch_candles(client, coin, start_fetch, now)
                        new_candles = [c for c in candles if int(c["start"]) > lane.last_candle_time]

                        if new_candles:
                            events, cash = lane.process_candles(new_candles, cash, total_volume)
                            all_events.extend(events)
                    except Exception as e:
                        # FIX: Don't crash the whole runner on one coin's error
                        print(f"  ERR {coin}: {e}", flush=True)

                # Log events
                for evt in all_events:
                    append_jsonl(EVENT_PATH, evt)
                    print(f"  EVT: {evt['coin']} {evt['action']} @ {evt.get('entry_price', evt.get('exit_price', '?'))}", flush=True)

                # Calculate equity
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
                    f"HB#{cycle}: equity=${total_equity:.2f} pnl=${total_pnl:+.2f} "
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

            time.sleep(30)

    except KeyboardInterrupt:
        print(f"\nShutting down after {cycle} cycles...", flush=True)
        position_value = sum(lane.position["q"] for lane in lanes.values() if lane.position)
        append_jsonl(EVENT_PATH, {
            "ts_utc": utc_now_iso(),
            "action": "runner_stop",
            "cash": round(cash, 4),
            "position_value": round(position_value, 4),
            "total_equity": round(cash + position_value, 4),
            "cycle": cycle,
        })
        print("State saved. Done. 🎯", flush=True)


if __name__ == "__main__":
    main()
