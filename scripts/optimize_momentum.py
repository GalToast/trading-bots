#!/usr/bin/env python3
"""
Momentum Breakout Parameter Optimization.

Fine-grained sweep of momentum breakout params for the profitable coins:
RAVE, BAL, IOTX, BLUR, FET

Grid:
- Lookback: 10, 15, 20, 25, 30, 40, 50, 75, 100
- TP: 3%, 5%, 7%, 10%, 12%, 15%, 20%
- SL: 2%, 3%, 4%, 5%, 7%, 10%
= 9 × 7 × 6 = 378 combos per coin × 5 coins = 1890 backtests

Usage:
    python scripts/optimize_momentum.py --window 30d
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from benchmark_regime_segmented import (
    fetch_candles_coinbase,
    normalize_candles,
    FEE_TIERS,
)

ROOT = Path(__file__).resolve().parent.parent

PROFITABLE_COINS = ["RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD", "FET-USD"]

LOOKBACKS = [10, 15, 20, 25, 30, 40, 50, 75, 100]
TPS = [3, 5, 7, 10, 12, 15, 20]
SLS = [2, 3, 4, 5, 7, 10]


def momentum_breakout(candles, lookback, tp_pct, sl_pct, fee_rate,
                      starting_cash=100.0, seed=42):
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    entry_slip = 0.0008
    exit_slip = 0.0

    for i in range(len(candles)):
        c = candles[i]
        high = c["high"]
        low = c["low"]
        candle_open = c["open"]

        ts = c["start"]
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        session_open = hour not in {0, 6, 12, 19}

        # EXIT
        if pos:
            pos["hold"] += 1
            exit_price = None
            if high >= pos["tp"]:
                exit_price = pos["tp"]
            elif pos["sl"] > 0 and low <= pos["sl"]:
                exit_price = pos["sl"]
            elif pos["hold"] >= pos["max_hold"]:
                exit_price = c["close"]

            if exit_price is not None:
                actual_exit = exit_price * (1 - exit_slip)
                units = pos["units"]
                gross = (actual_exit - pos["ep"]) * units
                entry_fee = pos["entry_fee"]
                exit_fee = actual_exit * units * fee_rate
                net = gross - entry_fee - exit_fee
                cash += pos["q"] + net
                closes_count += 1
                if net > 0:
                    wins += 1
                else:
                    losses += 1
                peak = max(peak, cash)
                dd = (peak - cash) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
                pos = None

        # ENTRY: breakout
        if pos is None and session_open and cash >= 10.0 and i >= lookback:
            highest = max(candles[j]["high"] for j in range(i - lookback, i))
            if high > highest:
                actual_entry = candle_open * (1 + entry_slip)
                deploy = cash
                if deploy < 10.0:
                    continue
                entry_fee = deploy * fee_rate
                units = (deploy - entry_fee) / actual_entry
                tp_price = actual_entry * (1 + tp_pct / 100.0)
                sl_price = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0
                cash -= deploy
                pos = {
                    "ep": actual_entry, "q": deploy, "hold": 0,
                    "tp": tp_price, "sl": sl_price, "units": units,
                    "entry_fee": entry_fee, "max_hold": max(lookback * 2, 48),
                }

    if pos:
        cash += pos["q"]
    net = cash - starting_cash
    wr = wins / max(closes_count, 1) * 100
    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 2),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "max_drawdown": round(max_dd * 100, 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", default="30d")
    parser.add_argument("--fee-tier", default="40bps")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    args = parser.parse_args()

    days = 7 if args.window == "7d" else 30
    fee_rate = FEE_TIERS.get(args.fee_tier, 0.004)

    print(f"Fetching candles for {len(PROFITABLE_COINS)} coins...")
    all_candles = {}
    for coin in PROFITABLE_COINS:
        print(f"  {coin}...", end=" ", flush=True)
        candles = normalize_candles(fetch_candles_coinbase(coin, days))
        all_candles[coin] = candles
        print(f"{len(candles)} candles")

    total = len(LOOKBACKS) * len(TPS) * len(SLS) * len(all_candles)
    print(f"\nRunning momentum optimization: {total} backtests")

    coin_optimal = {}
    all_qualifying = []

    for coin_idx, (coin, candles) in enumerate(all_candles.items()):
        print(f"\n[{coin_idx+1}/{len(all_candles)}] {coin} ({len(candles)} candles)...")
        best = None
        best_net = -999
        count = 0

        for lb in LOOKBACKS:
            for tp in TPS:
                for sl in SLS:
                    count += 1
                    if count % 200 == 0:
                        print(f"  {count}/{len(LOOKBACKS)*len(TPS)*len(SLS)}...", end="\r", flush=True)

                    r = momentum_breakout(candles, lb, tp, sl, fee_rate, args.starting_cash)

                    if r["net_pnl"] > best_net:
                        best_net = r["net_pnl"]
                        best = {**r, "lookback": lb, "tp_pct": tp, "sl_pct": sl}

                    if r["net_pnl"] > 0 and r["win_rate"] >= 40:
                        all_qualifying.append({
                            "coin": coin, "lookback": lb, "tp_pct": tp, "sl_pct": sl,
                            **r,
                        })

        print(f"  {count}/{count} done.")

        coin_optimal[coin] = best
        print(f"  Best: lookback={best['lookback']} TP={best['tp_pct']}% SL={best['sl_pct']}% → "
              f"${best['net_pnl']:+.2f} WR={best['win_rate']}% T={best['trades']} DD={best['max_drawdown']}%")

    # Top qualifying combos
    all_qualifying.sort(key=lambda x: x["net_pnl"], reverse=True)

    print(f"\n{'='*120}")
    print(f"MOMENTUM OPTIMIZATION — {args.window} ({args.fee_tier})")
    print(f"{'='*120}")
    print(f"Total combos: {total}")
    print(f"Qualifying (net>0, WR>=40%): {len(all_qualifying)}")

    # Top 30
    if all_qualifying:
        print(f"\nTOP 30 COMBOS:")
        hdr = f"{'#':<3} {'Coin':<15} {'LB':<5} {'TP%':<5} {'SL%':<5} {'Net':<10} {'WR%':<6} {'Trades':<7} {'DD%':<6}"
        print(hdr)
        print("-" * 120)
        for i, q in enumerate(all_qualifying[:30]):
            print(f"{i+1:<3} {q['coin']:<15} {q['lookback']:<5} {q['tp_pct']:<5} {q['sl_pct']:<5} "
                  f"${q['net_pnl']:<9.2f} {q['win_rate']:<6.1f} {q['trades']:<7} {q['max_drawdown']:<6.1f}")

    # Per-coin best
    print(f"\n{'='*120}")
    print(f"PER-COIN OPTIMAL MOMENTUM")
    print(f"{'='*120}")
    for coin, best in coin_optimal.items():
        print(f"\n  {coin}:")
        print(f"    lookback={best['lookback']} TP={best['tp_pct']}% SL={best['sl_pct']}%")
        print(f"    Net=${best['net_pnl']:+.2f} WR={best['win_rate']}% T={best['trades']} DD={best['max_drawdown']}%")

    # Multi-coin portfolio simulation
    print(f"\n{'='*120}")
    print(f"EQUAL-WEIGHT MULTI-COIN PORTFOLIO (5 coins, $100 each = $500 total)")
    print(f"{'='*120}")
    total_net = 0
    total_trades = 0
    for coin, best in coin_optimal.items():
        total_net += best["net_pnl"]
        total_trades += best["trades"]
        print(f"  {coin}: ${best['net_pnl']:+.2f} ({best['win_rate']}% WR, {best['trades']} trades, {best['max_drawdown']}% DD)")
    print(f"  TOTAL: ${total_net:+.2f} on $500 = {total_net/5:.1f}% return, {total_trades} trades")

    # Save
    report = {
        "window": args.window,
        "fee_tier": args.fee_tier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_combos": total,
        "qualifying_count": len(all_qualifying),
        "top30": all_qualifying[:30],
        "per_coin_optimal": {coin: best for coin, best in coin_optimal.items()},
        "portfolio_total": round(total_net, 2),
    }

    output_dir = ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"momentum_optimization_{args.window}.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
