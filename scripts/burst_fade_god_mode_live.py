#!/usr/bin/env python3
"""
Live God Mode shadow runner for Coinbase spot.

Combines all edges into one monolithic live shadow:
1. Single-Position Round Robin (cherry picks biggest burst)
2. Geometric Compounding (95% of available cash)
3. 0.5% Laddering (waits for spike past burst high)
4. Asymmetric Grid-Searched Targets & Stops
5. Dynamic Fee Tier Modeling (40bps -> 25bps -> 15bps)

Runs live against real-time Coinbase 5min candles.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "burst_fade_god_mode_live_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "burst_fade_god_mode_live_events.jsonl"

PRODUCTS = ["CHECK-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "CFG-USD", "COMP-USD", "DASH-USD", "BASED1-USD", "AVT-USD", "BOBBOB-USD"]

PRODUCT_PARAMS = {
    "CHECK-USD": {"bt": 2.0, "t": 1.0, "s": 0.1},
    "BAL-USD": {"bt": 2.0, "t": 0.6, "s": 0.1},
    "BLUR-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "ALEPH-USD": {"bt": 1.0, "t": 0.8, "s": 0.2},
    "CFG-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "COMP-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "DASH-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "BASED1-USD": {"bt": 2.0, "t": 1.0, "s": 0.1},
    "AVT-USD": {"bt": 1.0, "t": 0.8, "s": 0.1},
    "BOBBOB-USD": {"bt": 2.0, "t": 0.6, "s": 0.1},
}


def fetch_recent_candles(client: CoinbaseAdvancedClient, product_id: str, granularity: str, count: int) -> list[dict]:
    import time as _time
    g = str(granularity).upper()
    if g == "FIVE_MINUTE":
        interval = 300
    elif g == "FIFTEEN_MINUTE":
        interval = 900
    elif g == "ONE_HOUR":
        interval = 3600
    else:
        interval = 300
    end = int(_time.time())
    start = end - (count * interval) - 60
    try:
        resp = client.market_candles(product_id, start=start, end=end, granularity=g)
        candles = resp.get("candles", [])
        candles.sort(key=lambda c: int(c["start"]))
        return candles
    except Exception:
        return []


def get_fee_rate(total_volume: float) -> float:
    if total_volume >= 50000:
        return 0.0015
    elif total_volume >= 10000:
        return 0.0025
    return 0.0040


class GodModeShadowEngine:
    def __init__(self, *, starting_cash: float, max_concurrent: int):
        self.starting_cash = float(starting_cash)
        self.max_concurrent = int(max_concurrent)
        self.cash = float(starting_cash)
        self.positions: list[dict] = []
        self.total_volume = 0.0
        self.total_fees = 0.0
        self.realized_closes = 0
        self.realized_wins = 0
        self.realized_net = 0.0
        self.candle_buffer: dict[str, list[dict]] = {pid: [] for pid in PRODUCTS}

    def process_candle(self, pid: str, candle: dict, event_path: Path | None = None):
        """Process a single 5min candle for one product."""
        t = int(candle["start"])
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        close = float(candle["close"])

        # Check existing positions for this product
        still_open = []
        for pos in self.positions:
            if pos["pid"] != pid:
                still_open.append(pos)
                continue

            ep = pos["entry"]
            tp = pos["target"]
            sp = pos["stop"]
            tq = pos["quote"]
            units = tq / ep if ep > 0 else 0

            fee_rate = get_fee_rate(self.total_volume)
            closed = False
            exit_price = None

            # Check target (short: profit when price drops)
            if l <= tp:
                exit_price = tp
                closed = True
            # Check stop (short: loss when price rises)
            elif h >= sp:
                exit_price = sp
                closed = True

            if closed:
                gross = (ep - exit_price) * units
                ef = tq * fee_rate
                xf = exit_price * units * fee_rate
                net = gross - ef - xf
                self.cash += tq + net
                self.realized_closes += 1
                self.realized_net += net
                self.total_volume += tq + (exit_price * units)
                self.total_fees += ef + xf
                if net > 0:
                    self.realized_wins += 1

                if event_path:
                    append_jsonl(event_path, {
                        "ts_utc": utc_now_iso(),
                        "action": "close_position",
                        "pid": pid,
                        "entry": ep,
                        "exit": exit_price,
                        "target": tp,
                        "stop": sp,
                        "quote_size": tq,
                        "gross_pnl": round(gross, 4),
                        "fees": round(ef + xf, 4),
                        "net_pnl": round(net, 4),
                        "cash_after": round(self.cash, 4),
                        "fee_rate": fee_rate,
                    })
            else:
                still_open.append(pos)

        self.positions = still_open

        # Check for new entries
        free_slots = self.max_concurrent - len(self.positions)
        if free_slots > 0 and self.cash >= 10.0:
            params = PRODUCT_PARAMS.get(pid)
            if params:
                mid = (o + close) / 2 if (o + close) > 0 else 1
                rp = (h - l) / mid * 100
                if rp >= params["bt"]:
                    alloc_fraction = 1.0 / free_slots
                    if rp >= params["bt"] * 1.5:
                        alloc_fraction = min(1.0, alloc_fraction * 1.5)
                    tq = min(self.cash * 0.95, self.cash * alloc_fraction * 0.95)
                    if tq >= 10.0:
                        burst_high = h
                        ep = burst_high * 1.005  # 0.5% ladder
                        tp = ep * (1 - rp / 100 * params["t"])
                        sp = ep * (1 + rp / 100 * params["s"])
                        self.positions.append({
                            "pid": pid, "entry": ep, "target": tp,
                            "stop": sp, "quote": tq, "rp": rp,
                        })
                        self.cash -= tq
                        if event_path:
                            append_jsonl(event_path, {
                                "ts_utc": utc_now_iso(),
                                "action": "open_position",
                                "pid": pid,
                                "entry": ep,
                                "target": tp,
                                "stop": sp,
                                "quote_size": tq,
                                "burst_range_pct": round(rp, 3),
                                "cash_after": round(self.cash, 4),
                            })

    def snapshot(self) -> dict:
        return {
            "cash": round(self.cash, 4),
            "starting_cash": self.starting_cash,
            "net": round(self.cash - self.starting_cash, 4),
            "roi_pct": round((self.cash - self.starting_cash) / self.starting_cash * 100, 2),
            "realized_closes": self.realized_closes,
            "realized_wins": self.realized_wins,
            "win_rate": round(self.realized_wins / self.realized_closes * 100, 1) if self.realized_closes > 0 else 0,
            "realized_net": round(self.realized_net, 4),
            "total_volume": round(self.total_volume, 2),
            "total_fees": round(self.total_fees, 4),
            "current_fee_rate": get_fee_rate(self.total_volume),
            "open_positions": len(self.positions),
            "positions": self.positions,
        }


def save_state(path: Path, engine: GodModeShadowEngine, runner: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "engine": engine.snapshot(),
        "runner": runner,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="God Mode live shadow runner")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)

    engine = GodModeShadowEngine(
        starting_cash=args.starting_cash,
        max_concurrent=args.max_concurrent,
    )

    runner_status = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": utc_now_iso(),
        "poll_seconds": max(1.0, float(args.poll_seconds)),
        "heartbeat_at": None,
        "last_successful_run_at": None,
        "consecutive_exceptions": 0,
        "last_exception_at": None,
        "last_exception_type": "",
        "last_exception_message": "",
    }

    # Bootstrap with historical candles
    print(f"[{utc_now_iso()}] Bootstrapping God Mode shadow for {len(PRODUCTS)} products...")
    bootstrap_count = 50
    for pid in PRODUCTS:
        candles = fetch_recent_candles(client, pid, args.granularity, count=bootstrap_count)
        for c in candles[:-1]:
            engine.process_candle(pid, c, event_path=event_path)
        print(f"  {pid}: {len(candles)} candles bootstrapped")

    save_state(state_path, engine, runner_status)

    def run_once() -> None:
        # Fetch latest candle for each product
        for pid in PRODUCTS:
            candles = fetch_recent_candles(client, pid, args.granularity, count=2)
            if candles:
                latest = candles[-1]
                engine.process_candle(pid, latest, event_path=event_path)

        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
        save_state(state_path, engine, runner_status)

    try:
        run_once()
        snap = engine.snapshot()
        print(f"[{utc_now_iso()}] Run complete. Cash: ${snap['cash']:.2f}, Net: ${snap['net']:+.2f}, Closes: {snap['realized_closes']}")
        if args.once:
            return 0

        print(f"[{utc_now_iso()}] God Mode shadow started. Polling every {args.poll_seconds}s")
        while True:
            time.sleep(max(1.0, float(args.poll_seconds)))
            try:
                run_once()
            except Exception as exc:
                runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(exc).__name__
                runner_status["last_exception_message"] = str(exc)
                save_state(state_path, engine, runner_status)
                log_runner_exception(event_path, exc, phase="loop_run_once")
    except Exception as exc:
        runner_status["consecutive_exceptions"] = int(runner_status.get("consecutive_exceptions", 0) or 0) + 1
        runner_status["last_exception_at"] = utc_now_iso()
        runner_status["last_exception_type"] = type(exc).__name__
        runner_status["last_exception_message"] = str(exc)
        save_state(state_path, engine, runner_status)
        log_runner_exception(event_path, exc, phase="initial_run_once")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
