#!/usr/bin/env python3
"""
Round-Robin Best-Burst Live Shadow
At each 5-min candle, scan ALL products, take the BIGGEST burst.
One position at a time. $48 full deployment. 70% target, 20% stop.

Per-product optimal burst thresholds:
  CHECK-USD: 2.0%, BAL-USD: 3.0%, BLUR-USD: 3.0%, ALEPH-USD: 3.0%, CFG-USD: 3.0%,
  COMP-USD: 1.0%, DASH-USD: 3.0%, BASED1-USD: 1.7%, AVT-USD: 1.0%, BOBBOB-USD: 2.3%
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "reports" / "burst_fade_roundrobin_compound_state.json"
EVENT_PATH = ROOT / "reports" / "burst_fade_roundrobin_compound_events.jsonl"

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

# Per-product optimal parameters: {"burst_thresh", "target_frac", "stop_frac"}
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

PRODUCTS = list(PRODUCT_PARAMS.keys())


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


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
            if not cands: break
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


class RoundRobinBestBurstShadow:
    def __init__(self, products, product_params, quote=48.0, starting_cash=48.0):
        self.products = products
        self.product_params = product_params
        self.quote = quote
        self.starting_cash = starting_cash

        self.cash = starting_cash
        self.position = None  # {"pid": ..., "entry": ..., "target": ..., "stop": ..., "range_pct": ...}
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.fees = 0.0
        self.last_candle_time = {}  # pid -> int

    def process_tick(self, all_candles_by_pid, event_path):
        events = []

        # Exit first
        if self.position:
            pid = self.position["pid"]
            if pid in all_candles_by_pid:
                for c in all_candles_by_pid[pid]:
                    h = float(c["high"])
                    l = float(c["low"])
                    ep = self.position["entry"]
                    tp = self.position["target"]
                    sp = self.position["stop"]
                    trade_quote = self.position.get("quote", self.quote)
                    units = trade_quote / ep
                    if l <= tp:
                        gross = (ep - tp) * units
                        ef = ep * units * FEE_RATE
                        xf = tp * units * FEE_RATE
                        net = gross - ef - xf
                        self.realized_net += net
                        self.closes += 1
                        self.wins += 1
                        self.fees += ef + xf
                        self.cash += trade_quote + net
                        events.append({"ts_utc": utc_now_iso(), "action": "close_target", "product": pid,
                                       "entry": ep, "exit": tp, "net": round(net, 4), "fees": round(ef + xf, 4),
                                       "burst_range": self.position.get("range_pct", 0), "size": round(trade_quote, 2)})
                        self.position = None
                        break
                    elif h >= sp:
                        gross = (ep - sp) * units
                        ef = ep * units * FEE_RATE
                        xf = sp * units * FEE_RATE
                        net = gross - ef - xf
                        self.realized_net += net
                        self.closes += 1
                        self.losses += 1
                        self.fees += ef + xf
                        self.cash += trade_quote + net
                        events.append({"ts_utc": utc_now_iso(), "action": "close_stop", "product": pid,
                                       "entry": ep, "exit": sp, "net": round(net, 4), "fees": round(ef + xf, 4),
                                       "burst_range": self.position.get("range_pct", 0), "size": round(trade_quote, 2)})
                        self.position = None
                        break

        # Find the BIGGEST burst across all products
        if self.position is None and self.cash >= 10.0:
            best_range = 0
            best_pid = None
            best_c = None
            for pid in self.products:
                if pid not in all_candles_by_pid:
                    continue
                for c in all_candles_by_pid[pid]:
                    o = float(c["open"])
                    h = float(c["high"])
                    l = float(c["low"])
                    close = float(c["close"])
                    mid = (o + close) / 2 if (o + close) > 0 else 1
                    rp = (h - l) / mid * 100
                    
                    params = self.product_params.get(pid)
                    if not params: continue
                    bt = params.get("bt", 2.0)
                    
                    if rp > best_range and rp >= bt:
                        best_range = rp
                        best_pid = pid
                        best_c = c

            if best_pid and best_c:
                params = self.product_params[best_pid]
                trade_quote = self.cash * 0.95  # Compound with 95% of available cash
                burst_high = float(best_c["high"])
                
                # Laddering: Assume limit fill at burst high + 0.5%
                entry = burst_high * 1.005
                
                target = entry * (1 - best_range / 100 * params.get("t", 0.7))
                stop = entry * (1 + best_range / 100 * params.get("s", 0.2))
                
                self.position = {"pid": best_pid, "entry": entry, "target": target, "stop": stop, "range_pct": round(best_range, 4), "quote": trade_quote}
                self.cash -= trade_quote
                events.append({"ts_utc": utc_now_iso(), "action": "open_fade", "product": best_pid,
                               "entry": entry, "target": round(target, 6), "stop": round(stop, 6),
                               "range_pct": round(best_range, 4), "threshold": params.get("bt", 2.0), "size": round(trade_quote, 2)})

        return events

    def snapshot(self):
        return {
            "products": self.products,
            "product_params": self.product_params,
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 4),
            "realized_net_usd": round(self.realized_net, 4),
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "total_fees": round(self.fees, 4),
            "position": self.position,
            "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "avg_pnl_per_close": round(self.realized_net / max(1, self.closes), 4),
        }


def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quote", type=float, default=48.0)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--event-path", default=str(EVENT_PATH))
    parser.add_argument("--fresh-start", action="store_true")
    args = parser.parse_args()
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)

    client = CoinbaseAdvancedClient()
    engine = RoundRobinBestBurstShadow(
        products=PRODUCTS,
        product_params=PRODUCT_PARAMS,
        quote=args.quote,
        starting_cash=args.starting_cash,
    )

    runner = {
        "pid": os.getpid(), "script": Path(__file__).name, "started_at": utc_now_iso(),
        "poll_seconds": args.poll_seconds, "heartbeat_at": None,
        "last_successful_run_at": None, "consecutive_exceptions": 0,
        "last_exception_at": None, "last_exception_type": "", "last_exception_message": "",
    }

    # Backfill 72h
    now = int(time.time())
    start = now - 72 * 3600

    print("Fetching candles for all products...", flush=True)
    all_product_candles = {}
    for pid in PRODUCTS:
        c = fetch_candles_chunked(client, pid, start, now)
        all_product_candles[pid] = c
        engine.last_candle_time[pid] = 0
        print(f"  {pid}: {len(c)} candles", flush=True)
        time.sleep(0.2)

    # Merge into timeline
    all_times = set()
    for pid, candles in all_product_candles.items():
        for c in candles:
            all_times.add(int(c["start"]))
    all_times = sorted(all_times)

    time_lookup = {}
    for pid, candles in all_product_candles.items():
        for c in candles:
            t = int(c["start"])
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t].setdefault(pid, []).append(c)
            if t > engine.last_candle_time.get(pid, 0):
                engine.last_candle_time[pid] = t

    print(f"Processing {len(all_times)} time steps (72h backfill)...", flush=True)
    total_events = 0
    for t in all_times:
        tick_candles = time_lookup.get(t, {})
        events = engine.process_tick(tick_candles, event_path)
        for ev in events:
            append_jsonl(event_path, ev)
            total_events += 1

    print(f"Backfill: {engine.closes} closes, {engine.wins}W/{engine.losses}L, net=${engine.realized_net:.2f}, fees=${engine.fees:.2f}", flush=True)

    # Save state, clear events for live
    event_path.write_text(f"# Live round-robin best-burst events starting {utc_now_iso()}\n", encoding="utf-8")
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(state_path, engine, runner)

    snap = engine.snapshot()
    print(f"Live shadow: cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} closes={snap['closes']} WR={snap['win_rate']:.1f}%", flush=True)

    # Live loop
    try:
        while True:
            try:
                end = int(time.time())
                tick_candles = {}
                for pid in PRODUCTS:
                    st = engine.last_candle_time.get(pid, end - 3600)
                    try:
                        resp = client.market_candles(pid, start=st, end=end, granularity="FIVE_MINUTE")
                        new_c = [c for c in resp.get("candles", []) if int(c["start"]) > engine.last_candle_time.get(pid, 0)]
                        if new_c:
                            tick_candles[pid] = new_c
                            for c in new_c:
                                engine.last_candle_time[pid] = max(engine.last_candle_time.get(pid, 0), int(c["start"]))
                    except:
                        pass

                if tick_candles:
                    events = engine.process_tick(tick_candles, event_path)
                    for ev in events:
                        append_jsonl(event_path, ev)

                runner["heartbeat_at"] = utc_now_iso()
                runner["last_successful_run_at"] = runner["heartbeat_at"]
                runner["consecutive_exceptions"] = 0
                save_state(state_path, engine, runner)
                snap = engine.snapshot()
                pos_str = f"1pos @{snap['position']['pid']}" if snap['position'] else "flat"
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} {snap['closes']}c {snap['win_rate']:.1f}%WR {pos_str}", flush=True)
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
