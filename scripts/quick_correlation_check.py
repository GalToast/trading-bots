"""
Quick FX pair correlation analysis using MT5 H1 data.
Pulls last 500 H1 bars, computes return correlation matrix,
identifies most negatively correlated pairs for hedging thesis validation.
"""
import sys
import os

# Try MT5 first
MT5_AVAILABLE = False
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    pass

import json
import math
from datetime import datetime, timedelta
from itertools import combinations

# Fallback: check for any cached candle data in the repo
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "candle_cache")

SYMBOLS = ["EURUSD", "GBPUSD", "USDCHF", "USDJPY", "AUDUSD", "NZDUSD", "USDCAD", "DXY"]


def pearson(x, y):
    """Compute Pearson correlation between two equal-length lists."""
    n = len(x)
    if n == 0:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    num = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((x[i] - mean_x) ** 2 for i in range(n)))
    den_y = math.sqrt(sum((y[i] - mean_y) ** 2 for i in range(n)))
    den = den_x * den_y
    if den == 0:
        return 0.0
    return num / den


def returns_from_prices(prices):
    """Convert price list to list of returns (pct changes)."""
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices)) if prices[i - 1] != 0]


def fetch_mt5_data(symbols, bars=500, timeframe="H1"):
    """Pull last N bars from MT5 for each symbol."""
    if not MT5_AVAILABLE:
        print("[WARN] MetaTrader5 module not available.")
        return {}

    if not mt5.initialize():
        print("[WARN] MT5 initialize() failed. Is terminal running?")
        return {}

    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe, mt5.TIMEFRAME_H1)

    data = {}
    for sym in symbols:
        rates = mt5.copy_rates_from_pos(sym, tf, 0, bars)
        if rates is None or len(rates) == 0:
            print(f"  [WARN] No data for {sym} (error: {mt5.last_error()})")
            continue
        closes = [r["close"] for r in rates]
        data[sym] = closes
        print(f"  {sym}: {len(closes)} bars, last close={closes[-1]:.5f}")

    mt5.shutdown()
    return data


def fetch_cached_data(symbols):
    """Check candle_cache for any usable JSON files."""
    if not os.path.isdir(DATA_DIR):
        return {}

    cached = {}
    for f in os.listdir(DATA_DIR):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(DATA_DIR, f)) as fh:
                candles = json.load(fh)
            if not isinstance(candles, list) or len(candles) < 10:
                continue
            # Extract symbol from filename: "RAVE-USD_30d.json"
            sym = f.replace(".json", "").split("_")[0].replace("-", "").upper()
            closes = []
            for c in candles:
                try:
                    closes.append(float(c.get("close", c.get("Close", 0))))
                except (ValueError, TypeError):
                    pass
            if len(closes) >= 10:
                cached[sym] = closes
        except Exception:
            pass
    return cached


def main():
    print("=" * 70)
    print("FX Pair Correlation Analysis")
    print("=" * 70)

    # Step 1: Try MT5
    print("\n[1] Attempting MT5 data fetch (500 H1 bars)...")
    price_data = fetch_mt5_data(SYMBOLS, bars=500, timeframe="H1")

    # Step 2: Fallback to cache
    if len(price_data) < 2:
        print("\n[2] MT5 failed. Checking candle_cache for fallback data...")
        cached = fetch_cached_data(SYMBOLS)
        if cached:
            print(f"  Found cached data for: {list(cached.keys())}")
            price_data = cached
        else:
            print("  No usable cached data found.")

    if len(price_data) < 2:
        print("\n[ERROR] Insufficient data (need >= 2 symbols with data). Exiting.")
        print("  Ensure MT5 terminal is running and symbols are available in Market Watch.")
        sys.exit(1)

    # Step 3: Compute returns
    print(f"\n[3] Computing returns for {len(price_data)} symbols:")
    returns = {}
    for sym, closes in price_data.items():
        ret = returns_from_prices(closes)
        returns[sym] = ret
        print(f"  {sym}: {len(ret)} returns")

    # Step 4: Align lengths to shortest
    min_len = min(len(r) for r in returns.values())
    for sym in returns:
        returns[sym] = returns[sym][-min_len:]
    print(f"\n  Aligned to {min_len} common bars.")

    # Step 5: Correlation matrix
    syms = sorted(returns.keys())
    corr_matrix = {}
    pairs = []
    for s1, s2 in combinations(syms, 2):
        c = pearson(returns[s1], returns[s2])
        corr_matrix[(s1, s2)] = c
        pairs.append((s1, s2, c))

    # Sort by correlation (most negative first)
    pairs.sort(key=lambda x: x[2])

    # Print full matrix header
    print("\n[4] Correlation Matrix (returns):")
    header = "         " + "  ".join(f"{s:>10}" for s in syms)
    print(header)
    for s1 in syms:
        row = f"{s1:>8} "
        for s2 in syms:
            if s1 == s2:
                row += f"{'1.000':>10}"
            elif s1 < s2:
                row += f"{corr_matrix[(s1, s2)]:>10.3f}"
            else:
                row += f"{corr_matrix[(s2, s1)]:>10.3f}"
        print(row)

    # Step 6: Top 5 most negatively correlated
    print(f"\n[5] Top 5 Most Negatively Correlated Pairs:")
    print(f"  {'Rank':<5} {'Pair':<25} {'Correlation':>12}  {'Hedge Quality'}")
    print(f"  {'-' * 60}")
    for i, (s1, s2, c) in enumerate(pairs[:5], 1):
        if c < -0.7:
            quality = "STRONG hedge candidate"
        elif c < -0.4:
            quality = "MODERATE hedge"
        elif c < -0.1:
            quality = "WEAK hedge"
        else:
            quality = "Not useful for hedging"
        print(f"  {i:<5} {s1}/{s2:<20} {c:>12.4f}  {quality}")

    # Step 7: Top positively correlated (for context)
    pairs_pos = sorted(pairs, key=lambda x: x[2], reverse=True)
    print(f"\n[6] Top 5 Most Positively Correlated Pairs (for context):")
    for i, (s1, s2, c) in enumerate(pairs_pos[:5], 1):
        print(f"  {i:<5} {s1}/{s2:<20} {c:>12.4f}")

    # Step 8: Thesis assessment
    print("\n" + "=" * 70)
    print("THESIS ASSESSMENT: Cross-Symbol Hedging via Negative Correlation")
    print("=" * 70)

    neg_pairs = [(s1, s2, c) for s1, s2, c in pairs if c < -0.3]
    strong_neg = [(s1, s2, c) for s1, s2, c in pairs if c < -0.7]

    if strong_neg:
        print(f"\n  SUPPORTED: Found {len(strong_neg)} strongly negatively correlated pair(s).")
        print(f"  Best hedge pair: {strong_neg[0][0]}/{strong_neg[0][1]} (r={strong_neg[0][2]:.4f})")
        print(f"  Floating loss on one should partially offset floating gain on the other.")
    elif neg_pairs:
        print(f"\n  PARTIALLY SUPPORTED: Found {len(neg_pairs)} moderately negative pair(s),")
        print(f"  but none reach strong hedge territory (r < -0.7).")
        print(f"  Best: {neg_pairs[0][0]}/{neg_pairs[0][1]} (r={neg_pairs[0][2]:.4f})")
        print(f"  Expect partial but imperfect offset.")
    else:
        print(f"\n  NOT SUPPORTED: No meaningfully negative correlations found.")
        print(f"  Most negative: {pairs[0][0]}/{pairs[0][1]} (r={pairs[0][2]:.4f})")
        print(f"  Cross-symbol hedging thesis is NOT validated by this dataset.")

    # Classic FX hedge pairs note
    classic = [("EURUSD", "USDCHF"), ("GBPUSD", "USDCHF"), ("AUDUSD", "USDCHF")]
    print(f"\n  Classic negative-correlation hedge pairs (USDCHF vs EUR-funding):")
    for s1, s2 in classic:
        if (s1, s2) in corr_matrix:
            c = corr_matrix[(s1, s2)]
            print(f"    {s1}/{s2}: r={c:.4f}")
        elif (s2, s1) in corr_matrix:
            c = corr_matrix[(s2, s1)]
            print(f"    {s1}/{s2}: r={c:.4f}")
        else:
            print(f"    {s1}/{s2}: (data not available)")

    print()


if __name__ == "__main__":
    main()
