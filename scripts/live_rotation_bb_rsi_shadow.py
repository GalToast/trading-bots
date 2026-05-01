#!/usr/bin/env python3
"""
Live Shadow Runner — Rotation + BB+RSI Confluence + 10% TP / 2.5% SL

The champion system:
- Coin pool: RAVE, BAL, BLUR, ALEPH, IOTX
- Entry: BB lower band + RSI oversold confluence
- Exit: 10% TP / 2.5% SL / RSI overbought / 12-bar timeout
- Capital: Full rotation ($48/trade)
- No cooldown, no time filter
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
DEFAULT_STATE_PATH = ROOT / "reports" / "rotation_bb_rsi_shadow_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "rotation_bb_rsi_shadow_events.jsonl"
DEFAULT_PARAMS_PATH = ROOT / "reports" / "rsi_optimal_params.json"

TOP_5 = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD"]


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
            time.sleep(0.05)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


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


class RotationBBRSIEngine:
    def __init__(self, products, params, starting_cash=48.0, maker_fee_bps=40.0,
                 tp_pct=0.10, sl_pct=0.025, max_hold_bars=12, bb_period=10, bb_mult=1.5):
        self.products = products
        self.params = params
        self.starting_cash = starting_cash
        self.fee_rate = maker_fee_bps / 10000.0
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.max_hold_bars = max_hold_bars
        self.bb_period = bb_period
        self.bb_mult = bb_mult

        # State
        self.cash = starting_cash
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_fees = 0.0
        self.in_position = False
        self.position_pid = None
        self.entry_price = 0
        self.entry_fee = 0
        self.qty = 0
        self.entry_bar = 0
        self.entry_time = 0
        self.current_bar = 0
        self.price_history = {pid: [] for pid in products}
        self.last_candle_time = {pid: 0 for pid in products}

    def snapshot(self):
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
            "products": self.products,
            "config": {
                "tp_pct": self.tp_pct * 100,
                "sl_pct": self.sl_pct * 100,
                "max_hold_bars": self.max_hold_bars,
                "maker_fee_bps": self.fee_rate * 10000,
                "bb_period": self.bb_period,
                "bb_mult": self.bb_mult,
            },
            "current_position": {
                "pid": self.position_pid,
                "entry_price": self.entry_price,
                "entry_time": self.entry_time,
                "current_bar": self.current_bar,
            } if self.in_position else None,
            "last_candle_time": {pid: int(ts) for pid, ts in self.last_candle_time.items()},
        }

    def process_candle(self, pid, candle, event_path=None):
        cl = float(candle["close"])
        h = float(candle["high"])
        l = float(candle["low"])
        ts = int(candle.get("start", 0))
        self.last_candle_time[pid] = max(int(self.last_candle_time.get(pid, 0) or 0), ts)

        self.price_history[pid].append(cl)
        if len(self.price_history[pid]) > 100:
            self.price_history[pid] = self.price_history[pid][-100:]
        self.current_bar += 1

        # Exit
        if self.in_position and self.position_pid == pid:
            tp = self.entry_price * (1 + self.tp_pct)
            sl = self.entry_price * (1 - self.sl_pct)

            ph = self.price_history[pid]
            rsi_val = rsi(ph, 7)[-1] if len(ph) > 7 else 50

            exit_price = None
            exit_reason = None

            if h >= tp:
                exit_price = tp
                exit_reason = "tp"
            elif l <= sl:
                exit_price = sl
                exit_reason = "sl"
            elif rsi_val >= 75:
                exit_price = cl
                exit_reason = "rsi_ob"
            elif self.current_bar - self.entry_bar >= self.max_hold_bars:
                exit_price = cl
                exit_reason = "timeout"

            if exit_price is not None:
                gross = (exit_price - self.entry_price) * self.qty
                exit_fee = exit_price * self.qty * self.fee_rate
                net = gross - self.entry_fee - exit_fee

                self.realized_net += net
                self.closes += 1
                self.total_fees += self.entry_fee + exit_fee
                self.cash += exit_price * self.qty - exit_fee

                if net > 0:
                    self.wins += 1
                else:
                    self.losses += 1

                if event_path:
                    append_jsonl(event_path, {
                        "ts_utc": utc_now_iso(),
                        "action": "close",
                        "product": pid,
                        "entry_price": self.entry_price,
                        "exit_price": exit_price,
                        "exit_reason": exit_reason,
                        "entry_rsi": self.entry_rsi,
                        "exit_rsi": round(rsi_val, 2),
                        "gross_pnl": round(gross, 4),
                        "fee": round(self.entry_fee + exit_fee, 4),
                        "net_pnl": round(net, 4),
                        "hold_bars": self.current_bar - self.entry_bar,
                        "cash": round(self.cash, 2),
                        "realized_net": round(self.realized_net, 4),
                    })

                self.in_position = False
                self.position_pid = None

        # Entry: BB+RSI confluence across all coins
        if not self.in_position:
            best_pid = None
            best_rsi = 999
            self.entry_rsi = 50

            for check_pid in self.products:
                if check_pid not in self.price_history or check_pid not in self.params:
                    continue
                ph = self.price_history[check_pid]
                if len(ph) < 20:
                    continue

                # BB + RSI confluence
                closes = ph[-self.bb_period:]
                sma = sum(closes) / len(closes)
                std = (sum((c - sma)**2 for c in closes) / len(closes)) ** 0.5
                lower_bb = sma - self.bb_mult * std
                rsi_val = rsi(ph, 7)[-1]
                curr_price = ph[-1]

                if curr_price <= lower_bb * 1.005 and rsi_val < 30:
                    if rsi_val < best_rsi:
                        best_rsi = rsi_val
                        best_pid = check_pid
                        self.entry_rsi = rsi_val

            if best_pid and self.cash >= 1.0:
                # Get current candle for best pid
                if pid == best_pid:
                    entry_price = cl
                else:
                    # Use last known price
                    entry_price = self.price_history[best_pid][-1]

                deploy = self.cash
                entry_fee = entry_price * (deploy / entry_price) * self.fee_rate
                qty = (deploy - entry_fee) / entry_price

                if qty > 0:
                    self.cash -= deploy
                    self.in_position = True
                    self.position_pid = best_pid
                    self.entry_price = entry_price
                    self.entry_fee = entry_fee
                    self.qty = qty
                    self.entry_bar = self.current_bar
                    self.entry_time = ts

                    if event_path:
                        append_jsonl(event_path, {
                            "ts_utc": utc_now_iso(),
                            "action": "open",
                            "product": best_pid,
                            "entry_price": round(entry_price, 6),
                            "entry_rsi": round(self.entry_rsi, 2),
                            "deploy_usd": round(deploy, 2),
                            "cash_remaining": round(self.cash, 2),
                            "tp_pct": self.tp_pct * 100,
                            "sl_pct": self.sl_pct * 100,
                        })


def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "runner": runner, "state": engine.snapshot()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", nargs="+", default=TOP_5)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=40.0)
    parser.add_argument("--tp-pct", type=float, default=10.0)
    parser.add_argument("--sl-pct", type=float, default=2.5)
    parser.add_argument("--max-hold-bars", type=int, default=12)
    parser.add_argument("--bb-period", type=int, default=10)
    parser.add_argument("--bb-mult", type=float, default=1.5)
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

    engine = RotationBBRSIEngine(
        products=args.products,
        params=params,
        starting_cash=args.starting_cash,
        maker_fee_bps=args.maker_fee_bps,
        tp_pct=args.tp_pct / 100.0,
        sl_pct=args.sl_pct / 100.0,
        max_hold_bars=args.max_hold_bars,
        bb_period=args.bb_period,
        bb_mult=args.bb_mult,
    )

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

    # Bootstrap: backfill 72h
    print(f"[{utc_now_iso()}] Bootstrapping rotation BB+RSI shadow for {args.products}...", flush=True)
    now = int(time.time())
    start = now - 72 * 3600

    try:
        all_product_candles = {}
        for pid in args.products:
            raw = fetch_candles_chunked(client, pid, start, now)
            candles = [{"start": int(c.get("start", 0)), "open": float(c.get("open", 0)),
                         "high": float(c.get("high", 0)), "low": float(c.get("low", 0)),
                         "close": float(c.get("close", 0)), "volume": float(c.get("volume", 0))} for c in raw]
            all_product_candles[pid] = candles
            print(f"  {pid}: {len(candles)} candles", flush=True)

        # Build timeline
        all_times = set()
        time_lookup = {}
        for pid, candles in all_product_candles.items():
            for c in candles:
                t = c["start"]
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
    print(f"  Backfill: cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:+.2f} {snap['closes']}c {snap['win_rate']:.1f}%WR", flush=True)

    # Clear events for live, save state
    event_path.write_text(f"# Live rotation BB+RSI events starting {utc_now_iso()}\n", encoding="utf-8")
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
                    last_time = int(engine.last_candle_time.get(pid, 0) or 0)
                    if last_time <= 0:
                        last_time = end - 3600
                    try:
                        resp = client.market_candles(pid, start=last_time, end=end, granularity="FIVE_MINUTE")
                        for c in resp.get("candles", []):
                            if int(c.get("start", 0)) > last_time:
                                normalized = {"start": int(c.get("start", 0)), "open": float(c.get("open", 0)),
                                              "high": float(c.get("high", 0)), "low": float(c.get("low", 0)),
                                              "close": float(c.get("close", 0)), "volume": float(c.get("volume", 0))}
                                engine.process_candle(pid, normalized, event_path=event_path)
                    except Exception:
                        pass

                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                save_state(state_path, engine, runner)

                snap = engine.snapshot()
                pos_str = f"IN:{snap['current_position']['pid']}" if snap['current_position'] else "FLAT"
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:+.2f} {snap['closes']}c {snap['win_rate']:.1f}%WR [{pos_str}]", flush=True)

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
