#!/usr/bin/env python3
"""
Optimal Coin-Strategy Assignment
==================================

Given that momentum and theil-sen interfere when combined on the same coin,
what's the OPTIMAL assignment of strategy to coin?

Tests:
1. For each coin, which strategy is better? (momentum vs theil-sen)
2. What's the total if we assign each coin to its best strategy?
3. What if we add more coins beyond the core 5?

This answers: "Which coins should use momentum, which should use theil-sen?"
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from strategy_library import backtest

# All coins we have data for
ALL_COINS = [
    "RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD",
    "A8-USD", "BAL-USD", "CFG-USD", "IOTX-USD",
]

FEE_RATE = 0.004
STARTING_CASH = 100.0

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "optimal_coin_strategy_assignment.json"


def momentum_entry(candles_hist, closes, candle, params):
    lookback = params.get("lookback", 15)
    if len(candles_hist) < lookback + 2:
        return False
    current_high = float(candle["high"])
    highest = max(float(c["high"]) for c in candles_hist[-(lookback + 1):-1])
    return current_high > highest


def theil_sen_entry(candles_hist, closes, candle, params):
    reg_period = params.get("reg_period", 20)
    if len(closes) < reg_period + 5:
        return False
    recent = closes[-reg_period:]
    n = len(recent)
    x = list(range(n))
    y = recent
    slopes = []
    for i in range(0, n - 1, 2):
        if x[i + 1] - x[i] != 0:
            slopes.append((y[i + 1] - y[i]) / (x[i + 1] - x[i]))
    if not slopes:
        return False
    med_slope = sorted(slopes)[len(slopes) // 2]
    med_y = sorted(y)[len(y) // 2]
    med_x = sorted(x)[len(x) // 2]
    intercept = med_y - med_slope * med_x
    predicted = med_slope * n + intercept
    actual = y[-1]
    deviation = (predicted - actual) / actual
    return deviation < -0.02


# Param sweep for each strategy to find optimal per coin
MOMENTUM_PARAMS = [
    {"lookback": lb, "tp_pct": tp, "sl_pct": sl, "max_hold": mh}
    for lb in [10, 15, 20, 30, 50]
    for tp in [5.0, 8.0, 10.0, 15.0]
    for sl in [0.0, 3.0, 5.0]
    for mh in [24, 36, 48]
]

THEILSEN_PARAMS = [
    {"reg_period": rp, "tp_pct": tp, "sl_pct": sl, "max_hold": mh}
    for rp in [10, 15, 20, 30]
    for tp in [5.0, 8.0, 10.0, 15.0]
    for sl in [0.0, 2.0, 3.0]
    for mh in [24, 36, 48]
]


def main():
    print("=" * 80)
    print("  OPTIMAL COIN-STRATEGY ASSIGNMENT")
    print("=" * 80)
    print(f"Testing {len(ALL_COINS)} coins, momentum vs theil-sen")
    print(f"Momentum params: {len(MOMENTUM_PARAMS)} combos")
    print(f"Theil-Sen params: {len(THEILSEN_PARAMS)} combos")
    print()

    results = {}

    for coin_name in ALL_COINS:
        try:
            coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
            data = json.loads(open(coin_file).read())
            candles = data["candles"]

            # Momentum sweep
            print(f"  {coin_name}: sweeping momentum...", flush=True)
            best_mom_net = -999999
            best_mom = None
            best_mom_params = None
            for params in MOMENTUM_PARAMS:
                r = backtest(candles, momentum_entry, params, FEE_RATE, STARTING_CASH)
                if r["net_pnl"] > best_mom_net:
                    best_mom_net = r["net_pnl"]
                    best_mom = r
                    best_mom_params = params

            # Theil-Sen sweep
            print(f"  {coin_name}: sweeping theil-sen...", flush=True)
            best_ts_net = -999999
            best_ts = None
            best_ts_params = None
            for params in THEILSEN_PARAMS:
                r = backtest(candles, theil_sen_entry, params, FEE_RATE, STARTING_CASH)
                if r["net_pnl"] > best_ts_net:
                    best_ts_net = r["net_pnl"]
                    best_ts = r
                    best_ts_params = params

            # Determine winner
            winner = "momentum" if best_mom_net > best_ts_net else "theil_sen"
            winner_net = max(best_mom_net, best_ts_net)

            results[coin_name] = {
                "momentum_best": {
                    "net_pnl": round(best_mom_net, 2),
                    "win_rate": round(best_mom["win_rate"], 1) if best_mom else 0,
                    "trades": best_mom["trades"] if best_mom else 0,
                    "params": best_mom_params,
                },
                "theil_sen_best": {
                    "net_pnl": round(best_ts_net, 2),
                    "win_rate": round(best_ts["win_rate"], 1) if best_ts else 0,
                    "trades": best_ts["trades"] if best_ts else 0,
                    "params": best_ts_params,
                },
                "winner": winner,
                "winner_net": round(winner_net, 2),
                "edge": round(abs(best_mom_net - best_ts_net), 2),
            }

            r = results[coin_name]
            mom = r["momentum_best"]
            ts = r["theil_sen_best"]
            print(f"    Momentum:  ${mom['net_pnl']:+.2f} (WR={mom['win_rate']}%, {mom['trades']} trades)", flush=True)
            print(f"    Theil-Sen: ${ts['net_pnl']:+.2f} (WR={ts['win_rate']}%, {ts['trades']} trades)", flush=True)
            print(f"    🏆 Winner: {r['winner']} (${r['winner_net']:+.2f}, edge=${r['edge']:.2f})", flush=True)
            print()

        except Exception as e:
            print(f"  {coin_name}: ERROR — {e}", flush=True)

    # ==========================================
    # SUMMARY
    # ==========================================
    print(f"{'='*80}", flush=True)
    print("  OPTIMAL ASSIGNMENT", flush=True)
    print(f"{'='*80}", flush=True)

    momentum_coins = [(c, r) for c, r in results.items() if r["winner"] == "momentum"]
    theilsen_coins = [(c, r) for c, r in results.items() if r["winner"] == "theil_sen"]

    total_momentum = sum(r["winner_net"] for c, r in momentum_coins)
    total_theilsen = sum(r["winner_net"] for c, r in theilsen_coins)
    grand_total = total_momentum + total_theilsen

    print(f"\n  🏆 MOMENTUM coins ({len(momentum_coins)}):", flush=True)
    for coin, r in momentum_coins:
        print(f"     {coin}: ${r['winner_net']:+.2f}", flush=True)

    print(f"\n  🏆 THEIL-SEN coins ({len(theilsen_coins)}):", flush=True)
    for coin, r in theilsen_coins:
        print(f"     {coin}: ${r['winner_net']:+.2f}", flush=True)

    print(f"\n  Total optimal portfolio: ${grand_total:+.2f}/mo on ${STARTING_CASH * len(results):.0f} bankroll", flush=True)
    print(f"  (Momentum: ${total_momentum:+.2f}, Theil-Sen: ${total_theilsen:+.2f})", flush=True)

    # Save report
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "coins_tested": list(results.keys()),
        "assignment": {
            "momentum": [c for c, r in momentum_coins],
            "theil_sen": [c for c, r in theilsen_coins],
        },
        "results": results,
        "totals": {
            "momentum": round(total_momentum, 2),
            "theil_sen": round(total_theilsen, 2),
            "grand_total": round(grand_total, 2),
            "total_bankroll": STARTING_CASH * len(results),
        },
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
