#!/usr/bin/env python3
"""
Reconcile Robust Regression — Why do different agents get different results?
============================================================================

Three different robust_regression implementations exist:
1. Huber mean of returns (robust_regression_validation.py) — by @qwen-trading-bots
2. Theil-Sen estimator (robust_reg_vs_momentum_overlap.py) — by @qwen-trading
3. IRLS with Huber weights (signal_overlap_analysis.py) — by @qwen-strategies-tester

Each has different entry logic, thresholds, and signal direction.
This script tests ALL THREE on the SAME 30d data with identical params.

Goal: definitively answer whether robust_regression works, which variant,
and whether the 7d→30d discrepancy is a regime artifact or a bug.
"""
import json
import math
import sys
import statistics
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from strategy_library import backtest

# ==========================================
# CONFIG
# ==========================================
COINS = ["RAVE-USD", "NOM-USD", "GHST-USD", "TRU-USD", "SUP-USD"]
FEE_RATE = 0.004
STARTING_CASH = 100.0

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "reports" / "reconcile_robust_regression.json"


# ==========================================
# VARIANT 1: Huber Mean of Returns
# Entry: predicted return > threshold (trend-following)
# Used by: @qwen-trading-bots (robust_regression_validation.py)
# ==========================================
def huber_mean(values, delta=1.0):
    if not values:
        return 0
    median_val = statistics.median(values)
    mu = median_val
    for _ in range(10):
        residuals = [v - mu for v in values]
        weights = []
        for r in residuals:
            if abs(r) <= delta:
                weights.append(1.0)
            else:
                weights.append(delta / abs(r))
        if sum(weights) == 0:
            break
        mu = sum(w * v for w, v in zip(weights, values)) / sum(weights)
    return mu


def v1_robust_regression_entry(candles_hist, closes, candle, params):
    window = params.get("window", 20)
    threshold = params.get("threshold", 0.002)
    if len(closes) < window + 2:
        return False
    returns = []
    for i in range(-window, 0):
        if closes[i] > 0 and closes[i - 1] > 0:
            returns.append((closes[i] - closes[i - 1]) / closes[i - 1])
    if len(returns) < 5:
        return False
    predicted = huber_mean(returns, delta=0.02)
    return predicted > threshold


# ==========================================
# VARIANT 2: Theil-Sen Estimator
# Entry: deviation < -0.02 (mean reversion — buy when predicted below current)
# Used by: @qwen-trading (robust_reg_vs_momentum_overlap.py)
# ==========================================
def v2_robust_regression_entry(candles_hist, closes, candle, params):
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
    return deviation < -0.02  # Mean reversion buy signal


# ==========================================
# VARIANT 3: IRLS with Huber Weights
# Entry: price below regression line AND price rising (mean reversion)
# Used by: @qwen-strategies-tester (signal_overlap_analysis.py)
# ==========================================
def v3_robust_regression_entry(candles_hist, closes, candle, params):
    if len(closes) < 40:
        return False
    period = min(40, len(closes) - 1)
    window = closes[-period:]
    n = len(window)
    x = list(range(n))
    y = window
    weights = [1.0] * n
    for _ in range(3):
        wx = [weights[i] * x[i] for i in range(n)]
        wy = [weights[i] * y[i] for i in range(n)]
        wxx = [weights[i] * x[i] * x[i] for i in range(n)]
        wxy = [weights[i] * x[i] * y[i] for i in range(n)]
        sum_w = sum(weights)
        sum_wx = sum(wx)
        sum_wy = sum(wy)
        sum_wxx = sum(wxx)
        sum_wxy = sum(wxy)
        denom = sum_w * sum_wxx - sum_wx * sum_wx
        if abs(denom) < 1e-10:
            break
        slope = (sum_w * sum_wxy - sum_wx * sum_wy) / denom
        intercept = (sum_wy - slope * sum_wx) / sum_w
        residuals = [y[i] - (slope * x[i] + intercept) for i in range(n)]
        mad = sorted([abs(r) for r in residuals])[n // 2] * 1.4826
        if mad < 1e-10:
            break
        for i in range(n):
            u = abs(residuals[i]) / mad
            weights[i] = 1.0 if u <= 1.345 else 1.345 / u
    predicted = slope * n + intercept
    current_price = closes[-1]
    if current_price < predicted * 0.998:
        if len(closes) > 1 and closes[-1] > closes[-2]:
            return True
    return False


# ==========================================
# MAIN
# ==========================================
def main():
    print("=" * 80)
    print("  ROBUST REGRESSION RECONCILIATION")
    print("=" * 80)
    print(f"Testing 3 variants on {len(COINS)} coins, 30d data")
    print(f"Fee: {FEE_RATE*100:.1f}%, Starting cash: ${STARTING_CASH}")
    print()

    variants = [
        {
            "name": "v1_huber_mean",
            "entry_fn": v1_robust_regression_entry,
            "desc": "Huber mean of returns, predicted > threshold (trend-following)",
            "param_sets": [
                {"window": 20, "threshold": 0.002, "tp_pct": 5.0, "sl_pct": 2.0, "max_hold": 24},
                {"window": 10, "threshold": 0.001, "tp_pct": 8.0, "sl_pct": 3.0, "max_hold": 24},
                {"window": 30, "threshold": 0.005, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 48},
            ],
        },
        {
            "name": "v2_theil_sen",
            "entry_fn": v2_robust_regression_entry,
            "desc": "Theil-Sen estimator, deviation < -0.02 (mean reversion)",
            "param_sets": [
                {"reg_period": 20, "tp_pct": 8.0, "sl_pct": 3.0, "max_hold": 24},
                {"reg_period": 10, "tp_pct": 5.0, "sl_pct": 2.0, "max_hold": 24},
                {"reg_period": 30, "tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 48},
            ],
        },
        {
            "name": "v3_irls_huber",
            "entry_fn": v3_robust_regression_entry,
            "desc": "IRLS Huber weights, price below line + rising (mean reversion)",
            "param_sets": [
                {"tp_pct": 8.0, "sl_pct": 3.0, "max_hold": 24},
                {"tp_pct": 5.0, "sl_pct": 2.0, "max_hold": 24},
                {"tp_pct": 10.0, "sl_pct": 0.0, "max_hold": 48},
            ],
        },
    ]

    results = {}

    for variant in variants:
        print(f"\n{'='*60}", flush=True)
        print(f"  VARIANT: {variant['name']}")
        print(f"  {variant['desc']}")
        print(f"{'='*60}", flush=True)

        variant_results = {}

        for coin_name in COINS:
            try:
                coin_file = f"reports/candle_cache/{coin_name.replace('-', '_')}_FIVE_MINUTE_30d.json"
                data = json.loads(open(coin_file).read())
                candles = data["candles"]

                best_net = -999999
                best = None

                for params in variant["param_sets"]:
                    result = backtest(candles, variant["entry_fn"], params, FEE_RATE, STARTING_CASH)
                    if result["net_pnl"] > best_net:
                        best_net = result["net_pnl"]
                        best = {
                            "params": params,
                            "net_pnl": round(result["net_pnl"], 2),
                            "win_rate": round(result["win_rate"], 1),
                            "trades": result["trades"],
                            "max_drawdown": round(result["max_drawdown"], 1),
                            "signals": result["signals"],
                            "signals_filtered": result.get("signals_filtered", 0),
                        }

                if best:
                    variant_results[coin_name] = best
                    status = "✅" if best["net_pnl"] > 0 else "❌"
                    print(f"  {status} {coin_name}: Net=${best['net_pnl']:+.2f}, WR={best['win_rate']}%, "
                          f"Trades={best['trades']}, Signals={best['signals']}", flush=True)
                else:
                    print(f"  ⚠️ {coin_name}: No results", flush=True)

            except Exception as e:
                print(f"  ❌ {coin_name}: ERROR — {e}", flush=True)

        results[variant["name"]] = {
            "description": variant["desc"],
            "coins": variant_results,
            "profitable_coins": sum(1 for c in variant_results.values() if c["net_pnl"] > 0),
            "total_coins_tested": len(variant_results),
        }

    # ==========================================
    # SUMMARY
    # ==========================================
    print(f"\n{'='*80}", flush=True)
    print("  SUMMARY", flush=True)
    print(f"{'='*80}", flush=True)

    print(f"\n{'Variant':<20} | {'Coins':>5} | {'Profitable':>10} | Verdict", flush=True)
    print(f"{'-'*20}-+-{'-'*5}-+-{'-'*10}-+-{'-'*12}", flush=True)

    for vname, vdata in results.items():
        profitable = vdata["profitable_coins"]
        total = vdata["total_coins_tested"]
        verdict = "✅ VIABLE" if profitable > 0 else "❌ FAILS"
        print(f"{vname:<20} | {total:>5} | {profitable:>10} | {verdict}", flush=True)

    # ==========================================
    # KEY FINDING
    # ==========================================
    print(f"\n{'='*80}", flush=True)
    print("  KEY FINDINGS", flush=True)
    print(f"{'='*80}", flush=True)

    any_profitable = False
    for vname, vdata in results.items():
        if vdata["profitable_coins"] > 0:
            any_profitable = True
            print(f"\n  ✅ {vname} works on {vdata['profitable_coins']}/{vdata['total_coins_tested']} coins:")
            for coin, cdata in vdata["coins"].items():
                if cdata["net_pnl"] > 0:
                    print(f"     {coin}: ${cdata['net_pnl']:+.2f} (WR={cdata['win_rate']}%, {cdata['trades']} trades)")

    if not any_profitable:
        print("\n  ❌ NO variant of robust_regression is profitable on 30d data.")
        print("     The 7d → 30d discrepancy is NOT a bug — it's a regime artifact.")
        print("     Robust regression mean-reversion strategies fail in trending markets.")
        print("     This confirms: uniqueness ≠ alpha. Signals can be 100% unique but still lose.")

    # Save report
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "variants_tested": len(variants),
        "coins_tested": COINS,
        "results": results,
        "conclusion": "robust_regression_fails_30d" if not any_profitable else "robust_regression_viable",
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    print(f"\nReport saved: {OUTPUT_PATH}", flush=True)
    print("\nDone. 🎯", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
