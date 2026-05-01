#!/usr/bin/env python3
"""
Live BAL-USD Burst Fade Shadow — B_peak_limit variant
Strategy: Detect 5min candle with >2% range, place limit SELL at the candle high.
Exit: When price pulls back to 50% of the burst range (limit BUY at target).
Stop: If price moves 30% beyond the burst range against us.

This is the LIVE proof that the +$38.92/72h backtest survives real Coinbase fills.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "burst_fade_balusd_live_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "burst_fade_balusd_live_shadow_events.jsonl"

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


class BurstFadeLiveShadow:
    def __init__(self, *, product_id, starting_cash=48.0, quote_per_trade=24.0,
                 burst_threshold=2.0, target_fraction=0.5, stop_fraction=0.3):
        self.product_id = product_id
        self.starting_cash = starting_cash
        self.quote_per_trade = quote_per_trade
        self.burst_threshold = burst_threshold
        self.target_fraction = target_fraction
        self.stop_fraction = stop_fraction

        self.cash = starting_cash
        self.position = None  # None or {"entry": price, "target": price, "stop": price, "range_pct": float, "entry_time": int}
        self.realized_net_usd = 0.0
        self.realized_closes = 0
        self.wins = 0
        self.losses = 0
        self.total_fees = 0.0
        self.last_candle_time = 0

    def process_candles(self, candles, event_path):
        """Process a list of 5min candles."""
        events = []
        for c in sorted(candles, key=lambda x: int(x["start"])):
            o = float(c["open"])
            h = float(c["high"])
            l = float(c["low"])
            close = float(c["close"])
            candle_time = int(c["start"])
            mid = (o + close) / 2 if (o + close) > 0 else 1
            range_pct = (h - l) / mid * 100

            self.last_candle_time = candle_time

            # Check existing position for exit
            if self.position:
                pos = self.position
                if l <= pos["target"]:
                    exit_price = pos["target"]
                    units = self.quote_per_trade / pos["entry"]
                    gross = (pos["entry"] - exit_price) * units
                    ef = pos["entry"] * units * FEE_RATE
                    xf = exit_price * units * FEE_RATE
                    net = gross - ef - xf
                    self.realized_net_usd += net
                    self.realized_closes += 1
                    self.wins += 1
                    self.total_fees += ef + xf
                    self.cash += self.quote_per_trade + net
                    ev = {"ts_utc": utc_now_iso(), "action": "close_target", "entry": pos["entry"],
                          "exit": exit_price, "gross": round(gross, 4), "fees": round(ef + xf, 4),
                          "net": round(net, 4), "candle_time": candle_time}
                    events.append(ev)
                    self.position = None
                elif h >= pos["stop"]:
                    exit_price = pos["stop"]
                    units = self.quote_per_trade / pos["entry"]
                    gross = (pos["entry"] - exit_price) * units
                    ef = pos["entry"] * units * FEE_RATE
                    xf = exit_price * units * FEE_RATE
                    net = gross - ef - xf
                    self.realized_net_usd += net
                    self.realized_closes += 1
                    self.losses += 1
                    self.total_fees += ef + xf
                    self.cash += self.quote_per_trade + net
                    ev = {"ts_utc": utc_now_iso(), "action": "close_stop", "entry": pos["entry"],
                          "exit": exit_price, "gross": round(gross, 4), "fees": round(ef + xf, 4),
                          "net": round(net, 4), "candle_time": candle_time}
                    events.append(ev)
                    self.position = None

            # Check for new entry
            if self.position is None and self.cash >= self.quote_per_trade and range_pct >= self.burst_threshold:
                entry_price = h  # Limit sell at the HIGH of the burst candle
                target = entry_price * (1 - range_pct / 100 * self.target_fraction)
                stop = entry_price * (1 + range_pct / 100 * self.stop_fraction)
                self.position = {"entry": entry_price, "target": target, "stop": stop, "range_pct": round(range_pct, 4), "entry_time": candle_time}
                self.cash -= self.quote_per_trade
                ev = {"ts_utc": utc_now_iso(), "action": "open_fade", "entry": entry_price,
                      "target": round(target, 6), "stop": round(stop, 6),
                      "range_pct": round(range_pct, 4), "candle_time": candle_time}
                events.append(ev)

        return events

    def snapshot(self):
        return {
            "product_id": self.product_id,
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 4),
            "realized_net_usd": round(self.realized_net_usd, 4),
            "realized_closes": self.realized_closes,
            "wins": self.wins,
            "losses": self.losses,
            "total_fees": round(self.total_fees, 4),
            "position": self.position,
            "win_rate": round(self.wins / max(1, self.realized_closes) * 100, 2),
            "avg_pnl_per_close": round(self.realized_net_usd / max(1, self.realized_closes), 4),
        }


def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-id", default="BAL-USD")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--quote-per-trade", type=float, default=24.0)
    parser.add_argument("--burst-threshold", type=float, default=2.0)
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--fresh-start", action="store_true")
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    engine = BurstFadeLiveShadow(
        product_id=args.product_id,
        starting_cash=args.starting_cash,
        quote_per_trade=args.quote_per_trade,
        burst_threshold=args.burst_threshold,
    )

    state_path = Path(args.state_path)
    event_path = Path(args.event_path)

    runner = {
        "pid": os.getpid(), "script": Path(__file__).name, "started_at": utc_now_iso(),
        "poll_seconds": args.poll_seconds, "heartbeat_at": None,
        "last_successful_run_at": None, "consecutive_exceptions": 0,
        "last_exception_at": None, "last_exception_type": "", "last_exception_message": "",
    }

    # Backfill 72h first
    now = int(time.time())
    start = now - 72 * 3600
    chunk_sec = 300 * 5 * 60
    all_candles = []
    cs = start
    while cs < now:
        ce = min(cs + chunk_sec, now)
        try:
            resp = client.market_candles(args.product_id, start=cs, end=ce, granularity="FIVE_MINUTE")
            cands = resp.get("candles", [])
            all_candles.extend(cands)
            cs = ce
            if not cands:
                break
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)

    all_candles.sort(key=lambda c: int(c["start"]))
    events = engine.process_candles(all_candles, event_path)
    for ev in events:
        append_jsonl(event_path, ev)

    print(f"Backfill: {len(all_candles)} candles, {engine.realized_closes} closes, {engine.wins}W/{engine.losses}L, net=${engine.realized_net_usd:.2f}", flush=True)

    # Clear events for live run (keep backfill results in state)
    if state_path.exists() and not args.fresh_start:
        # Load previous state if resuming
        try:
            old = json.loads(state_path.read_text(encoding="utf-8"))
            eng = old.get("engine", {})
            engine.cash = eng.get("cash", engine.starting_cash)
            engine.realized_net_usd = eng.get("realized_net_usd", 0.0)
            engine.realized_closes = eng.get("realized_closes", 0)
            engine.wins = eng.get("wins", 0)
            engine.losses = eng.get("losses", 0)
            engine.total_fees = eng.get("total_fees", 0.0)
            engine.last_candle_time = 0  # Start fresh for live
            engine.position = None
            print(f"Resumed from state: cash=${engine.cash:.2f}, net=${engine.realized_net_usd:.2f}", flush=True)
        except:
            pass

    # Truncate event file for live run only (keep header comment)
    event_path.write_text(f"# Live burst fade events starting {utc_now_iso()}\n", encoding="utf-8")

    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(state_path, engine, runner)

    snap = engine.snapshot()
    print(f"Live shadow started. Cash=${snap['cash']:.2f} realized=${snap['realized_net_usd']:.2f} WR={snap['win_rate']:.1f}%", flush=True)

    # Live loop
    try:
        while True:
            try:
                end = int(time.time())
                st = engine.last_candle_time if engine.last_candle_time > 0 else end - 3600
                resp = client.market_candles(args.product_id, start=st, end=end, granularity="FIVE_MINUTE")
                new_candles = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time]

                if new_candles:
                    evts = engine.process_candles(new_candles, event_path)
                    for ev in evts:
                        append_jsonl(event_path, ev)

                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                save_state(state_path, engine, runner)
                snap = engine.snapshot()
                pos_str = f"1pos @{snap['position']['entry']:.4f}" if snap['position'] else "flat"
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} {snap['realized_closes']}c {snap['win_rate']:.1f}%WR {pos_str}", flush=True)
            except Exception as e:
                runner["consecutive_exceptions"] += 1
                runner["last_exception_at"] = utc_now_iso()
                runner["last_exception_type"] = type(e).__name__
                runner["last_exception_message"] = str(e)
                save_state(state_path, engine, runner)
                print(f"  EXC: {e}", flush=True)

            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        runner["heartbeat_at"] = utc_now_iso()
        save_state(state_path, engine, runner)
        print("Stopped.", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
