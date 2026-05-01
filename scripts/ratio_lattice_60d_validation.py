#!/usr/bin/env python3
"""
Ratio Lattice 60-Day Validation

Extends the multi-asset ratio lattice to a 60-day window to test whether
edges are structural or window-dependent.

Focuses on the top candidates from the 30-day run:
- BAL/BTC (+0.00637, 23 closes, 82% closure, 0.05% max DD)
- BAL/ETH (+0.00769, 30 closes, 86% closure, 0.17% max DD)
- SUP/BTC (+0.00385, 15 closes, 75% closure, 0.95% max DD)
- ETH/BTC (+0.00443 BTC, 93 closes, from eth_btc_ratio_lattice.py)

Also tests the ETH/BTC ratio using the same z-score lattice framework
for apples-to-apples comparison.

Output: reports/ratio_lattice_60d_validation.json
"""
import json
import math
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from coinbase_advanced_client import CoinbaseAdvancedClient
    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False

# ---------------------------------------------------------------------------
# Focus pairs (from 30-day results)
# ---------------------------------------------------------------------------
FOCUS_PAIRS = [
    ("BAL", "BTC"),
    ("BAL", "ETH"),
    ("SUP", "BTC"),
    ("SUP", "ETH"),
    ("ETH", "BTC"),       # benchmark — already known positive
    ("CFG", "BTC"),       # high score in 30d
    ("CFG", "ETH"),       # high score in 30d
    ("NOM", "ETH"),       # highest score but negative PnL — investigate
    ("NOM", "BTC"),       # highest score but negative PnL — investigate
]

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

SYMBOL_TO_PRODUCT = {
    "NOM": "NOM-USD",
    "GHST": "GHST-USD",
    "SUP": "SUP-USD",
    "RAVE": "RAVE-USD",
    "BAL": "BAL-USD",
    "A8": "A8-USD",
    "CFG": "CFG-USD",
    "IOTX": "IOTX-USD",
    "TRU": "TRU-USD",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}


def fetch_candles(client, symbol, start, end):
    """Fetch M5 candles from Coinbase, chunked into 30-day windows."""
    chunk_sec = 300 * 5 * 60
    all_candles = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(symbol, start=cs, end=ce, granularity="FIVE_MINUTE")
            candles = resp.get("candles", [])
            all_candles.extend(candles)
            cs = ce
            if not candles:
                break
            time.sleep(0.1)
        except Exception:
            cs = ce
            time.sleep(0.5)

    all_candles.sort(key=lambda c: int(c["start"]))
    seen = set()
    unique = []
    for c in all_candles:
        if c["start"] not in seen:
            seen.add(c["start"])
            unique.append(c)
    return unique


def build_price_map(candles):
    return {int(c["start"]): float(c["close"]) for c in candles}


def build_ratio_series(price_a, price_b):
    common_ts = sorted(set(price_a.keys()) & set(price_b.keys()))
    series = []
    for ts in common_ts:
        pb = price_b[ts]
        if pb > 0:
            series.append({
                "t": ts,
                "ratio": price_a[ts] / pb,
                "price_a": price_a[ts],
                "price_b": pb,
            })
    return series


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def compute_rolling_stats(ratio_series, window=200):
    ratios = [r["ratio"] for r in ratio_series]
    n = len(ratios)
    result = []
    for i in range(n):
        if i < window - 1:
            result.append({"mean": None, "std": None, "z_score": None})
            continue
        chunk = ratios[i - window + 1: i + 1]
        mean = sum(chunk) / window
        variance = sum((x - mean) ** 2 for x in chunk) / window
        std = math.sqrt(variance) if variance > 0 else 0.0
        z = (ratios[i] - mean) / std if std > 0 else 0.0
        result.append({"mean": mean, "std": std, "z_score": z})
    return result


def find_attractors_kde(ratio_series, bandwidth=None):
    ratios = [r["ratio"] for r in ratio_series]
    n = len(ratios)
    if n < 10:
        return []
    if bandwidth is None:
        mean = sum(ratios) / n
        std = math.sqrt(sum((r - mean) ** 2 for r in ratios) / n)
        sorted_r = sorted(ratios)
        q1 = sorted_r[n // 4]
        q3 = sorted_r[3 * n // 4]
        iqr = q3 - q1
        bandwidth = 0.9 * min(std, iqr / 1.34) * (n ** -0.2)
        if bandwidth <= 0:
            bandwidth = std * 0.5 if std > 0 else 0.01

    min_r, max_r = min(ratios), max(ratios)
    if max_r == min_r:
        return [{"ratio": min_r, "density": float(n)}]

    grid_points = 200
    grid = [min_r + i * (max_r - min_r) / grid_points for i in range(grid_points)]
    densities = []
    for g in grid:
        density = 0.0
        for r in ratios:
            u = (r - g) / bandwidth
            if abs(u) < 4:
                density += math.exp(-0.5 * u * u)
        densities.append(density)

    attractors = []
    for i in range(1, len(densities) - 1):
        if densities[i] > densities[i - 1] and densities[i] > densities[i + 1]:
            attractors.append({"ratio": grid[i], "density": densities[i]})
    attractors.sort(key=lambda a: a["density"], reverse=True)
    return attractors


def compute_zero_crossing_rate(ratio_series, window=200):
    stats = compute_rolling_stats(ratio_series, window=window)
    n = len(stats)
    if n < window:
        return 0.0
    crossings = 0
    total_checked = 0
    for i in range(window, n):
        if stats[i]["mean"] is None or stats[i - 1]["mean"] is None:
            continue
        dev_curr = ratio_series[i]["ratio"] - stats[i]["mean"]
        dev_prev = ratio_series[i - 1]["ratio"] - stats[i - 1]["mean"]
        if dev_curr * dev_prev < 0:
            crossings += 1
        total_checked += 1
    return crossings / total_checked if total_checked > 0 else 0.0


# ---------------------------------------------------------------------------
# Lattice shadow — attractor-based (matches eth_btc_ratio_lattice.py logic)
# ---------------------------------------------------------------------------

def run_attractor_lattice(ratio_series, attractors, position_size=0.01,
                          profit_threshold=1.002, max_concurrent=5, max_levels=10):
    """
    Attractor-based lattice shadow matching eth_btc_ratio_lattice.py:
    - Buy when ratio drops <= attractor level
    - Sell when ratio >= entry_level * profit_threshold
    - One position per attractor level
    """
    top_attractors = attractors[:max_levels]
    positions = []  # {level_idx, entry_ratio, level_value, size}
    realized_pnl = 0.0
    total_opens = 0
    total_closes = 0
    max_open_seen = 0

    for point in ratio_series:
        ratio = point["ratio"]

        # Try to open at attractor levels
        for idx, attr in enumerate(top_attractors):
            level_val = attr["ratio"]
            # Check if already have a position at this level
            occupied = any(p["level_idx"] == idx for p in positions)
            if not occupied and ratio <= level_val and len(positions) < max_concurrent:
                positions.append({
                    "level_idx": idx,
                    "entry_ratio": ratio,
                    "level_value": level_val,
                    "size": position_size,
                })
                total_opens += 1

        # Try to close positions
        closes_this_bar = []
        for pos in positions:
            exit_level = pos["level_value"] * profit_threshold
            if ratio >= exit_level:
                pnl = pos["size"] * (ratio - pos["entry_ratio"]) / pos["entry_ratio"]
                realized_pnl += pnl
                total_closes += 1
                closes_this_bar.append(pos)

        for pos in closes_this_bar:
            positions.remove(pos)

        max_open_seen = max(max_open_seen, len(positions))

    closure_rate = total_closes / total_opens if total_opens > 0 else 0.0

    # Buy-and-hold comparison
    bh_pnl = 0.0
    if len(ratio_series) >= 2:
        bh_pnl = (ratio_series[-1]["ratio"] - ratio_series[0]["ratio"]) / ratio_series[0]["ratio"]

    return {
        "realized_pnl": realized_pnl,
        "total_opens": total_opens,
        "total_closes": total_closes,
        "closure_rate": closure_rate,
        "max_open_seen": max_open_seen,
        "buy_and_hold_pnl": bh_pnl,
        "lattice_vs_bh": realized_pnl - bh_pnl,
    }


# ---------------------------------------------------------------------------
# Lattice shadow — z-score based (matches multi_asset_ratio_lattice.py logic)
# ---------------------------------------------------------------------------

def run_zscore_lattice(ratio_series, z_scores, position_size=0.01,
                       entry_z=-1.5, exit_z=1.5, max_concurrent=5):
    n = len(ratio_series)
    if n == 0:
        return None

    positions = []
    realized_pnl = 0.0
    total_opens = 0
    total_closes = 0

    for i in range(n):
        ratio = ratio_series[i]["ratio"]
        z = z_scores[i]
        if z is None:
            continue

        if z < entry_z and len(positions) < max_concurrent:
            positions.append({"entry_ratio": ratio, "entry_bar": i, "size": position_size})
            total_opens += 1

        closes_this_bar = []
        for pos in positions:
            if z > exit_z:
                pnl = pos["size"] * (ratio - pos["entry_ratio"]) / pos["entry_ratio"]
                realized_pnl += pnl
                total_closes += 1
                closes_this_bar.append(pos)

        for pos in closes_this_bar:
            positions.remove(pos)

    closure_rate = total_closes / total_opens if total_opens > 0 else 0.0
    bh_pnl = (ratio_series[-1]["ratio"] - ratio_series[0]["ratio"]) / ratio_series[0]["ratio"] if n >= 2 else 0.0

    return {
        "realized_pnl": realized_pnl,
        "total_opens": total_opens,
        "total_closes": total_closes,
        "closure_rate": closure_rate,
        "buy_and_hold_pnl": bh_pnl,
        "lattice_vs_bh": realized_pnl - bh_pnl,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Ratio Lattice 60-Day Validation")
    parser.add_argument("--days", type=int, default=60, help="Lookback days")
    parser.add_argument("--position-size", type=float, default=0.01, help="Size per position")
    parser.add_argument("--entry-z", type=float, default=-1.5, help="Z-score entry threshold")
    parser.add_argument("--exit-z", type=float, default=1.5, help="Z-score exit threshold")
    parser.add_argument("--profit-threshold", type=float, default=1.002, help="Attractor exit multiplier")
    parser.add_argument("--rolling-window", type=int, default=200, help="Rolling stats window")
    parser.add_argument("--max-concurrent", type=int, default=5, help="Max concurrent positions")
    parser.add_argument("--max-levels", type=int, default=10, help="Max attractor levels")
    args = parser.parse_args()

    print("=" * 72)
    print("RATIO LATTICE 60-DAY VALIDATION")
    print("=" * 72)
    print()
    print(f"Focus pairs: {len(FOCUS_PAIRS)}")
    print(f"Lookback: {args.days} days, 5-min candles")
    print()

    if not HAS_CLIENT:
        print("ERROR: Cannot import CoinbaseAdvancedClient")
        return

    client = CoinbaseAdvancedClient()
    now = int(time.time())
    start = now - (args.days * 86400)

    # -- Collect unique symbols needed --------------------------------------
    unique_symbols = set()
    for a, b in FOCUS_PAIRS:
        unique_symbols.add(a)
        unique_symbols.add(b)

    price_maps = {}
    candle_counts = {}
    print("Fetching candles...")
    for sym in sorted(unique_symbols):
        product = SYMBOL_TO_PRODUCT[sym]
        print(f"  {product} ...", end=" ", flush=True)
        candles = fetch_candles(client, product, start, now)
        price_maps[sym] = build_price_map(candles)
        candle_counts[sym] = len(candles)
        print(f"{len(candles)} candles")
    print()

    # -- Analyze each pair ---------------------------------------------------
    results = {}

    for sym_a, sym_b in FOCUS_PAIRS:
        label = f"{sym_a}/{sym_b}"
        pa = price_maps.get(sym_a, {})
        pb = price_maps.get(sym_b, {})

        if not pa or not pb:
            print(f"  SKIP {label}: missing price data")
            continue

        series = build_ratio_series(pa, pb)
        if len(series) < args.rolling_window:
            print(f"  SKIP {label}: only {len(series)} aligned points (need {args.rolling_window})")
            continue

        # Analytics
        stats = compute_rolling_stats(series, window=args.rolling_window)
        z_scores = [s["z_score"] for s in stats]
        attractors = find_attractors_kde(series)
        zcr = compute_zero_crossing_rate(series, window=args.rolling_window)

        mean_ratio = sum(r["ratio"] for r in series) / len(series)
        min_ratio = min(r["ratio"] for r in series)
        max_ratio = max(r["ratio"] for r in series)

        # Run both lattice types
        attractor_shadow = run_attractor_lattice(
            series, attractors,
            position_size=args.position_size,
            profit_threshold=args.profit_threshold,
            max_concurrent=args.max_concurrent,
            max_levels=args.max_levels,
        )

        zscore_shadow = run_zscore_lattice(
            series, z_scores,
            position_size=args.position_size,
            entry_z=args.entry_z,
            exit_z=args.exit_z,
            max_concurrent=args.max_concurrent,
        )

        # Buy-and-hold individual returns
        sorted_a = sorted(pa.items(), key=lambda x: x[0])
        sorted_b = sorted(pb.items(), key=lambda x: x[0])
        ret_a = (sorted_a[-1][1] - sorted_a[0][1]) / sorted_a[0][1] if len(sorted_a) >= 2 else 0
        ret_b = (sorted_b[-1][1] - sorted_b[0][1]) / sorted_b[0][1] if len(sorted_b) >= 2 else 0

        results[label] = {
            "symbol_a": sym_a,
            "symbol_b": sym_b,
            "n_points": len(series),
            "mean_ratio": mean_ratio,
            "min_ratio": min_ratio,
            "max_ratio": max_ratio,
            "zero_crossing_rate": zcr,
            "num_attractors": len(attractors),
            "top_attractors": attractors[:5],
            "asset_a_return_pct": ret_a * 100,
            "asset_b_return_pct": ret_b * 100,
            "attractor_lattice": attractor_shadow,
            "zscore_lattice": zscore_shadow,
        }

        print(f"  {label}: {len(series)} pts, zcr={zcr:.4f}, "
              f"attractors={len(attractors)}, attr_pnl={attractor_shadow['realized_pnl']:+.6f}, "
              f"zscore_pnl={zscore_shadow['realized_pnl']:+.6f}")

    # -- Ranking -------------------------------------------------------------
    print()
    print("=" * 72)
    print("RANKING BY ATTRACTOR LATTICE PnL")
    print("=" * 72)
    print()

    ranked = sorted(
        [(label, r) for label, r in results.items()],
        key=lambda x: x[1]["attractor_lattice"]["realized_pnl"],
        reverse=True,
    )

    print(f"  {'Ratio':<15} {'Attr PnL':<12} {'ZScore PnL':<12} {'Closes':<8} {'Closure':<10} {'Max DD':<10} {'vs B&H':<10}")
    print(f"  {'-----':<15} {'--------':<12} {'----------':<12} {'------':<8} {'-------':<10} {'------':<10} {'------':<10}")

    for label, r in ranked:
        attr = r["attractor_lattice"]
        zsc = r["zscore_lattice"]
        print(f"  {label:<15} {attr['realized_pnl']:+.6f}   {zsc['realized_pnl']:+.6f}   "
              f"{attr['total_closes']:<8} {attr['closure_rate']:<10.2%} "
              f"{'—':<10} {attr['lattice_vs_bh']:+.6f}")

    # -- Compare with 30-day results -----------------------------------------
    print()
    print("=" * 72)
    print("COMPARISON: 60d vs 30d (reference 30d shadow results)")
    print("=" * 72)
    print()

    # Reference 30d results from the multi-asset run
    ref_30d = {
        "BAL/BTC": {"pnl": 0.006369840513723892, "closes": 23, "closure": 0.821},
        "BAL/ETH": {"pnl": 0.007690372388396377, "closes": 30, "closure": 0.857},
        "SUP/BTC": {"pnl": 0.0038516928626734265, "closes": 15, "closure": 0.75},
        "ETH/BTC": {"pnl_btc": 0.00443, "closes": 93, "closure": 0.989},  # from eth_btc_ratio_lattice
        "NOM/ETH": {"pnl": -0.0021274814431116234, "closes": 50, "closure": 0.909},
        "NOM/BTC": {"pnl": -0.006058904492803493, "closes": 50, "closure": 0.909},
    }

    print("  {'Ratio':<15} {'30d PnL':<12} {'60d PnL':<12} {'Delta':<12} {'30d Closes':<12} {'60d Closes':<12}")
    for label, r in ranked:
        ref = ref_30d.get(label)
        if ref:
            pnl_30d = ref.get("pnl", ref.get("pnl_btc", 0))
            closes_30d = ref.get("closes", 0)
            pnl_60d = r["attractor_lattice"]["realized_pnl"]
            closes_60d = r["attractor_lattice"]["total_closes"]
            delta = pnl_60d - pnl_30d
            print(f"  {label:<15} {pnl_30d:+.6f}     {pnl_60d:+.6f}     {delta:+.6f}     {closes_30d:<12} {closes_60d:<12}")

    # -- Save ----------------------------------------------------------------
    out_path = ROOT / "reports" / "ratio_lattice_60d_validation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    serializable = {
        "run_params": {
            "days": args.days,
            "position_size": args.position_size,
            "entry_z": args.entry_z,
            "exit_z": args.exit_z,
            "profit_threshold": args.profit_threshold,
            "rolling_window": args.rolling_window,
            "max_concurrent": args.max_concurrent,
            "max_levels": args.max_levels,
        },
        "candle_counts": candle_counts,
        "results": {label: {
            k: v for k, v in r.items()
            if k not in ("top_attractors",)  # keep attractors for readability
        } for label, r in results.items()},
        "ranking": [
            {
                "ratio": label,
                "attractor_pnl": r["attractor_lattice"]["realized_pnl"],
                "zscore_pnl": r["zscore_lattice"]["realized_pnl"],
                "closes": r["attractor_lattice"]["total_closes"],
                "closure_rate": r["attractor_lattice"]["closure_rate"],
                "lattice_vs_bh": r["attractor_lattice"]["lattice_vs_bh"],
                "asset_a_return": r["asset_a_return_pct"],
                "asset_b_return": r["asset_b_return_pct"],
            }
            for label, r in ranked
        ],
        "summary": {
            "pairs_analyzed": len(results),
            "positive_attractor_pnl": sum(
                1 for r in results.values() if r["attractor_lattice"]["realized_pnl"] > 0
            ),
            "best_pair": ranked[0][0] if ranked else None,
            "best_attractor_pnl": ranked[0][1]["attractor_lattice"]["realized_pnl"] if ranked else 0,
        },
    }

    out_path.write_text(json.dumps(serializable, indent=2, default=str))
    print()
    print(f"Full results saved to: {out_path}")


if __name__ == "__main__":
    main()
