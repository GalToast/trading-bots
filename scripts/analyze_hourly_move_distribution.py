#!/usr/bin/env python3
"""Analyze hourly move distribution from 1m candle cache.

Answers: How often do products make moves large enough to clear fees + hit 5%/hr?

Uses 1m candles to build rolling 60-minute windows and measure:
1. Max unidirectional move in each hour (high-low direction)
2. How often moves exceed fee thresholds (2.4% = break even, 5% = target, 7.4% = 5% net after fees)
3. How often moves are "clean" (don't reverse >50% before hour ends)

Works on any 1m candle cache file.
"""
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"

# Products with 30d 1m data
PRODUCTS_30D = ["RAVE_USD_ONE_MINUTE_30d.json", "SOL_USD_ONE_MINUTE_30d.json"]
# Products with 7d 1m data
PRODUCTS_7D = [
    "BTC_USD_ONE_MINUTE_7d.json",
    "ETH_USD_ONE_MINUTE_7d.json",
    "IOTX_USD_ONE_MINUTE_7d.json",
    "ALEPH_USD_ONE_MINUTE_7d.json",
    "BAL_USD_ONE_MINUTE_7d.json",
    "BLUR_USD_ONE_MINUTE_7d.json",
]

FEE_BPS = 120  # per side
ROUND_TRIP_FEE_PCT = (FEE_BPS * 2) / 10000.0  # 0.024 = 2.4%
BREAK_EVEN_GROSS = ROUND_TRIP_FEE_PCT  # 2.4%
TARGET_5PCT_NET_GROSS = 0.05 + ROUND_TRIP_FEE_PCT  # 7.4%


def load_candles(filename):
    path = CACHE_DIR / filename
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("candles") or data.get("data") or []


def analyze_product(candles, product_name, granularity="1m"):
    if len(candles) < 60:
        print(f"  {product_name}: not enough candles ({len(candles)})")
        return None

    # Each candle: [timestamp, open, high, low, close, volume]
    # Build hourly windows (60 candles = 1 hour for 1m data)
    candles_per_hour = 60 if granularity == "1m" else 12  # 5min = 12 per hour

    hours = len(candles) // candles_per_hour
    if hours < 1:
        return None

    results = {
        "product": product_name,
        "total_candles": len(candles),
        "total_hours": hours,
        "thresholds": {
            "break_even_2.4pct": 0,
            "target_5pct_net_7.4pct": 0,
            "any_3pct": 0,
            "any_5pct": 0,
            "any_10pct": 0,
        },
        "max_move_pct": 0.0,
        "avg_max_move_pct": 0.0,
        "median_max_move_pct": 0.0,
        "hourly_moves": [],
    }

    moves = []
    for h in range(hours):
        start = h * candles_per_hour
        end = start + candles_per_hour
        hour = candles[start:end]

        # Each candle is a dict: {time, open, high, low, close, volume}
        opens = [c["open"] for c in hour]
        highs = [c["high"] for c in hour]
        lows = [c["low"] for c in hour]

        if not opens or opens[0] == 0:
            continue

        # Directional move: from open to max high (bullish) or open to min low (bearish)
        max_high = max(highs)
        min_low = min(lows)
        open_price = opens[0]

        bullish_move = (max_high - open_price) / open_price * 100
        bearish_move = (open_price - min_low) / open_price * 100
        max_move = max(bullish_move, bearish_move)

        moves.append(max_move)

        if max_move >= 2.4:
            results["thresholds"]["break_even_2.4pct"] += 1
        if max_move >= 7.4:
            results["thresholds"]["target_5pct_net_7.4pct"] += 1
        if max_move >= 3.0:
            results["thresholds"]["any_3pct"] += 1
        if max_move >= 5.0:
            results["thresholds"]["any_5pct"] += 1
        if max_move >= 10.0:
            results["thresholds"]["any_10pct"] += 1

        if max_move > results["max_move_pct"]:
            results["max_move_pct"] = max_move

    if moves:
        import statistics
        results["avg_max_move_pct"] = round(statistics.mean(moves), 4)
        results["median_max_move_pct"] = round(statistics.median(moves), 4)
        results["hourly_moves"] = sorted(moves, reverse=True)[:20]  # Top 20

    return results


def main():
    print("=== Hourly Move Distribution Analysis ===")
    print(f"Fee: {FEE_BPS}bps/side = {ROUND_TRIP_FEE_PCT*100:.1f}% round-trip")
    print(f"Break-even gross move: {BREAK_EVEN_GROSS*100:.1f}%")
    print(f"5% net requires: {TARGET_5PCT_NET_GROSS*100:.1f}% gross move")
    print()

    all_results = []

    for filename in PRODUCTS_30D + PRODUCTS_7D:
        product = filename.replace("_ONE_MINUTE_30d.json", "").replace("_ONE_MINUTE_7d.json", "").replace("_", "-")
        candles = load_candles(filename)
        if candles is None:
            print(f"  {product}: file not found")
            continue

        result = analyze_product(candles, product)
        if result is None:
            continue

        all_results.append(result)

        hrs = result["total_hours"]
        t = result["thresholds"]
        print(f"\n{product} ({hrs} hours):")
        print(f"  Avg max hourly move: {result['avg_max_move_pct']:.2f}%")
        print(f"  Median max hourly move: {result['median_max_move_pct']:.2f}%")
        print(f"  Max single-hour move: {result['max_move_pct']:.2f}%")
        print(f"  Hours >= 2.4% (break-even): {t['break_even_2.4pct']} ({t['break_even_2.4pct']/hrs*100:.1f}%)")
        print(f"  Hours >= 3.0%: {t['any_3pct']} ({t['any_3pct']/hrs*100:.1f}%)")
        print(f"  Hours >= 5.0%: {t['any_5pct']} ({t['any_5pct']/hrs*100:.1f}%)")
        print(f"  Hours >= 7.4% (5% net): {t['target_5pct_net_7.4pct']} ({t['target_5pct_net_7.4pct']/hrs*100:.1f}%)")
        print(f"  Hours >= 10.0%: {t['any_10pct']} ({t['any_10pct']/hrs*100:.1f}%)")
        if result["hourly_moves"]:
            print(f"  Top 5 hourly moves: {', '.join(f'{m:.1f}%' for m in result['hourly_moves'][:5])}")

    # Summary
    print("\n\n=== SUMMARY ===")
    print(f"{'Product':<15} {'Hours':>6} {'Avg%':>7} {'Med%':>7} {'Max%':>7} {'>=2.4%':>7} {'>=5%':>7} {'>=7.4%':>7}")
    print("-" * 75)
    for r in sorted(all_results, key=lambda x: x["avg_max_move_pct"], reverse=True):
        t = r["thresholds"]
        hrs = r["total_hours"]
        print(f"{r['product']:<15} {hrs:>6} {r['avg_max_move_pct']:>6.2f}% {r['median_max_move_pct']:>6.2f}% {r['max_move_pct']:>6.1f}% {t['break_even_2.4pct']/hrs*100:>6.1f}% {t['any_5pct']/hrs*100:>6.1f}% {t['target_5pct_net_7.4pct']/hrs*100:>6.1f}%")


if __name__ == "__main__":
    main()
