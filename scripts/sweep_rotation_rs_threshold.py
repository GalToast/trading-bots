#!/usr/bin/env python3
"""
Rotation RS Threshold Sweep
=============================
Tests different entry/exit thresholds to find the optimal balance between
signal frequency and edge quality.

Hypothesis: 2-3% entry threshold fires more pairs than 5% while maintaining
edge quality (since mean-reversion is a structural property, not threshold-dependent).

Sweeps:
- Entry thresholds: 1%, 2%, 3%, 4%, 5%
- Exit thresholds: 0.1%, 0.2%, 0.5%, 1.0%

Uses current live RS data to count potential signals per config.

Usage:
  python scripts/sweep_rotation_rs_threshold.py
"""
import json
import sys
from pathlib import Path
from itertools import combinations

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from rotation_lattice_shadow import compute_rolling_returns, COINS, WINDOW, fetch_candles, utc_now_iso

ENTRY_THRESHOLDS = [0.01, 0.02, 0.03, 0.04, 0.05]
EXIT_THRESHOLDS = [0.001, 0.002, 0.005, 0.010]

REPORT_PATH = ROOT / "reports" / "rotation_rs_threshold_sweep.json"
REPORT_MD = ROOT / "reports" / "rotation_rs_threshold_sweep.md"


def analyze_pair(rs_data, pair_name, entry_threshold, exit_threshold, max_hold=96):
    """Simulate signals for a pair with given thresholds."""
    signals = []
    position = None

    for i, data_point in enumerate(rs_data):
        rs = data_point["rs"]

        if position is None:
            # Check entry
            if rs < -entry_threshold:
                position = {
                    "entry_idx": i,
                    "entry_rs": rs,
                    "entry_price_a": data_point["price_a"],
                    "entry_price_b": data_point["price_b"],
                }
        else:
            # Check exit conditions
            hold_bars = i - position["entry_idx"]

            # Mean reversion exit
            if rs > -exit_threshold:
                signals.append({
                    "pair": pair_name,
                    "entry_rs": position["entry_rs"],
                    "exit_rs": rs,
                    "exit_reason": "mean_reversion",
                    "hold_bars": hold_bars,
                    "pnl_direction": "positive",  # RS moved from negative toward zero
                })
                position = None
            # Overshoot exit (RS went positive)
            elif rs > entry_threshold:
                signals.append({
                    "pair": pair_name,
                    "entry_rs": position["entry_rs"],
                    "exit_rs": rs,
                    "exit_reason": "overshoot",
                    "hold_bars": hold_bars,
                    "pnl_direction": "positive",
                })
                position = None
            # Timeout exit
            elif hold_bars >= max_hold:
                signals.append({
                    "pair": pair_name,
                    "entry_rs": position["entry_rs"],
                    "exit_rs": rs,
                    "exit_reason": "timeout",
                    "hold_bars": hold_bars,
                    "pnl_direction": "unknown",
                })
                position = None

    # Handle open position
    if position is not None:
        signals.append({
            "pair": pair_name,
            "entry_rs": position["entry_rs"],
            "exit_rs": rs_data[-1]["rs"] if rs_data else 0,
            "exit_reason": "still_open",
            "hold_bars": len(rs_data) - position["entry_idx"],
            "pnl_direction": "unknown",
        })

    return signals


def main():
    print("=" * 70)
    print("ROTATION RS THRESHOLD SWEEP")
    print("=" * 70)
    print(f"Entry thresholds: {[f'{t*100:.0f}%' for t in ENTRY_THRESHOLDS]}")
    print(f"Exit thresholds: {[f'{t*100:.2f}%' for t in EXIT_THRESHOLDS]}")
    print(f"Pairs: {list(combinations(COINS, 2))}")
    print(f"Window: {WINDOW} bars")
    print()

    # Fetch current candles
    print("Fetching candle data...")
    candles = {}
    for coin in COINS:
        try:
            c = fetch_candles(coin, FETCH_LOOKBACK_MINUTES=520)
            candles[coin] = c
            print(f"  {coin}: {len(c)} candles")
        except Exception as e:
            print(f"  {coin}: FAILED — {e}")
            candles[coin] = []

    # Compute RS data for all pairs
    print("\nComputing RS series...")
    pair_rs = {}
    for coin_a, coin_b in combinations(COINS, 2):
        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
        try:
            rs_data = compute_rolling_returns(candles.get(coin_a, []), candles.get(coin_b, []))
            pair_rs[pair_name] = rs_data
            if rs_data:
                print(f"  {pair_name}: {len(rs_data)} RS points, latest RS={rs_data[-1]['rs']*100:.2f}%")
            else:
                print(f"  {pair_name}: insufficient data")
        except Exception as e:
            print(f"  {pair_name}: FAILED — {e}")
            pair_rs[pair_name] = []

    # Sweep thresholds
    print("\nSweeping thresholds...")
    results = []

    for entry_thresh in ENTRY_THRESHOLDS:
        for exit_thresh in EXIT_THRESHOLDS:
            total_signals = 0
            mean_reversion_exits = 0
            timeout_exits = 0
            overshoot_exits = 0
            still_open = 0
            entry_rs_values = []

            for pair_name, rs_data in pair_rs.items():
                if not rs_data:
                    continue

                signals = analyze_pair(rs_data, pair_name, entry_thresh, exit_thresh)
                total_signals += len(signals)

                for sig in signals:
                    entry_rs_values.append(abs(sig["entry_rs"]))
                    if sig["exit_reason"] == "mean_reversion":
                        mean_reversion_exits += 1
                    elif sig["exit_reason"] == "timeout":
                        timeout_exits += 1
                    elif sig["exit_reason"] == "overshoot":
                        overshoot_exits += 1
                    elif sig["exit_reason"] == "still_open":
                        still_open += 1

            avg_entry_rs = sum(entry_rs_values) / len(entry_rs_values) if entry_rs_values else 0

            results.append({
                "entry_threshold": entry_thresh,
                "exit_threshold": exit_thresh,
                "total_signals": total_signals,
                "mean_reversion_exits": mean_reversion_exits,
                "timeout_exits": timeout_exits,
                "overshoot_exits": overshoot_exits,
                "still_open": still_open,
                "avg_entry_rs": avg_entry_rs,
            })

    # Sort by total signals descending
    results.sort(key=lambda r: r["total_signals"], reverse=True)

    # Print results
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"\n{'Entry':>6} {'Exit':>6} {'Signals':>8} {'MeanRev':>8} {'Timeout':>8} {'Overshoot':>10} {'Open':>5} {'Avg Entry RS':>12}")
    print(f"{'-'*75}")

    for r in results:
        print(
            f"{r['entry_threshold']*100:>5.0f}% {r['exit_threshold']*100:>5.2f}% "
            f"{r['total_signals']:>8} {r['mean_reversion_exits']:>8} "
            f"{r['timeout_exits']:>8} {r['overshoot_exits']:>10} "
            f"{r['still_open']:>5} {r['avg_entry_rs']*100:>11.2f}%"
        )

    # Find optimal
    # Optimal = high mean_reversion rate, reasonable signal count
    best_mr_rate = 0
    best_config = None
    for r in results:
        total_exits = r["mean_reversion_exits"] + r["timeout_exits"] + r["overshoot_exits"]
        mr_rate = r["mean_reversion_exits"] / total_exits if total_exits > 0 else 0
        # Prefer configs with >2 signals and high mean reversion rate
        if r["total_signals"] >= 2 and mr_rate > best_mr_rate:
            best_mr_rate = mr_rate
            best_config = r

    print("\n" + "=" * 70)
    print("RECOMMENDED CONFIG")
    print("=" * 70)

    if best_config:
        print(f"Entry threshold: {best_config['entry_threshold']*100:.0f}%")
        print(f"Exit threshold: {best_config['exit_threshold']*100:.2f}%")
        print(f"Total signals: {best_config['total_signals']}")
        print(f"Mean reversion exits: {best_config['mean_reversion_exits']}")
        print(f"Timeout exits: {best_config['timeout_exits']}")
        print(f"Overshoot exits: {best_config['overshoot_exits']}")
        print(f"Avg entry RS: {best_config['avg_entry_rs']*100:.2f}%")
    else:
        print("No config met minimum signal threshold.")
        print(f"Current 5% / 0.2% config has {results[-1]['total_signals']} signals.")

    # Save results
    report = {
        "timestamp": utc_now_iso(),
        "results": results,
        "recommended": best_config,
        "pair_rs_summary": {
            pair_name: {
                "rs_points": len(rs_data),
                "latest_rs": rs_data[-1]["rs"] if rs_data else None,
                "min_rs": min(d["rs"] for d in rs_data) if rs_data else None,
                "max_rs": max(d["rs"] for d in rs_data) if rs_data else None,
            }
            for pair_name, rs_data in pair_rs.items()
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nWrote {REPORT_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
