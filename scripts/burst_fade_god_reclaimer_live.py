#!/usr/bin/env python3
"""
Live God Reclaimer shadow runner for Coinbase spot.

This is the long-only sibling to the God Mode / God Killer burst-fade shell:
- multi-coin candidate ranking
- geometric compounding
- dynamic fee-tier modeling
- supervised live shadow execution

But the trade thesis is long-only:
- detect a downside flush from prior close
- require a reclaim off the lows by candle close
- buy the reclaim
- exit on rebound target or downside stop
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
DEFAULT_STATE_PATH = ROOT / "reports" / "burst_fade_god_reclaimer_live_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "burst_fade_god_reclaimer_live_events.jsonl"

PRODUCTS = [
    "RAVE-USD",
    "TROLL-USD",
    "BAL-USD",
    "NOM-USD",
    "MASK-USD",
    "ALEPH-USD",
    "CHECK-USD",
    "BLUR-USD",
    "AVT-USD",
    "IOTX-USD",
    "IRYS-USD",
    "CFG-USD",
    "BOBBOB-USD",
    "DASH-USD",
    "FARTCOIN-USD",
    "COMP-USD",
    "MON-USD",
    "ZEC-USD",
    "VVV-USD",
    "ALGO-USD",
    "ARB-USD",
    "ETH-USD",
    "BASED1-USD",
    "SKL-USD",
    "TAO-USD",
]

PRODUCT_PARAMS = {
    "RAVE-USD": {"bt": 2.0, "t": 0.9, "s": 0.35},
    "TROLL-USD": {"bt": 2.0, "t": 0.9, "s": 0.35},
    "BAL-USD": {"bt": 2.0, "t": 0.8, "s": 0.3},
    "NOM-USD": {"bt": 2.0, "t": 0.9, "s": 0.35},
    "MASK-USD": {"bt": 1.0, "t": 0.8, "s": 0.25},
    "ALEPH-USD": {"bt": 1.0, "t": 0.8, "s": 0.35},
    "CHECK-USD": {"bt": 2.0, "t": 0.9, "s": 0.3},
    "BLUR-USD": {"bt": 2.0, "t": 0.8, "s": 0.3},
    "AVT-USD": {"bt": 1.0, "t": 0.8, "s": 0.25},
    "IOTX-USD": {"bt": 2.0, "t": 0.9, "s": 0.35},
    "IRYS-USD": {"bt": 3.0, "t": 1.0, "s": 0.4},
    "CFG-USD": {"bt": 2.0, "t": 0.8, "s": 0.3},
    "BOBBOB-USD": {"bt": 2.0, "t": 0.7, "s": 0.3},
    "DASH-USD": {"bt": 2.0, "t": 0.8, "s": 0.3},
    "FARTCOIN-USD": {"bt": 2.0, "t": 0.9, "s": 0.4},
    "COMP-USD": {"bt": 2.0, "t": 0.8, "s": 0.3},
    "MON-USD": {"bt": 2.0, "t": 0.9, "s": 0.4},
    "ZEC-USD": {"bt": 2.0, "t": 0.9, "s": 0.3},
    "VVV-USD": {"bt": 2.0, "t": 0.8, "s": 0.3},
    "ALGO-USD": {"bt": 2.0, "t": 0.9, "s": 0.3},
    "ARB-USD": {"bt": 1.0, "t": 0.9, "s": 0.35},
    "ETH-USD": {"bt": 1.0, "t": 0.7, "s": 0.2},
    "BASED1-USD": {"bt": 2.0, "t": 0.9, "s": 0.3},
    "SKL-USD": {"bt": 1.0, "t": 0.8, "s": 0.25},
    "TAO-USD": {"bt": 2.0, "t": 0.8, "s": 0.35},
}


def fetch_recent_candles(client: CoinbaseAdvancedClient, product_id: str, granularity: str, count: int) -> list[dict]:
    g = str(granularity).upper()
    if g == "FIVE_MINUTE":
        interval = 300
    elif g == "FIFTEEN_MINUTE":
        interval = 900
    elif g == "ONE_HOUR":
        interval = 3600
    else:
        interval = 300
    end = int(time.time())
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
    if total_volume >= 10000:
        return 0.0025
    return 0.0040


class GodReclaimerShadowEngine:
    def __init__(self, *, starting_cash: float, max_concurrent: int, reclaim_floor: float):
        self.starting_cash = float(starting_cash)
        self.max_concurrent = int(max_concurrent)
        self.reclaim_floor = float(reclaim_floor)
        self.cash = float(starting_cash)
        self.positions: list[dict[str, Any]] = []
        self.total_volume = 0.0
        self.total_fees = 0.0
        self.realized_closes = 0
        self.realized_wins = 0
        self.realized_net = 0.0
        self.last_close_by_pid: dict[str, float] = {}

    def _flush_reclaim_signal(self, pid: str, candle: dict[str, Any]) -> dict[str, float] | None:
        prev_close = float(self.last_close_by_pid.get(pid) or 0.0)
        if prev_close <= 0.0:
            return None
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        close = float(candle["close"])
        if min(prev_close, o, h, l, close) <= 0.0 or h <= l:
            return None
        params = PRODUCT_PARAMS.get(pid)
        if not params:
            return None
        flush_pct = ((prev_close - l) / prev_close) * 100.0
        reclaim_pct = ((close - l) / l) * 100.0
        close_location = (close - l) / (h - l)
        still_below_prev = close < prev_close * 0.998
        if flush_pct < float(params["bt"]):
            return None
        if close_location < self.reclaim_floor:
            return None
        if reclaim_pct < max(0.35 * flush_pct, 0.35):
            return None
        if not still_below_prev:
            return None
        entry = close * 1.001
        target = entry * (1.0 + (flush_pct / 100.0) * float(params["t"]))
        stop = entry * (1.0 - (flush_pct / 100.0) * float(params["s"]))
        score = flush_pct * max(0.0, close_location - self.reclaim_floor + 0.25)
        return {
            "flush_pct": flush_pct,
            "reclaim_pct": reclaim_pct,
            "close_location": close_location,
            "entry": entry,
            "target": target,
            "stop": stop,
            "score": score,
        }

    def process_tick(self, all_candles_by_pid: dict[str, list[dict[str, Any]]], event_path: Path | None = None) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        still_open: list[dict[str, Any]] = []
        for pos in self.positions:
            pid = str(pos["pid"])
            closed = False
            for candle in all_candles_by_pid.get(pid, []):
                h = float(candle["high"])
                l = float(candle["low"])
                ep = float(pos["entry"])
                tp = float(pos["target"])
                sp = float(pos["stop"])
                tq = float(pos["quote"])
                units = tq / ep if ep > 0 else 0.0
                fee_rate = get_fee_rate(self.total_volume)
                exit_price = None
                if h >= tp:
                    exit_price = tp
                elif l <= sp:
                    exit_price = sp
                if exit_price is None:
                    continue
                gross = (exit_price - ep) * units
                entry_fee = tq * fee_rate
                exit_fee = exit_price * units * fee_rate
                net = gross - entry_fee - exit_fee
                self.cash += tq + net
                self.realized_closes += 1
                self.realized_net += net
                self.total_volume += tq + (exit_price * units)
                self.total_fees += entry_fee + exit_fee
                if net > 0:
                    self.realized_wins += 1
                if event_path:
                    append_jsonl(event_path, {
                        "ts_utc": utc_now_iso(),
                        "action": "close_reclaim",
                        "pid": pid,
                        "entry": ep,
                        "exit": exit_price,
                        "target": tp,
                        "stop": sp,
                        "quote_size": tq,
                        "gross_pnl": round(gross, 4),
                        "fees": round(entry_fee + exit_fee, 4),
                        "net_pnl": round(net, 4),
                        "cash_after": round(self.cash, 4),
                        "fee_rate": fee_rate,
                    })
                closed = True
                break
            if not closed:
                still_open.append(pos)
        self.positions = still_open

        free_slots = self.max_concurrent - len(self.positions)
        if free_slots > 0 and self.cash >= 10.0:
            candidates: list[dict[str, Any]] = []
            for pid in PRODUCTS:
                if any(p["pid"] == pid for p in self.positions):
                    for candle in all_candles_by_pid.get(pid, []):
                        self.last_close_by_pid[pid] = float(candle["close"])
                    continue
                latest_signal: dict[str, Any] | None = None
                for candle in sorted(all_candles_by_pid.get(pid, []), key=lambda item: int(item["start"])):
                    signal = self._flush_reclaim_signal(pid, candle)
                    if signal is not None:
                        latest_signal = {"pid": pid, "candle": candle, **signal}
                    self.last_close_by_pid[pid] = float(candle["close"])
                if latest_signal is not None:
                    candidates.append(latest_signal)

            candidates.sort(key=lambda item: float(item["score"]), reverse=True)
            for candidate in candidates[:free_slots]:
                if self.cash < 10.0:
                    break
                alloc_fraction = 1.0 / max(1, free_slots)
                if float(candidate["flush_pct"]) >= float(PRODUCT_PARAMS[candidate["pid"]]["bt"]) * 1.5:
                    alloc_fraction = min(1.0, alloc_fraction * 1.5)
                tq = min(self.cash * 0.95, self.cash * alloc_fraction * 0.95)
                if tq < 10.0:
                    continue
                self.positions.append({
                    "pid": candidate["pid"],
                    "entry": float(candidate["entry"]),
                    "target": float(candidate["target"]),
                    "stop": float(candidate["stop"]),
                    "quote": tq,
                    "flush_pct": float(candidate["flush_pct"]),
                    "reclaim_pct": float(candidate["reclaim_pct"]),
                    "close_location": float(candidate["close_location"]),
                })
                self.cash -= tq
                if event_path:
                    append_jsonl(event_path, {
                        "ts_utc": utc_now_iso(),
                        "action": "open_reclaim",
                        "pid": candidate["pid"],
                        "entry": round(float(candidate["entry"]), 8),
                        "target": round(float(candidate["target"]), 8),
                        "stop": round(float(candidate["stop"]), 8),
                        "quote_size": round(tq, 4),
                        "flush_pct": round(float(candidate["flush_pct"]), 4),
                        "reclaim_pct": round(float(candidate["reclaim_pct"]), 4),
                        "close_location": round(float(candidate["close_location"]), 4),
                        "cash_after": round(self.cash, 4),
                    })
                free_slots -= 1
                if free_slots <= 0:
                    break
        return events

    def snapshot(self) -> dict[str, Any]:
        losses = max(0, self.realized_closes - self.realized_wins)
        return {
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 4),
            "realized_net_usd": round(self.realized_net, 4),
            "closes": self.realized_closes,
            "wins": self.realized_wins,
            "losses": losses,
            "total_fees": round(self.total_fees, 4),
            "total_volume": round(self.total_volume, 4),
            "fee_rate_bps": round(get_fee_rate(self.total_volume) * 10000, 1),
            "positions": self.positions,
            "win_rate": round(self.realized_wins / max(1, self.realized_closes) * 100, 2),
            "avg_pnl_per_close": round(self.realized_net / max(1, self.realized_closes), 4),
        }


def save_state(path: Path, engine: GodReclaimerShadowEngine, runner: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="God Reclaimer long-only live shadow runner")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--bootstrap-count", type=int, default=60)
    parser.add_argument("--reclaim-floor", type=float, default=0.6)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    engine = GodReclaimerShadowEngine(
        starting_cash=args.starting_cash,
        max_concurrent=args.max_concurrent,
        reclaim_floor=args.reclaim_floor,
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

    print(f"[{utc_now_iso()}] Bootstrapping God Reclaimer shadow for {len(PRODUCTS)} products...")
    for pid in PRODUCTS:
        candles = fetch_recent_candles(client, pid, args.granularity, count=max(5, int(args.bootstrap_count)))
        if candles:
            engine.last_close_by_pid[pid] = float(candles[0]["close"])
            for candle in candles[1:-1]:
                engine.process_tick({pid: [candle]}, event_path=event_path)
        print(f"  {pid}: {len(candles)} candles bootstrapped")

    save_state(state_path, engine, runner_status)

    def run_once() -> None:
        tick_candles: dict[str, list[dict[str, Any]]] = {}
        for pid in PRODUCTS:
            candles = fetch_recent_candles(client, pid, args.granularity, count=2)
            if candles:
                tick_candles[pid] = [candles[-1]]
        engine.process_tick(tick_candles, event_path=event_path)
        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        runner_status["consecutive_exceptions"] = 0
        save_state(state_path, engine, runner_status)

    try:
        run_once()
        snap = engine.snapshot()
        print(
            f"[{utc_now_iso()}] Run complete. Cash: ${snap['cash']:.2f}, "
            f"Realized: ${snap['realized_net_usd']:+.2f}, Closes: {snap['closes']}"
        )
        if args.once:
            return 0
        print(f"[{utc_now_iso()}] God Reclaimer shadow started. Polling every {args.poll_seconds}s")
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
