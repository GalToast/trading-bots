#!/usr/bin/env python3
"""Crypto Spot Lattice: Cross-Product Relative Strength Test.

Concept: Long strong products, short-proxy-long weak products.
The spread between them is our edge. No ML, no geometry matching.

Uses pulse candles (1m aggregated) to compute hourly returns,
then tests long-strong + long-weak pairs for spread profitability.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from itertools import combinations

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
PULSE_PATH = REPORTS / "cache" / "coinbase_spot_pulse_candles.json"

FEE_BPS_PER_SIDE = 120  # Coinbase Intro 1 taker
SPREAD_BPS_DEFAULT = 13.5  # Average spread
FEE_ROUND_TRIP_BPS = 2 * FEE_BPS_PER_SIDE + SPREAD_BPS_DEFAULT  # ~253.5 bps = 2.535%

def main():
    print("=" * 80)
    print("CRYPTO SPOT LATTICE: Cross-Product Relative Strength")
    print("=" * 80)
    
    # Load pulse candles
    with open(PULSE_PATH) as f:
        data = json.load(f)
    
    entries = data.get("entries", {})
    print(f"Loaded {len(entries)} product entries from pulse candles")
    
    # Build hourly return matrix
    product_returns = {}
    
    for key, entry in entries.items():
        product_id = key.split("|")[0]
        candles_1m = entry.get("candles", [])
        
        if len(candles_1m) < 60:
            continue
        
        df = pd.DataFrame(candles_1m)
        # Aggregate to 1h candles
        df["ts_bin"] = (df["start"] // 3600) * 3600
        hourly = df.groupby("ts_bin").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }).sort_index()
        
        if len(hourly) < 10:
            continue
        
        # Compute hourly returns
        hourly["ret"] = hourly["close"].pct_change() * 100  # in percent
        hourly["product_id"] = product_id
        
        product_returns[product_id] = hourly[["ret", "product_id"]].dropna()
    
    # Find products with at least 24h of data
    qualified = {}
    for pid, rets in product_returns.items():
        if len(rets) >= 24:
            qualified[pid] = rets
    
    print(f"Found {len(qualified)} products with 24h+ of data")
    
    if len(qualified) < 4:
        print("ERROR: Not enough qualified products")
        return
    
    # Find overlapping timestamps among qualified products
    # Use pairwise approach: for each pair, find their common timestamps
    # Then test all pairs
    
    # Build list of products sorted by data freshness (most recent first)
    pid_list = sorted(qualified.keys(), key=lambda p: max(qualified[p].index), reverse=True)
    
    # Find the largest common timestamp set among top products
    best_common = None
    best_products = []
    
    for start_idx in range(min(50, len(pid_list))):
        p = pid_list[start_idx]
        ts = set(qualified[p].index)
        candidates = [p]
        for p2 in pid_list[start_idx+1:min(50, len(pid_list))]:
            ts2 = set(qualified[p2].index)
            overlap = ts & ts2
            if len(overlap) >= 12:  # At least 12 common hours
                ts = overlap
                candidates.append(p2)
        
        if len(ts) > (len(best_common) if best_common else 0) and len(candidates) >= 4:
            best_common = ts
            best_products = candidates
    
    if not best_common or len(best_common) < 12:
        print("ERROR: Cannot find sufficient common timestamps")
        return
    
    common_ts = sorted(best_common)
    print(f"Found {len(common_ts)} common hourly timestamps across {len(best_products)} products")
    print(f"Products: {', '.join(best_products[:10])}{'...' if len(best_products) > 10 else ''}")
    
    # Build return matrix for common products
    product_returns = {p: qualified[p] for p in best_products}
    
    if len(common_ts) < 5:
        print("ERROR: Need at least 5 common timestamps")
        return
    
    # Build return matrix
    ret_matrix = {}
    for pid, rets in product_returns.items():
        ret_matrix[pid] = [rets.loc[ts, "ret"] if ts in rets.index else np.nan for ts in common_ts]
    
    df_ret = pd.DataFrame(ret_matrix, index=common_ts).dropna(axis=1, thresh=len(common_ts) * 0.8)
    products = list(df_ret.columns)
    print(f"Return matrix: {len(df_ret)} hours x {len(products)} products")
    
    # Test 1: Momentum ranking — long top quartile, short-proxy long bottom quartile
    print("\n" + "=" * 80)
    print("TEST 1: Momentum Spread Strategy (Long Strong - Long Weak)")
    print("=" * 80)
    
    # For each hour, compute 1h, 2h, 3h momentum
    for lookback in [1, 2, 3]:
        if len(df_ret) <= lookback:
            continue
        
        # Rolling momentum (sum of returns over lookback hours)
        momentum = df_ret.rolling(lookback).sum()
        
        # For each hour, rank products by momentum
        # Long top quartile, short-proxy long bottom quartile
        spreads = []
        n_quartile = max(1, len(products) // 4)
        
        for t in range(lookback, len(df_ret)):
            mom_row = momentum.iloc[t].dropna()
            if len(mom_row) < 4:
                continue
            
            ranked = mom_row.sort_values(ascending=False)
            top_q = ranked.head(n_quartile).index.tolist()
            bottom_q = ranked.tail(n_quartile).index.tolist()
            
            # Next hour return spread
            next_ret = df_ret.iloc[t + 1] if t + 1 < len(df_ret) else None
            if next_ret is None:
                continue
            
            top_next = next_ret.reindex(top_q).mean()
            bottom_next = next_ret.reindex(bottom_q).mean()
            spread = top_next - bottom_next
            
            # Net after fees (fee charged on BOTH legs)
            fee_pct = FEE_ROUND_TRIP_BPS / 100.0 * 2  # Both legs
            net = spread - fee_pct
            
            spreads.append({
                "hour": t,
                "momentum_lookback": lookback,
                "strong": ", ".join(top_q[:3]),
                "weak": ", ".join(bottom_q[:3]),
                "strong_next": top_next,
                "weak_next": bottom_next,
                "spread": spread,
                "fee_pct": fee_pct,
                "net": net,
                "win": net > 0,
            })
        
        if not spreads:
            continue
        
        df_spreads = pd.DataFrame(spreads)
        wins = df_spreads["win"].sum()
        total = len(df_spreads)
        
        print(f"\n  Momentum lookback: {lookback}h")
        print(f"  Trades: {total}, Wins: {wins} ({wins/total:.1%})")
        print(f"  Avg spread: {df_spreads['spread'].mean():.3f}%")
        print(f"  Avg fee: {df_spreads['fee_pct'].mean():.3f}%")
        print(f"  Avg net: {df_spreads['net'].mean():.3f}%")
        print(f"  Cum net: {df_spreads['net'].sum():.2f}%")
        print(f"  Best: {df_spreads['net'].max():.3f}%, Worst: {df_spreads['net'].min():.3f}%")
        
        # Best/worst trades
        best = df_spreads.nlargest(1, "net").iloc[0]
        worst = df_spreads.nsmallest(1, "net").iloc[0]
        print(f"  Best trade: {best['strong']} vs {best['weak']} → net {best['net']:.3f}%")
        print(f"  Worst trade: {worst['strong']} vs {worst['weak']} → net {worst['net']:.3f}%")
    
    # Test 2: Volatility-ranked pairs
    print("\n" + "=" * 80)
    print("TEST 2: Volatility Spread (Long High-Vol - Long Low-Vol)")
    print("=" * 80)
    
    vol = df_ret.std()
    high_vol = vol.nlargest(len(products) // 4).index.tolist()
    low_vol = vol.nsmallest(len(products) // 4).index.tolist()
    
    hv_rets = df_ret[high_vol].mean(axis=1)
    lv_rets = df_ret[low_vol].mean(axis=1)
    vol_spreads = hv_rets - lv_rets
    
    fee_pct = FEE_ROUND_TRIP_BPS / 100.0 * 2
    vol_net = vol_spreads - fee_pct
    
    wins = (vol_net > 0).sum()
    total = len(vol_net)
    print(f"  Trades: {total}, Wins: {wins} ({wins/total:.1%})")
    print(f"  Avg spread: {vol_spreads.mean():.3f}%")
    print(f"  Avg net: {vol_net.mean():.3f}%")
    print(f"  Cum net: {vol_net.sum():.2f}%")
    
    # Test 3: Correlation-filtered pairs (only uncorrelated products)
    print("\n" + "=" * 80)
    print("TEST 3: Low-Correlation Pairs (|corr| < 0.3)")
    print("=" * 80)
    
    corr = df_ret.corr()
    low_corr_pairs = []
    
    for i, p1 in enumerate(products):
        for p2 in products[i+1:]:
            c = corr.loc[p1, p2]
            if abs(c) < 0.3:
                # Check spread: long p1, short-proxy long p2
                spread = df_ret[p1] - df_ret[p2]
                net = spread - fee_pct
                low_corr_pairs.append({
                    "pair": f"{p1} vs {p2}",
                    "p1": p1,
                    "p2": p2,
                    "corr": c,
                    "avg_spread": spread.mean(),
                    "avg_net": net.mean(),
                    "cum_net": net.sum(),
                    "win_rate": (net > 0).mean(),
                    "n": len(net),
                })
    
    if low_corr_pairs:
        df_pairs = pd.DataFrame(low_corr_pairs)
        positive = df_pairs[df_pairs["avg_net"] > 0].sort_values("avg_net", ascending=False)
        
        print(f"  Found {len(df_pairs)} low-corr pairs, {len(positive)} positive net")
        
        if len(positive) > 0:
            print(f"\n  Top 10 positive pairs:")
            for _, r in positive.head(10).iterrows():
                print(f"    {r['pair']:20s}: corr={r['corr']:+.2f}, net={r['avg_net']:.3f}%, win={r['win_rate']:.1%}, cum={r['cum_net']:.1f}%")
    
    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)
    
    # Check if any test is profitable after fees
    momentum_profits = []
    for lookback in [1, 2, 3]:
        spreads_list = []
        momentum = df_ret.rolling(lookback).sum()
        n_quartile = max(1, len(products) // 4)
        for t in range(lookback, len(df_ret) - 1):
            mom_row = momentum.iloc[t].dropna()
            if len(mom_row) < 4:
                continue
            ranked = mom_row.sort_values(ascending=False)
            top_q = ranked.head(n_quartile).index.tolist()
            bottom_q = ranked.tail(n_quartile).index.tolist()
            next_ret = df_ret.iloc[t + 1]
            spread = next_ret.reindex(top_q).mean() - next_ret.reindex(bottom_q).mean()
            spreads_list.append(spread)
        if spreads_list:
            avg_spread = np.mean(spreads_list)
            avg_net = avg_spread - fee_pct
            momentum_profits.append((lookback, avg_net))
    
    if momentum_profits:
        best_lb, best_net = max(momentum_profits, key=lambda x: x[1])
        if best_net > 0:
            print(f"  MOMENTUM SPREAD: {best_lb}h lookback, avg net +{best_net:.3f}% per trade")
            print(f"  This is POTENTIALLY profitable after fees!")
        else:
            print(f"  MOMENTUM SPREAD: Best is {best_lb}h at avg net {best_net:.3f}% — still negative after fees")
            print(f"  Fee wall remains the barrier")
    
    if len(positive) > 0:
        best_pair = positive.iloc[0]
        print(f"  BEST LOW-CORR PAIR: {best_pair['pair']}, avg net +{best_pair['avg_net']:.3f}%")
    else:
        print(f"  LOW-CORR PAIRS: No positive pairs found")

if __name__ == "__main__":
    main()
