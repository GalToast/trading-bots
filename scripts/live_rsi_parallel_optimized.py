#!/usr/bin/env python3
"""
RSI Parallel Optimized — IOTX + SKL + IRYS + ALEPH portfolio.
Auto-switches fee rate when Gobblin hits 15bps tier.
Based on coin expansion scan results: IOTX $6.84, SKL $3.71, IRYS $2.25, ALEPH $1.28
Total: $14.08/11 days vs old portfolio ~$5.84 (2.4x improvement)
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
DEFAULT_STATE_PATH = ROOT / "reports" / "rsi_parallel_optimized_state.json"
DEFAULT_EVENT_PATH = ROOT / "reports" / "rsi_parallel_optimized_events.jsonl"

# OPTIMIZED portfolio: IOTX + SKL + IRYS + ALEPH
# Based on 11-day coin expansion scan
OPTIMIZED_4 = ["IOTX-USD", "SKL-USD", "IRYS-USD", "ALEPH-USD"]

# Fee tier monitor path (written by Gobblin swarm)
FEE_TIER_PATH = ROOT / "reports" / "gobblin_fee_tier.json"

def load_current_fee_bps():
    """Check if Gobblin has unlocked a lower fee tier."""
    try:
        if FEE_TIER_PATH.exists():
            with open(FEE_TIER_PATH) as f:
                data = json.load(f)
            return data.get("fee_bps", 40.0)
    except:
        pass
    return 40.0

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
            time.sleep(0.15)
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

class RSIParallelOptimized:
    def __init__(self, products, params, starting_cash=48.0, maker_fee_bps=40.0):
        self.products = products
        self.params = params
        self.starting_cash = starting_cash
        self.per_coin = starting_cash / len(products)
        self.fee_rate = maker_fee_bps / 10000.0

        self.coin = {}
        for pid in products:
            p = params.get(pid, {})
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
                "tp": p.get("t", 5.0) / 100.0,
                "sl": p.get("s", 3.0) / 100.0,
                "ob": p.get("ob", 80),
                "p": p.get("p", 7),
                "os": p.get("os", 30),
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
            "current_fee_bps": round(self.fee_rate * 10000, 1),
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

    def update_fee_rate(self, fee_bps):
        """Update fee rate when Gobblin unlocks a new tier."""
        new_rate = fee_bps / 10000.0
        if new_rate != self.fee_rate:
            self.fee_rate = new_rate
            return True
        return False

    def process_candle(self, pid, candle, event_path=None):
        cl = float(candle["close"])
        h = float(candle["high"])
        l = float(candle["low"])
        ts = int(candle.get("time", candle.get("start", 0)))
        st = self.coin[pid]
        if not st:
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
                        "fee_bps": round(self.fee_rate * 10000, 1),
                        "gross_pnl": round(gross, 4),
                        "fee": round(st["entry_fee"] + exit_fee, 4),
                        "net_pnl": round(net, 4),
                        "cash": round(st["cash"], 2),
                    })

        # Entry (cross detection)
        if not st["in_position"]:
            rsi_vals = rsi(st["price_hist"], st["p"])
            rsi_val = rsi_vals[-1] if rsi_vals else 50
            was_below = st["rsi_below_os"]
            is_below = rsi_val <= st["os"]

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

                        if event_path:
                            append_jsonl(event_path, {
                                "ts_utc": utc_now_iso(),
                                "action": "open",
                                "product": pid,
                                "entry_price": cl,
                                "entry_rsi": round(rsi_val, 2),
                                "deploy_usd": round(deploy, 2),
                                "fee_bps": round(self.fee_rate * 10000, 1),
                            })

            st["rsi_below_os"] = is_below

def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "runner": runner, "state": engine.snapshot()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--products", nargs="+", default=OPTIMIZED_4)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--maker-fee-bps", type=float, default=40.0)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--event-path", default=str(DEFAULT_EVENT_PATH))
    parser.add_argument("--params-path", default=str(ROOT / "reports" / "rsi_optimal_params.json"))
    args = parser.parse_args()

    client = CoinbaseAdvancedClient()

    # Load params
    with open(args.params_path) as f:
        all_params = json.load(f)
    params = {pid: all_params.get(pid, {}) for pid in args.products}

    engine = RSIParallelOptimized(args.products, params, args.starting_cash, args.maker_fee_bps)
    runner = {
        "script": "live_rsi_parallel_optimized.py",
        "pid": os.getpid(),
        "poll_seconds": args.poll_seconds,
        "started_at": utc_now_iso(),
        "last_successful_run_at": utc_now_iso(),
        "last_exception_at": None,
        "last_exception_type": "",
        "last_exception_message": "",
        "consecutive_exceptions": 0,
    }

    # Backfill
    now = int(time.time())
    start = now - 72 * 3600
    print(f"Backfilling 72h for optimized portfolio: {args.products}")
    for pid in args.products:
        try:
            raw = fetch_candles_chunked(client, pid, start, now)
            for c in new_candles_since(raw, engine.coin[pid]["last_candle_time"]):
                engine.process_candle(pid, c, args.event_path)
                engine.coin[pid]["last_candle_time"] = c["time"]
            print(f"  {pid}: {len(raw)} candles loaded")
        except Exception as e:
            print(f"  {pid}: backfill error: {e}")

    snap = engine.snapshot()
    print(f"Live started: cash=${snap['total_cash']:.2f} realized=${snap['total_realized']:.2f} "
          f"{snap['total_closes']} closes {snap['win_rate']}%WR fee={snap['current_fee_bps']}bps")
    save_state(Path(args.state_path), engine, runner)

    if args.once:
        return 0

    try:
        while True:
            try:
                # Check for fee tier updates from Gobblin
                current_fee = load_current_fee_bps()
                if engine.update_fee_rate(current_fee):
                    print(f"  FEE TIER UPDATE: {current_fee}bps")

                end = int(time.time())
                any_new = False
                for pid in args.products:
                    try:
                        raw = fetch_candles_chunked(client, pid, engine.coin[pid]["last_candle_time"], end)
                        new = new_candles_since(raw, engine.coin[pid]["last_candle_time"])
                        for c in new:
                            engine.process_candle(pid, c, args.event_path)
                            engine.coin[pid]["last_candle_time"] = c["time"]
                            any_new = True
                    except Exception:
                        pass

                if any_new or True:  # Always heartbeat
                    snap = engine.snapshot()
                    runner["last_successful_run_at"] = utc_now_iso()
                    save_state(Path(args.state_path), engine, runner)
                    print(f"  HB cash=${snap['total_cash']:.2f} realized=${snap['total_realized']:.2f} "
                          f"{snap['total_closes']}c {snap['win_rate']}%WR fee={snap['current_fee_bps']}bps", flush=True)
            except Exception as e:
                runner["consecutive_exceptions"] += 1
                runner["last_exception_at"] = utc_now_iso()
                runner["last_exception_type"] = type(e).__name__
                runner["last_exception_message"] = str(e)
                print(f"  EXC: {e}", flush=True)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0

if __name__ == "__main__":
    main()
