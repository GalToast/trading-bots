#!/usr/bin/env python3
"""
God Mode Live Shadow
Combines:
1. Single-Position Round Robin
2. Geometric Compounding
3. 0.5% Laddering
4. Asymmetric Grid-Searched Targets & Stops
5. Dynamic Fee Tier Modeling
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
STATE_PATH = ROOT / "reports" / "burst_fade_god_mode_state.json"
EVENT_PATH = ROOT / "reports" / "burst_fade_god_mode_events.jsonl"

PRODUCT_PARAMS = {
    "RAVE-USD": {"bt": 2.0, "t": 0.6, "s": 0.1},
    "TROLL-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "BAL-USD": {"bt": 2.0, "t": 0.6, "s": 0.1},
    "NOM-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "MASK-USD": {"bt": 1.0, "t": 0.8, "s": 0.1},
    "ALEPH-USD": {"bt": 1.0, "t": 0.8, "s": 0.2},
    "CHECK-USD": {"bt": 2.0, "t": 1.0, "s": 0.1},
    "BLUR-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "AVT-USD": {"bt": 1.0, "t": 0.8, "s": 0.1},
    "IOTX-USD": {"bt": 2.0, "t": 1.0, "s": 0.2},
    "IRYS-USD": {"bt": 3.0, "t": 1.0, "s": 0.2},
    "CFG-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "BOBBOB-USD": {"bt": 2.0, "t": 0.6, "s": 0.1},
    "DASH-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "FARTCOIN-USD": {"bt": 2.0, "t": 0.6, "s": 0.2},
    "COMP-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "MON-USD": {"bt": 2.0, "t": 1.0, "s": 0.2},
    "ZEC-USD": {"bt": 2.0, "t": 1.0, "s": 0.1},
    "VVV-USD": {"bt": 2.0, "t": 0.8, "s": 0.1},
    "ALGO-USD": {"bt": 2.0, "t": 1.0, "s": 0.1},
    "ARB-USD": {"bt": 1.0, "t": 1.0, "s": 0.2},
    "ETH-USD": {"bt": 1.0, "t": 0.8, "s": 0.1},
    "BASED1-USD": {"bt": 2.0, "t": 1.0, "s": 0.1},
    "SKL-USD": {"bt": 1.0, "t": 0.8, "s": 0.1},
    "TAO-USD": {"bt": 2.0, "t": 0.8, "s": 0.2}
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

class GodModeShadow:
    def __init__(self, starting_cash=48.0, max_concurrent=5):
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions = []
        self.max_concurrent = max_concurrent
        
        self.realized_net = 0.0
        self.closes = 0
        self.wins = 0
        self.losses = 0
        self.total_volume = 0.0
        self.total_fees_paid = 0.0
        
        self.last_candle_time = {}

    def get_fee_rate(self):
        if self.total_volume >= 50000:
            return 0.0015
        elif self.total_volume >= 10000:
            return 0.0025
        else:
            return 0.0040

    def process_tick(self, all_candles_by_pid, event_path):
        events = []
        fee_rate = self.get_fee_rate()
        
        still_open = []
        for pos in self.positions:
            pid = pos["pid"]
            closed = False
            if pid in all_candles_by_pid:
                for c in all_candles_by_pid[pid]:
                    h = float(c["high"])
                    l = float(c["low"])
                    ep = pos["entry"]
                    tp = pos["target"]
                    sp = pos["stop"]
                    tq = pos["quote"]
                    units = tq / ep
                    
                    if l <= tp:
                        gross = (ep - tp) * units
                        ef = tq * fee_rate
                        xf = tp * units * fee_rate
                        net = gross - ef - xf
                        self.cash += tq + net
                        self.realized_net += net
                        self.closes += 1
                        self.wins += 1
                        self.total_volume += tq + (tp * units)
                        self.total_fees_paid += ef + xf
                        events.append({"ts_utc": utc_now_iso(), "action": "close_target", "product": pid,
                                       "entry": ep, "exit": tp, "net": round(net, 4), "fees": round(ef + xf, 4),
                                       "burst_range": pos.get("rp", 0), "size": round(tq, 2)})
                        closed = True
                        break
                    elif h >= sp:
                        gross = (ep - sp) * units
                        ef = tq * fee_rate
                        xf = sp * units * fee_rate
                        net = gross - ef - xf
                        self.cash += tq + net
                        self.realized_net += net
                        self.closes += 1
                        self.losses += 1
                        self.total_volume += tq + (sp * units)
                        self.total_fees_paid += ef + xf
                        events.append({"ts_utc": utc_now_iso(), "action": "close_stop", "product": pid,
                                       "entry": ep, "exit": sp, "net": round(net, 4), "fees": round(ef + xf, 4),
                                       "burst_range": pos.get("rp", 0), "size": round(tq, 2)})
                        closed = True
                        break
            
            if not closed:
                still_open.append(pos)
                
        self.positions = still_open
        
        # Entries
        free_slots = self.max_concurrent - len(self.positions)
        if free_slots > 0 and self.cash >= 10.0:
            candidates = []
            for pid in PRODUCTS:
                if pid not in all_candles_by_pid: continue
                if any(p["pid"] == pid for p in self.positions): continue
                
                for c in all_candles_by_pid[pid]:
                    params = PRODUCT_PARAMS.get(pid)
                    if not params: continue
                    o = float(c["open"])
                    h = float(c["high"])
                    l = float(c["low"])
                    close = float(c["close"])
                    mid = (o + close) / 2 if (o + close) > 0 else 1
                    rp = (h - l) / mid * 100
                    
                    if rp >= params["bt"]:
                        candidates.append({"pid": pid, "rp": rp, "c": c, "params": params})
            
            candidates.sort(key=lambda x: x["rp"], reverse=True)
            
            for cand in candidates[:free_slots]:
                if self.cash < 10.0: break
                
                pid = cand["pid"]
                c = cand["c"]
                params = cand["params"]
                rp = cand["rp"]
                
                alloc_fraction = 1.0 / free_slots
                if rp >= params["bt"] * 1.5:
                    alloc_fraction = min(1.0, alloc_fraction * 1.5)
                
                tq = min(self.cash * 0.95, self.cash * alloc_fraction * 0.95)
                if tq < 10.0: continue
                
                burst_high = float(c["high"])
                ep = burst_high * 1.005 # Laddering 0.5%
                tp = ep * (1 - rp / 100 * params["t"])
                sp = ep * (1 + rp / 100 * params["s"])
                
                self.positions.append({"pid": pid, "entry": ep, "target": tp, "stop": sp, "quote": tq, "rp": rp})
                self.cash -= tq
                events.append({"ts_utc": utc_now_iso(), "action": "open_fade", "product": pid,
                               "entry": ep, "target": round(tp, 6), "stop": round(sp, 6),
                               "range_pct": round(rp, 4), "threshold": params["bt"], "size": round(tq, 2)})
                free_slots -= 1
                
        return events

    def snapshot(self):
        return {
            "starting_cash": self.starting_cash,
            "cash": round(self.cash, 4),
            "realized_net_usd": round(self.realized_net, 4),
            "closes": self.closes,
            "wins": self.wins,
            "losses": self.losses,
            "total_fees": round(self.total_fees_paid, 4),
            "total_volume": round(self.total_volume, 4),
            "fee_rate_bps": round(self.get_fee_rate() * 10000, 1),
            "positions": self.positions,
            "win_rate": round(self.wins / max(1, self.closes) * 100, 2),
            "avg_pnl_per_close": round(self.realized_net / max(1, self.closes), 4),
        }

def save_state(path, engine, runner):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": utc_now_iso(), "engine": engine.snapshot(), "runner": runner}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--starting-cash", type=float, default=48.0)
    parser.add_argument("--max-concurrent", type=int, default=5)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--event-path", default=str(EVENT_PATH))
    parser.add_argument("--fresh-start", action="store_true")
    args = parser.parse_args()
    
    state_path = Path(args.state_path)
    event_path = Path(args.event_path)

    client = CoinbaseAdvancedClient()
    engine = GodModeShadow(
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
        engine.last_candle_time[pid] = 0
        print(f"  {pid}: {len(c)} candles", flush=True)
        time.sleep(0.2)

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
    for t in all_times:
        tick_candles = time_lookup.get(t, {})
        events = engine.process_tick(tick_candles, event_path)
        for ev in events:
            append_jsonl(event_path, ev)

    print(f"Backfill: {engine.closes} closes, {engine.wins}W/{engine.losses}L, net=${engine.realized_net:.2f}, vol=${engine.total_volume:.2f}, fees=${engine.total_fees_paid:.2f}", flush=True)

    event_path.write_text(f"# Live God Mode events starting {utc_now_iso()}\n", encoding="utf-8")
    runner["heartbeat_at"] = utc_now_iso()
    runner["last_successful_run_at"] = runner["heartbeat_at"]
    save_state(state_path, engine, runner)

    snap = engine.snapshot()
    print(f"Live shadow: cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} closes={snap['closes']} WR={snap['win_rate']:.1f}% Tier={snap['fee_rate_bps']}bps", flush=True)

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
                pos_str = f"{len(snap['positions'])}pos" if snap['positions'] else "flat"
                print(f"  HB cash=${snap['cash']:.2f} net=${snap['realized_net_usd']:.2f} {snap['closes']}c {snap['win_rate']:.1f}%WR Tier={snap['fee_rate_bps']}bps {pos_str}", flush=True)
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
