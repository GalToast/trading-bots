#!/usr/bin/env python3
"""
WR Improvement Study — Testing entry refinements to push Win Rate higher.

Tests entry parameter variants against the baseline RSI MR strategy:
1. RSI<25 (tighter entry — fewer signals, higher quality)
2. RSI<20 (even tighter — deep oversold only)
3. RSI<35 (wider entry — more signals, lower quality)
4. Volume filter (only enter when volume >= median)
5. Combined: RSI<25 + volume filter

Uses measured_forward_session_gated fill model (7.1bps entry, 87.1bps round-trip).

Usage:
    python scripts/wr_improvement_study.py --window 30d --coins RAVE-USD
"""
import argparse
import json
import os
import sys
import random
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from benchmark_shared import RAVE_RSI_MR_BASELINE_PARAMS, FEE_TIERS
from candle_cache_service import load_candles

# Entry variants to test
ENTRY_VARIANTS = {
    "baseline_rsi30": {
        "name": "RSI(3) < 30 (baseline)",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS},
    },
    "rsi25_tight": {
        "name": "RSI(3) < 25 (tight)",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "os_thresh": 25},
    },
    "rsi20_deep": {
        "name": "RSI(3) < 20 (deep oversold)",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "os_thresh": 20},
    },
    "rsi35_wide": {
        "name": "RSI(3) < 35 (wide)",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "os_thresh": 35},
    },
    "rsi25_vol_filter": {
        "name": "RSI(3) < 25 + volume filter",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "os_thresh": 25, "vol_filter": True},
    },
    "rsi30_vol_filter": {
        "name": "RSI(3) < 30 + volume filter",
        "params": {**RAVE_RSI_MR_BASELINE_PARAMS, "os_thresh": 30, "vol_filter": True},
    },
}

FILL_MODEL = {
    "fill_prob": 1.0,
    "entry_slippage_bps": 7.1,
    "exit_slippage_bps": 0.0,
}

FEE_BPS = 40
STARTING_CASH = 48.0


def compute_rsi(closes, period=3):
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


def compute_median_volume(candles, lookback=50):
    """Compute median volume over the last N candles."""
    if len(candles) < lookback:
        vols = [float(c.get("volume", 0)) for c in candles]
    else:
        vols = [float(c.get("volume", 0)) for c in candles[-lookback:]]
    vols.sort()
    n = len(vols)
    if n % 2 == 0:
        return (vols[n // 2 - 1] + vols[n // 2]) / 2
    return vols[n // 2]


def run_backtest(candles, strategy_params, fill_model, fee_bps, starting_cash, seed=42):
    rng = random.Random(seed)
    cash = starting_cash
    pos = None
    history = []
    peak = starting_cash
    max_dd = 0.0
    closes_count = 0
    wins = 0
    losses = 0
    signals = 0
    filled = 0
    session_filtered = 0
    regime_filtered = 0

    fill_prob = fill_model.get("fill_prob", 1.0)
    entry_slip = fill_model.get("entry_slippage_bps", 0) / 10000.0
    exit_slip = fill_model.get("exit_slippage_bps", 0) / 10000.0
    fee_rate = fee_bps / 10000
    os_thresh = strategy_params.get("os_thresh", 30)
    vol_filter = strategy_params.get("vol_filter", False)
    rsi_period = strategy_params.get("rsi_period", 3)

    for i in range(len(candles)):
        c = candles[i]
        ts_raw = c.get("start", c.get("time", 0))
        try:
            ts = int(ts_raw)
        except (ValueError, TypeError):
            try:
                ts = int(datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp())
            except Exception:
                ts = 0
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        candle_open = float(c["open"])

        history.append(close)
        if len(history) > 500:
            history = history[-500:]

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
        if pos is None and cash >= 10.0 and session_open:
            if len(history) >= rsi_period + 2:
                rsi_val = compute_rsi(history[:-1], rsi_period)

                if rsi_val <= os_thresh:
                    # Volume filter check
                    if vol_filter:
                        median_vol = compute_median_volume(candles[:i], 50)
                        current_vol = float(candles[i].get("volume", 0))
                        if current_vol < median_vol:
                            continue

                    signals += 1
                    if rng.random() > fill_prob:
                        session_filtered += 1
                        continue

                    actual_entry = candle_open * (1 + entry_slip)
                    deploy = cash
                    entry_fee = deploy * fee_rate
                    units = (deploy - entry_fee) / actual_entry
                    tp = actual_entry * (1 + strategy_params.get("tp_pct", 25) / 100.0)
                    sl = actual_entry * (1 - strategy_params.get("sl_pct", 0) / 100.0) if strategy_params.get("sl_pct", 0) > 0 else 0

                    cash -= deploy
                    pos = {
                        "ep": actual_entry, "q": deploy, "hold": 0,
                        "tp": tp, "sl": sl, "units": units,
                        "entry_fee": entry_fee,
                        "max_hold": strategy_params.get("max_hold", 48),
                    }
                    filled += 1

    if pos:
        cash += pos["q"]

    net = cash - starting_cash
    wr = wins / max(1, closes_count) * 100

    return {
        "net": round(net, 2),
        "return_pct": round(net / starting_cash * 100, 1),
        "closes": closes_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "max_dd": round(max_dd * 100, 1),
        "signals": signals,
        "filled": filled,
        "fill_rate": round(filled / max(1, signals) * 100, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="WR Improvement Study")
    parser.add_argument("--window", default="30d", choices=["7d", "30d"])
    parser.add_argument("--coins", nargs="+", default=["RAVE-USD"])
    parser.add_argument("--fee-bps", type=int, default=40)
    args = parser.parse_args()

    days = 7 if args.window == "7d" else 30
    granularity = "FIVE_MINUTE"

    print(f"WR Improvement Study — Window: {args.window}, Coins: {args.coins}", flush=True)
    print(f"Fill model: measured_forward_session_gated (87.1bps round-trip)", flush=True)
    print(f"Fee: {args.fee_bps}bps", flush=True)
    print()

    all_results = {}

    for coin in args.coins:
        print(f"Loading candles for {coin} ({days}d)...")
        candles = load_candles(coin, granularity, days, max_age_minutes=10000)
        if not candles:
            print(f"  No candles found, skipping {coin}", flush=True)
            continue
        print(f"  Loaded {len(candles)} candles", flush=True)

        coin_results = {}
        for variant_name, variant_config in ENTRY_VARIANTS.items():
            params = variant_config["params"]
            result = run_backtest(candles, params, FILL_MODEL, args.fee_bps, STARTING_CASH)
            coin_results[variant_name] = {
                "name": variant_config["name"],
                **result,
            }
            print(f"  {variant_config['name']:40s} → net=${result['net']:>8.2f} closes={result['closes']:>3} wr={result['win_rate']:>5.1f}% dd={result['max_dd']:>5.1f}% signals={result['signals']:>3}", flush=True)

        all_results[coin] = coin_results

    # Print comparison table
    print(f"\n{'='*100}")
    print(f"WR IMPROVEMENT STUDY — {args.window} window, {args.fee_bps}bps fees, 87.1bps round-trip cost")
    print(f"{'='*100}")
    print(f"{'Variant':<40} {'Net $':>8} {'Closes':>8} {'WR%':>6} {'DD%':>6} {'Signals':>8} {'Monthly $':>10}")
    print("-" * 100)

    baseline_net = None
    for variant_name, variant_config in ENTRY_VARIANTS.items():
        for coin in args.coins:
            if coin in all_results and variant_name in all_results[coin]:
                r = all_results[coin][variant_name]
                # Monthly projection (30d window = actual 30d, 7d = annualize)
                monthly_proj = r["net"] if args.window == "30d" else r["net"] * 30 / 7
                if variant_name == "baseline_rsi30":
                    baseline_net = r["net"]
                delta = f""
                if baseline_net is not None and variant_name != "baseline_rsi30":
                    delta_val = r["net"] - baseline_net
                    delta = f" ({delta_val:+.2f})"
                print(f"{r['name']:<40} ${r['net']:>7.2f} {r['closes']:>8} {r['win_rate']:>5.1f}% {r['max_dd']:>5.1f}% {r['signals']:>8} ${monthly_proj:>9.2f}{delta}")

    print(f"\nKey insight: Improving WR by 5% (e.g., 55%→60%) adds ~$50-75/month on $48.")
    print(f"Tighter entries (RSI<25, RSI<20) should increase WR but reduce trade count.")
    print(f"Volume filter should filter low-liquidity traps.")

    # Save report
    output_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"wr_improvement_study_{args.window}.json")
    with open(output_path, "w") as f:
        json.dump({
            "window": args.window,
            "fee_bps": args.fee_bps,
            "fill_model": "measured_forward_session_gated",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "results": all_results,
        }, f, indent=2)
    print(f"\nReport saved: {output_path}")


if __name__ == "__main__":
    main()
