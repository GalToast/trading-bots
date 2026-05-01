#!/usr/bin/env python3
"""
TP/SL Parameter Sweep — Find what works across diverse market structures.

Tests every coin across a grid of TP and SL values to find profitable
configurations beyond the RAVE-specific 25% TP + no SL setup.

Grid:
  TP: 3%, 5%, 8%, 10%, 15%, 20%, 25%
  SL: 0%, 5%, 10%, 15%
  = 28 combos per coin × 20 coins = 560 backtests

Usage:
    python scripts/tp_sl_parameter_sweep.py --window 30d
    python scripts/tp_sl_parameter_sweep.py --window 7d --coins RAVE-USD BAL-USD IOTX-USD
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
    compute_rsi,
    FEE_TIERS,
    FILL_MODELS,
)

ROOT = Path(__file__).resolve().parent.parent

COINS = [
    "RAVE-USD", "BAL-USD", "IOTX-USD", "BLUR-USD",
    "SOL-USD", "DOGE-USD", "XRP-USD", "PEPE-USD",
    "WIF-USD", "AAVE-USD", "LINK-USD", "UNI-USD",
    "AVAX-USD", "NEAR-USD", "FET-USD", "RENDER-USD",
    "TIA-USD", "SEI-USD", "SUI-USD", "ONDO-USD",
]

TP_VALUES = [3, 5, 8, 10, 15, 20, 25]
SL_VALUES = [0, 5, 10, 15]

STRATEGY_BASE = {
    "rsi_period": 3,
    "os_thresh": 30,
    "max_hold": 48,
}


def run_backtest_fast(
    candles: list[dict],
    tp_pct: float,
    sl_pct: float,
    fee_rate: float,
    fill_prob: float = 1.0,
    entry_slip: float = 0.0008,
    exit_slip: float = 0.0,
    starting_cash: float = 100.0,
    seed: int = 42,
) -> dict:
    """Fast backtest — no regime, no logging, just PnL."""
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    history = []
    rsi_period = 3
    os_thresh = 30
    max_hold = 48
    tp = 0
    sl = 0

    for i in range(len(candles)):
        c = candles[i]
        close = c["close"]
        high = c["high"]
        low = c["low"]
        candle_open = c["open"]
        history.append(close)
        if len(history) > 500:
            history = history[-500:]

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
                exit_price = close

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

        # ENTRY
        if pos is None and session_open:
            if len(history) >= rsi_period + 2:
                deltas = [history[j] - history[j-1] for j in range(len(history)-rsi_period, len(history))]
                gains = [d if d > 0 else 0 for d in deltas]
                losses_rsi = [-d if d < 0 else 0 for d in deltas]
                avg_g = sum(gains) / rsi_period
                avg_l = sum(losses_rsi) / rsi_period
                if avg_l > 0:
                    rs = avg_g / avg_l
                    rsi_val = 100 - 100 / (1 + rs)
                else:
                    rsi_val = 100.0

                if rsi_val <= os_thresh:
                    if rng.random() > fill_prob:
                        continue
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
                        "entry_fee": entry_fee, "max_hold": max_hold,
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
    parser.add_argument("--coins", nargs="+", default=None)
    parser.add_argument("--fee-tier", default="40bps")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    args = parser.parse_args()

    days = 7 if args.window == "7d" else 30
    fee_rate = FEE_TIERS.get(args.fee_tier, 0.004)
    coins = args.coins or COINS

    # Fetch all candles first
    print(f"Fetching candles for {len(coins)} coins ({args.window})...")
    all_candles = {}
    for coin in coins:
        print(f"  {coin}...", end=" ", flush=True)
        try:
            candles = normalize_candles(fetch_candles_coinbase(coin, days))
            all_candles[coin] = candles
            print(f"{len(candles)} candles")
        except Exception as e:
            print(f"ERROR: {e}")

    print(f"\nRunning parameter sweep: {len(TP_VALUES)} TPs × {len(SL_VALUES)} SLs × {len(all_candles)} coins = {len(TP_VALUES)*len(SL_VALUES)*len(all_candles)} backtests")

    # Sweep
    grid_results = {}  # coin -> list of (tp, sl, result)
    qualifying = []  # combos with net > 0

    for coin_idx, (coin, candles) in enumerate(all_candles.items()):
        if len(candles) < 100:
            continue

        print(f"\n[{coin_idx+1}/{len(all_candles)}] {coin} ({len(candles)} candles)...")
        coin_results = []
        best_result = None
        best_net = -999

        for tp in TP_VALUES:
            for sl in SL_VALUES:
                r = run_backtest_fast(candles, tp, sl, fee_rate, starting_cash=args.starting_cash)
                r["tp_pct"] = tp
                r["sl_pct"] = sl
                coin_results.append(r)

                if r["net_pnl"] > best_net:
                    best_net = r["net_pnl"]
                    best_result = r

                if r["net_pnl"] > 0 and r["win_rate"] >= 40:
                    qualifying.append({
                        "coin": coin,
                        "tp": tp,
                        "sl": sl,
                        "net_pnl": r["net_pnl"],
                        "win_rate": r["win_rate"],
                        "trades": r["trades"],
                        "max_drawdown": r["max_drawdown"],
                    })

        # Print best for this coin
        print(f"  Best: TP={best_result['tp_pct']}% SL={best_result['sl_pct']}% → "
              f"Net=${best_result['net_pnl']:+.2f}, WR={best_result['win_rate']}%, "
              f"Trades={best_result['trades']}, DD={best_result['max_drawdown']}%")

        # Print top 3 combos
        sorted_results = sorted(coin_results, key=lambda x: x["net_pnl"], reverse=True)
        for rank, r in enumerate(sorted_results[:3]):
            marker = "🔥" if r["net_pnl"] > 0 else ""
            print(f"  #{rank+1}: TP={r['tp_pct']}% SL={r['sl_pct']}% → "
                  f"${r['net_pnl']:+.2f} WR={r['win_rate']}% T={r['trades']} DD={r['max_drawdown']}% {marker}")

        grid_results[coin] = {
            "candles": len(candles),
            "best": best_result,
            "all": coin_results,
        }

    # Print qualifying combos ranked by net PnL
    qualifying.sort(key=lambda x: x["net_pnl"], reverse=True)

    print(f"\n{'='*110}")
    print(f"TP/SL PARAMETER SWEEP — {args.window} ({args.fee_tier})")
    print(f"{'='*110}")
    print(f"Total combos tested: {sum(len(v['all']) for v in grid_results.values())}")
    print(f"Qualifying (net>0, WR>=40%): {len(qualifying)}")

    if qualifying:
        print(f"\n{'='*110}")
        print(f"TOP QUALIFYING COMBOS (ranked by net PnL)")
        print(f"{'='*110}")
        hdr = f"{'#':<3} {'Coin':<15} {'TP%':<5} {'SL%':<5} {'Net':<10} {'WR%':<6} {'Trades':<7} {'DD%':<6}"
        print(hdr)
        print("-" * 110)
        for i, q in enumerate(qualifying[:30]):
            print(f"{i+1:<3} {q['coin']:<15} {q['tp']:<5} {q['sl']:<5} "
                  f"${q['net_pnl']:<9.2f} {q['win_rate']:<6.1f} {q['trades']:<7} {q['max_drawdown']:<6.1f}")

    # Per-coin summary: best TP/SL for each
    print(f"\n{'='*110}")
    print(f"PER-COIN BEST CONFIGURATION")
    print(f"{'='*110}")
    hdr = f"{'Coin':<15} {'Best TP%':<10} {'Best SL%':<10} {'Net':<10} {'WR%':<6} {'Trades':<7} {'DD%':<6} {'RAVE-like?':<10}"
    print(hdr)
    print("-" * 110)
    for coin, data in grid_results.items():
        b = data["best"]
        rave_like = "✅" if b["tp_pct"] >= 20 and b["sl_pct"] == 0 else "❌"
        print(f"{coin:<15} {b['tp_pct']:<10} {b['sl_pct']:<10} "
              f"${b['net_pnl']:<9.2f} {b['win_rate']:<6.1f} {b['trades']:<7} {b['max_drawdown']:<6.1f} {rave_like:<10}")

    # Save report
    report = {
        "window": args.window,
        "fee_tier": args.fee_tier,
        "tp_values": TP_VALUES,
        "sl_values": SL_VALUES,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_combos": sum(len(v["all"]) for v in grid_results.values()),
        "qualifying_count": len(qualifying),
        "qualifying_top30": qualifying[:30],
        "per_coin_best": {
            coin: {
                "candles": data["candles"],
                "best": data["best"],
            }
            for coin, data in grid_results.items()
        },
    }

    output_dir = ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"tp_sl_sweep_{args.window}.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
