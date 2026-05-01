#!/usr/bin/env python3
"""
RSI Parallel Equal-Weight Live Shadow Runner.

4 coins (RAVE, BLUR, ALEPH, IOTX), $12 each from $48 bankroll.
NO compounding — fixed position sizes per coin.
This is the verified architecture from backtesting.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from coinbase_advanced_client import CoinbaseAdvancedClient
from live_penetration_lattice_shadow import append_jsonl, log_runner_exception, utc_now_iso

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = ROOT / "reports" / "rsi_parallel_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "rsi_parallel_shadow_events.jsonl"
DEFAULT_PARAMS_PATH = ROOT / "reports" / "rsi_optimal_params.json"

# Top 4 profitable coins (drop BAL-USD which loses money in parallel)
TOP_4 = ["RAVE-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]


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
            time.sleep(0.06)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def normalize_candle(candle):
    return {
        "time": int(candle.get("start", candle.get("time", 0))),
        "open": float(candle.get("open", 0)),
        "high": float(candle.get("high", 0)),
        "low": float(candle.get("low", 0)),
        "close": float(candle.get("close", 0)),
        "volume": float(candle.get("volume", 0)),
    }


def new_candles_since(raw_candles, last_candle_time):
    last_seen = int(last_candle_time or 0)
    out = []
    for raw in sorted(raw_candles, key=lambda candle: int(candle.get("start", candle.get("time", 0)))):
        candle = normalize_candle(raw)
        if candle["time"] <= last_seen:
            continue
        out.append(candle)
        last_seen = candle["time"]
    return out


def rsi(closes, period):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [50.0] * period
    if avg_l > 0:
        result.append(100 - 100 / (1 + avg_g / avg_l))
    else:
        result.append(100.0)
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l > 0:
            result.append(100 - 100 / (1 + avg_g / avg_l))
        else:
            result.append(100.0)
    return result


class RSIParallelEngine:
    def __init__(self, products, params, starting_cash=48.0, maker_fee_bps=40.0):
        self.products = products
        self.params = params
        self.starting_cash = starting_cash
        self.per_coin = starting_cash / len(products)
        self.fee_rate = maker_fee_bps / 10000.0

        # Per-coin state
        self.coin = {}
        for pid in products:
            self.coin[pid] = {
                "cash": self.per_coin,
                "realized": 0.0,
                "closes": 0,
                "wins": 0,
                "losses": 0,
                "fees": 0.0,
                "in_position": False,
                "price_hist": [],
                "entry_price": 0,
                "entry_fee": 0,
                "qty": 0,
                "tp": 0,
                "sl": 0,
                "ob": 0,
                "p": 0,
                "os": 0,
                "entry_bar": 0,
                "current_bar": 0,
                "rsi_below_os": False,
                "last_candle_time": 0,
            }

    def snapshot(self):
        total_cash = sum(c["cash"] for c in self.coin.values())
        total_realized = sum(c["realized"] for c in self.coin.values())
        total_closes = sum(c["closes"] for c in self.coin.values())
        total_wins = sum(c["wins"] for c in self.coin.values())
        total_fees = sum(c["fees"] for c in self.coin.values())
        return {
            "starting_cash": self.starting_cash,
            "per_coin": round(self.per_coin, 2),
            "total_cash": round(total_cash, 2),
            "total_realized": round(total_realized, 4),
            "total_closes": total_closes,
            "total_wins": total_wins,
            "total_losses": total_closes - total_wins,
            "win_rate": round(total_wins / max(1, total_closes) * 100, 1),
            "total_fees": round(total_fees, 4),
            "products": self.products,
            "per_coin_details": {
                pid: {
                    "cash": round(c["cash"], 2),
                    "realized": round(c["realized"], 4),
                    "closes": c["closes"],
                    "wins": c["wins"],
                    "win_rate": round(c["wins"] / max(1, c["closes"]) * 100, 1),
                    "in_position": c["in_position"],
                    "entry_price": c["entry_price"],
                    "last_candle_time": c["last_candle_time"],
                }
                for pid, c in self.coin.items()
            },
        }

    def process_candle(self, pid, candle, event_path=None):
        cl = float(candle["close"])
        h = float(candle["high"])
        l = float(candle["low"])
        ts = int(candle.get("time", candle.get("start", 0)))
        st = self.coin[pid]
        p = self.params.get(pid, {})
        if not p:
            return

        st["price_hist"].append(cl)
        if len(st["price_hist"]) > 100:
            st["price_hist"] = st["price_hist"][-100:]
        st["current_bar"] += 1

        # Exit
        if st["in_position"]:
            rsi_vals = rsi(st["price_hist"], st["p"])
            rsi_val = rsi_vals[-1] if rsi_vals else 50
            tp = st["entry_price"] * (1 + st["tp"])
            sl = st["entry_price"] * (1 - st["sl"])

            exit_price = None
            exit_reason = None
            if h >= tp:
                exit_price = tp
                exit_reason = "tp"
            elif l <= sl:
                exit_price = sl
                exit_reason = "sl"
            elif rsi_val >= st["ob"]:
                exit_price = cl
                exit_reason = "rsi_ob"

            if exit_price is not None:
                gross = (exit_price - st["entry_price"]) * st["qty"]
                exit_fee = exit_price * st["qty"] * self.fee_rate
                net = gross - st["entry_fee"] - exit_fee
                st["realized"] += net
                st["closes"] += 1
                st["fees"] += st["entry_fee"] + exit_fee
                st["cash"] += exit_price * st["qty"] - exit_fee
                if net > 0:
                    st["wins"] += 1
                else:
                    st["losses"] += 1
                st["in_position"] = False
                st["rsi_below_os"] = False

                if event_path:
                    append_jsonl(event_path, {
                        "ts_utc": utc_now_iso(),
                        "action": "close",
                        "product": pid,
                        "entry_price": st["entry_price"],
                        "exit_price": exit_price,
                        "exit_reason": exit_reason,
                        "gross_pnl": round(gross, 4),
                        "fee": round(st["entry_fee"] + exit_fee, 4),
                        "net_pnl": round(net, 4),
                        "cash": round(st["cash"], 2),
                    })

        # Entry (cross detection)
        if not st["in_position"]:
            rsi_vals = rsi(st["price_hist"], p["p"])
            rsi_val = rsi_vals[-1] if rsi_vals else 50
            was_below = st["rsi_below_os"]
            is_below = rsi_val <= p["os"]

            if is_below and not was_below:
                deploy = st["cash"]
                if deploy >= 1.0:
                    entry_fee = cl * (deploy / cl) * self.fee_rate
                    qty = (deploy - entry_fee) / cl
                    if qty > 0:
                        st["cash"] -= deploy
                        st["fees"] += entry_fee
                        st["in_position"] = True
                        st["entry_price"] = cl
                        st["entry_fee"] = entry_fee
                        st["qty"] = qty
                        st["tp"] = p["t"] / 100.0
                        st["sl"] = p["s"] / 100.0
                        st["ob"] = p["ob"]
                        st["p"] = p["p"]
                        st["os"] = p["os"]
                        st["entry_bar"] = st["current_bar"]

                        if event_path:
                            append_jsonl(event_path, {
                                "ts_utc": utc_now_iso(),
                                "action": "open",
                                "product": pid,
                                "entry_price": cl,
                                "entry_rsi": round(rsi_val, 2),
                                "deploy_usd": round(deploy, 2),
                            })

            st["rsi_below_os"] = is_below


def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "runner": runner, "state": engine.snapshot()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", nargs="+", default=TOP_4)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=40.0)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--params-path", default=str(DEFAULT_PARAMS_PATH))
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()

    params_path = Path(args.params_path)
    all_params = json.loads(params_path.read_text(encoding="utf-8"))
    params = {pid: all_params[pid] for pid in args.products if pid in all_params}

    engine = RSIParallelEngine(args.products, params, args.starting_cash, args.maker_fee_bps)

    runner = {
        "pid": os.getpid(), "script": Path(__file__).name,
        "started_at": utc_now_iso(), "poll_seconds": args.poll_seconds,
        "heartbeat_at": None, "last_successful_run_at": None,
        "consecutive_exceptions": 0, "last_exception_at": None,
        "last_exception_type": "", "last_exception_message": "",
    }

    state_path = Path(args.state_path)
    event_path = Path(args.event_path)
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(state_path, engine, runner)

    # Bootstrap
    print(f"[{utc_now_iso()}] Bootstrapping RSI parallel shadow for {args.products}...", flush=True)
    now = int(time.time())
    start = now - 72 * 3600

    if args.fresh_start:
        event_path.parent.mkdir(parents=True, exist_ok=True)
        event_path.write_text("", encoding="utf-8")

    try:
        all_product_candles = {}
        for pid in args.products:
            raw = fetch_candles_chunked(client, pid, start, now)
            candles = [normalize_candle(c) for c in raw]
            all_product_candles[pid] = candles
            if candles:
                engine.coin[pid]["last_candle_time"] = max(candle["time"] for candle in candles)
            print(f"  {pid}: {len(candles)} candles", flush=True)

        all_times = set()
        time_lookup = {}
        for pid, candles in all_product_candles.items():
            for c in candles:
                t = c["time"]
                all_times.add(t)
                if t not in time_lookup:
                    time_lookup[t] = {}
                time_lookup[t][pid] = c
        all_times = sorted(all_times)

        print(f"  Processing {len(all_times)} time steps...", flush=True)
        for t in all_times:
            tick = time_lookup.get(t, {})
            for pid, c in tick.items():
                engine.process_candle(pid, c, event_path=event_path)
    except Exception as e:
        runner["consecutive_exceptions"] += 1
        runner["last_exception_at"] = utc_now_iso()
        runner["last_exception_type"] = type(e).__name__
        runner["last_exception_message"] = str(e)
        save_state(state_path, engine, runner)
        print(f"Backfill error: {e}", flush=True)

    snap = engine.snapshot()
    print(f"  Backfill: cash=${snap['total_cash']:.2f} net=${snap['total_realized']:+.2f} {snap['total_closes']}c {snap['win_rate']:.1f}%WR", flush=True)

    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(state_path, engine, runner)

    if args.once:
        return 0

    # Live loop
    try:
        while True:
            try:
                end = int(time.time())
                for pid in args.products:
                    st = int(engine.coin[pid].get("last_candle_time") or 0)
                    if st <= 0:
                        st = end - 3600
                    try:
                        resp = client.market_candles(pid, start=st, end=end, granularity="FIVE_MINUTE")
                        for candle in new_candles_since(resp.get("candles", []), engine.coin[pid]["last_candle_time"]):
                            engine.process_candle(pid, candle, event_path=event_path)
                            engine.coin[pid]["last_candle_time"] = candle["time"]
                    except Exception:
                        pass

                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                save_state(state_path, engine, runner)

                snap = engine.snapshot()
                pos_str = " | ".join(f"{pid}:{'pos' if c['in_position'] else 'flat'}" for pid, c in engine.coin.items())
                print(f"  HB cash=${snap['total_cash']:.2f} net=${snap['total_realized']:+.2f} {snap['total_closes']}c {snap['win_rate']:.1f}%WR [{pos_str}]", flush=True)

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
