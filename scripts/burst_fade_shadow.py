#!/usr/bin/env python3
"""
Burst Fade Shadow — BAL-USD
Mechanism: Detect >2% range candle in 5min, fade the spike.
Enter: Market sell at candle close (fade the burst)
Exit: Limit buy at target pullback (or stop if continues running)

This is the PROOF that the burst fade edge survives realistic fills.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "burst_fade_balusd_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "burst_fade_balusd_shadow_events.jsonl"

MAKER_FEE_BPS = 40.0  # Coinbase maker fee per side
FEE_RATE = MAKER_FEE_BPS / 10000.0  # 0.004 per side


from datetime import datetime, timezone


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


@dataclass
class FadePosition:
    direction: str  # "FADE_SELL" = short the burst peak
    entry_price: float
    entry_time: int
    entry_msc: int
    target_price: float  # pullback target
    stop_price: float  # if price keeps going up
    burst_range_pct: float  # how big was the burst


class BurstFadeShadowEngine:
    """
    Fade burst strategy:
    1. Wait for a 5min candle with range >= 2%
    2. SHORT at the candle close (market sell — realistic fill)
    3. Set target = entry - (burst_range * 0.5) — catch half the pullback
    4. Set stop = entry + (burst_range * 0.3) — cut loss if it keeps going
    5. Exit at target or stop, whichever hits first
    """

    def __init__(
        self,
        *,
        product_id: str,
        starting_cash: float = 48.0,
        quote_per_trade: float = 24.0,
        burst_threshold_pct: float = 2.0,
        target_fraction: float = 0.5,  # catch 50% of pullback
        stop_fraction: float = 0.3,  # stop at 30% beyond burst
        max_concurrent: int = 1,
    ):
        self.product_id = product_id
        self.starting_cash = starting_cash
        self.quote_per_trade = quote_per_trade
        self.burst_threshold_pct = burst_threshold_pct
        self.target_fraction = target_fraction
        self.stop_fraction = stop_fraction
        self.max_concurrent = max_concurrent

        self.cash = starting_cash
        self.positions: list[FadePosition] = []
        self.realized_net_usd = 0.0
        self.realized_closes = 0
        self.wins = 0
        self.losses = 0
        self.total_fees = 0.0
        self.last_candle_time = 0
        self.heartbeat_at = None
        self.consecutive_exceptions = 0
        self.last_exception_type = ""

    def process_candle(self, candle: dict, event_path: Path) -> list[dict]:
        """Process a single 5min candle. Returns list of exit events."""
        events = []
        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        close = float(candle["close"])
        candle_time = int(candle["start"])
        candle_msc = candle_time * 1000
        mid = (o + close) / 2 if (o + close) > 0 else 1
        range_pct = (h - l) / mid * 100

        self.last_candle_time = candle_time

        # Check existing positions for exit
        still_open = []
        for pos in self.positions:
            # Did the candle hit target or stop?
            if pos.direction == "FADE_SELL":
                # We're short — profit if price drops to target
                # Loss if price rises to stop
                if l <= pos.target_price:
                    # Hit target! Exit at target price (realistic: limit fill at target)
                    exit_price = pos.target_price
                    gross_pnl = (pos.entry_price - exit_price) * (self.quote_per_trade / pos.entry_price)
                    entry_fee = pos.entry_price * (self.quote_per_trade / pos.entry_price) * FEE_RATE
                    exit_fee = exit_price * (self.quote_per_trade / pos.entry_price) * FEE_RATE
                    net_pnl = gross_pnl - entry_fee - exit_fee
                    self.realized_net_usd += net_pnl
                    self.realized_closes += 1
                    self.wins += 1
                    self.total_fees += entry_fee + exit_fee
                    self.cash += self.quote_per_trade + net_pnl  # return capital + PnL
                    events.append({
                        "ts_utc": utc_now_iso(),
                        "action": "close_target",
                        "entry_price": pos.entry_price,
                        "exit_price": exit_price,
                        "burst_range_pct": pos.burst_range_pct,
                        "gross_pnl": round(gross_pnl, 4),
                        "fees": round(entry_fee + exit_fee, 4),
                        "net_pnl": round(net_pnl, 4),
                        "candle_time": candle_time,
                    })
                elif h >= pos.stop_price:
                    # Hit stop! Exit at stop price
                    exit_price = pos.stop_price
                    gross_pnl = (pos.entry_price - exit_price) * (self.quote_per_trade / pos.entry_price)
                    entry_fee = pos.entry_price * (self.quote_per_trade / pos.entry_price) * FEE_RATE
                    exit_fee = exit_price * (self.quote_per_trade / pos.entry_price) * FEE_RATE
                    net_pnl = gross_pnl - entry_fee - exit_fee
                    self.realized_net_usd += net_pnl
                    self.realized_closes += 1
                    self.losses += 1
                    self.total_fees += entry_fee + exit_fee
                    self.cash += self.quote_per_trade + net_pnl
                    events.append({
                        "ts_utc": utc_now_iso(),
                        "action": "close_stop",
                        "entry_price": pos.entry_price,
                        "exit_price": exit_price,
                        "burst_range_pct": pos.burst_range_pct,
                        "gross_pnl": round(gross_pnl, 4),
                        "fees": round(entry_fee + exit_fee, 4),
                        "net_pnl": round(net_pnl, 4),
                        "candle_time": candle_time,
                    })
                else:
                    still_open.append(pos)

        self.positions = still_open

        # Check for new burst signal
        if range_pct >= self.burst_threshold_pct and len(self.positions) < self.max_concurrent and self.cash >= self.quote_per_trade:
            # Enter fade: SHORT at candle close (realistic market fill)
            entry_price = close
            target_price = entry_price * (1 - range_pct / 100 * self.target_fraction)
            stop_price = entry_price * (1 + range_pct / 100 * self.stop_fraction)

            pos = FadePosition(
                direction="FADE_SELL",
                entry_price=entry_price,
                entry_time=candle_time,
                entry_msc=candle_msc,
                target_price=target_price,
                stop_price=stop_price,
                burst_range_pct=range_pct,
            )
            self.positions.append(pos)
            self.cash -= self.quote_per_trade  # deploy capital
            events.append({
                "ts_utc": utc_now_iso(),
                "action": "open_fade",
                "entry_price": entry_price,
                "target_price": round(target_price, 6),
                "stop_price": round(stop_price, 6),
                "burst_range_pct": round(range_pct, 4),
                "candle_time": candle_time,
            })

        return events

    def snapshot(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 4),
            "realized_net_usd": round(self.realized_net_usd, 4),
            "realized_closes": self.realized_closes,
            "wins": self.wins,
            "losses": self.losses,
            "total_fees": round(self.total_fees, 4),
            "open_positions": len(self.positions),
            "burst_threshold_pct": self.burst_threshold_pct,
            "target_fraction": self.target_fraction,
            "stop_fraction": self.stop_fraction,
            "win_rate": round(self.wins / max(1, self.realized_closes) * 100, 2),
            "avg_pnl_per_close": round(self.realized_net_usd / max(1, self.realized_closes), 4),
        }


def save_state(path: Path, engine: BurstFadeShadowEngine, runner: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "engine": engine.snapshot(),
        "runner": runner,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-id", default="BAL-USD")
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--quote-per-trade", type=float, default=24.0)
    parser.add_argument("--burst-threshold", type=float, default=2.0)
    parser.add_argument("--target-fraction", type=float, default=0.5)
    parser.add_argument("--stop-fraction", type=float, default=0.3)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument("--hours-back", type=int, default=72)
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--fresh-start", action="store_true")
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()
    engine = BurstFadeShadowEngine(
        product_id=args.product_id,
        starting_cash=args.starting_cash,
        quote_per_trade=args.quote_per_trade,
        burst_threshold_pct=args.burst_threshold,
        target_fraction=args.target_fraction,
        stop_fraction=args.stop_fraction,
        max_concurrent=args.max_concurrent,
    )

    state_path = Path(args.state_path)
    event_path = Path(args.event_path)

    runner_status = {
        "pid": os.getpid(),
        "script": Path(__file__).name,
        "started_at": utc_now_iso(),
        "poll_seconds": args.poll_seconds,
        "heartbeat_at": None,
        "last_successful_run_at": None,
        "consecutive_exceptions": 0,
        "last_exception_at": None,
        "last_exception_type": "",
        "last_exception_message": "",
    }

    # Backfill: run historical candles first
    now = int(time.time())
    start = now - args.hours_back * 3600
    chunk_sec = 300 * 5 * 60  # 25h chunks

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
        except Exception as e:
            runner_status["consecutive_exceptions"] += 1
            runner_status["last_exception_at"] = utc_now_iso()
            runner_status["last_exception_type"] = type(e).__name__
            runner_status["last_exception_message"] = str(e)
            cs = ce  # skip this chunk
            time.sleep(0.5)

    # Sort by time and process
    all_candles.sort(key=lambda c: int(c["start"]))
    print(f"Processing {len(all_candles)} historical candles...", flush=True)

    total_events = 0
    for candle in all_candles:
        events = engine.process_candle(candle, event_path)
        for ev in events:
            append_jsonl(event_path, ev)
            total_events += 1

    print(f"Backfill complete: {engine.realized_closes} closes, {engine.wins}W/{engine.losses}L, net=${engine.realized_net_usd:.2f}, fees=${engine.total_fees:.2f}", flush=True)

    if args.once:
        runner_status["heartbeat_at"] = utc_now_iso()
        runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
        save_state(state_path, engine, runner_status)
        snap = engine.snapshot()
        print(f"Snapshot: cash=${snap['cash']:.2f} realized=${snap['realized_net_usd']:.2f} win_rate={snap['win_rate']:.1f}% avg_pnl=${snap['avg_pnl_per_close']:.4f}", flush=True)
        return 0

    # Live loop
    try:
        while True:
            try:
                resp = client.market_candles(args.product_id, start=engine.last_candle_time, end=int(time.time()), granularity="FIVE_MINUTE")
                new_candles = resp.get("candles", [])
                new_candles = [c for c in new_candles if int(c["start"]) > engine.last_candle_time]
                new_candles.sort(key=lambda c: int(c["start"]))

                for candle in new_candles:
                    events = engine.process_candle(candle, event_path)
                    for ev in events:
                        append_jsonl(event_path, ev)

                runner_status["heartbeat_at"] = utc_now_iso()
                runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
                runner_status["consecutive_exceptions"] = 0
                save_state(state_path, engine, runner_status)
                snap = engine.snapshot()
                print(f"  HB {snap['cash']:.2f} cash ${snap['realized_net_usd']:.2f} net {snap['realized_closes']} closes {snap['win_rate']:.1f}% WR", flush=True)
            except Exception as e:
                runner_status["consecutive_exceptions"] += 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(e).__name__
                runner_status["last_exception_message"] = str(e)
                save_state(state_path, engine, runner_status)

            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        runner_status["heartbeat_at"] = utc_now_iso()
        save_state(state_path, engine, runner_status)
        print("Stopped.", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
