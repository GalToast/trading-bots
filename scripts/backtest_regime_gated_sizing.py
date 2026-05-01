#!/usr/bin/env python3
"""
Regime-Gated Position Sizing Backtest.

Compares static vs regime-gated sizing strategies on same data.

Usage:
    python scripts/backtest_regime_gated_sizing.py --coin RAVE-USD --window 30d
    python scripts/backtest_regime_gated_sizing.py --coin RAVE-USD --window 7d
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

from regime_detection import regime_score
from benchmark_regime_segmented import (
    fetch_candles_coinbase,
    normalize_candles,
    _align_btc_candles,
    compute_rsi,
    FEE_TIERS,
    FILL_MODELS,
    STRATEGY_REGISTRY,
)

ROOT = Path(__file__).resolve().parent.parent

SIZING_POLICIES = {
    "static_full": {
        "label": "Static Full (100% all signals)",
        "sizing": {"hot": 1.0, "cold": 1.0, "choppy": 1.0},
    },
    "gated_aggressive": {
        "label": "Gated Aggressive (HOT=100%, COLD=50%, CHOPPY=0%)",
        "sizing": {"hot": 1.0, "cold": 0.5, "choppy": 0.0},
    },
    "gated_moderate": {
        "label": "Gated Moderate (HOT=100%, COLD=75%, CHOPPY=25%)",
        "sizing": {"hot": 1.0, "cold": 0.75, "choppy": 0.25},
    },
    "gated_soft": {
        "label": "Gated Soft (HOT=100%, COLD=50%, CHOPPY=50%)",
        "sizing": {"hot": 1.0, "cold": 0.5, "choppy": 0.5},
    },
    "skip_choppy": {
        "label": "Skip Choppy (HOT=100%, COLD=100%, CHOPPY=0%)",
        "sizing": {"hot": 1.0, "cold": 1.0, "choppy": 0.0},
    },
}


def _score_to_regime(score: float) -> str:
    if score >= 70:
        return "hot"
    elif score >= 40:
        return "cold"
    else:
        return "choppy"


def classify_each_candle(candles: list[dict], btc_candles: list[dict], window: int = 30) -> list[str]:
    """Classify each candle into regime labels."""
    labels = []
    for i in range(len(candles)):
        if i < window - 1:
            wc = candles[:i + 1]
        else:
            wc = candles[i - window + 1:i + 1]
        aligned_btc = _align_btc_candles(wc, btc_candles)
        score = regime_score(wc, aligned_btc)
        labels.append(_score_to_regime(score["score"]))
    return labels


def run_gated_backtest(
    candles: list[dict],
    regime_labels: list[str],
    sizing: dict,
    strategy_params: dict,
    fill_model: dict,
    fee_rate: float,
    starting_cash: float = 100.0,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    closes_count = 0
    wins = 0
    losses = 0
    peak = starting_cash
    max_dd = 0.0
    history = []
    signals_taken = 0
    signals_skipped = 0
    position_sizes = []

    fill_prob = fill_model.get("fill_prob", 1.0)
    entry_slip = fill_model.get("entry_slippage_bps", 0) / 10000.0
    exit_slip = fill_model.get("exit_slippage_bps", 0) / 10000.0
    rsi_period = strategy_params.get("rsi_period", 3)
    os_thresh = strategy_params.get("os_thresh", 30)
    tp_pct = strategy_params.get("tp_pct", 25)
    sl_pct = strategy_params.get("sl_pct", 0)
    max_hold = strategy_params.get("max_hold", 48)

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
        current_regime = regime_labels[i] if i < len(regime_labels) else "cold"
        size_frac = sizing.get(current_regime, 1.0)

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
                rsi_val = compute_rsi(history[:-1], rsi_period)
                if rsi_val <= os_thresh:
                    if rng.random() > fill_prob:
                        continue
                    if size_frac == 0.0:
                        signals_skipped += 1
                        continue
                    deploy = cash * size_frac
                    if deploy < 10.0:
                        signals_skipped += 1
                        continue
                    signals_taken += 1
                    actual_entry = candle_open * (1 + entry_slip)
                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / actual_entry
                    tp = actual_entry * (1 + tp_pct / 100.0)
                    sl = actual_entry * (1 - sl_pct / 100.0) if sl_pct > 0 else 0
                    cash -= deploy
                    position_sizes.append(size_frac)
                    pos = {
                        "ep": actual_entry, "q": deploy, "hold": 0,
                        "tp": tp, "sl": sl, "units": units,
                        "entry_fee": entry_fee, "max_hold": max_hold,
                    }

    if pos:
        cash += pos["q"]
    net = cash - starting_cash
    avg_size = sum(position_sizes) / len(position_sizes) if position_sizes else 0
    return {
        "net_pnl": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 2),
        "trades": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / max(closes_count, 1) * 100, 1),
        "max_drawdown": round(max_dd * 100, 1),
        "signals_taken": signals_taken,
        "signals_skipped": signals_skipped,
        "avg_position_size_pct": round(avg_size * 100, 1),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coin", default="RAVE-USD")
    parser.add_argument("--window", default="30d")
    parser.add_argument("--strategy", default="rsi_mr")
    parser.add_argument("--fill-model", default="empirical")
    parser.add_argument("--fee-tier", default="40bps")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    args = parser.parse_args()

    days = 7 if args.window == "7d" else 30
    fee_rate = FEE_TIERS.get(args.fee_tier, 0.004)
    strategy_params = STRATEGY_REGISTRY[args.strategy]["params"]
    fill_model = FILL_MODELS.get(args.fill_model, FILL_MODELS["realistic"])

    print(f"Fetching {args.window} candles for {args.coin} and BTC-USD...")
    candles = normalize_candles(fetch_candles_coinbase(args.coin, days))
    btc_candles = normalize_candles(fetch_candles_coinbase("BTC-USD", days))
    print(f"Loaded {len(candles)} {args.coin}, {len(btc_candles)} BTC.")

    print("Classifying regimes...")
    regime_labels = classify_each_candle(candles, btc_candles)
    counts = {"hot": 0, "cold": 0, "choppy": 0}
    for r in regime_labels:
        counts[r] += 1
    total = len(regime_labels)
    print(f"HOT={counts['hot']} ({counts['hot']/total*100:.1f}%), "
          f"COLD={counts['cold']} ({counts['cold']/total*100:.1f}%), "
          f"CHOPPY={counts['choppy']} ({counts['choppy']/total*100:.1f}%)")

    print(f"\n{'='*90}")
    print(f"REGIME-GATED SIZING -- {args.coin} ({args.window}, {args.fill_model}, {args.fee_tier})")
    print(f"{'='*90}")

    all_results = {}
    for pname, pinfo in SIZING_POLICIES.items():
        r = run_gated_backtest(candles, regime_labels, pinfo["sizing"],
                               strategy_params, fill_model, fee_rate, args.starting_cash)
        all_results[pname] = {"label": pinfo["label"], "sizing": pinfo["sizing"], "result": r}
        print(f"\n{pinfo['label']}:")
        print(f"  Trades={r['trades']} WR={r['win_rate']}% Net=${r['net_pnl']:+.2f} "
              f"Ret={r['return_pct']:+.1f}% DD={r['max_drawdown']}% "
              f"Taken={r['signals_taken']} Skip={r['signals_skipped']} "
              f"AvgSize={r['avg_position_size_pct']:.0f}%")

    # Comparison table
    print(f"\n{'='*90}")
    tbl_hdr = f"{'Policy':<35} {'Trades':<7} {'WR%':<6} {'Net':<10} {'Ret%':<7} {'DD%':<6} {'Taken':<6} {'Skip':<6}"
    print(tbl_hdr)
    print("-" * 90)
    for pname, data in all_results.items():
        r = data["result"]
        print(f"{data['label'][:35]:<35} {r['trades']:<7} {r['win_rate']:<6.1f} "
              f"${r['net_pnl']:<9.2f} {r['return_pct']:<7.1f} {r['max_drawdown']:<6.1f} "
              f"{r['signals_taken']:<6} {r['signals_skipped']:<6}")

    # vs baseline
    baseline = all_results["static_full"]["result"]
    print(f"\nvs Static Full baseline (${baseline['net_pnl']:+.2f}, {baseline['max_drawdown']}% DD):")
    for pname, data in all_results.items():
        if pname == "static_full":
            continue
        r = data["result"]
        pnl_diff = r["net_pnl"] - baseline["net_pnl"]
        dd_diff = r["max_drawdown"] - baseline["max_drawdown"]
        print(f"  {data['label'][:45]:<45} PnL: ${pnl_diff:+.2f} | DD: {dd_diff:+.1f}pps")

    # Save
    report = {
        "coin": args.coin, "window": args.window,
        "fill_model": args.fill_model, "fee_tier": args.fee_tier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regime_distribution": counts,
        "policies": {n: {"label": d["label"], "sizing": d["sizing"], "result": d["result"]}
                     for n, d in all_results.items()},
    }
    output_dir = ROOT / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    coin_safe = args.coin.replace("-", "_")
    output_path = output_dir / f"regime_gated_sizing_{coin_safe}_{args.window}.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {output_path}")


if __name__ == "__main__":
    main()
