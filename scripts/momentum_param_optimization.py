#!/usr/bin/env python3
"""
Momentum Parameter Optimization — Per-coin optimal params.
===========================================================
Sweeps lookback, TP, SL across the 4 confirmed Momentum coins.
Uses strategy_library.py exclusively — library-backed results.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from candle_cache_service import load_candles
from strategy_library import backtest, _momentum_entry

ROOT = Path(__file__).resolve().parent.parent
GRANULARITY = "FIVE_MINUTE"
WINDOW_DAYS = 30
FEE_RATE = 0.004
STARTING_CASH = 100.0

COINS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD"]
LOOKBACKS = [5, 10, 15, 20, 30, 50]
TP_PCTS = [5.0, 8.0, 10.0, 15.0, 20.0]
SL_PCTS = [0.0, 3.0, 5.0, 7.0, 10.0]
MAX_HOLDS = [24, 36, 48]


def main():
    print("=" * 80)
    print("  MOMENTUM PARAMETER OPTIMIZATION")
    print("=" * 80)
    print(f"  Coins: {COINS}")
    print(f"  Lookbacks: {LOOKBACKS}")
    print(f"  TP%: {TP_PCTS}")
    print(f"  SL%: {SL_PCTS}")
    print(f"  Max holds: {MAX_HOLDS}")
    total = len(COINS) * len(LOOKBACKS) * len(TP_PCTS) * len(SL_PCTS) * len(MAX_HOLDS)
    print(f"  Total combos: {total}")
    print()

    all_results = []
    done = 0
    t_start = time.time()

    for coin in COINS:
        print(f"--- {coin} ---")
        candles = load_candles(coin, GRANULARITY, WINDOW_DAYS, max_age_minutes=WINDOW_DAYS * 24 * 60)
        if not candles or len(candles) < 200:
            print(f"  SKIP: {len(candles) if candles else 0} candles")
            continue

        best_net = -999
        best = None
        coin_results = []

        for lookback in LOOKBACKS:
            for tp_pct in TP_PCTS:
                for sl_pct in SL_PCTS:
                    for max_hold in MAX_HOLDS:
                        done += 1
                        params = {
                            "lookback": lookback,
                            "tp_pct": tp_pct,
                            "sl_pct": sl_pct,
                            "max_hold": max_hold,
                        }
                        result = backtest(candles, _momentum_entry, params, FEE_RATE, STARTING_CASH)
                        result["coin"] = coin
                        result["params"] = params
                        coin_results.append(result)

                        if result["net_pnl"] > best_net:
                            best_net = result["net_pnl"]
                            best = dict(result)

                        # Progress indicator
                        if done % 100 == 0:
                            elapsed = time.time() - t_start
                            rate = done / elapsed if elapsed > 0 else 0
                            eta = (total - done) / rate if rate > 0 else 0
                            print(f"  [{done}/{total}] {done/total*100:.0f}% | Best: ${best_net:+.2f} | ETA: {eta:.0f}s", flush=True)

        # Print best for this coin
        if best:
            print(f"  BEST: lookback={best['params']['lookback']}, "
                  f"TP={best['params']['tp_pct']:.0f}%, SL={best['params']['sl_pct']:.0f}%, "
                  f"hold={best['params']['max_hold']} | "
                  f"Net ${best['net_pnl']:+.2f}, WR {best['win_rate']:.1f}%, "
                  f"Trades {best['trades']}, DD {best['max_drawdown']:.1f}%")

        all_results.extend(coin_results)

    # Sort by net PnL
    all_results.sort(key=lambda x: x["net_pnl"], reverse=True)

    # Print top 20
    print(f"\n{'='*80}")
    print(f"  TOP 20 COMBOS (by net PnL)")
    print(f"{'='*80}")
    print(f"  {'#':>3} {'COIN':<12} {'LB':>4} {'TP%':>5} {'SL%':>5} "
          f"{'Hold':>5} {'Net $':>8} {'WR%':>5} {'Trades':>6} {'DD%':>5}")
    print(f"  {'-'*3} {'-'*12} {'-'*4} {'-'*5} {'-'*5} {'-'*5} {'-'*8} {'-'*5} {'-'*6} {'-'*5}")

    for rank, r in enumerate(all_results[:20], 1):
        p = r["params"]
        print(f"  {rank:>3} {r['coin']:<12} {p['lookback']:>4} "
              f"{p['tp_pct']:>5.0f} {p['sl_pct']:>5.0f} "
              f"{p['max_hold']:>5} ${r['net_pnl']:>7.2f} "
              f"{r['win_rate']:>5.1f}% {r['trades']:>6} {r['max_drawdown']:>5.1f}%")

    # Per-coin summary
    print(f"\n{'='*80}")
    print(f"  PER-COIN OPTIMAL (best by net PnL)")
    print(f"{'='*80}")

    for coin in COINS:
        coin_results = [r for r in all_results if r["coin"] == coin]
        if coin_results:
            best = coin_results[0]
            p = best["params"]
            print(f"  {coin}: LB={p['lookback']}, TP={p['tp_pct']:.0f}%, "
                  f"SL={p['sl_pct']:.0f}%, Hold={p['max_hold']} | "
                  f"${best['net_pnl']:+.2f}, {best['win_rate']:.1f}% WR, "
                  f"{best['trades']} trades, {best['max_drawdown']:.1f}% DD")

    # Save report
    report = {
        "params": {"granularity": GRANULARITY, "window_days": WINDOW_DAYS,
                    "fee_rate": FEE_RATE, "starting_cash": STARTING_CASH},
        "sweep_space": {"lookbacks": LOOKBACKS, "tp_pcts": TP_PCTS,
                         "sl_pcts": SL_PCTS, "max_holds": MAX_HOLDS},
        "total_combos": total,
        "combos_run": len(all_results),
        "top20": [{"coin": r["coin"], "params": r["params"], "net_pnl": r["net_pnl"],
                    "win_rate": r["win_rate"], "trades": r["trades"],
                    "max_drawdown": r["max_drawdown"]} for r in all_results[:20]],
        "per_coin_optimal": {},
    }

    for coin in COINS:
        coin_results = [r for r in all_results if r["coin"] == coin]
        if coin_results:
            best = coin_results[0]
            report["per_coin_optimal"][coin] = {
                "params": best["params"],
                "net_pnl": best["net_pnl"],
                "win_rate": best["win_rate"],
                "trades": best["trades"],
                "max_drawdown": best["max_drawdown"],
            }

    output_path = ROOT / "reports" / "momentum_param_optimization.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
