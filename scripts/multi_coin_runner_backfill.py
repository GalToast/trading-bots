#!/usr/bin/env python3
"""
Multi-Coin Runner Backfill Validation Mode.

Runs the current multi-coin runner config through 30d of historical data
to validate integrated shared-bankroll behavior before live deployment.

Output: reports/multi_coin_runner_backfill.json
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient
import multi_coin_portfolio as live_runner

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "reports" / "multi_coin_runner_backfill.json"

WINDOW_DAYS = 30
STARTING_CASH = 48.0
MIN_CASH_PER_POSITION = 10.0
DEPLOY_FRACTION = 0.95


def build_coin_configs() -> list[dict]:
    configs: list[dict] = []
    for coin, cfg in live_runner.STRATEGY_CONFIGS.items():
        row = {
            "coin": coin,
            "strategy": cfg["type"],
            "tp_pct": cfg["tp_pct"],
            "sl_pct": cfg["sl_pct"],
            "max_hold": cfg["max_hold"],
        }
        if cfg["type"] == "momentum":
            row["lookback"] = cfg["lookback"]
        elif cfg["type"] == "range_breakout":
            row["range_lookback"] = cfg["range_lookback"]
        elif cfg["type"] == "rsi_mr":
            row["rsi_period"] = cfg["rsi_period"]
            row["os_thresh"] = cfg["os_thresh"]
        configs.append(row)
    return configs


COIN_CONFIGS = build_coin_configs()


def utc_now_iso() -> str:
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


def get_fee_rate(total_volume: float) -> float:
    if total_volume >= 50000:
        return 0.0015
    if total_volume >= 10000:
        return 0.0025
    return 0.0040


def compute_rsi(closes, period):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l > 0:
        rs = avg_g / avg_l
        return 100 - 100 / (1 + rs)
    return 100.0


def range_breakout_signal(candle_history, current_high: float, range_lookback: int) -> bool:
    if len(candle_history) <= range_lookback:
        return False
    recent_high = max(float(c["high"]) for c in candle_history[-(range_lookback + 1):-1])
    return current_high > recent_high


def run_multi_coin_backtest(coin_candles, coin_configs, starting_cash):
    """
    Run all coin lanes simultaneously through historical data.
    Processes candles in timestamp order with a shared bankroll.
    """
    cash = starting_cash
    total_volume = 0.0
    total_fees_paid = 0.0

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
            "coin_fees": 0.0,
            "coin_volume": 0.0,
        }

    all_timestamps = set()
    for candles in coin_candles.values():
        for candle in candles:
            all_timestamps.add(int(candle["start"]))
    sorted_timestamps = sorted(all_timestamps)

    candle_lookup = {}
    for coin, candles in coin_candles.items():
        for candle in candles:
            candle_lookup[(coin, int(candle["start"]))] = candle

    equity_curve = []
    peak_equity = starting_cash
    max_dd = 0.0

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
                lane["candle_history"] = lane["candle_history"][-500:]

            if lane["position"]:
                pos = lane["position"]
                pos["hold"] += 1
                fee_rate = get_fee_rate(total_volume)
                exit_price = None

                if high >= pos["tp"]:
                    exit_price = pos["tp"]
                elif cfg["sl_pct"] > 0 and low <= pos["sl"]:
                    exit_price = pos["sl"]
                elif pos["hold"] >= cfg["max_hold"]:
                    exit_price = close

                if exit_price is not None:
                    units = pos["units"]
                    gross = (exit_price - pos["ep"]) * units
                    entry_fee = pos["entry_fee"]
                    exit_fee = exit_price * units * fee_rate
                    net = gross - entry_fee - exit_fee

                    cash += pos["q"] + net
                    lane["closes"] += 1
                    lane["coin_pnl"] += net
                    lane["coin_fees"] += entry_fee + exit_fee
                    lane["coin_volume"] += pos["q"] + (exit_price * units)
                    total_volume += pos["q"] + (exit_price * units)
                    total_fees_paid += entry_fee + exit_fee

                    if net > 0:
                        lane["wins"] += 1
                    else:
                        lane["losses"] += 1

                    lane["position"] = None

            if lane["position"] is None and cash >= MIN_CASH_PER_POSITION:
                signal_fired = False
                strategy = cfg.get("strategy")

                if strategy == "rsi_mr":
                    rsi_period = cfg.get("rsi_period", 3)
                    os_thresh = cfg.get("os_thresh", 30)
                    if len(lane["history"]) > rsi_period + 1:
                        rsi_val = compute_rsi(lane["history"][:-1], rsi_period)
                        if rsi_val <= os_thresh:
                            signal_fired = True
                elif strategy == "range_breakout":
                    signal_fired = range_breakout_signal(
                        lane["candle_history"], high, cfg.get("range_lookback", 20)
                    )
                else:
                    lookback = cfg.get("lookback", 10)
                    if len(lane["candle_history"]) > lookback + 1:
                        recent_high = max(
                            float(c["high"]) for c in lane["candle_history"][-(lookback + 1):-1]
                        )
                        if high > recent_high:
                            signal_fired = True

                if signal_fired:
                    lane["signals"] += 1
                    fee_rate = get_fee_rate(total_volume)
                    deploy = cash * DEPLOY_FRACTION
                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / open_price
                    tp = open_price * (1 + cfg["tp_pct"])
                    sl = open_price * (1 - cfg["sl_pct"]) if cfg["sl_pct"] > 0 else 0

                    cash -= deploy
                    lane["position"] = {
                        "ep": open_price,
                        "q": deploy,
                        "units": units,
                        "tp": tp,
                        "sl": sl,
                        "hold": 0,
                        "entry_fee": entry_fee,
                    }

        pos_value = sum(l["position"]["q"] for l in lanes.values() if l["position"])
        equity = cash + pos_value
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        equity_curve.append(equity)

    for lane in lanes.values():
        if lane["position"]:
            last_close = float(lane["candle_history"][-1]["close"]) if lane["candle_history"] else lane["position"]["ep"]
            pos = lane["position"]
            fee_rate = get_fee_rate(total_volume)
            units = pos["units"]
            gross = (last_close - pos["ep"]) * units
            entry_fee = pos["entry_fee"]
            exit_fee = last_close * units * fee_rate
            net = gross - entry_fee - exit_fee

            cash += pos["q"] + net
            lane["closes"] += 1
            lane["coin_pnl"] += net
            lane["coin_fees"] += entry_fee + exit_fee
            lane["coin_volume"] += pos["q"] + (last_close * units)
            total_volume += pos["q"] + (last_close * units)
            total_fees_paid += entry_fee + exit_fee

            if net > 0:
                lane["wins"] += 1
            else:
                lane["losses"] += 1

            lane["position"] = None

    total_equity = cash
    total_pnl = total_equity - starting_cash
    return_pct = total_pnl / starting_cash * 100

    sharpe = 0.0
    if len(equity_curve) > 1:
        returns = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0
        ]
        if len(returns) > 1:
            avg_ret = statistics.mean(returns)
            std_ret = statistics.stdev(returns)
            sharpe = (avg_ret / std_ret) * 86.4 if std_ret > 0 else 0.0

    coin_results = {}
    for coin, lane in lanes.items():
        wr = lane["wins"] / max(1, lane["closes"]) * 100
        coin_results[coin] = {
            "net_pnl": round(lane["coin_pnl"], 2),
            "win_rate": round(wr, 1),
            "closes": lane["closes"],
            "wins": lane["wins"],
            "losses": lane["losses"],
            "signals": lane["signals"],
            "total_fees": round(lane["coin_fees"], 2),
            "total_volume": round(lane["coin_volume"], 2),
        }

    return {
        "total_equity": round(total_equity, 2),
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 1),
        "max_dd": round(max_dd, 1),
        "sharpe": round(sharpe, 2),
        "total_fees": round(total_fees_paid, 2),
        "total_volume": round(total_volume, 2),
        "total_signals": sum(l["signals"] for l in lanes.values()),
        "total_closes": sum(l["closes"] for l in lanes.values()),
        "total_wins": sum(l["wins"] for l in lanes.values()),
        "total_losses": sum(l["losses"] for l in lanes.values()),
        "coins": coin_results,
        "equity_curve_length": len(equity_curve),
    }


def main():
    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - WINDOW_DAYS * 86400

    print("=" * 70, flush=True)
    print("MULTI-COIN RUNNER â€” 30d BACKFILL VALIDATION", flush=True)
    print(f"Coins: {', '.join(cfg['coin'] for cfg in COIN_CONFIGS)}", flush=True)
    print(f"Starting cash: ${STARTING_CASH}", flush=True)
    print("=" * 70, flush=True)

    coin_candles = {}
    for cfg in COIN_CONFIGS:
        coin = cfg["coin"]
        print(f"Fetching {WINDOW_DAYS}d candles for {coin}...", flush=True)
        candles = fetch_candles(client, coin, start, now)
        coin_candles[coin] = candles
        print(f"  {coin}: {len(candles)} candles", flush=True)

    print("\nRunning multi-coin backtest...", flush=True)
    result = run_multi_coin_backtest(coin_candles, COIN_CONFIGS, STARTING_CASH)

    print(f"\n{'=' * 70}", flush=True)
    print(f"BACKFILL RESULTS â€” 30d, ${STARTING_CASH}", flush=True)
    print(f"{'=' * 70}", flush=True)
    print(f"\n  Total PnL: ${result['total_pnl']:.2f} ({result['return_pct']:.1f}%)", flush=True)
    print(f"  Max DD: {result['max_dd']:.1f}%  |  Sharpe: {result['sharpe']:.2f}", flush=True)
    print(f"  Total signals: {result['total_signals']}  |  Closes: {result['total_closes']}", flush=True)
    print(f"  Win rate: {result['total_wins']}/{result['total_closes']} = {result['total_wins'] / max(1, result['total_closes']) * 100:.1f}%", flush=True)
    print(f"  Total fees: ${result['total_fees']:.2f}", flush=True)

    ordered_coins = [cfg["coin"] for cfg in COIN_CONFIGS]
    print(f"\n{'=' * 70}", flush=True)
    print("PER-COIN BREAKDOWN", flush=True)
    print(f"{'=' * 70}", flush=True)
    print(f"{'Coin':<14} | {'PnL':>8} | {'WR':>5} | {'Trades':>6} | {'Signals':>7} | {'Fees':>7}", flush=True)
    print(f"{'-' * 14}-+-{'-' * 8}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 7}-+-{'-' * 7}", flush=True)
    for coin in ordered_coins:
        row = result["coins"].get(coin, {})
        print(
            f"{coin:<14} | ${row.get('net_pnl', 0):>7.2f} | {row.get('win_rate', 0):>4.1f}% | "
            f"{row.get('closes', 0):>6} | {row.get('signals', 0):>7} | ${row.get('total_fees', 0):>6.2f}",
            flush=True,
        )

    print(f"\n  Key insight: The runner shares $48 across {len(COIN_CONFIGS)} coins.", flush=True)
    print("  This backfill uses the same source config as the live runner.", flush=True)

    report = {
        "run_at": utc_now_iso(),
        "window_days": WINDOW_DAYS,
        "starting_cash": STARTING_CASH,
        "coin_configs": COIN_CONFIGS,
        "runner_result": result,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
