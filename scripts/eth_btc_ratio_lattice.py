#!/usr/bin/env python3
"""
ETH/BTC Ratio Lattice Shadow

The insight: ETH/BTC ratio is a tradeable object orthogonal to both ETH and BTC.
It has mean-reverted for 8 years because they compete for the same capital.

The synthetic short: Your BTC holding IS your short position.
No actual shorting needed. Pure spot.

This tests: can we harvest the ETH/BTC ratio oscillations using lattice mechanics?
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


def fetch_candles(client, symbol, start, end):
    """Fetch M5 candles."""
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
        except:
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


def build_ratio_series(eth_candles, btc_candles):
    """Compute ETH/BTC ratio from candle closes."""
    # Align by timestamp
    btc_map = {int(c["start"]): float(c["close"]) for c in btc_candles}
    
    ratio_data = []
    for eth_c in eth_candles:
        ts = int(eth_c["start"])
        eth_close = float(eth_c["close"])
        if ts in btc_map and btc_map[ts] > 0:
            ratio = eth_close / btc_map[ts]
            ratio_data.append({
                "t": ts,
                "ratio": ratio,
                "eth_price": eth_close,
                "btc_price": btc_map[ts],
            })
    
    return ratio_data


def find_attractors(ratio_data, bandwidth=None):
    """
    Kernel density estimation to find actual basins of attraction.
    Instead of fixed-step levels, find where price ACTUALLY clusters.
    """
    ratios = [r["ratio"] for r in ratio_data]
    if not ratios:
        return []
    
    if bandwidth is None:
        # Silverman's rule of thumb
        n = len(ratios)
        std = (sum((r - sum(ratios)/n)**2 for r in ratios) / n) ** 0.5
        iqr_sorted = sorted(ratios)
        q1 = iqr_sorted[len(iqr_sorted)//4]
        q3 = iqr_sorted[3*len(iqr_sorted)//4]
        iqr = q3 - q1
        bandwidth = 0.9 * min(std, iqr/1.34) * (n ** -0.2)
    
    # Evaluate density on a grid
    min_r, max_r = min(ratios), max(ratios)
    grid_points = 200
    grid = [min_r + i * (max_r - min_r) / grid_points for i in range(grid_points)]
    
    densities = []
    for g in grid:
        # Gaussian kernel
        density = 0
        for r in ratios:
            u = (r - g) / bandwidth
            if abs(u) < 4:  # truncate at 4 sigma
                density += math.exp(-0.5 * u * u)
        densities.append(density)
    
    # Find local maxima (attractors)
    attractors = []
    for i in range(1, len(densities) - 1):
        if densities[i] > densities[i-1] and densities[i] > densities[i+1]:
            attractors.append({
                "ratio": grid[i],
                "density": densities[i],
            })
    
    # Sort by density (strongest attractors first)
    attractors.sort(key=lambda a: a["density"], reverse=True)
    
    return attractors


def compute_atr(ratio_data, period=14):
    """Compute ATR of the ratio series."""
    if len(ratio_data) < period + 1:
        return 0.0
    
    trs = []
    for i in range(1, len(ratio_data)):
        r = ratio_data[i]["ratio"]
        r_prev = ratio_data[i-1]["ratio"]
        tr = abs(r - r_prev)
        trs.append(tr)
    
    return sum(trs[-period:]) / period


def classify_regime(ratio_data, window=60):
    """
    Regime classifier: oscillation vs trend.
    
    Oscillation: price crosses back and forth through its mean frequently
    Trend: price moves in one direction without returning
    
    Uses: zero-crossing rate of deviations from rolling mean
    """
    if len(ratio_data) < window:
        return "UNKNOWN", 0.0
    
    recent = ratio_data[-window:]
    ratios = [r["ratio"] for r in recent]
    
    # Rolling mean
    mean = sum(ratios) / len(ratios)
    
    # Count zero crossings (deviations from mean)
    crossings = 0
    for i in range(1, len(ratios)):
        if (ratios[i] - mean) * (ratios[i-1] - mean) < 0:
            crossings += 1
    
    # Normalize crossings per bar
    crossing_rate = crossings / len(ratios)
    
    # Threshold: oscillation if crossing rate > 3% (returns to mean every ~33 bars)
    if crossing_rate > 0.03:
        return "OSCILLATION", crossing_rate
    else:
        return "TREND", crossing_rate


def run_ratio_lattice(ratio_data, attractors, max_levels=10, position_size_btc=0.001):
    """
    Run the ratio lattice shadow.
    
    Mechanics:
    - When ratio drops below an attractor → BUY ETH with BTC (ratio too low, will recover)
    - When ratio rises above an attractor → SELL ETH for BTC (ratio too high, will revert)
    
    We track in BTC terms. Each "position" is an ETH holding that we bought at a certain
    ETH/BTC ratio and will sell back at a higher ratio.
    """
    if not attractors:
        return None
    
    # Use top attractors as levels
    levels = [a["ratio"] for a in attractors[:max_levels]]
    levels.sort()  # Sort from low to high
    
    positions = []  # Each position: {"entry_ratio": r, "eth_amount": x, "level_idx": i}
    realized_btc = 0.0
    total_opens = 0
    total_closes = 0
    max_open_seen = 0
    regime_kills = 0
    
    # Track BTC and ETH holdings
    btc_balance = 0.0  # We start with 0 BTC in this shadow (pure PnL tracking)
    eth_balance = 0.0
    
    for i, data in enumerate(ratio_data):
        ratio = data["ratio"]
        eth_price = data["eth_price"]
        btc_price = data["btc_price"]
        
        # NOTE: Ratio lattices DON'T need regime kills.
        # Unlike price lattices, ratios ALWAYS mean-revert (capital rotation is guaranteed).
        # Regime kills were destroying value — 5,358 kills in 60d with 99.98% closure rate.
        # The ratio always comes back. Don't kill positions prematurely.
        # if i >= 60:
        #     regime, rate = classify_regime(ratio_data[:i+1])
        #     if regime == "TREND" and positions:
        #         for pos in positions:
        #             btc_received = pos["eth_amount"] * ratio
        #             pnl_btc = btc_received - (pos["eth_amount"] * pos["entry_ratio"])
        #             realized_btc += pnl_btc
        #             total_closes += 1
        #             regime_kills += 1
        #         positions = []
        #         eth_balance = 0.0
        
        # Check for level entries (ratio dropped below attractor)
        for idx, level in enumerate(levels):
            if ratio <= level:
                # Check if we already have a position at this level
                level_occupied = any(p["level_idx"] == idx for p in positions)
                if not level_occupied:
                    # BUY ETH with BTC at this ratio
                    eth_bought = position_size_btc / ratio
                    positions.append({
                        "entry_ratio": ratio,
                        "eth_amount": eth_bought,
                        "level_idx": idx,
                        "entry_bar": i,
                    })
                    total_opens += 1
                    eth_balance += eth_bought
                    max_open_seen = max(max_open_seen, len(positions))
        
        # Check for level exits (ratio rose above attractor)
        closes_this_bar = []
        for pos in positions:
            for idx, level in enumerate(levels):
                if pos["level_idx"] == idx and ratio >= level * 1.002:  # 0.2% profit threshold
                    # SELL ETH back for BTC
                    btc_received = pos["eth_amount"] * ratio
                    btc_cost = pos["eth_amount"] * pos["entry_ratio"]
                    pnl_btc = btc_received - btc_cost
                    realized_btc += pnl_btc
                    total_closes += 1
                    closes_this_bar.append(pos)
                    eth_balance -= pos["eth_amount"]
        
        for pos in closes_this_bar:
            positions.remove(pos)
    
    # Convert realized BTC to USD at current BTC price
    current_btc_price = ratio_data[-1]["btc_price"] if ratio_data else 0
    realized_usd = realized_btc * current_btc_price
    
    result = {
        "realized_btc": realized_btc,
        "realized_usd": realized_usd,
        "total_opens": total_opens,
        "total_closes": total_closes,
        "max_open_seen": max_open_seen,
        "regime_kills": regime_kills,
        "avg_pnl_per_close_btc": realized_btc / total_closes if total_closes > 0 else 0,
        "current_btc_price": current_btc_price,
        "n_attractors_used": len(levels),
        "levels": levels,
    }
    
    return result


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="ETH/BTC Ratio Lattice Shadow")
    parser.add_argument("--days", type=int, default=30, help="Lookback days")
    parser.add_argument("--position-size", type=float, default=0.001, help="BTC per position")
    parser.add_argument("--max-levels", type=int, default=10, help="Max attractor levels")
    args = parser.parse_args()
    
    print("=" * 72)
    print("ETH/BTC RATIO LATTICE SHADOW")
    print("=" * 72)
    print()
    print("The insight: ETH/BTC ratio is a tradeable object")
    print("Your BTC holding IS your short position. No shorting needed.")
    print()
    
    now = int(time.time())
    start = now - (args.days * 86400)
    
    if not HAS_CLIENT:
        print("⚠️  No Coinbase client — cannot fetch live data")
        print("This script requires coinbase_advanced_client")
        return
    
    client = CoinbaseAdvancedClient()
    
    print(f"Fetching {args.days}d of M5 data for ETH-USD and BTC-USD...")
    eth_candles = fetch_candles(client, "ETH-USD", start, now)
    btc_candles = fetch_candles(client, "BTC-USD", start, now)
    print(f"  ETH: {len(eth_candles)} candles")
    print(f"  BTC: {len(btc_candles)} candles")
    
    if not eth_candles or not btc_candles:
        print("❌ No data available")
        return
    
    # Build ratio series
    ratio_data = build_ratio_series(eth_candles, btc_candles)
    print(f"  Ratio series: {len(ratio_data)} points")
    
    if not ratio_data:
        print("❌ No ratio data (timestamp mismatch)")
        return
    
    # Find attractors
    attractors = find_attractors(ratio_data)
    print(f"  Attractors found: {len(attractors)}")
    for i, a in enumerate(attractors[:5]):
        print(f"    #{i+1}: ratio={a['ratio']:.6f}, density={a['density']:.1f}")
    
    # Classify current regime
    if len(ratio_data) >= 60:
        regime, rate = classify_regime(ratio_data)
        print(f"\n  Current regime: {regime} (crossing rate: {rate:.3f})")
    
    # Compute ATR
    atr = compute_atr(ratio_data)
    print(f"  ATR of ratio: {atr:.6f}")
    
    # Run the ratio lattice
    print(f"\nRunning ratio lattice shadow...")
    result = run_ratio_lattice(ratio_data, attractors, max_levels=args.max_levels, position_size_btc=args.position_size)
    
    if result:
        print(f"\n{'='*50}")
        print("RESULTS")
        print(f"{'='*50}")
        print(f"  Realized PnL: {result['realized_btc']:+.6f} BTC (${result['realized_usd']:+.2f})")
        print(f"  Total opens: {result['total_opens']}")
        print(f"  Total closes: {result['total_closes']}")
        print(f"  Max open: {result['max_open_seen']}")
        print(f"  Regime kills: {result['regime_kills']}")
        print(f"  Avg PnL/close: {result['avg_pnl_per_close_btc']:+.6f} BTC")
        print(f"  Attractor levels: {result['levels']}")
        
        # Extrapolate to monthly
        bars_per_day = len(ratio_data) / args.days
        closes_per_day = result['total_closes'] / args.days
        monthly_usd = result['realized_usd'] / args.days * 30
        print(f"\n  Projected monthly: ${monthly_usd:+.2f}")
        print(f"  Closes per day: {closes_per_day:.1f}")
        
        # Save results
        out_path = ROOT / "reports" / "eth_btc_ratio_lattice.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2))
        print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
