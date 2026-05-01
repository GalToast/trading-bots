#!/usr/bin/env python3
"""Cross-Product Correlation & Spread Capture Simulator.

Hypothesis: Two correlated products move together, but one moves MORE.
If we go long the stronger + short the weaker during bubble hours,
we capture the SPREAD between them. Each leg pays 2.4% fees, but the
spread differential could be 5-10%+.

Think of it as: long RAVE-USD + short SOL-USD.
If RAVE moves +8% and SOL moves +3% in the same hour,
our net is +5% minus 4.8% fees = +0.2%.

The key insight: we DON'T need to predict direction. We just need
to predict which product will OUTPERFORM. Even if both go down,
if RAVE goes -5% and SOL goes -8%, our spread is +3%.

This turns a directional problem into a RELATIVE VALUE problem.
"""
import json
import numpy as np
from pathlib import Path
from itertools import combinations

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"

# Products with 1-minute data (30d preferred for statistical significance)
PRODUCTS = {
    "RAVE": "RAVE_USD_ONE_MINUTE_30d.json",
    "SOL": "SOL_USD_ONE_MINUTE_30d.json",
}

# Also load 7d products for shorter analysis
PRODUCTS_7D = {
    "BTC": "BTC_USD_ONE_MINUTE_7d.json",
    "ETH": "ETH_USD_ONE_MINUTE_7d.json",
    "IOTX": "IOTX_USD_ONE_MINUTE_7d.json",
    "ALEPH": "ALEPH_USD_ONE_MINUTE_7d.json",
    "BAL": "BAL_USD_ONE_MINUTE_7d.json",
    "BLUR": "BLUR_USD_ONE_MINUTE_7d.json",
}

FEE_PCT = 0.024  # 2.4% round trip per leg
SPREAD_FEE = FEE_PCT * 2  # 4.8% total (long + short)

# Hourly window for analysis
WINDOW_BARS = 60  # 1-hour windows on 1m candles


def load_candles(filename):
    path = CACHE_DIR / filename
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("candles") or data.get("data") or []


def parse_candles(raw):
    closes = []
    timestamps = []
    for c in raw:
        ts = int(c.get("time", 0))
        cl = float(c.get("close", 0))
        if ts > 0 and cl > 0:
            timestamps.append(ts)
            closes.append(cl)
    return np.array(timestamps), np.array(closes)


def align_timestamps(ts1, closes1, ts2, closes2):
    """Align two time series to common timestamps."""
    ts_set1 = set(ts1)
    ts_set2 = set(ts2)
    common = sorted(ts_set1 & ts_set2)
    
    if len(common) < WINDOW_BARS * 2:
        return None, None, None
    
    idx1 = {t: i for i, t in enumerate(ts1)}
    idx2 = {t: i for i, t in enumerate(ts2)}
    
    c1 = np.array([closes1[idx1[t]] for t in common])
    c2 = np.array([closes2[idx2[t]] for t in common])
    
    return np.array(common), c1, c2


def compute_hourly_returns(closes, timestamps):
    """Compute returns for each non-overlapping hourly window."""
    n = len(closes)
    hourly_returns = []
    hourly_times = []
    
    for i in range(0, n - WINDOW_BARS + 1, WINDOW_BARS):
        start_close = closes[i]
        end_close = closes[i + WINDOW_BARS - 1]
        ret = (end_close / start_close) - 1.0
        hourly_returns.append(ret)
        hourly_times.append(timestamps[i])
    
    return np.array(hourly_returns), np.array(hourly_times)


def analyze_pair(name1, name2, timestamps, closes1, closes2):
    """Analyze a pair for spread capture potential."""
    ret1, times = compute_hourly_returns(closes1, timestamps)
    ret2, _ = compute_hourly_returns(closes2, timestamps)
    
    if len(ret1) < 10:
        return None
    
    # Correlation of hourly returns
    corr = np.corrcoef(ret1, ret2)[0, 1]
    
    # Spread: ret1 - ret2 (if we go long 1, short 2)
    spread = ret1 - ret2
    reverse_spread = ret2 - ret1  # long 2, short 1
    
    # Statistics
    spread_mean = np.mean(spread)
    spread_std = np.std(spread)
    reverse_mean = np.mean(reverse_spread)
    reverse_std = np.std(reverse_spread)
    
    # Win rate for spread > fees
    spread_win_rate = np.mean(spread > SPREAD_FEE)
    reverse_win_rate = np.mean(reverse_spread > SPREAD_FEE)
    
    # Best spread trades
    best_spreads = sorted(spread, reverse=True)[:10]
    best_reverse = sorted(reverse_spread, reverse=True)[:10]
    
    # How many hours have spread > 4.8%?
    profitable_spreads = np.sum(np.abs(spread) > SPREAD_FEE)
    profitable_reverse = np.sum(np.abs(reverse_spread) > SPREAD_FEE)
    
    print(f"\n{'='*60}")
    print(f"PAIR: {name1} vs {name2}")
    print(f"{'='*60}")
    print(f"  Hours analyzed: {len(ret1)}")
    print(f"  Correlation: {corr:.4f}")
    print(f"  {name1} avg hourly return: {np.mean(ret1)*100:.4f}%")
    print(f"  {name2} avg hourly return: {np.mean(ret2)*100:.4f}%")
    print(f"")
    print(f"  Long {name1} + Short {name2}:")
    print(f"    Spread mean: {spread_mean*100:.4f}%, std: {spread_std*100:.4f}%")
    print(f"    Win rate (> {SPREAD_FEE*100:.1f}%): {spread_win_rate:.1%}")
    print(f"    Profitable hours: {profitable_spreads}/{len(spread)}")
    print(f"    Best 3 spreads: {[f'{s*100:.2f}%' for s in best_spreads[:3]]}")
    print(f"")
    print(f"  Long {name2} + Short {name1}:")
    print(f"    Spread mean: {reverse_mean*100:.4f}%, std: {reverse_std*100:.4f}%")
    print(f"    Win rate (> {SPREAD_FEE*100:.1f}%): {reverse_win_rate:.1%}")
    print(f"    Profitable hours: {profitable_reverse}/{len(reverse_spread)}")
    print(f"    Best 3 spreads: {[f'{s*100:.2f}%' for s in best_reverse[:3]]}")
    
    # Simulate: always go long the product with higher hourly return
    # This is "perfect hindsight" — what's the theoretical max?
    perfect_spread = np.maximum(spread, reverse_spread)
    perfect_wins = np.sum(perfect_spread > SPREAD_FEE)
    perfect_cumulative = np.sum(perfect_spread[perfect_spread > SPREAD_FEE]) - np.sum(perfect_spread[perfect_spread <= SPREAD_FEE])
    
    print(f"\n  PERFECT HINDSIGHT (always pick winning leg):")
    print(f"    Profitable hours: {perfect_wins}/{len(perfect_spread)}")
    print(f"    Cumulative spread net: {perfect_cumulative*100:.2f}%")
    print(f"    Avg winning spread: {np.mean(perfect_spread[perfect_spread > SPREAD_FEE])*100:.4f}%")
    
    # Realistic: only trade when spread exceeds threshold AND we can predict direction
    # Use a simple momentum signal: if product 1 led last hour, go long 1
    # (This is a toy signal — real signal would need more features)
    signal_wins = 0
    signal_cumulative = 0.0
    for i in range(1, len(spread)):
        # Signal: go with last hour's winner
        if spread[i-1] > 0:
            # Predict spread continues positive
            if spread[i] > SPREAD_FEE:
                signal_wins += 1
                signal_cumulative += spread[i] - SPREAD_FEE
            else:
                signal_cumulative += spread[i] - SPREAD_FEE
        else:
            # Predict reverse spread
            if reverse_spread[i] > SPREAD_FEE:
                signal_wins += 1
                signal_cumulative += reverse_spread[i] - SPREAD_FEE
            else:
                signal_cumulative += reverse_spread[i] - SPREAD_FEE
    
    signal_trades = len(spread) - 1
    print(f"\n  TOY SIGNAL (follow last hour's winner):")
    print(f"    Trades: {signal_trades}, Wins: {signal_wins}")
    print(f"    Cumulative net: {signal_cumulative*100:.2f}%")
    print(f"    Avg per trade: {(signal_cumulative/signal_trades)*100:.4f}%")
    
    return {
        "pair": f"{name1} vs {name2}",
        "correlation": corr,
        "spread_mean": spread_mean,
        "spread_std": spread_std,
        "spread_win_rate": spread_win_rate,
        "profitable_hours": profitable_spreads,
        "perfect_wins": perfect_wins,
        "perfect_cumulative": perfect_cumulative,
        "signal_cumulative": signal_cumulative,
        "signal_wins": signal_wins,
        "total_hours": len(ret1),
    }


def main():
    print("=" * 80)
    print("CROSS-PRODUCT CORRELATION & SPREAD CAPTURE")
    print("=" * 80)
    print(f"Fee per leg: {FEE_PCT:.1%} (total spread fee: {SPREAD_FEE:.1%})")
    print(f"Window: {WINDOW_BARS} bars (1m = {WINDOW_BARS}min)")
    
    # Load all products
    loaded = {}
    for name, file in {**PRODUCTS, **PRODUCTS_7D}.items():
        raw = load_candles(file)
        if raw:
            ts, cl = parse_candles(raw)
            loaded[name] = (ts, cl)
            print(f"Loaded {name}: {len(cl)} candles")
    
    # Align and analyze pairs
    results = []
    pairs_analyzed = 0
    
    for (name1, (ts1, cl1)), (name2, (ts2, cl2)) in combinations(loaded.items(), 2):
        common_ts, c1, c2 = align_timestamps(ts1, cl1, ts2, cl2)
        if common_ts is None:
            print(f"\n  {name1} vs {name2}: insufficient common timestamps")
            continue
        
        pairs_analyzed += 1
        result = analyze_pair(name1, name2, common_ts, c1, c2)
        if result:
            results.append(result)
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY — All Pairs")
    print(f"{'='*80}")
    print(f"{'Pair':<25} {'Corr':>6} {'SprMean%':>10} {'SprStd%':>10} {'Win%':>6} {'PerfCum%':>10} {'SignalCum%':>12}")
    print("-" * 80)
    
    for r in results:
        print(f"{r['pair']:<25} {r['correlation']:>6.4f} {r['spread_mean']*100:>10.4f} "
              f"{r['spread_std']*100:>10.4f} {r['spread_win_rate']:>6.1%} "
              f"{r['perfect_cumulative']*100:>10.2f} {r['signal_cumulative']*100:>12.2f}")
    
    if results:
        best_perfect = max(results, key=lambda r: r["perfect_cumulative"])
        best_signal = max(results, key=lambda r: r["signal_cumulative"])
        best_corr = max(results, key=lambda r: r["correlation"])
        
        print(f"\n{'='*80}")
        print(f"BEST PERFECT HINDSIGHT: {best_perfect['pair']} → "
              f"{best_perfect['perfect_cumulative']*100:.2f}% cumulative")
        print(f"BEST TOY SIGNAL: {best_signal['pair']} → "
              f"{best_signal['signal_cumulative']*100:.2f}% cumulative")
        print(f"HIGHEST CORRELATION: {best_corr['pair']} → "
              f"{best_corr['correlation']:.4f}")
        print(f"{'='*80}")
    
    print(f"\nPairs analyzed: {pairs_analyzed}")
    print("DONE")


if __name__ == "__main__":
    main()
