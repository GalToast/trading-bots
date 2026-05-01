#!/usr/bin/env python3
"""
Regime Classifier Comparison — Resolves divergence between classifiers.

Compares:
1. regime_detection.py (BTC-correlated, 4-component score)
2. Simplified classifier (ATR% + ADX only, no BTC correlation)

Usage:
    python scripts/compare_regime_classifiers.py --coin RAVE-USD --window 30d
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from regime_detection import regime_score
from benchmark_regime_segmented import (
    fetch_candles_coinbase,
    normalize_candles,
    _align_btc_candles,
    run_backtest_segment,
    FEE_TIERS,
    FILL_MODELS,
    STRATEGY_REGISTRY,
)

ROOT = Path(__file__).resolve().parent.parent


def _score_to_regime(score: float) -> str:
    if score >= 70:
        return "hot"
    elif score >= 40:
        return "cold"
    else:
        return "choppy"


def simplified_regime_score(candles: list[dict], window: int = 30) -> dict:
    """Regime score without BTC correlation. ATR% + ADX only, 0-100 scale."""
    if len(candles) < 28:
        return {"score": 50, "atr_pct": 0, "adx": 25, "regime": "cold"}

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    trs = []
    for i in range(1, len(highs)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)

    atr_period = 14
    if len(trs) >= atr_period:
        atr = sum(trs[-atr_period:]) / atr_period
    else:
        atr = sum(trs) / max(len(trs), 1)

    avg_price = sum(closes[-atr_period:]) / atr_period
    atr_pct = (atr / avg_price * 100) if avg_price > 0 else 0
    atr_score = min(50, max(0, (atr_pct - 1.0) / 3.0 * 50))

    plus_dm = []
    minus_dm = []
    for i in range(1, len(highs)):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0)
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0)

    def wilder_smooth(values, period):
        if len(values) < period:
            return sum(values) / max(len(values), 1)
        result = sum(values[:period]) / period
        for i in range(period, len(values)):
            result = (result * (period - 1) + values[i]) / period
        return result

    adx_period = 14
    atr_val = wilder_smooth(trs, adx_period)
    plus_di = 100 * wilder_smooth(plus_dm, adx_period) / max(atr_val, 0.001)
    minus_di = 100 * wilder_smooth(minus_dm, adx_period) / max(atr_val, 0.001)
    adx = 100 * abs(plus_di - minus_di) / max(plus_di + minus_di, 0.001)
    adx_score = min(50, max(0, (50 - adx) / 35 * 50))

    total_score = atr_score + adx_score
    return {
        "score": total_score,
        "atr_pct": atr_pct,
        "adx": adx,
        "regime": _score_to_regime(total_score),
    }


def main():
    parser = argparse.ArgumentParser(description="Compare regime classifiers")
    parser.add_argument("--coin", default="RAVE-USD")
    parser.add_argument("--window", default="30d")
    parser.add_argument("--strategy", default="rsi_mr")
    parser.add_argument("--fill-model", default="empirical")
    parser.add_argument("--fee-tier", default="40bps")
    args = parser.parse_args()

    days = 7 if args.window == "7d" else 30
    fee_rate = FEE_TIERS.get(args.fee_tier, 0.004)
    strategy_params = STRATEGY_REGISTRY[args.strategy]["params"]
    fill_model = FILL_MODELS.get(args.fill_model, FILL_MODELS["realistic"])

    print(f"Fetching {args.window} candles for {args.coin} and BTC-USD...")
    candles = normalize_candles(fetch_candles_coinbase(args.coin, days))
    btc_candles = normalize_candles(fetch_candles_coinbase("BTC-USD", days))
    print(f"Loaded {len(candles)} {args.coin} candles, {len(btc_candles)} BTC candles.")

    # Classify each candle with both classifiers
    print("Classifying with both classifiers...")
    labels = []
    window_size = 30

    for i in range(len(candles)):
        if i < window_size - 1:
            wc = candles[:i + 1]
        else:
            wc = candles[i - window_size + 1:i + 1]

        aligned_btc = _align_btc_candles(wc, btc_candles)
        full_score = regime_score(wc, aligned_btc)
        full_regime = _score_to_regime(full_score["score"])

        simple_score = simplified_regime_score(wc, window_size)

        labels.append({
            "idx": i,
            "time": wc[-1]["start"],
            "price": wc[-1]["close"],
            "full": {
                "regime": full_regime,
                "score": full_score["score"],
                "atr_pct": full_score.get("atr_pct", 0),
                "btc_corr": full_score.get("btc_corr", 0),
                "volume_ratio": full_score.get("volume_ratio", 0),
                "adx": full_score.get("adx", 0),
                "components": full_score.get("components", {}),
            },
            "simple": {
                "regime": simple_score["regime"],
                "score": simple_score["score"],
                "atr_pct": simple_score["atr_pct"],
                "adx": simple_score["adx"],
            },
            "agree": full_regime == simple_score["regime"],
        })

    # Agreement rate
    agree_count = sum(1 for l in labels if l["agree"])
    disagree_count = len(labels) - agree_count
    agreement_rate = agree_count / len(labels) * 100

    hdr = "Full\\Simple"
    print(f"\n{'='*70}")
    print(f"REGIME CLASSIFIER COMPARISON -- {args.coin} ({args.window})")
    print(f"{'='*70}")
    print(f"Total candles: {len(labels)}")
    print(f"Agreement rate: {agreement_rate:.1f}% ({agree_count}/{len(labels)})")
    print(f"Disagreements: {disagree_count}")

    # Confusion matrix
    regimes = ["hot", "cold", "choppy"]
    confusion = {f: {t: 0 for t in regimes} for f in regimes}
    for l in labels:
        confusion[l["full"]["regime"]][l["simple"]["regime"]] += 1

    col_headers = f"{'HOT':<10} {'COLD':<10} {'CHOPPY':<10}"
    print(f"\nConfusion Matrix ({hdr}):")
    print(f"{hdr:<12} {col_headers}")
    print("-" * 42)
    for f_regime in regimes:
        row = confusion[f_regime]
        print(f"{f_regime.upper():<12} {row['hot']:<10} {row['cold']:<10} {row['choppy']:<10}")

    # Time distribution
    full_counts = Counter(l["full"]["regime"] for l in labels)
    simple_counts = Counter(l["simple"]["regime"] for l in labels)

    print(f"\nTime Distribution:")
    dist_hdr = f"{'Regime':<12} {'Full %':<10} {'Full #':<10} {'Simple %':<10} {'Simple #':<10}"
    print(dist_hdr)
    print("-" * 52)
    for r in regimes:
        full_pct = full_counts.get(r, 0) / len(labels) * 100
        simple_pct = simple_counts.get(r, 0) / len(labels) * 100
        print(f"{r.upper():<12} {full_pct:<10.1f} {full_counts.get(r, 0):<10} {simple_pct:<10.1f} {simple_counts.get(r, 0):<10}")

    # Disagreement analysis
    disagreements = [l for l in labels if not l["agree"]]
    if disagreements:
        label_pairs = Counter((l["full"]["regime"], l["simple"]["regime"]) for l in disagreements)
        pairs_str = ", ".join(f"{k[0]}->{k[1]}: {v}" for k, v in label_pairs.items())

        full_avg = sum(l["full"]["score"] for l in disagreements) / len(disagreements)
        simple_avg = sum(l["simple"]["score"] for l in disagreements) / len(disagreements)
        btc_avg = sum(abs(l["full"]["btc_corr"]) for l in disagreements) / len(disagreements)
        prices = [l["price"] for l in disagreements]

        print(f"\nDisagreement Analysis ({len(disagreements)} candles):")
        print(f"  Label pairs: {pairs_str}")
        print(f"  Full avg score: {full_avg:.1f}")
        print(f"  Simple avg score: {simple_avg:.1f}")
        print(f"  Avg |BTC corr| in disagreements: {btc_avg:.3f}")
        print(f"  Price range: ${min(prices):.4f} - ${max(prices):.4f}")

    # Benchmark comparison
    print(f"\n{'='*70}")
    print(f"BENCHMARK COMPARISON")
    print(f"{'='*70}")

    benchmark_results = {}

    for classifier_name in ["full", "simple"]:
        regime_candles = {}
        for l in labels:
            regime = l[classifier_name]["regime"]
            if regime not in regime_candles:
                regime_candles[regime] = []
            regime_candles[regime].append(candles[l["idx"]])

        print(f"\n--- {classifier_name.upper()} classifier ---")
        total_trades = 0
        total_pnl = 0
        classifier_results = {}

        for regime in regimes:
            rc = regime_candles.get(regime, [])
            if not rc:
                print(f"  {regime.upper()}: No candles")
                classifier_results[regime] = {"trades": 0, "win_rate": 0, "net_pnl": 0, "max_dd": 0, "candles": 0}
                continue

            result = run_backtest_segment(rc, strategy_params, fill_model, fee_rate, 100.0)
            total_trades += result["trades"]
            total_pnl += result["net_pnl"]
            classifier_results[regime] = {
                "trades": result["trades"],
                "win_rate": result["win_rate"],
                "net_pnl": result["net_pnl"],
                "max_dd": result["max_drawdown"],
                "candles": len(rc),
            }

            print(f"  {regime.upper():<8}: {len(rc):>5} candles, {result['trades']:>3} trades, "
                  f"WR={result['win_rate']:>5.1f}%, PnL=${result['net_pnl']:>8.2f}, "
                  f"DD={result['max_drawdown']:>5.1f}%")

        print(f"  TOTAL: {total_trades} trades, ${total_pnl:.2f} net")
        benchmark_results[classifier_name] = {
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "per_regime": classifier_results,
        }

    # Key insight: which classifier better separates regimes?
    print(f"\n{'='*70}")
    print(f"REGIME SEPARATION ANALYSIS")
    print(f"{'='*70}")

    for cname, bref in benchmark_results.items():
        pr = bref["per_regime"]
        hot_wr = pr.get("hot", {}).get("win_rate", 0)
        cold_wr = pr.get("cold", {}).get("win_rate", 0)
        choppy_wr = pr.get("choppy", {}).get("win_rate", 0)
        hot_net = pr.get("hot", {}).get("net_pnl", 0)
        cold_net = pr.get("cold", {}).get("net_pnl", 0)
        choppy_net = pr.get("choppy", {}).get("net_pnl", 0)

        separation = hot_wr - choppy_wr
        print(f"\n  {cname.upper()}:")
        print(f"    HOT-CHOPPY WR gap: {separation:.1f} pps")
        print(f"    HOT net: ${hot_net:+.2f}, COLD net: ${cold_net:+.2f}, CHOPPY net: ${choppy_net:+.2f}")
        print(f"    Is CHOPPY losing? {'Yes' if choppy_net < 0 else 'No'} (net ${choppy_net:+.2f})")

    # Save report
    report = {
        "coin": args.coin,
        "window": args.window,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agreement_rate": round(agreement_rate, 1),
        "disagreements": disagree_count,
        "confusion_matrix": confusion,
        "time_distribution": {
            r: {
                "full_pct": round(full_counts.get(r, 0) / len(labels) * 100, 1),
                "full_count": full_counts.get(r, 0),
                "simple_pct": round(simple_counts.get(r, 0) / len(labels) * 100, 1),
                "simple_count": simple_counts.get(r, 0),
            }
            for r in regimes
        },
        "disagreement_summary": {
            "count": len(disagreements),
            "label_pairs": {f"{k[0]}_to_{k[1]}": v for k, v in Counter(
                (l["full"]["regime"], l["simple"]["regime"]) for l in disagreements
            ).items()},
            "avg_full_score": round(full_avg, 1) if disagreements else 0,
            "avg_simple_score": round(simple_avg, 1) if disagreements else 0,
            "avg_abs_btc_corr": round(btc_avg, 3) if disagreements else 0,
            "price_range": [round(min(l["price"] for l in disagreements), 4),
                           round(max(l["price"] for l in disagreements), 4)] if disagreements else [],
        },
        "benchmark_comparison": benchmark_results,
        "sample_disagreements": [
            {
                "idx": l["idx"],
                "time": datetime.fromtimestamp(l["time"], tz=timezone.utc).isoformat(),
                "price": l["price"],
                "full": {k: v for k, v in l["full"].items() if k != "components"},
                "simple": l["simple"],
            }
            for l in disagreements[:20]
        ],
    }

    output_dir = ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    coin_safe = args.coin.replace("-", "_")
    output_path = output_dir / f"regime_classifier_comparison_{coin_safe}_{args.window}.json"

    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    main()
