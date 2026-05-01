#!/usr/bin/env python3
"""
Rotation RS Diagnostic
======================
Quick check of current relative strength values for all rotation pairs.
Answers: why is the rotation runner at 0 signals after 32 cycles?
"""
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from itertools import combinations

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from coinbase_advanced_client import CoinbaseAdvancedClient
from multi_coin_isolated_runner import fetch_candles

COINS = ["CFG-USD", "RAVE-USD", "BAL-USD", "SUP-USD"]
WINDOW = 96  # 8 hours of 5-min bars
ENTRY_THRESHOLD = 0.05  # 5%


def compute_rs(candles_a, candles_b, window=WINDOW):
    """Compute relative strength series (inlined from rotation_lattice_shadow.py)."""
    ts_a = {int(c["start"]): float(c["close"]) for c in candles_a}
    ts_b = {int(c["start"]): float(c["close"]) for c in candles_b}
    common_ts = sorted(set(ts_a.keys()) & set(ts_b.keys()))

    if len(common_ts) < window + 1:
        return []

    rel_strength = []
    for i in range(window, len(common_ts)):
        ts_now = common_ts[i]
        ts_then = common_ts[i - window]

        ret_a = (ts_a[ts_now] - ts_a[ts_then]) / ts_a[ts_then]
        ret_b = (ts_b[ts_now] - ts_b[ts_then]) / ts_b[ts_then]

        rel_strength.append({
            "timestamp": ts_now,
            "rs": ret_a - ret_b,
            "price_a": ts_a[ts_now],
        })

    return rel_strength


def main():
    print("=" * 72)
    print("ROTATION RS DIAGNOSTIC")
    print("=" * 72)
    print()

    try:
        client = CoinbaseAdvancedClient()
    except Exception as e:
        print(f"⚠️  Cannot initialize client: {e}")
        print("  This script needs Coinbase API credentials from .env")
        return

    pairs = list(combinations(COINS, 2))
    print(f"Pairs: {len(pairs)}")
    print(f"Window: {WINDOW} bars (8 hours)")
    print(f"Entry threshold: {ENTRY_THRESHOLD*100:.0f}% RS divergence")
    print()

    now = int(time.time())
    start = now - 520 * 60  # 8.7 hours lookback

    all_candles = {}
    for coin in COINS:
        try:
            candles = fetch_candles(client, coin, start, now, "FIVE_MINUTE")
            all_candles[coin] = candles
            print(f"{coin}: {len(candles)} candles")
        except Exception as e:
            print(f"{coin}: ERROR — {e}")
            all_candles[coin] = []

    print()
    print(f"{'Pair':<20} {'RS Bars':>7} {'Latest RS':>10} {'Max |RS|':>10} {'Signal?':>10}")
    print("-" * 62)

    signals_possible = 0
    all_max_abs = []

    for coin_a, coin_b in pairs:
        pair_name = f"{coin_a.replace('-USD', '')}/{coin_b.replace('-USD', '')}"
        candles_a = all_candles.get(coin_a, [])
        candles_b = all_candles.get(coin_b, [])

        rs_data = compute_rs(candles_a, candles_b, window=WINDOW)

        if not rs_data:
            print(f"{pair_name:<20} {'no data':>7} {'N/A':>10} {'N/A':>10} {'no data':>10}")
            continue

        bars = len(rs_data)
        latest_rs = rs_data[-1]["rs"]
        max_abs_rs = max(abs(r["rs"]) for r in rs_data)
        can_fire = latest_rs < -ENTRY_THRESHOLD

        all_max_abs.append(max_abs_rs)

        signal_str = "✅ YES" if can_fire else "❌ no"
        if can_fire:
            signals_possible += 1

        print(f"{pair_name:<20} {bars:>7} {latest_rs:>10.4%} {max_abs_rs:>10.4%} {signal_str:>10}")

    print()
    if signals_possible == 0:
        print(f"🔒 No pair meets the {ENTRY_THRESHOLD*100:.0f}% entry threshold RIGHT NOW.")

    if all_max_abs:
        overall_max = max(all_max_abs)
        overall_mean = sum(all_max_abs) / len(all_max_abs)
        print(f"\n   Max RS seen (any pair, any bar): {overall_max:.4%}")
        print(f"   Mean max RS per pair:            {overall_mean:.4%}")
        print(f"   Entry threshold:                 {ENTRY_THRESHOLD:.4%}")
        if overall_max < ENTRY_THRESHOLD:
            print(f"\n   📉 The 5% bar has NEVER been reached in the last 8 hours for ANY pair.")
            print(f"   → The threshold is too high for current market regime.")
            print(f"   → Recommendation: lower to 2-3% and re-evaluate.")
        else:
            print(f"\n   ✅ The 5% bar WAS reached during the window, just not at the latest bar.")
            print(f"   → The runner IS working correctly; market just hasn't diverged enough recently.")


if __name__ == "__main__":
    main()
