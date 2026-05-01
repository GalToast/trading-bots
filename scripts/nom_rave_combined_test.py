#!/usr/bin/env python3
"""
NOM + RAVE Combined Test — Can range_breakout + momentum share a bankroll?

Tests:
1. RAVE momentum (lb=15, TP=10%, SL=0%) + NOM range_breakout (lb=10, TP=10%, SL=1%)
2. Shared $48 bankroll
3. Does NOM's high frequency (263 signals/month) destroy the shared pool?

This is THE question for scaling the runner.
"""
import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "nom_rave_combined_test.json"

COIN_CONFIGS = [
    {"coin": "RAVE-USD", "strategy": "momentum", "lookback": 15, "tp_pct": 0.10, "sl_pct": 0.00, "max_hold": 36},
    {"coin": "NOM-USD",  "strategy": "range_breakout", "lookback": 10, "tp_pct": 0.10, "sl_pct": 0.01, "max_hold": 24},
]

WINDOW_DAYS = 30
STARTING_CASH = 48.0
FEE_RATE = 0.0040


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
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
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.3)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c


def run_combined(coin_candles, coin_configs, starting_cash):
    cash = starting_cash
    lanes = {}
    for cfg in coin_configs:
        lanes[cfg["coin"]] = {
            "config": cfg, "position": None, "history": [], "candle_history": [],
            "signals": 0, "closes": 0, "wins": 0, "losses": 0, "coin_pnl": 0.0,
        }

    all_timestamps = set()
    for candles in coin_candles.values():
        for c in candles:
            all_timestamps.add(int(c["start"]))
    sorted_timestamps = sorted(all_timestamps)

    candle_lookup = {}
    for coin, candles in coin_candles.items():
        for c in candles:
            candle_lookup[(coin, int(c["start"]))] = c

    peak = starting_cash
    max_dd = 0.0
    equity_curve = []

    for ts in sorted_timestamps:
        for cfg in coin_configs:
            coin = cfg["coin"]
            lane = lanes[coin]
            candle = candle_lookup.get((coin, ts))
            if not candle:
                continue

            close = float(candle["close"])
            high = float(candle["high"])
            low = float(candle["low"])
            open_price = float(candle["open"])

            lane["history"].append(close)
            lane["candle_history"].append(candle)
            if len(lane["history"]) > 500:
                lane["history"] = lane["history"][-500:]

            # EXIT
            if lane["position"]:
                pos = lane["position"]
                pos["hold"] += 1
                exit_price = None
                exit_reason = None

                if high >= pos["tp"]:
                    exit_price = pos["tp"]
                    exit_reason = "tp"
                elif cfg["sl_pct"] > 0 and low <= pos["sl"]:
                    exit_price = pos["sl"]
                    exit_reason = "stop"
                elif pos["hold"] >= cfg["max_hold"]:
                    exit_price = close
                    exit_reason = "timeout"

                if exit_price is not None:
                    units = pos["units"]
                    gross = (exit_price - pos["ep"]) * units
                    entry_fee = pos["entry_fee"]
                    exit_fee = exit_price * units * FEE_RATE
                    net = gross - entry_fee - exit_fee

                    cash += pos["q"] + net
                    lane["closes"] += 1
                    lane["coin_pnl"] += net
                    if net > 0:
                        lane["wins"] += 1
                    else:
                        lane["losses"] += 1
                    lane["position"] = None

            # ENTRY — WITH GUARDS
            if lane["position"] is None and cash >= 10.0:
                lookback = cfg["lookback"]
                if len(lane["candle_history"]) > lookback + 1:
                    recent_high = max(float(c["high"]) for c in lane["candle_history"][-(lookback+1):-1])
                    if high > recent_high:
                        # GUARD: skip if open price is zero or invalid
                        if open_price <= 0:
                            continue
                        lane["signals"] += 1
                        deploy = cash * 0.95
                        entry_price = open_price
                        entry_fee = deploy * FEE_RATE
                        units = (deploy - entry_fee) / entry_price
                        tp = entry_price * (1 + cfg["tp_pct"])
                        sl = entry_price * (1 - cfg["sl_pct"]) if cfg["sl_pct"] > 0 else 0
                        cash -= deploy
                        lane["position"] = {
                            "ep": entry_price, "q": deploy, "units": units,
                            "tp": tp, "sl": sl, "hold": 0, "entry_fee": entry_fee,
                        }

        pos_value = sum(l["position"]["q"] for l in lanes.values() if l["position"])
        equity = cash + pos_value
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
        equity_curve.append(equity)

    # Close remaining
    for coin, lane in lanes.items():
        if lane["position"]:
            last_close = float(lane["candle_history"][-1]["close"]) if lane["candle_history"] else lane["position"]["ep"]
            pos = lane["position"]
            units = pos["units"]
            gross = (last_close - pos["ep"]) * units
            entry_fee = pos["entry_fee"]
            exit_fee = last_close * units * FEE_RATE
            net = gross - entry_fee - exit_fee
            cash += pos["q"] + net
            lane["closes"] += 1
            lane["coin_pnl"] += net
            if net > 0:
                lane["wins"] += 1
            else:
                lane["losses"] += 1
            lane["position"] = None

    total_pnl = cash - starting_cash
    total_closes = sum(l["closes"] for l in lanes.values())
    total_wins = sum(l["wins"] for l in lanes.values())
    wr = total_wins / max(1, total_closes) * 100

    if len(equity_curve) > 1:
        returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1] for i in range(1, len(equity_curve)) if equity_curve[i-1] > 0]
        sharpe = (statistics.mean(returns) / statistics.stdev(returns)) * 86.4 if len(returns) > 1 and statistics.stdev(returns) > 0 else 0
    else:
        sharpe = 0

    return {
        "total_pnl": round(total_pnl, 2), "return_pct": round(total_pnl/starting_cash*100, 1),
        "max_dd": round(max_dd, 1), "sharpe": round(sharpe, 2),
        "total_signals": sum(l["signals"] for l in lanes.values()),
        "total_closes": total_closes, "overall_wr": round(wr, 1),
        "coins": {coin: {"signals": l["signals"], "closes": l["closes"], "wins": l["wins"], "losses": l["losses"],
                         "win_rate": round(l["wins"]/max(1,l["closes"])*100, 1), "coin_pnl": round(l["coin_pnl"], 2)}
                  for coin, l in lanes.items()},
    }


def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"NOM + RAVE COMBINED TEST — {WINDOW_DAYS}d, ${STARTING_CASH}", flush=True)
    print(f"=" * 70, flush=True)

    coin_candles = {}
    for cfg in COIN_CONFIGS:
        print(f"Fetching {cfg['coin']}...", flush=True)
        candles = fetch_candles(client, cfg["coin"], start, now)
        coin_candles[cfg["coin"]] = candles
        print(f"  {cfg['coin']}: {len(candles)} candles", flush=True)

    print(f"\nRunning NOM + RAVE combined (shared $48)...", flush=True)
    result = run_combined(coin_candles, COIN_CONFIGS, STARTING_CASH)

    print(f"\n{'='*70}", flush=True)
    print(f"RESULTS", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"\n  Total PnL: ${result['total_pnl']:+.2f} ({result['return_pct']:+.1f}%)", flush=True)
    print(f"  Max DD: {result['max_dd']:.1f}%  |  Sharpe: {result['sharpe']:.2f}", flush=True)
    print(f"  Overall WR: {result['overall_wr']:.1f}% ({result['total_closes']} trades)", flush=True)

    print(f"\n  Per-coin:", flush=True)
    for coin, data in result["coins"].items():
        print(f"    {coin:<14} | PnL=${data['coin_pnl']:>+8.2f} | WR={data['win_rate']:>5.1f}% | "
              f"Signals={data['signals']:>3} | Trades={data['closes']:>3}", flush=True)

    # Compare
    print(f"\n{'='*70}", flush=True)
    print(f"COMPARISON", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"  RAVE alone (individual):  ~$1,858/month", flush=True)
    print(f"  NOM alone (individual):   ~$3,737/month", flush=True)
    print(f"  RAVE alone (combined):    ~${result['coins']['RAVE-USD']['coin_pnl']:+.2f}", flush=True)
    print(f"  NOM alone (combined):     ~${result['coins']['NOM-USD']['coin_pnl']:+.2f}", flush=True)
    print(f"  COMBINED total:           ${result['total_pnl']:+.2f}", flush=True)

    if result['total_pnl'] > 1858:  # Better than RAVE alone
        print(f"\n  → NOM ADDS VALUE in shared mode! Combined > RAVE alone.", flush=True)
        print(f"  → Add NOM to the live runner.", flush=True)
    else:
        print(f"\n  → NOM DESTROYS shared bankroll. Combined < RAVE alone.", flush=True)
        print(f"  → Run NOM in a SEPARATE instance with its own bankroll.", flush=True)

    report = {"run_at": utc_now_iso(), "window_days": WINDOW_DAYS, "starting_cash": STARTING_CASH,
              "coin_configs": COIN_CONFIGS, "result": result}
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)
    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
