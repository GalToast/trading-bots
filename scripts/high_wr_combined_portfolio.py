#!/usr/bin/env python3
"""
High-WR Combined Portfolio Test — RAVE + GHST + BAL momentum only.

Tests the hypothesis: a shared-bankroll portfolio of ONLY >50% WR strategies
will work, because no single coin dominates with low-WR high-frequency signals.

This directly tests the combined portfolio question:
- @qwen-trading-bots found shared bankroll → $0 (with NOM range_breakout at 17.6% WR)
- I hypothesize: removing low-WR coins saves the shared pool
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
OUTPUT_PATH = ROOT / "reports" / "high_wr_combined_portfolio.json"

# Only coins with >50% WR in individual momentum backtests
COIN_CONFIGS = [
    {"coin": "RAVE-USD", "lookback": 15, "tp_pct": 0.10, "sl_pct": 0.00, "max_hold": 36},
    {"coin": "GHST-USD", "lookback": 20, "tp_pct": 0.15, "sl_pct": 0.03, "max_hold": 24},
    {"coin": "BAL-USD",  "lookback": 50, "tp_pct": 0.10, "sl_pct": 0.03, "max_hold": 36},
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


def run_combined_portfolio(coin_candles, coin_configs, starting_cash):
    """Run all coins simultaneously with shared bankroll."""
    cash = starting_cash
    total_volume = 0.0
    total_fees = 0.0

    lanes = {}
    for cfg in coin_configs:
        lanes[cfg["coin"]] = {
            "config": cfg,
            "position": None,
            "history": [],
            "candle_history": [],
            "signals": 0,
            "closes": 0,
            "wins": 0,
            "losses": 0,
            "coin_pnl": 0.0,
        }

    # Build timestamp lookup
    all_timestamps = set()
    for candles in coin_candles.values():
        for c in candles:
            all_timestamps.add(int(c["start"]))
    sorted_timestamps = sorted(all_timestamps)

    candle_lookup = {}
    for coin, candles in coin_candles.items():
        for c in candles:
            candle_lookup[(coin, int(c["start"]))] = c

    peak_equity = starting_cash
    max_dd = 0.0
    equity_curve = []

    for ts in sorted_timestamps:
        for cfg in coin_configs:
            coin = cfg["coin"]
            lane = lanes[coin]
            candle = candle_lookup.get((coin, ts))
            if candle is None:
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
                fee_rate = FEE_RATE
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
                    exit_fee = exit_price * units * fee_rate
                    net = gross - entry_fee - exit_fee

                    cash += pos["q"] + net
                    lane["closes"] += 1
                    lane["coin_pnl"] += net
                    total_volume += pos["q"] + (exit_price * units)
                    total_fees += entry_fee + exit_fee

                    if net > 0:
                        lane["wins"] += 1
                    else:
                        lane["losses"] += 1

                    lane["position"] = None

            # ENTRY
            if lane["position"] is None and cash >= 10.0:
                lookback = cfg["lookback"]
                if len(lane["candle_history"]) > lookback + 1:
                    recent_high = max(float(c["high"]) for c in lane["candle_history"][-(lookback+1):-1])
                    if high > recent_high:
                        lane["signals"] += 1

                        fee_rate = FEE_RATE
                        deploy = cash * 0.95
                        entry_price = open_price
                        entry_fee = deploy * fee_rate
                        units = (deploy - entry_fee) / entry_price

                        tp = entry_price * (1 + cfg["tp_pct"])
                        sl = entry_price * (1 - cfg["sl_pct"]) if cfg["sl_pct"] > 0 else 0

                        cash -= deploy
                        lane["position"] = {
                            "ep": entry_price,
                            "q": deploy,
                            "units": units,
                            "tp": tp,
                            "sl": sl,
                            "hold": 0,
                            "entry_fee": entry_fee,
                        }

        # Track equity
        pos_value = sum(l["position"]["q"] for l in lanes.values() if l["position"])
        equity = cash + pos_value
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd
        equity_curve.append(equity)

    # Close remaining positions
    for coin, lane in lanes.items():
        if lane["position"]:
            last_candles = lane["candle_history"][-1:]
            last_close = float(last_candles[0]["close"]) if last_candles else lane["position"]["ep"]
            pos = lane["position"]
            units = pos["units"]
            fee_rate = FEE_RATE
            gross = (last_close - pos["ep"]) * units
            entry_fee = pos["entry_fee"]
            exit_fee = last_close * units * fee_rate
            net = gross - entry_fee - exit_fee
            cash += pos["q"] + net
            lane["closes"] += 1
            lane["coin_pnl"] += net
            total_volume += pos["q"] + (last_close * units)
            total_fees += entry_fee + exit_fee
            if net > 0:
                lane["wins"] += 1
            else:
                lane["losses"] += 1
            lane["position"] = None

    total_equity = cash
    total_pnl = total_equity - starting_cash
    return_pct = total_pnl / starting_cash * 100

    total_closes = sum(l["closes"] for l in lanes.values())
    total_wins = sum(l["wins"] for l in lanes.values())
    overall_wr = total_wins / max(1, total_closes) * 100

    # Sharpe
    if len(equity_curve) > 1:
        returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
                   for i in range(1, len(equity_curve)) if equity_curve[i-1] > 0]
        if returns and len(returns) > 1:
            sharpe = (statistics.mean(returns) / statistics.stdev(returns)) * 86.4
        else:
            sharpe = 0
    else:
        sharpe = 0

    return {
        "total_equity": round(total_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "max_dd": round(max_dd, 1),
        "sharpe": round(sharpe, 2),
        "total_fees": round(total_fees, 2),
        "total_signals": sum(l["signals"] for l in lanes.values()),
        "total_closes": total_closes,
        "total_wins": total_wins,
        "overall_wr": round(overall_wr, 1),
        "coins": {
            coin: {
                "signals": lane["signals"],
                "closes": lane["closes"],
                "wins": lane["wins"],
                "losses": lane["losses"],
                "win_rate": round(lane["wins"] / max(1, lane["closes"]) * 100, 1),
                "coin_pnl": round(lane["coin_pnl"], 2),
            }
            for coin, lane in lanes.items()
        },
    }


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print(f"=" * 70, flush=True)
    print(f"HIGH-WR COMBINED PORTFOLIO TEST — {WINDOW_DAYS}d, ${STARTING_CASH}", flush=True)
    print(f"Coins: {', '.join(c['coin'] for c in COIN_CONFIGS)}", flush=True)
    print(f"=" * 70, flush=True)

    # Fetch candles
    coin_candles = {}
    for cfg in COIN_CONFIGS:
        coin = cfg["coin"]
        print(f"Fetching {coin}...", flush=True)
        candles = fetch_candles(client, coin, start, now)
        coin_candles[coin] = candles
        print(f"  {coin}: {len(candles)} candles", flush=True)

    # Run combined portfolio
    print(f"\nRunning combined portfolio (shared $48, all momentum)...", flush=True)
    result = run_combined_portfolio(coin_candles, COIN_CONFIGS, STARTING_CASH)

    print(f"\n{'='*70}", flush=True)
    print(f"RESULTS", flush=True)
    print(f"{'='*70}", flush=True)

    print(f"\n  Total PnL: ${result['total_pnl']:.2f} ({result['return_pct']:.1f}%)", flush=True)
    print(f"  Max DD: {result['max_dd']:.1f}%  |  Sharpe: {result['sharpe']:.2f}", flush=True)
    print(f"  Overall WR: {result['overall_wr']:.1f}% ({result['total_wins']}/{result['total_closes']})", flush=True)

    print(f"\n  Per-coin:", flush=True)
    for coin, data in result["coins"].items():
        print(f"    {coin:<14} | PnL=${data['coin_pnl']:>+8.2f} | WR={data['win_rate']:>5.1f}% | "
              f"Signals={data['signals']:>3} | Trades={data['closes']:>3}", flush=True)

    # Compare with the failed combined portfolio
    print(f"\n{'='*70}", flush=True)
    print(f"COMPARISON: High-WR vs Full Portfolio (from @qwen-trading-bots)", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"  Full portfolio (10 coins, mixed strategies): $0 (total loss)", flush=True)
    print(f"  High-WR portfolio (3 coins, all >50% WR):   ${result['total_pnl']:+.2f}", flush=True)

    if result['total_pnl'] > 0:
        print(f"\n  → HIGH-WR PORTFOLIO WORKS! Shared bankroll is viable with >50% WR coins only.", flush=True)
        print(f"  → The runner's design is VALIDATED — no per-coin bankroll needed for this config.", flush=True)
    else:
        print(f"\n  → HIGH-WR PORTFOLIO ALSO FAILS. @qwen-trading-bots was right — shared bankroll is doomed.", flush=True)
        print(f"  → Per-coin bankrolls are REQUIRED for any multi-coin deployment.", flush=True)

    # Save report
    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "starting_cash": STARTING_CASH,
        "coin_configs": COIN_CONFIGS,
        "result": result,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)


if __name__ == "__main__":
    main()
