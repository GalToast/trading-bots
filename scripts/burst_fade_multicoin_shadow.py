#!/usr/bin/env python3
"""
Multi-Coin Burst Fade Rotation Shadow — deploy $24 across 10 products simultaneously.
Max 2 concurrent positions. B_peak_limit entry (limit sell at burst candle high).
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
STATE_PATH = ROOT / "reports" / "burst_fade_multicoin_rotation_state.json"
EVENT_PATH = ROOT / "reports" / "burst_fade_multicoin_rotation_events.jsonl"

MAKER_FEE_BPS = 40.0
FEE_RATE = MAKER_FEE_BPS / 10000.0

PRODUCTS = ["BAL-USD", "CHECK-USD", "ALEPH-USD", "BLUR-USD", "BOBBOB-USD", "CFG-USD", "COMP-USD", "DASH-USD", "BASED1-USD", "AVT-USD"]


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def fetch_candles_chunked(client, pid, start, end):
    """Fetch candles in chunks respecting 350-candle limit."""
    chunk_sec = 300 * 5 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity="FIVE_MINUTE")
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


class MultiCoinRotationShadow:
    def __init__(self, products, quote=24.0, starting_cash=48.0, burst_thresh=2.0,
                 target_frac=0.5, stop_frac=0.3, max_concurrent=2):
        self.products = products
        self.quote = quote
        self.starting_cash = starting_cash
        self.burst_thresh = burst_thresh
        self.target_frac = target_frac
        self.stop_frac = stop_frac
        self.max_concurrent = max_concurrent

        self.cash = starting_cash
        self.positions = {}  # pid -> {"entry", "target", "stop"}
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.fees = 0.0
        self.last_candle_time = {}  # pid -> time

    def process_tick(self, all_candles_by_pid, event_path):
        """Process one tick of candles across all products."""
        events = []
        # Process exits first
        exit_pids = []
        for pid, pos in list(self.positions.items()):
            if pid not in all_candles_by_pid:
                continue
            for c in all_candles_by_pid[pid]:
                h = float(c["high"])
                l = float(c["low"])
                ep = pos["entry"]
                tp = pos["target"]
                sp = pos["stop"]
                units = self.quote / ep
                if l <= tp:
                    gross = (ep - tp) * units
                    ef = ep * units * FEE_RATE
                    xf = tp * units * FEE_RATE
                    net = gross - ef - xf
                    self.realized_net += net
                    self.closes += 1
                    self.wins += 1
                    self.fees += ef + xf
                    self.cash += self.quote + net
                    events.append({"ts_utc": utc_now_iso(), "action": "close_target", "product": pid,
                                   "entry": ep, "exit": tp, "net": round(net, 4), "fees": round(ef + xf, 4)})
                    exit_pids.append(pid)
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
                    self.cash += self.quote + net
                    events.append({"ts_utc": utc_now_iso(), "action": "close_stop", "product": pid,
                                   "entry": ep, "exit": sp, "net": round(net, 4), "fees": round(ef + xf, 4)})
                    exit_pids.append(pid)
                    break

        for pid in exit_pids:
            self.positions.pop(pid, None)

        # Check for new entries
        if self.cash >= self.quote and len(self.positions) < self.max_concurrent:
            for pid in self.products:
                if pid in self.positions or pid not in all_candles_by_pid:
                    continue
                for c in all_candles_by_pid[pid]:
                    o = float(c["open"])
                    h = float(c["high"])
                    l = float(c["low"])
                    close = float(c["close"])
                    mid = (o + close) / 2 if (o + close) > 0 else 1
                    range_pct = (h - l) / mid * 100
                    if range_pct >= self.burst_thresh:
                        entry = h
                        target = entry * (1 - range_pct / 100 * self.target_frac)
                        stop = entry * (1 + range_pct / 100 * self.stop_frac)
                        self.positions[pid] = {"entry": entry, "target": target, "stop": stop}
                        self.cash -= self.quote
                        events.append({"ts_utc": utc_now_iso(), "action": "open_fade", "product": pid,
                                       "entry": entry, "target": round(target, 6), "stop": round(stop, 6),
                                       "range_pct": round(range_pct, 4)})
                        break

        return events

    def snapshot(self):
        return {
            "products": self.products,
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 4),
            "realized_net_usd": round(self.realized_net, 4),
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "total_fees": round(self.fees, 4),
            "open_positions": {pid: {"entry": p["entry"], "target": p["target"], "stop": p["stop"]} for pid, p in self.positions.items()},
            "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "avg_pnl_per_close": round(self.realized_net / max(1, self.closes), 4),
        }


def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quote", type=float, default=24.0)
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--max-concurrent", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--event-path", default=str(EVENT_PATH))
    parser.add_argument("--fresh-start", action="store_true")
    args = parser.parse_args()
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)

    client = CoinbaseAdvancedClient()
    engine = MultiCoinRotationShadow(
        products=PRODUCTS,
        quote=args.quote,
        starting_cash=args.starting_cash,
        max_concurrent=args.max_concurrent,
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
        print(f"  {pid}: {len(c)} candles", flush=True)

    # Merge into timeline
    all_times = set()
    for pid, candles in all_product_candles.items():
        for c in candles:
            all_times.add(int(c["start"]))
    all_times = sorted(all_times)

    # Build lookup
    time_lookup = {}
    for pid, candles in all_product_candles.items():
        for c in candles:
            t = int(c["start"])
            if t not in time_lookup:
                time_lookup[t] = {}
            time_lookup[t][pid] = c
            if t > engine.last_candle_time.get(pid, 0):
                engine.last_candle_time[pid] = t

    print(f"Processing {len(all_times)} time steps...", flush=True)
    total_events = 0
    for t in all_times:
        tick_candles_raw = time_lookup.get(t, {})
        # Wrap single candle dicts in lists for process_tick compatibility
        tick_candles = {pid: [c] for pid, c in tick_candles_raw.items()}
        events = engine.process_tick(tick_candles, event_path)
        for ev in events:
            append_jsonl(event_path, ev)
            total_events += 1

    print(f"Backfill: {engine.closes} closes, {engine.wins}W/{engine.losses}L, net=${engine.realized_net:.2f}, fees=${engine.fees:.2f}", flush=True)

    # Clear events for live, save state
    event_path.write_text(f"# Live multi-coin rotation events starting {utc_now_iso()}\n", encoding="utf-8")
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(state_path, engine, runner)

    snap = engine.snapshot()
    print(f"Live shadow started. Cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} WR={snap['win_rate']:.1f}%", flush=True)

    # Live loop — fetch latest candles for each product every poll
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
                pos_str = f"{len(snap['open_positions'])}pos" if snap['open_positions'] else "flat"
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
