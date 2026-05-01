#!/usr/bin/env python3
"""
RSI Compound God Mode — Independent Live Shadow Runner

Built by qwen-main from scratch, separate codebase from gemini's version.
Uses the Top 5 verified coins: RAVE, BAL, BLUR, ALEPH, IOTX

Strategy:
- Long-only RSI mean reversion with coin-specific parameters
- Single-position cherry-picker (max_concurrent=1)
- Dynamic sizing: deploy 95% of available cash per trade
- Compounding: winners increase the bankroll, increasing trade size
- Fee tier tracking: log volume for fee tier arbitrage

Parameters from rsi_optimal_params.json, validated independently.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "rsi_compound_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "rsi_compound_shadow_events.jsonl"
DEFAULT_PARAMS_PATH = ROOT / "reports" / "rsi_optimal_params.json"


# Top 5 coins from @gemini's optimization, validated by @qwen-main
TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]


@dataclass
class RSITrade:
    product_id: str
    entry_time: int
    entry_price: float
    entry_rsi: float
    quantity: float
    deploy_usd: float
    entry_fee: float
    rsi_period: int
    os_level: int
    ob_level: int
    tp_pct: float
    sl_pct: float
    max_hold: int
    entry_bar: int = 0
    exit_time: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_rsi: float = 0.0
    gross_pnl: float = 0.0
    total_fee: float = 0.0
    net_pnl: float = 0.0
    hold_bars: int = 0


class RSICompoundEngine:
    def __init__(
        self,
        products: list[str],
        params: dict[str, dict],
        starting_cash: float = 48.0,
        max_concurrent: int = 1,
        deploy_fraction: float = 0.95,
        maker_fee_bps: float = 40.0,
    ):
        self.products = products
        self.params = params
        self.starting_cash = starting_cash
        self.max_concurrent = max_concurrent
        self.deploy_fraction = deploy_fraction
        self.maker_fee_bps = maker_fee_bps
        self.fee_rate = maker_fee_bps / 10000.0

        # State
        self.cash = starting_cash
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_fees = 0.0
        self.total_volume = 0.0
        self.in_position = False
        self.current_trade: RSITrade | None = None
        self.price_history: dict[str, list[float]] = {pid: [] for pid in products}
        self.rsi_cache: dict[str, list[float]] = {}
        self.last_candle_time: dict[str, int] = {pid: 0 for pid in products}
        self.current_bar: dict[str, int] = {pid: 0 for pid in products}
        self.rsi_below_os: dict[str, bool] = {pid: False for pid in products}  # Track RSI cross

    def snapshot(self) -> dict[str, Any]:
        return {
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 2),
            "realized_net_usd": round(self.realized_net, 4),
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.wins / max(1, self.closes) * 100, 1),
            "avg_pnl_per_close": round(self.realized_net / max(1, self.closes), 4) if self.closes > 0 else 0,
            "total_fees": round(self.total_fees, 4),
            "total_volume": round(self.total_volume, 2),
            "current_trade": asdict(self.current_trade) if self.current_trade else None,
            "products": self.products,
            "max_concurrent": self.max_concurrent,
            "deploy_fraction": self.deploy_fraction,
            "maker_fee_bps": self.maker_fee_bps,
        }

    def compute_rsi(self, prices: list[float], period: int) -> float:
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[-(i+1)] - prices[-(i+2)] for i in range(period)]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_g = sum(gains) / period
        avg_l = sum(losses) / period
        if avg_l > 0:
            rs = avg_g / avg_l
            return 100 - 100 / (1 + rs)
        return 100.0

    def process_tick(self, pid: str, candle: dict[str, Any], event_path: Path | None = None) -> list[dict]:
        """Process one candle for one product. Returns events."""
        events = []
        cl = float(candle["close"])
        h = float(candle["high"])
        l = float(candle["low"])
        ts = int(candle.get("time", candle.get("start", 0)))

        # Update price history
        self.price_history[pid].append(cl)
        if len(self.price_history[pid]) > 100:
            self.price_history[pid] = self.price_history[pid][-100:]

        p = self.params.get(pid)
        if not p:
            return events

        # Check exit conditions for open position
        if self.in_position and self.current_trade is not None:
            trade = self.current_trade
            tp_price = trade.entry_price * (1 + trade.tp_pct)
            sl_price = trade.entry_price * (1 - trade.sl_pct)
            current_rsi = self.compute_rsi(self.price_history[pid], trade.rsi_period)
            bars_held = self.current_bar[pid] - trade.entry_bar

            exit_price = None
            exit_reason = None

            if h >= tp_price:
                exit_price = tp_price
                exit_reason = "tp"
            elif l <= sl_price:
                exit_price = sl_price
                exit_reason = "sl"
            elif current_rsi >= trade.ob_level:
                exit_price = cl
                exit_reason = "rsi_ob"
            elif bars_held >= trade.max_hold:
                exit_price = cl
                exit_reason = "timeout"

            if exit_reason:
                qty = trade.quantity
                gross = (exit_price - trade.entry_price) * qty
                exit_fee = exit_price * qty * self.fee_rate
                net = gross - trade.entry_fee - exit_fee

                trade.exit_price = exit_price
                trade.exit_reason = exit_reason
                trade.exit_rsi = round(current_rsi, 2)
                trade.gross_pnl = round(gross, 4)
                trade.total_fee = round(trade.entry_fee + exit_fee, 4)
                trade.net_pnl = round(net, 4)
                trade.hold_bars = bars_held
                trade.exit_time = ts

                self.cash += exit_price * qty - exit_fee
                self.realized_net += net
                self.closes += 1
                self.total_fees += trade.entry_fee + exit_fee
                self.total_volume += trade.entry_price * qty + exit_price * qty

                if net > 0:
                    self.wins += 1
                else:
                    self.losses += 1

                if event_path:
                    append_jsonl(event_path, {
                        "ts_utc": utc_now_iso(),
                        "action": "close",
                        "product": pid,
                        "entry_price": trade.entry_price,
                        "exit_price": exit_price,
                        "entry_rsi": trade.entry_rsi,
                        "exit_rsi": round(current_rsi, 2),
                        "exit_reason": exit_reason,
                        "gross_pnl": round(gross, 4),
                        "fee": round(trade.entry_fee + exit_fee, 4),
                        "net_pnl": round(net, 4),
                        "hold_bars": bars_held,
                        "cash": round(self.cash, 2),
                        "realized_net": round(self.realized_net, 4),
                    })

                self.in_position = False
                self.current_trade = None
                self.rsi_below_os[pid] = False  # Reset cross detection

        # Check entry (only if not in position)
        if not self.in_position:
            rsi_val = self.compute_rsi(self.price_history[pid], p["p"])
            was_below = self.rsi_below_os.get(pid, False)
            is_below = rsi_val <= p["os"]

            # Only trigger on CROSS (was above, now below)
            if is_below and not was_below:
                deploy = self.cash * self.deploy_fraction
                if deploy >= 1.0:
                    entry_price = cl
                    entry_fee = entry_price * (deploy / entry_price) * self.fee_rate
                    qty = (deploy - entry_fee) / entry_price

                    if qty > 0:
                        self.cash -= deploy
                        self.total_volume += entry_price * qty

                        self.current_trade = RSITrade(
                            product_id=pid,
                            entry_time=ts,
                            entry_price=entry_price,
                            entry_rsi=round(rsi_val, 2),
                            quantity=qty,
                            deploy_usd=round(deploy, 2),
                            entry_fee=round(entry_fee, 6),
                            rsi_period=p["p"],
                            os_level=p["os"],
                            ob_level=p["ob"],
                            tp_pct=p["t"] / 100.0,
                            sl_pct=p["s"] / 100.0,
                            max_hold=p["h"],
                            entry_bar=self.current_bar[pid],
                        )
                        self.in_position = True

                        if event_path:
                            append_jsonl(event_path, {
                                "ts_utc": utc_now_iso(),
                                "action": "open",
                                "product": pid,
                                "entry_price": entry_price,
                                "entry_rsi": round(rsi_val, 2),
                                "quantity": round(qty, 6),
                                "deploy_usd": round(deploy, 2),
                                "cash_remaining": round(self.cash, 2),
                                "tp_pct": p["t"],
                                "sl_pct": p["s"],
                                "max_hold": p["h"],
                            })

            self.rsi_below_os[pid] = is_below

        self.current_bar[pid] += 1
        self.last_candle_time[pid] = ts
        return events


def save_state(path: Path, engine: RSICompoundEngine, runner: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now_iso(),
        "runner": runner or {},
        "state": engine.snapshot(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def fetch_candles_chunked(client, pid, start, end, granularity="FIVE_MINUTE"):
    """Fetch candles in chunks respecting 350-candle limit."""
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
            time.sleep(0.08)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def main() -> int:
    parser = argparse.ArgumentParser(description="RSI Compound God Mode Shadow Runner")
    parser.add_argument("--products", nargs="+", default=TOP_5)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument("--deploy-fraction", type=float, default=0.95)
    parser.add_argument("--maker-fee-bps", type=float, default=40.0)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--params-path", default=str(DEFAULT_PARAMS_PATH))
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()

    # Load optimal params
    params_path = Path(args.params_path)
    if params_path.exists():
        all_params = json.loads(params_path.read_text(encoding="utf-8"))
        params = {pid: all_params[pid] for pid in args.products if pid in all_params}
    else:
        print(f"ERROR: params file not found at {params_path}")
        return 1

    engine = RSICompoundEngine(
        products=args.products,
        params=params,
        starting_cash=args.starting_cash,
        max_concurrent=args.max_concurrent,
        deploy_fraction=args.deploy_fraction,
        maker_fee_bps=args.maker_fee_bps,
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

    state_path = Path(args.state_path)
    event_path = Path(args.event_path)

    # Bootstrap: backfill 72h of candles
    print(f"[{utc_now_iso()}] Bootstrapping RSI compound shadow for {args.products}...", flush=True)
    now = int(time.time())
    start = now - 72 * 3600

    all_product_candles = {}
    for pid in args.products:
        raw = fetch_candles_chunked(client, pid, start, now)
        # Normalize candle format
        candles = []
        for c in raw:
            candles.append({
                "time": int(c.get("start", 0)),
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": float(c.get("volume", 0)),
            })
        all_product_candles[pid] = candles
        print(f"  {pid}: {len(candles)} candles", flush=True)

    # Build timeline
    all_times = set()
    time_lookup = {}
    for pid, candles in all_product_candles.items():
        for c in candles:
            t = c["time"]
            all_times.add(t)
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t][pid] = c
            if t > engine.last_candle_time.get(pid, 0):
                engine.last_candle_time[pid] = t

    all_times = sorted(all_times)
    print(f"  Processing {len(all_times)} time steps...", flush=True)

    for t in all_times:
        tick = time_lookup.get(t, {})
        for pid, c in tick.items():
            engine.process_tick(pid, c, event_path=event_path)

    print(f"  Backfill complete: cash=${engine.cash:.2f} net=${engine.realized_net:.2f} {engine.closes}c {engine.wins}W/{engine.losses}L", flush=True)

    # Clear events for live, save state
    event_path.write_text(f"# Live RSI compound events starting {utc_now_iso()}\n", encoding="utf-8")
    runner_status["heartbeat_at"] = utc_now_iso()
    runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
    save_state(state_path, engine, runner=runner_status)

    snap = engine.snapshot()
    print(f"  Live shadow started. Cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} WR={snap['win_rate']:.1f}%", flush=True)

    if args.once:
        return 0

    # Live loop
    try:
        while True:
            try:
                end = int(time.time())
                for pid in args.products:
                    st = engine.last_candle_time.get(pid, end - 3600)
                    try:
                        resp = client.market_candles(pid, start=st, end=end, granularity="FIVE_MINUTE")
                        new_c = [c for c in resp.get("candles", []) if int(c.get("start", 0)) > engine.last_candle_time.get(pid, 0)]
                        for c in new_c:
                            normalized = {
                                "time": int(c.get("start", 0)),
                                "open": float(c.get("open", 0)),
                                "high": float(c.get("high", 0)),
                                "low": float(c.get("low", 0)),
                                "close": float(c.get("close", 0)),
                                "volume": float(c.get("volume", 0)),
                            }
                            engine.process_tick(pid, normalized, event_path=event_path)
                            engine.last_candle_time[pid] = max(engine.last_candle_time.get(pid, 0), int(c.get("start", 0)))
                    except Exception:
                        pass

                runner_status["heartbeat_at"] = utc_now_iso()
                runner_status["last_successful_run_at"] = runner_status["heartbeat_at"]
                runner_status["consecutive_exceptions"] = 0
                save_state(state_path, engine, runner=runner_status)

                snap = engine.snapshot()
                pos_str = f"1pos:{snap['current_trade']['product_id']}" if snap['current_trade'] else "flat"
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} {snap['closes']}c {snap['win_rate']:.1f}%WR {pos_str}", flush=True)

            except Exception as e:
                runner_status["consecutive_exceptions"] += 1
                runner_status["last_exception_at"] = utc_now_iso()
                runner_status["last_exception_type"] = type(e).__name__
                runner_status["last_exception_message"] = str(e)
                save_state(state_path, engine, runner=runner_status)
                print(f"  EXC: {e}", flush=True)

            time.sleep(args.poll_seconds)

    except KeyboardInterrupt:
        runner_status["heartbeat_at"] = utc_now_iso()
        save_state(state_path, engine, runner=runner_status)
        print("Stopped.", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
