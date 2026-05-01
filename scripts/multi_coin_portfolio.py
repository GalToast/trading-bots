#!/usr/bin/env python3
"""
Multi-Coin Portfolio Orchestrator — LIVE TRADING

Runs all proven strategies in ONE process:
1. RAVE Momentum (10-bar breakout, 10% TP/SL)
2. NOM Range Breakout (10-bar range breakout, 10% TP, 1% SL)
3. SUP Range Breakout (8-bar range breakout, 8% TP, 1% SL)
4. BAL Range Breakout (50-bar range breakout, 10% TP, 3% SL)

Backtest projection: $913/month on $500 (182.7% return).
One state file, one event log, one heartbeat. Auto-reconnect.

Usage:
    python scripts/multi_coin_portfolio.py --starting-cash 500
    python scripts/multi_coin_portfolio.py --starting-cash 50 --coins RAVE-USD
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "multi_coin_portfolio_state.json"
EVENT_PATH = ROOT / "reports" / "multi_coin_portfolio_events.jsonl"

BTC = "BTC-USD"
SESSION_DEAD_HOURS = {0, 6, 12, 19}
CANDLE_BUFFER = 200  # Keep last N candles per coin

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def default_output_paths(coins: list[str] | None = None) -> tuple[Path, Path]:
    if not coins:
        return STATE_PATH, EVENT_PATH
    normalized = [str(coin).replace("-", "").replace("_", "").lower() for coin in coins if coin]
    if len(normalized) == 1:
        slug = normalized[0]
        return (
            ROOT / "reports" / f"multi_coin_portfolio_{slug}_state.json",
            ROOT / "reports" / f"multi_coin_portfolio_{slug}_events.jsonl",
        )
    return STATE_PATH, EVENT_PATH

def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

def compute_rsi(closes, period=3):
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

def compute_bb(closes, period=20, std_mult=2.0):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    sma = sum(window) / period
    variance = sum((x - sma) ** 2 for x in window) / period
    std = variance ** 0.5
    return sma, sma + std_mult * std, sma - std_mult * std

def get_fee_rate(total_volume):
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


# ---- Strategy Definitions ----

def momentum_signal(candles, idx, lookback):
    """Price breaks above highest high of last `lookback` bars."""
    if idx < lookback:
        return False
    current_high = candles[idx]["high"]
    highest = max(candles[j]["high"] for j in range(idx - lookback, idx))
    return current_high > highest

def range_breakout_signal(candles, idx, range_lookback):
    """Price breaks above the recent range high using range_breakout semantics."""
    if idx < range_lookback:
        return False
    current_high = candles[idx]["high"]
    highest = max(candles[j]["high"] for j in range(idx - range_lookback, idx))
    return current_high > highest

def bb_reversion_signal(candles, idx, rsi_period=3, rsi_thresh=30, bb_period=20, proximity_pct=3.0):
    """RSI oversold AND price near BB lower band."""
    if idx < bb_period + 2:
        return False
    closes = [float(candles[j]["close"]) for j in range(idx + 1)]
    rsi = compute_rsi(closes, rsi_period)
    _, _, lower = compute_bb(closes, bb_period)
    if lower is None:
        return False
    current_price = float(candles[idx]["close"])
    proximity = (current_price - lower) / lower * 100 if lower > 0 else 999
    return rsi <= rsi_thresh and proximity <= proximity_pct


# ---- Consolidated Runner Config (all 30d-verified through strategy_library.py) ----
# Verified: 2026-04-12. No further changes without explicit team consensus.

STRATEGY_CONFIGS = {
    "MOG-USD": {
        "type": "rsi_mr",
        "rsi_period": 4,
        "os_thresh": 45,
        "tp_pct": 7.5,
        "sl_pct": 0.5,
        "max_hold": 48,
        "weight": 1.0,
    },
    "GHST-USD": {
        "type": "momentum",
        "lookback": 20,
        "tp_pct": 15.0,
        "sl_pct": 3.0,
        "max_hold": 24,
        "weight": 1.0,
    },
    "TRU-USD": {
        "type": "momentum",
        "lookback": 10,
        "tp_pct": 10.0,
        "sl_pct": 3.0,
        "max_hold": 24,
        "weight": 1.0,
    },
    "RAVE-USD": {
        "type": "momentum",
        "lookback": 15,
        "tp_pct": 10.0,
        "sl_pct": 0.0,
        "max_hold": 36,
        "weight": 1.0,
    },
    "SUP-USD": {
        "type": "range_breakout",
        "range_lookback": 8,
        "tp_pct": 8.0,
        "sl_pct": 1.0,
        "max_hold": 24,
        "weight": 1.0,
    },
    "A8-USD": {
        "type": "momentum",
        "lookback": 10,
        "tp_pct": 15.0,
        "sl_pct": 0.0,
        "max_hold": 48,
        "weight": 1.0,
    },
    "CFG-USD": {
        "type": "momentum",
        "lookback": 50,
        "tp_pct": 15.0,
        "sl_pct": 0.0,
        "max_hold": 48,
        "weight": 1.0,
    },
    "BAL-USD": {
        "type": "range_breakout",
        "range_lookback": 50,
        "tp_pct": 10.0,
        "sl_pct": 3.0,
        "max_hold": 36,
        "weight": 1.0,
    },
    "PRL-USD": {
        "type": "momentum",
        "lookback": 25,
        "tp_pct": 10.0,
        "sl_pct": 3.0,
        "max_hold": 36,
        "weight": 1.0,
    },
    "BLUR-USD": {
        "type": "momentum",
        "lookback": 15,
        "tp_pct": 8.0,
        "sl_pct": 5.0,
        "max_hold": 48,
        "weight": 1.0,
    },
    "ALEPH-USD": {
        "type": "momentum",
        "lookback": 30,
        "tp_pct": 15.0,
        "sl_pct": 5.0,
        "max_hold": 48,
        "weight": 1.0,
    },
    "IOTX-USD": {
        "type": "momentum",
        "lookback": 25,
        "tp_pct": 5.0,
        "sl_pct": 2.0,
        "max_hold": 48,
        "weight": 1.0,
    },
    "MDT-USD": {
        "type": "momentum",
        "lookback": 25,
        "tp_pct": 5.0,
        "sl_pct": 2.0,
        "max_hold": 60,
        "weight": 1.0,
    },
    "TROLL-USD": {
        "type": "momentum",
        "lookback": 30,
        "tp_pct": 12.0,
        "sl_pct": 8.0,
        "max_hold": 96,
        "weight": 1.0,
    },
    "NOM-USD": {
        "type": "range_breakout",
        "range_lookback": 10,
        "tp_pct": 10.0,
        "sl_pct": 1.0,
        "max_hold": 24,
        "weight": 1.0,
    },
}


class CoinEngine:
    """Manages one coin's strategy lifecycle."""
    def __init__(self, coin: str, config: dict, starting_cash: float):
        self.coin = coin
        self.cfg = config
        self.cash = starting_cash
        self.starting_cash = starting_cash
        self.position = None
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_volume = 0.0
        self.total_fees = 0.0
        self.candles = []  # Full candle history
        self.closes_history = []  # Just close prices
        self.signals = 0
        self.last_candle_time = 0

    def process_candle(self, candle, event_path: Path, phase: str):
        ts = int(candle["start"])
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        candle_open = float(candle["open"])

        self.candles.append(candle)
        if len(self.candles) > CANDLE_BUFFER:
            self.candles = self.candles[-CANDLE_BUFFER:]
        self.closes_history.append(close)
        if len(self.closes_history) > 500:
            self.closes_history = self.closes_history[-500:]
        self.last_candle_time = ts

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
            elif self.position["hold"] >= self.cfg["max_hold"]:
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

                append_jsonl(event_path, {
                    "ts_utc": utc_now_iso(), "coin": self.coin, "phase": phase,
                    "action": "close", "exit_price": round(exit_price, 6),
                    "entry_price": self.position["ep"], "net": round(net, 4),
                    "reason": exit_reason, "hold_bars": self.position["hold"],
                    "fees": round(entry_fee + exit_fee, 4),
                })
                self.position = None

        # ENTRY
        if self.position is None and session_open and self.cash >= 10.0:
            idx = len(self.candles) - 1
            signal = False

            if self.cfg["type"] == "momentum":
                signal = momentum_signal(self.candles, idx, self.cfg["lookback"])
            elif self.cfg["type"] == "range_breakout":
                signal = range_breakout_signal(self.candles, idx, self.cfg["range_lookback"])
            elif self.cfg["type"] == "rsi_mr":
                # RSI Mean Reversion signal
                period = self.cfg.get("rsi_period", 3)
                thresh = self.cfg.get("os_thresh", 30)
                if len(self.closes_history) >= period + 2:
                    rsi = compute_rsi(self.closes_history[:-1], period)
                    signal = rsi <= thresh
            elif self.cfg["type"] == "bb_reversion":
                signal = bb_reversion_signal(
                    self.candles, idx,
                    rsi_period=self.cfg.get("rsi_period", 3),
                    rsi_thresh=self.cfg.get("rsi_thresh", 30),
                    bb_period=self.cfg.get("bb_period", 20),
                    proximity_pct=self.cfg.get("proximity_pct", 3.0),
                )

            if signal:
                self.signals += 1
                entry_slip = 0.0008
                actual_entry = candle_open * (1 + entry_slip)
                deploy = self.cash
                entry_fee = deploy * fr
                units = (deploy - entry_fee) / actual_entry

                # Compute TP
                if self.cfg["type"] == "bb_reversion" and self.cfg.get("tp_pct", 0) == 0:
                    # TP = BB middle band
                    sma, _, _ = compute_bb(self.closes_history, self.cfg.get("bb_period", 20))
                    tp = sma if sma else actual_entry * 1.05
                else:
                    tp = actual_entry * (1 + self.cfg["tp_pct"] / 100.0)

                sl = actual_entry * (1 - self.cfg["sl_pct"] / 100.0) if self.cfg["sl_pct"] > 0 else 0

                self.cash -= deploy
                self.position = {
                    "ep": actual_entry, "q": deploy, "hold": 0,
                    "tp": tp, "sl": sl, "units": units,
                    "entry_fee": entry_fee,
                }

                append_jsonl(event_path, {
                    "ts_utc": utc_now_iso(), "coin": self.coin, "phase": phase,
                    "action": "open", "entry_price": round(actual_entry, 6),
                    "tp": round(tp, 6), "sl": round(sl, 6),
                    "deploy": round(deploy, 4), "signals": self.signals,
                    "strategy": self.cfg["type"],
                })

    def snapshot(self):
        wr = self.wins / max(1, self.closes) * 100
        pos_value = self.position["q"] if self.position else 0
        return {
            "coin": self.coin,
            "strategy": self.cfg["type"],
            "cash": round(self.cash, 4),
            "realized_net": round(self.realized_net, 4),
            "total_pnl": round(self.cash + pos_value - self.starting_cash, 4),
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(wr, 1),
            "total_volume": round(self.total_volume, 4),
            "total_fees": round(self.total_fees, 4),
            "signals": self.signals,
            "position": "active" if self.position else "flat",
            "position_bars": self.position["hold"] if self.position else 0,
            "candles_buffered": len(self.candles),
        }


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coins", nargs="+", default=None)
    parser.add_argument("--starting-cash", type=float, default=500.0)
    parser.add_argument("--backfill-hours", type=int, default=72)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--event-path", default=None)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--max-loops", type=int, default=0)
    args = parser.parse_args()

    # Determine which coins to run
    if args.coins:
        coins_to_run = args.coins
    else:
        coins_to_run = list(STRATEGY_CONFIGS.keys())

    default_state_path, default_event_path = default_output_paths(coins_to_run)
    state_path = Path(args.state_path) if args.state_path else default_state_path
    event_path = Path(args.event_path) if args.event_path else default_event_path

    # Allocate capital equally
    per_coin_cash = args.starting_cash / len(coins_to_run)
    print(f"Portfolio: {len(coins_to_run)} coins, ${args.starting_cash} total, ${per_coin_cash:.2f} each")
    print(f"State path: {state_path}")
    print(f"Event path: {event_path}")
    print(f"Poll seconds: {args.poll_seconds}")
    if args.max_loops > 0:
        print(f"Max loops: {args.max_loops}")

    client = CoinbaseAdvancedClient()

    # Create engines
    engines = {}
    for coin in coins_to_run:
        cfg = STRATEGY_CONFIGS.get(coin, STRATEGY_CONFIGS.get(coin.split("-")[0], None))
        if cfg is None:
            print(f"WARNING: No config for {coin}, skipping")
            continue
        engines[coin] = CoinEngine(coin, cfg, per_coin_cash)
        lookback_value = cfg.get("lookback", cfg.get("range_lookback", "N/A"))
        print(f"  {coin}: {cfg['type']} (lookback={lookback_value}, TP={cfg.get('tp_pct', 'BB_mid')}, SL={cfg['sl_pct']}%)")

    # Backfill
    now = int(time.time())
    start = now - args.backfill_hours * 3600
    print(f"\nBackfilling {args.backfill_hours}h M5 candles...", flush=True)

    # Fetch BTC once
    btc_candles = fetch_candles_chunked(client, BTC, start, now)
    btc_by_ts = {int(c["start"]): c for c in btc_candles}

    for coin, engine in engines.items():
        print(f"  {coin}...", end=" ", flush=True)
        candles = fetch_candles_chunked(client, coin, start, now)
        for c in candles:
            btc_c = btc_by_ts.get(int(c["start"]))
            engine.process_candle(c, event_path, phase="startup_backfill")
        snap = engine.snapshot()
        print(f"{len(candles)} candles, closes={snap['closes']} wr={snap['win_rate']}% "
              f"realized=${snap['realized_net']:.2f} pos={snap['position']}", flush=True)

    # Save initial state
    full_state = {
        "updated_at": utc_now_iso(),
        "started_at": utc_now_iso(),
        "total_starting_cash": args.starting_cash,
        "per_coin_cash": per_coin_cash,
        "coins": {coin: engine.snapshot() for coin, engine in engines.items()},
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(full_state, indent=2, sort_keys=True), encoding="utf-8")

    total_pnl = sum(e.cash + (e.position["q"] if e.position else 0) - per_coin_cash for e in engines.values())
    total_realized = sum(e.realized_net for e in engines.values())
    total_closes = sum(e.closes for e in engines.values())
    total_wr = sum(e.wins for e in engines.values()) / max(1, total_closes) * 100

    print(f"\n{'='*80}")
    print(f"PORTFOLIO LIVE — ${args.starting_cash} across {len(engines)} coins")
    print(f"Backfill: closes={total_closes} realized=${total_realized:.2f} WR={total_wr:.1f}% PnL=${total_pnl:.2f}")
    print(f"{'='*80}\n", flush=True)

    # Live loop
    try:
        loop_count = 0
        while True:
            try:
                end = int(time.time())

                # Find the engine with the oldest candle and fetch new ones
                stalest = max(engines.values(), key=lambda e: end - e.last_candle_time)
                if stalest.last_candle_time >= end - 60:
                    time.sleep(30)
                    continue

                fetch_start = stalest.last_candle_time
                resp = client.market_candles(stalest.coin, start=fetch_start, end=end, granularity="FIVE_MINUTE")
                new = [c for c in resp.get("candles", []) if int(c["start"]) > stalest.last_candle_time]

                # Also fetch BTC for the same window
                btc_resp = client.market_candles(BTC, start=fetch_start, end=end, granularity="FIVE_MINUTE")
                btc_by_ts_live = {int(c["start"]): c for c in btc_resp.get("candles", [])}

                for c in new:
                    stalest.process_candle(c, event_path, phase="live_forward")

                # Check ALL engines for exits on their buffered candles
                # (in case we missed any updates)
                for coin, engine in engines.items():
                    if engine.last_candle_time < end - 600:  # >10 min stale
                        try:
                            resp2 = client.market_candles(coin, start=engine.last_candle_time, end=end, granularity="FIVE_MINUTE")
                            new2 = [c for c in resp2.get("candles", []) if int(c["start"]) > engine.last_candle_time]
                            for c in new2:
                                engine.process_candle(c, event_path, phase="live_forward")
                        except Exception:
                            pass

                # Print portfolio snapshot
                total_pnl = sum(e.cash + (e.position["q"] if e.position else 0) - per_coin_cash for e in engines.values())
                total_realized = sum(e.realized_net for e in engines.values())
                total_closes = sum(e.closes for e in engines.values())
                total_wins = sum(e.wins for e in engines.values())
                total_wr = total_wins / max(1, total_closes) * 100

                # Per-coin summary
                summaries = []
                for coin, e in engines.items():
                    s = e.snapshot()
                    summaries.append(
                        f"{coin}: ${s['total_pnl']:+.2f} ({s['closes']}t, {s['win_rate']}% WR, {s['position']})"
                    )

                heartbeat = f"HB {datetime.now(timezone.utc).strftime('%H:%M:%S')} | "
                heartbeat += f"PnL=${total_pnl:+.2f} | Real=${total_realized:+.2f} | "
                heartbeat += f"Closes={total_closes} WR={total_wr:.1f}% | "
                heartbeat += " | ".join(summaries)
                print(heartbeat, flush=True)

                # Save state
                full_state = {
                    "updated_at": utc_now_iso(),
                    "total_starting_cash": args.starting_cash,
                    "per_coin_cash": per_coin_cash,
                    "portfolio_pnl": round(total_pnl, 4),
                    "portfolio_realized": round(total_realized, 4),
                    "portfolio_closes": total_closes,
                    "portfolio_wr": round(total_wr, 1),
                    "coins": {coin: engine.snapshot() for coin, engine in engines.items()},
                }
                state_path.write_text(json.dumps(full_state, indent=2, sort_keys=True), encoding="utf-8")
                loop_count += 1
                if args.max_loops > 0 and loop_count >= args.max_loops:
                    print(f"Reached max loops ({args.max_loops}); exiting.", flush=True)
                    return 0

            except Exception as e:
                print(f"EXC: {e}", flush=True)
                traceback.print_exc()
                # Save state on error
                try:
                    full_state = {
                        "updated_at": utc_now_iso(),
                        "error": str(e),
                        "coins": {coin: engine.snapshot() for coin, engine in engines.items()},
                    }
                    state_path.write_text(json.dumps(full_state, indent=2, sort_keys=True), encoding="utf-8")
                except Exception:
                    pass

            time.sleep(max(1.0, float(args.poll_seconds)))

    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
        full_state = {
            "updated_at": utc_now_iso(),
            "shutdown": "user_interrupt",
            "total_starting_cash": args.starting_cash,
            "coins": {coin: engine.snapshot() for coin, engine in engines.items()},
        }
        state_path.write_text(json.dumps(full_state, indent=2, sort_keys=True), encoding="utf-8")
        return 0


if __name__ == "__main__":
    main()
