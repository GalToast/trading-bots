#!/usr/bin/env python3
"""
Momentum Parameter Optimization — Per-coin optimal params.

Sweeps lookback, TP, SL across the 4 confirmed Momentum coins.
Uses strategy_library.py exclusively — library-backed results.

Usage:
    python scripts/optimize_momentum_params.py --window 30d
"""
import sys, os, json
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))

from strategy_library import momentum
from benchmark_regime_segmented import fetch_candles_coinbase, normalize_candles

ROOT = Path(__file__).resolve().parent.parent

COINS = ["RAVE-USD", "IOTX-USD", "BAL-USD", "BLUR-USD"]
LOOKBACKS = [5, 10, 15, 20, 30, 50]
TPS = [5, 8, 10, 15, 20]
SLS = [0, 3, 5, 7, 10]

def main():
    print("Fetching candles...", flush=True)
    candles = {}
    for coin in COINS:
        c = normalize_candles(fetch_candles_coinbase(coin, 30))
        candles[coin] = c
        print(f"  {coin}: {len(c)} candles", flush=True)

    total = len(LOOKBACKS) * len(TPS) * len(SLS) * len(COINS)
    print(f"\nRunning {total} backtests...", flush=True)

    all_results = []
    coin_best = {}

    for coin_idx, coin in enumerate(COINS):
        c = candles[coin]
        print(f"\n[{coin_idx+1}/{len(COINS)}] {coin}...", flush=True)
        best = None
        best_net = -999
        count = 0

        for lb in LOOKBACKS:
            for tp in TPS:
                for sl in SLS:
                    count += 1
                    r = momentum(c, lookback=lb, tp_pct=tp, sl_pct=sl,
                                max_hold=max(lb * 2, 48), fee_rate=0.004,
                                starting_cash=100.0, seed=42)

                    entry = {
                        "coin": coin, "lookback": lb, "tp_pct": tp, "sl_pct": sl,
                        "net_pnl": r["net_pnl"], "win_rate": r["win_rate"],
                        "trades": r["trades"], "max_drawdown": r["max_drawdown"],
                        "signals": r["signals"], "fill_rate": r["fill_rate"],
                    }
                    all_results.append(entry)

                    if r["net_pnl"] > best_net:
                        best_net = r["net_pnl"]
                        best = entry

        coin_best[coin] = best
        print(f"  Best: lb={best['lookback']} TP={best['tp_pct']}% SL={best['sl_pct']}% "
              f"→ ${best['net_pnl']:+.2f} WR={best['win_rate']}% T={best['trades']} DD={best['max_drawdown']}%", flush=True)

    # Top 30 overall
    all_results.sort(key=lambda x: x["net_pnl"], reverse=True)
    qualifying = [r for r in all_results if r["net_pnl"] > 0 and r["win_rate"] >= 40]

    print(f"\n{'='*110}")
    print(f"MOMENTUM PARAMETER OPTIMIZATION — 30d, 40bps fees")
    print(f"{'='*110}")
    print(f"Total combos: {total}")
    print(f"Qualifying (net>0, WR>=40%): {len(qualifying)}")

    if qualifying:
        print(f"\nTOP 20:")
        hdr = f"{'#':<3} {'Coin':<12} {'LB':<5} {'TP%':<5} {'SL%':<5} {'Net':<10} {'WR%':<6} {'Trades':<7} {'DD%':<6}"
        print(hdr)
        print("-" * 110)
        for i, q in enumerate(qualifying[:20]):
            print(f"{i+1:<3} {q['coin']:<12} {q['lookback']:<5} {q['tp_pct']:<5} {q['sl_pct']:<5} "
                  f"${q['net_pnl']:<9.2f} {q['win_rate']:<6.1f} {q['trades']:<7} {q['max_drawdown']:<6.1f}")

    print(f"\n{'='*110}")
    print(f"PER-COIN OPTIMAL")
    print(f"{'='*110}")
    for coin, best in coin_best.items():
        print(f"  {coin}: lb={best['lookback']} TP={best['tp_pct']}% SL={best['sl_pct']}% "
              f"→ ${best['net_pnl']:+.2f} WR={best['win_rate']}% T={best['trades']} DD={best['max_drawdown']}%")

    # Portfolio total with optimal params
    portfolio_net = sum(b["net_pnl"] for b in coin_best.values())
    portfolio_trades = sum(b["trades"] for b in coin_best.values())
    print(f"\n  PORTFOLIO (optimal per coin): ${portfolio_net:+.2f} on $400, {portfolio_trades} trades")

    # Save
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_combos": total,
        "qualifying_count": len(qualifying),
        "top20": qualifying[:20],
        "per_coin_optimal": coin_best,
        "portfolio_total": round(portfolio_net, 2),
    }
    output_path = ROOT / "reports" / "momentum_param_optimization.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport: {output_path}")

if __name__ == "__main__":
    main()
