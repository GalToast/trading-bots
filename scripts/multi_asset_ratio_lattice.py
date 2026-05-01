#!/usr/bin/env python3
"""
Multi-Asset Ratio Lattice

Extends the ETH/BTC ratio concept to ALL pairwise ratios across:
  NOM, GHST, SUP, RAVE, BAL, A8, CFG, IOTX, TRU  (+ BTC, ETH as benchmarks)

Key insight: some coin pairs move together tightly (high correlation),
making their ratio a clean oscillator. Others don't, making their ratio noisy.
We're looking for the clean oscillators -- the relationships where the
geometry is harvestable.

Output: reports/multi_asset_ratio_lattice_results.json
"""
import json
import math
import time
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from coinbase_advanced_client import CoinbaseAdvancedClient
    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False

# ---------------------------------------------------------------------------
# Asset universe
# ---------------------------------------------------------------------------
ALT_SYMBOLS = ["NOM", "GHST", "SUP", "RAVE", "BAL", "A8", "CFG", "IOTX", "TRU"]
BENCHMARK_SYMBOLS = ["BTC", "ETH"]
ALL_SYMBOLS = ALT_SYMBOLS + BENCHMARK_SYMBOLS

# Named ratio groups (for reporting)
RATIO_GROUPS = {
    "fibonacci_trio": ["NOM/GHST", "NOM/SUP", "GHST/SUP"],
    "supertrend_group": ["RAVE/BAL", "RAVE/IOTX", "BAL/IOTX"],
    "momentum_pair": ["A8/CFG"],
    "alt_vs_btc": [f"{sym}/BTC" for sym in ALT_SYMBOLS],
    "alt_vs_eth": [f"{sym}/ETH" for sym in ALT_SYMBOLS],
}

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_candles(client, symbol, start, end):
    """Fetch M5 candles from Coinbase, chunked into 30-day windows."""
    chunk_sec = 300 * 5 * 60  # 30 days in seconds
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
    """Map timestamp -> close price from candle list."""
    return {int(c["start"]): float(c["close"]) for c in candles}


def build_ratio_series(price_a, price_b):
    """
    Compute ratio = price_a / price_b for aligned timestamps.
    Returns list of {t, ratio, price_a, price_b}.
    """
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
# Ratio analytics
# ---------------------------------------------------------------------------

def compute_rolling_stats(ratio_series, window=200):
    """Compute rolling mean, std, and z-score over the ratio series."""
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


def compute_atr_series(ratio_series, period=14):
    """Compute rolling ATR of the ratio (volatility of the relationship)."""
    ratios = [r["ratio"] for r in ratio_series]
    n = len(ratios)
    if n < 2:
        return []

    trs = [0.0] + [abs(ratios[i] - ratios[i - 1]) for i in range(1, n)]

    atr_series = []
    for i in range(n):
        if i < period:
            atr_series.append(None)
        else:
            atr_series.append(sum(trs[i - period + 1: i + 1]) / period)

    return atr_series


def compute_zero_crossing_rate(ratio_series, window=200):
    """
    Zero-crossing rate: how often the ratio deviates from and returns
    to its rolling mean (oscillation frequency).
    """
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


def find_attractors_kde(ratio_series, bandwidth=None):
    """
    Kernel density estimation to find attractor basins (KDE peaks).
    Returns list of {ratio, density} sorted by density descending.
    """
    ratios = [r["ratio"] for r in ratio_series]
    n = len(ratios)
    if n < 10:
        return []

    if bandwidth is None:
        # Silverman's rule of thumb
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

    # Find local maxima
    attractors = []
    for i in range(1, len(densities) - 1):
        if densities[i] > densities[i - 1] and densities[i] > densities[i + 1]:
            attractors.append({"ratio": grid[i], "density": densities[i]})

    attractors.sort(key=lambda a: a["density"], reverse=True)
    return attractors


# ---------------------------------------------------------------------------
# Tradeability scoring
# ---------------------------------------------------------------------------

def compute_tradeability_score(zero_crossing_rate, atr_series, ratio_series):
    """
    Score each ratio for lattice tradeability.

    - High zero-crossing rate = good (frequent oscillation)
    - Low ATR relative to mean = good (tight relationship)
    - Multiple attractors = good (rich structure to harvest)

    Score = (zero_crossing_rate * 0.4) + (1/min(atr/mean, 0.1) * 0.3) + (num_attractors * 0.3)
    """
    ratios = [r["ratio"] for r in ratio_series]
    mean_ratio = sum(ratios) / len(ratios) if ratios else 1.0

    # Final ATR (most recent)
    valid_atrs = [a for a in atr_series if a is not None]
    final_atr = valid_atrs[-1] if valid_atrs else 0.0

    atr_ratio = final_atr / mean_ratio if mean_ratio > 0 else 0.0
    atr_component = 1.0 / min(max(atr_ratio, 1e-6), 0.1)

    num_attractors = len(find_attractors_kde(ratio_series))

    score = (zero_crossing_rate * 0.4) + (atr_component * 0.3) + (num_attractors * 0.3)

    return {
        "score": score,
        "zero_crossing_rate": zero_crossing_rate,
        "atr_mean_ratio": atr_ratio,
        "atr_component": atr_component,
        "num_attractors": num_attractors,
    }


# ---------------------------------------------------------------------------
# Lattice shadow backtest
# ---------------------------------------------------------------------------

def run_lattice_shadow(ratio_series, z_scores, position_size=0.01,
                       entry_z=-1.5, exit_z=1.5, max_concurrent=5):
    """
    Simple mean-reversion lattice shadow on z-scores.

    - Open long when z < entry_z (ratio is cheap)
    - Close when z > exit_z (ratio recovered)

    Tracks PnL, max drawdown, closure rate.
    """
    n = len(ratio_series)
    if n == 0:
        return None

    positions = []  # {entry_ratio, entry_bar, size}
    realized_pnl = 0.0
    total_opens = 0
    total_closes = 0
    max_drawdown = 0.0
    peak_pnl = 0.0
    cumulative_pnl = 0.0
    pnl_curve = []

    for i in range(n):
        ratio = ratio_series[i]["ratio"]
        z = z_scores[i]

        if z is None:
            continue

        # Open: z below threshold and room for more positions
        if z < entry_z and len(positions) < max_concurrent:
            positions.append({
                "entry_ratio": ratio,
                "entry_bar": i,
                "size": position_size,
            })
            total_opens += 1

        # Close: z above exit threshold
        closes_this_bar = []
        for pos in positions:
            if z > exit_z:
                pnl = pos["size"] * (ratio - pos["entry_ratio"]) / pos["entry_ratio"]
                realized_pnl += pnl
                cumulative_pnl += pnl
                total_closes += 1
                closes_this_bar.append(pos)

        for pos in closes_this_bar:
            positions.remove(pos)

        # Track drawdown
        peak_pnl = max(peak_pnl, cumulative_pnl)
        dd = peak_pnl - cumulative_pnl
        max_drawdown = max(max_drawdown, dd)
        pnl_curve.append(cumulative_pnl)

    closure_rate = total_closes / total_opens if total_opens > 0 else 0.0

    # Buy-and-hold PnL for comparison (hold asset_a, short asset_b via ratio)
    bh_pnl = 0.0
    if n >= 2:
        bh_pnl = (ratio_series[-1]["ratio"] - ratio_series[0]["ratio"]) / ratio_series[0]["ratio"]

    return {
        "realized_pnl": realized_pnl,
        "total_opens": total_opens,
        "total_closes": total_closes,
        "closure_rate": closure_rate,
        "max_drawdown": max_drawdown,
        "final_cumulative_pnl": cumulative_pnl,
        "buy_and_hold_pnl": bh_pnl,
        "pnl_curve_sample": pnl_curve[::max(1, len(pnl_curve) // 100)],  # subsample for output
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Multi-Asset Ratio Lattice")
    parser.add_argument("--days", type=int, default=30, help="Lookback days")
    parser.add_argument("--position-size", type=float, default=0.01, help="Size per position")
    parser.add_argument("--entry-z", type=float, default=-1.5, help="Z-score entry threshold")
    parser.add_argument("--exit-z", type=float, default=1.5, help="Z-score exit threshold")
    parser.add_argument("--rolling-window", type=int, default=200, help="Rolling stats window")
    parser.add_argument("--max-concurrent", type=int, default=5, help="Max concurrent positions per ratio")
    parser.add_argument("--top-n", type=int, default=10, help="Show top N ratios")
    parser.add_argument("--shadow-count", type=int, default=5, help="Run lattice shadow on top N ratios")
    args = parser.parse_args()

    print("=" * 72)
    print("MULTI-ASSET RATIO LATTICE")
    print("=" * 72)
    print()
    print(f"Universe: {', '.join(ALL_SYMBOLS)}")
    print(f"Lookback: {args.days} days, 5-min candles")
    print(f"Rolling window: {args.rolling_window}")
    print()

    # -- Fetch data ----------------------------------------------------------
    if not HAS_CLIENT:
        print("ERROR: Cannot import CoinbaseAdvancedClient")
        return

    client = CoinbaseAdvancedClient()

    now = int(time.time())
    start = now - (args.days * 86400)

    symbol_to_product = {
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

    price_maps = {}
    candle_counts = {}
    print("Fetching candles...")
    for sym in ALL_SYMBOLS:
        product = symbol_to_product[sym]
        print(f"  {product} ...", end=" ", flush=True)
        candles = fetch_candles(client, product, start, now)
        price_maps[sym] = build_price_map(candles)
        candle_counts[sym] = len(candles)
        print(f"{len(candles)} candles")

    print()

    # -- Build all pairwise ratios -------------------------------------------
    print("Building pairwise ratios...")
    ratio_results = {}  # "A/B" -> {series, stats, atr, zcr, attractors, score}

    # Define explicit pairs we want to analyze
    explicit_pairs = []

    # Fibonacci trio
    explicit_pairs.extend([("NOM", "GHST"), ("NOM", "SUP"), ("GHST", "SUP")])
    # Supertrend group
    explicit_pairs.extend([("RAVE", "BAL"), ("RAVE", "IOTX"), ("BAL", "IOTX")])
    # Momentum pair
    explicit_pairs.append(("A8", "CFG"))
    # Alt vs BTC
    for alt in ALT_SYMBOLS:
        explicit_pairs.append((alt, "BTC"))
    # Alt vs ETH
    for alt in ALT_SYMBOLS:
        explicit_pairs.append((alt, "ETH"))

    for sym_a, sym_b in explicit_pairs:
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

        # Rolling stats
        stats = compute_rolling_stats(series, window=args.rolling_window)
        z_scores = [s["z_score"] for s in stats]

        # ATR
        atr_series = compute_atr_series(series)

        # Zero-crossing rate
        zcr = compute_zero_crossing_rate(series, window=args.rolling_window)

        # Attractors
        attractors = find_attractors_kde(series)

        # Tradeability score
        score_info = compute_tradeability_score(zcr, atr_series, series)

        valid_atrs = [a for a in atr_series if a is not None]
        final_atr = valid_atrs[-1] if valid_atrs else 0.0

        ratio_results[label] = {
            "symbol_a": sym_a,
            "symbol_b": sym_b,
            "n_points": len(series),
            "zero_crossing_rate": zcr,
            "atr": final_atr,
            "mean_ratio": sum(r["ratio"] for r in series) / len(series),
            "min_ratio": min(r["ratio"] for r in series),
            "max_ratio": max(r["ratio"] for r in series),
            "num_attractors": len(attractors),
            "attractors": attractors[:5],  # top 5 only
            "score": score_info,
            "z_scores": z_scores,
            "ratio_series": series,
            "group": None,  # filled below
        }

        print(f"  {label}: {len(series)} pts, zcr={zcr:.4f}, atr={final_atr:.6f}, "
              f"attractors={len(attractors)}, score={score_info['score']:.4f}")

    # Tag groups
    for group_name, group_labels in RATIO_GROUPS.items():
        for label in group_labels:
            if label in ratio_results:
                ratio_results[label]["group"] = group_name

    # -- Rank by tradeability score ------------------------------------------
    ranked = sorted(
        [(label, info) for label, info in ratio_results.items()],
        key=lambda x: x[1]["score"]["score"],
        reverse=True,
    )

    print()
    print("=" * 72)
    print(f"TOP {args.top_n} MOST TRADEABLE RATIOS (by lattice score)")
    print("=" * 72)
    print()
    print(f"  {'Rank':<5} {'Ratio':<15} {'Score':<10} {'ZCR':<8} {'ATR/Mean':<10} {'Attractors':<10} {'Group':<15}")
    print(f"  {'----':<5} {'-----':<15} {'-----':<10} {'---':<8} {'-------':<10} {'----------':<10} {'-----':<15}")

    top_n_ratios = ranked[:args.top_n]
    for rank, (label, info) in enumerate(top_n_ratios, 1):
        s = info["score"]
        group = info.get("group") or ""
        print(f"  {rank:<5} {label:<15} {s['score']:<10.4f} {s['zero_crossing_rate']:<8.4f} "
              f"{s['atr_mean_ratio']:<10.4f} {s['num_attractors']:<10} {group:<15}")

    # -- Run lattice shadow on top ratios ------------------------------------
    print()
    print("=" * 72)
    print(f"LATTICE SHADOW: TOP {args.shadow_count} RATIOS")
    print("=" * 72)
    print()

    shadow_results = {}
    for rank, (label, info) in enumerate(ranked[:args.shadow_count], 1):
        series = info["ratio_series"]
        z_scores = info["z_scores"]

        shadow = run_lattice_shadow(
            series, z_scores,
            position_size=args.position_size,
            entry_z=args.entry_z,
            exit_z=args.exit_z,
            max_concurrent=args.max_concurrent,
        )

        if shadow is None:
            print(f"  {label}: no shadow result (insufficient data)")
            continue

        shadow_results[label] = shadow

        print(f"  [{rank}] {label}")
        print(f"      Lattice PnL:    {shadow['realized_pnl']:+.6f}")
        print(f"      Buy-and-hold:   {shadow['buy_and_hold_pnl']:+.6f}")
        print(f"      Opens/Closes:   {shadow['total_opens']} / {shadow['total_closes']}")
        print(f"      Closure rate:   {shadow['closure_rate']:.2%}")
        print(f"      Max drawdown:   {shadow['max_drawdown']:.6f}")
        print()

    # -- Comparison: ratio lattice vs individual assets ----------------------
    print("=" * 72)
    print("COMPARISON: RATIO LATTICE PnL vs INDIVIDUAL ASSET PnL")
    print("=" * 72)
    print()

    # Compute individual asset returns
    asset_returns = {}
    for sym in ALL_SYMBOLS:
        pm = price_maps.get(sym, {})
        if len(pm) >= 2:
            sorted_prices = sorted(pm.items(), key=lambda x: x[0])
            first_price = sorted_prices[0][1]
            last_price = sorted_prices[-1][1]
            ret = (last_price - first_price) / first_price if first_price > 0 else 0.0
            asset_returns[sym] = {
                "first_price": first_price,
                "last_price": last_price,
                "return_pct": ret * 100,
            }

    print("  Individual asset returns:")
    for sym in ALL_SYMBOLS:
        if sym in asset_returns:
            ar = asset_returns[sym]
            print(f"    {sym:<8}: {ar['first_price']:>12.6f} -> {ar['last_price']:>12.6f}  ({ar['return_pct']:+.2f}%)")

    print()
    print("  Ratio lattice shadow PnL vs best individual asset:")
    for label, shadow in shadow_results.items():
        info = ratio_results[label]
        sym_a = info["symbol_a"]
        sym_b = info["symbol_b"]
        ret_a = asset_returns.get(sym_a, {}).get("return_pct", 0.0)
        ret_b = asset_returns.get(sym_b, {}).get("return_pct", 0.0)
        lattice_pnl_pct = shadow["realized_pnl"] * 100

        print(f"    {label:<15}: lattice {lattice_pnl_pct:+.4f}%  "
              f"vs  {sym_a} {ret_a:+.2f}%  {sym_b} {ret_b:+.2f}%")

    # -- Recommendation ------------------------------------------------------
    print()
    print("=" * 72)
    print("RECOMMENDATION: RATIOS WORTH TRADING LIVE")
    print("=" * 72)
    print()

    live_candidates = []
    for label, info in top_n_ratios[:5]:
        shadow = shadow_results.get(label)
        if shadow is None:
            continue

        s = info["score"]
        # Criteria for live trading:
        # - Score > 1.0 (decent tradeability)
        # - Closure rate > 30%
        # - Max drawdown < 0.1
        # - More closes than opens * 0.3
        is_candidate = (
            s["score"] > 1.0
            and shadow["closure_rate"] > 0.30
            and shadow["max_drawdown"] < 0.10
            and shadow["total_closes"] >= max(3, shadow["total_opens"] * 0.3)
        )

        if is_candidate:
            live_candidates.append({
                "ratio": label,
                "score": s["score"],
                "closure_rate": shadow["closure_rate"],
                "max_drawdown": shadow["max_drawdown"],
                "total_closes": shadow["total_closes"],
                "lattice_pnl": shadow["realized_pnl"],
                "group": info.get("group"),
            })

    if live_candidates:
        print("  LIVE TRADING CANDIDATES:")
        for c in live_candidates:
            print(f"    {c['ratio']:<15}: score={c['score']:.4f}, "
                  f"closures={c['total_closes']}, closure_rate={c['closure_rate']:.2%}, "
                  f"max_dd={c['max_drawdown']:.4f}, pnl={c['lattice_pnl']:+.6f}")
    else:
        print("  No ratios meet the live trading threshold.")
        print("  Consider relaxing thresholds or extending lookback period.")

    # -- Save results --------------------------------------------------------
    out_path = ROOT / "reports" / "multi_asset_ratio_lattice_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Build serializable output (strip large arrays)
    serializable = {
        "run_params": {
            "days": args.days,
            "position_size": args.position_size,
            "entry_z": args.entry_z,
            "exit_z": args.exit_z,
            "rolling_window": args.rolling_window,
            "max_concurrent": args.max_concurrent,
        },
        "candle_counts": candle_counts,
        "top_10_ratios": [],
        "shadow_results": {},
        "asset_returns": asset_returns,
        "live_candidates": live_candidates,
        "summary": {
            "total_ratios_analyzed": len(ratio_results),
            "total_ratios_valid": len(ranked),
            "shadow_count": len(shadow_results),
            "live_candidate_count": len(live_candidates),
        },
    }

    for rank, (label, info) in enumerate(top_n_ratios, 1):
        s = info["score"]
        entry = {
            "rank": rank,
            "ratio": label,
            "group": info.get("group"),
            "n_points": info["n_points"],
            "mean_ratio": info["mean_ratio"],
            "min_ratio": info["min_ratio"],
            "max_ratio": info["max_ratio"],
            "score": s["score"],
            "zero_crossing_rate": s["zero_crossing_rate"],
            "atr_mean_ratio": s["atr_mean_ratio"],
            "num_attractors": s["num_attractors"],
            "attractors": info["attractors"],
        }
        serializable["top_10_ratios"].append(entry)

    for label, shadow in shadow_results.items():
        serializable["shadow_results"][label] = shadow

    out_path.write_text(json.dumps(serializable, indent=2, default=str))
    print()
    print(f"Full results saved to: {out_path}")


if __name__ == "__main__":
    main()
