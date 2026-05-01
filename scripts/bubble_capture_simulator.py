#!/usr/bin/env python3
"""Bubble Capture Simulator — can we capture 5-15% gross per trade by riding full bubbles?

The foundry tested short holds (5-20 bars on 5m = 25-100min) and found max MFE +2.5%.
But the hourly move distribution shows bubbling products move 7.4%+ in 60min windows.
Hypothesis: extend hold to capture the FULL bubble, then trail on reversal.

Tests:
1. Find all 5%+ moves in rolling 60-minute windows
2. Enter at ignition (first candle of the move)
3. Trail at X% from peak (test 20%, 30%, 50%, 85% retention)
4. Measure: gross capture, net after 120bps taker fees, cumulative

If we capture 5-15% gross per trade → even 2.4% fees leave 2.6-12.6% net.
4x in a day needs ~4 trades at 35% net each. Let's find out if that's real.
"""
import json
import numpy as np
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"

# 1-minute candle files
PRODUCTS_1M = {
    "RAVE-USD": ["RAVE_USD_ONE_MINUTE_7d.json", "RAVE_USD_ONE_MINUTE_30d.json"],
    "SOL-USD": ["SOL_USD_ONE_MINUTE_30d.json"],
    "BTC-USD": ["BTC_USD_ONE_MINUTE_7d.json"],
    "ETH-USD": ["ETH_USD_ONE_MINUTE_7d.json"],
    "IOTX-USD": ["IOTX_USD_ONE_MINUTE_7d.json"],
    "ALEPH-USD": ["ALEPH_USD_ONE_MINUTE_7d.json"],
    "BAL-USD": ["BAL_USD_ONE_MINUTE_7d.json"],
    "BLUR-USD": ["BLUR_USD_ONE_MINUTE_7d.json"],
}

FEE_PCT = 0.024  # 120bps taker entry + 120bps exit = 2.4%

# Trail pullback levels to test (absolute % drop from peak that triggers exit)
# These are NOT percentage of bubble — they're absolute pullback thresholds
TRAIL_PULLBACKS = [0.005, 0.01, 0.02, 0.05]  # 0.5%, 1%, 2%, 5% pullback from peak

# Minimum bubble size to detect (5% move)
MIN_BUBBLE_PCT = 0.05

# Window size in 1-minute candles
WINDOW_BARS = 60  # 60 minutes


def load_candles(filename):
    path = CACHE_DIR / filename
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    candles = data.get("candles") or data.get("data") or []
    return candles


def parse_candles(raw_candles):
    """Parse raw candle dicts into numpy arrays."""
    timestamps = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    
    for c in raw_candles:
        ts = int(c.get("time", 0))
        o = float(c.get("open", 0))
        h = float(c.get("high", 0))
        l = float(c.get("low", 0))
        cl = float(c.get("close", 0))
        v = float(c.get("volume", 0))
        if ts > 0 and o > 0 and h > 0 and l > 0 and cl > 0:
            timestamps.append(ts)
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(cl)
            volumes.append(v)
    
    return {
        "timestamps": np.array(timestamps),
        "opens": np.array(opens),
        "highs": np.array(highs),
        "lows": np.array(lows),
        "closes": np.array(closes),
        "volumes": np.array(volumes),
    }


def find_bubbles(candles, window=WINDOW_BARS, min_bubble=MIN_BUBBLE_PCT):
    """Find all bubbles: 60-bar windows where price moved min_bubble% up.
    
    Returns list of (start_idx, peak_idx, bubble_pct) tuples.
    Uses non-overlapping detection: once a bubble is found, skip ahead.
    """
    closes = candles["closes"]
    highs = candles["highs"]
    n = len(closes)
    
    bubbles = []
    i = 0
    while i < n - window:
        # Look at the window starting at i
        window_closes = closes[i:i + window]
        window_highs = highs[i:i + window]
        
        # Find the max high in the window
        max_high_idx = np.argmax(window_highs)
        max_high = window_highs[max_high_idx]
        entry_price = window_closes[0]
        
        if entry_price <= 0:
            i += 1
            continue
        
        bubble_pct = (max_high / entry_price) - 1.0
        
        if bubble_pct >= min_bubble:
            # Found a bubble
            peak_idx = i + max_high_idx
            bubbles.append((i, peak_idx, bubble_pct))
            # Skip past this bubble to avoid double-counting
            i = peak_idx + window // 2  # Skip half a window
        else:
            i += window // 4  # Slide by 15 minutes
    
    return bubbles


def simulate_trail(candles, bubbles, trail_pullback):
    """For each bubble, simulate a trailing stop exit.
    
    Entry: close at bubble start
    Trail: exit when price drops trail_pullback from peak (absolute %)
    Returns list of trade dicts.
    """
    closes = candles["closes"]
    highs = candles["highs"]
    lows = candles["lows"]
    timestamps = candles["timestamps"]
    n = len(closes)
    
    trades = []
    for start_idx, peak_idx, bubble_pct in bubbles:
        entry_price = closes[start_idx]
        if entry_price <= 0:
            continue
        
        # Track the running peak from entry onward
        running_peak = entry_price
        exit_idx = None
        exit_price = None
        
        # Walk forward from entry to find trail exit
        for j in range(start_idx, min(start_idx + WINDOW_BARS * 3, n)):
            # Update running peak
            if highs[j] > running_peak:
                running_peak = highs[j]
            
            # Check if price trails below pullback threshold
            trail_level = running_peak * (1.0 - trail_pullback)
            if lows[j] <= trail_level:
                exit_idx = j
                exit_price = trail_level  # Slippage: exit at trail level
                break
        
        # If no trail triggered, exit at end of extended window
        if exit_idx is None:
            exit_idx = min(start_idx + WINDOW_BARS * 3, n - 1)
            exit_price = closes[exit_idx]
            running_peak = max(highs[start_idx:exit_idx + 1])
        
        # Calculate results
        gross_pct = (exit_price / entry_price) - 1.0
        net_pct = gross_pct - FEE_PCT
        
        # MFE (max favorable excursion)
        mfe_pct = (running_peak / entry_price) - 1.0
        
        # MAE (max adverse excursion)
        low_after = min(lows[start_idx:exit_idx + 1]) if exit_idx > start_idx else lows[start_idx]
        mae_pct = (low_after / entry_price) - 1.0
        
        hold_minutes = exit_idx - start_idx
        
        trades.append({
            "start_idx": start_idx,
            "exit_idx": exit_idx,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "peak_price": running_peak,
            "bubble_pct": bubble_pct,
            "gross_pct": gross_pct,
            "net_pct": net_pct,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "hold_minutes": hold_minutes,
            "timestamp_start": int(timestamps[start_idx]),
            "timestamp_exit": int(timestamps[exit_idx]),
            "trail_pullback": trail_pullback,
        })
    
    return trades


def analyze_trades(trades, product_name, trail_pullback):
    """Print analysis for a set of trades."""
    if not trades:
        print(f"  {product_name} @ {trail_pullback*100:.1f}% pullback: NO TRADES")
        return
    
    nets = [t["net_pct"] for t in trades]
    gross = [t["gross_pct"] for t in trades]
    wins = sum(1 for n in nets if n > 0)
    win_rate = wins / len(trades)
    avg_net = np.mean(nets)
    avg_gross = np.mean(gross)
    cumulative_net = sum(nets)
    best_trade = max(nets)
    worst_trade = min(nets)
    avg_hold = np.mean([t["hold_minutes"] for t in trades])
    
    # Compounding: what if we compounded each trade?
    equity = 1.0
    for net in nets:
        equity *= (1.0 + net)
    compounded_return = (equity - 1.0) * 100
    
    print(f"\n  {product_name} @ {trail_pullback*100:.1f}% pullback: {len(trades)} trades")
    print(f"    Win rate: {win_rate:.1%} ({wins}/{len(trades)})")
    print(f"    Avg gross: {avg_gross:.4f}%, Avg net: {avg_net:.4f}%")
    print(f"    Best trade: {best_trade:.4f}%, Worst: {worst_trade:.4f}%")
    print(f"    Cumulative net: {cumulative_net:.2f}%")
    print(f"    Compounded: {compounded_return:.2f}%")
    print(f"    Avg hold: {avg_hold:.0f} min")
    
    # Show top 5 trades
    top5 = sorted(trades, key=lambda t: t["net_pct"], reverse=True)[:5]
    print(f"    Top 5:")
    for t in top5:
        print(f"      +{t['gross_pct']:.2f}% gross → +{t['net_pct']:.2f}% net (bubble {t['bubble_pct']:.1%}, hold {t['hold_minutes']}m)")
    
    return {
        "product": product_name,
        "trail_pullback": trail_pullback,
        "num_trades": len(trades),
        "win_rate": win_rate,
        "avg_net": avg_net,
        "cumulative_net": cumulative_net,
        "compounded_return": compounded_return,
        "best_trade": best_trade,
        "avg_hold": avg_hold,
    }


def main():
    print("=" * 80)
    print("BUBBLE CAPTURE SIMULATOR")
    print("=" * 80)
    print(f"Window: {WINDOW_BARS} bars (1m candles = {WINDOW_BARS}min)")
    print(f"Min bubble: {MIN_BUBBLE_PCT:.1%}")
    print(f"Fee: {FEE_PCT:.1%} round trip (120bps taker x2)")
    print(f"Trail pullbacks: {[f'{p*100:.1f}%' for p in TRAIL_PULLBACKS]}")
    
    all_results = []
    
    for product, files in PRODUCTS_1M.items():
        print(f"\n{'='*60}")
        print(f"PRODUCT: {product}")
        print(f"{'='*60}")
        
        # Load longest available cache
        candles_raw = None
        for f in sorted(files, key=lambda x: -len(x)):  # Prefer 30d over 7d
            candles_raw = load_candles(f)
            if candles_raw:
                print(f"  Loaded: {f} ({len(candles_raw)} candles)")
                break
        
        if not candles_raw:
            print(f"  NO CANDLE DATA for {product}")
            continue
        
        candles = parse_candles(candles_raw)
        print(f"  Parsed: {len(candles['closes'])} valid candles, "
              f"range: {candles['timestamps'][0]} → {candles['timestamps'][-1]}")
        
        # Find bubbles
        bubbles = find_bubbles(candles)
        print(f"  Found {len(bubbles)} bubbles (≥{MIN_BUBBLE_PCT:.1%} move in {WINDOW_BARS}min)")
        
        if not bubbles:
            continue
        
        # Bubble size distribution
        bubble_sizes = [b[2] for b in bubbles]
        print(f"  Bubble sizes: min={min(bubble_sizes):.1%}, "
              f"median={np.median(bubble_sizes):.1%}, "
              f"max={max(bubble_sizes):.1%}")
        
        # Simulate each trail pullback
        for pullback in TRAIL_PULLBACKS:
            trades = simulate_trail(candles, bubbles, pullback)
            result = analyze_trades(trades, product, pullback)
            if result:
                all_results.append(result)
    
    # Summary across all products
    print("\n" + "=" * 80)
    print("SUMMARY — All Products, All Trail Retentions")
    print("=" * 80)
    print(f"{'Product':<12} {'Trail':<8} {'Trades':>6} {'Win%':>6} {'AvgNet%':>10} {'CumNet%':>10} {'Compounded%':>12}")
    print("-" * 80)
    
    for r in all_results:
        print(f"{r['product']:<12} {r['trail_pullback']*100:>5.1f}%   {r['num_trades']:>6} "
              f"{r['win_rate']:>6.1%} {r['avg_net']:>10.4f} {r['cumulative_net']:>10.2f} "
              f"{r['compounded_return']:>12.2f}")
    
    # Find the best configuration
    if all_results:
        best_by_compounded = max(all_results, key=lambda r: r["compounded_return"])
        best_by_cumulative = max(all_results, key=lambda r: r["cumulative_net"])
        best_by_winrate = max(all_results, key=lambda r: r["win_rate"])
        
        print(f"\n{'='*80}")
        print(f"BEST BY COMPOUNDED: {best_by_compounded['product']} @ "
              f"{best_by_compounded['trail_pullback']*100:.1f}% pullback → "
              f"{best_by_compounded['compounded_return']:.2f}% ({best_by_compounded['num_trades']} trades)")
        print(f"BEST BY CUMULATIVE: {best_by_cumulative['product']} @ "
              f"{best_by_cumulative['trail_pullback']*100:.1f}% pullback → "
              f"{best_by_cumulative['cumulative_net']:.2f}% ({best_by_cumulative['num_trades']} trades)")
        print(f"BEST BY WIN RATE: {best_by_winrate['product']} @ "
              f"{best_by_winrate['trail_pullback']*100:.1f}% pullback → "
              f"{best_by_winrate['win_rate']:.1%} ({best_by_winrate['num_trades']} trades)")
        
        # How many trades per day at best configuration?
        trades_per_day = best_by_compounded['num_trades'] / 30  # 30 days
        print(f"\n  Trades per day (avg): {trades_per_day:.1f}")
        print(f"  Avg net per trade: {best_by_compounded['avg_net']:.4f}%")
        print(f"  If {trades_per_day:.0f} trades/day at {best_by_compounded['avg_net']:.4f}% net: "
              f"{'%.2f' % (trades_per_day * best_by_compounded['avg_net'])}% daily")
    
    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)


if __name__ == "__main__":
    main()
