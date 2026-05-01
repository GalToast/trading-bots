#!/usr/bin/env python3
"""
Lead-Follower Audit — Testing @gemini's Propagation Lag hypothesis

Tests:
1. Partial correlation (RAVE→LRDS controlling for BTC)
2. 3-window stability (does the lead survive across time?)
3. LRDS execution realism (spread, fill feasibility)
4. Reverse causality (does LRDS also lead RAVE?)

If the lead survives after controlling for BTC AND is stable across windows AND LRDS has tight enough spreads, it's a real edge.
"""
import json, os, sys, time, statistics, math
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

TEST_PAIRS = [
    ("RAVE-USD", "LRDS-USD"),
    ("MOG-USD", "RAVE-USD"),
    ("IDEX-USD", "STRK-USD"),
    ("AST-USD", "DOGINME-USD"),
]
BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="ONE_MINUTE"):
    chunk_sec = 300 * 60
    all_c = []
    cs = start
    while cs < end:
        ce = min(cs + chunk_sec, end)
        try:
            resp = client.market_candles(pid, start=cs, end=ce, granularity=granularity)
            cands = resp.get("candles", [])
            all_c.extend(cands)
            cs = ce
            if not cands: break
            time.sleep(0.1)
        except:
            cs = ce
            time.sleep(0.5)
    all_c.sort(key=lambda c: int(c["start"]))
    return all_c

def compute_returns(candles):
    """Compute 1-min returns from candles."""
    closes = [float(c["close"]) for c in candles]
    returns = []
    for i in range(1, len(closes)):
        ret = (closes[i] - closes[i-1]) / closes[i-1] if closes[i-1] != 0 else 0
        returns.append(ret)
    return returns

def correlation(x, y):
    """Pearson correlation."""
    if len(x) < 10 or len(y) < 10:
        return 0.0
    n = min(len(x), len(y))
    x = x[:n]
    y = y[:n]
    mean_x = statistics.mean(x)
    mean_y = statistics.mean(y)
    var_x = sum((xi - mean_x)**2 for xi in x)
    var_y = sum((yi - mean_y)**2 for yi in y)
    if var_x == 0 or var_y == 0:
        return 0.0
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    return cov / math.sqrt(var_x * var_y)

def partial_correlation(x, y, z):
    """Partial correlation between x and y controlling for z."""
    r_xy = correlation(x, y)
    r_xz = correlation(x, z)
    r_yz = correlation(y, z)
    
    denom = math.sqrt((1 - r_xz**2) * (1 - r_yz**2))
    if denom == 0:
        return 0.0
    return (r_xy - r_xz * r_yz) / denom

def cross_correlation(x, y, max_lag=5):
    """Cross-correlation at different lags. Positive lag means x leads y."""
    results = {}
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            x_lag = x[lag:] if lag > 0 else x
            y_lag = y[:len(y)-lag] if lag > 0 else y
        else:
            x_lag = x[:len(x)+lag]
            y_lag = y[-lag:]
        n = min(len(x_lag), len(y_lag))
        if n >= 10:
            results[lag] = correlation(x_lag[:n], y_lag[:n])
        else:
            results[lag] = 0.0
    return results

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600
    
    print(f"Fetching {days}-day M1 data for lead-follower audit...")
    
    # Fetch all needed coins + BTC
    all_coins = set()
    for leader, follower in TEST_PAIRS:
        all_coins.add(leader)
        all_coins.add(follower)
    all_coins.add(BTC)
    
    candle_data = {}
    for coin in all_coins:
        candles = fetch_candles(client, coin, start, now, "ONE_MINUTE")
        candle_data[coin] = candles
        print(f"  {coin}: {len(candles)} candles")
    
    results = []
    
    print(f"\n{'=' * 100}")
    print(f"LEAD-FOLLOWER AUDIT — Testing Propagation Lag hypothesis")
    print(f"{'=' * 100}")
    
    for leader, follower in TEST_PAIRS:
        print(f"\n--- {leader} → {follower} ---")
        
        # Get returns
        leader_ret = compute_returns(candle_data[leader])
        follower_ret = compute_returns(candle_data[follower])
        btc_ret = compute_returns(candle_data[BTC])
        
        # Align lengths
        n = min(len(leader_ret), len(follower_ret), len(btc_ret))
        leader_ret = leader_ret[:n]
        follower_ret = follower_ret[:n]
        btc_ret = btc_ret[:n]
        
        # 1. Raw correlation at 1-min lag (leader leads by 1 min)
        # Shift leader forward by 1: leader[t] vs follower[t+1]
        leader_shifted = leader_ret[1:]  # leader at t
        follower_shifted = follower_ret[:-1]  # wait, we want leader[t] vs follower[t+1]
        # Actually: if leader leads, then leader[t] should correlate with follower[t+1]
        # So: correlate leader_ret[:-1] with follower_ret[1:]
        
        raw_corr = correlation(leader_ret[:-1], follower_ret[1:])
        btc_raw = correlation(btc_ret[:-1], follower_ret[1:])
        leader_btc = correlation(leader_ret[:-1], btc_ret[1:])
        
        # 2. Partial correlation controlling for BTC
        partial_corr = partial_correlation(leader_ret[:-1], follower_ret[1:], btc_ret[1:])
        
        # 3. Cross-correlation at multiple lags
        cross_corr = cross_correlation(leader_ret, follower_ret, max_lag=5)
        best_lag = max(cross_corr, key=cross_corr.get)
        best_corr = cross_corr[best_lag]
        
        # 4. 3-window stability
        window_size = n // 3
        window_corrs = []
        for w in range(3):
            w_start = w * window_size
            w_end = (w + 1) * window_size
            if w_end - w_start > 10:
                w_corr = correlation(
                    leader_ret[w_start:w_end-1], 
                    follower_ret[w_start+1:w_end]
                )
                w_partial = partial_correlation(
                    leader_ret[w_start:w_end-1],
                    follower_ret[w_start+1:w_end],
                    btc_ret[w_start+1:w_end]
                )
                window_corrs.append({"window": w+1, "raw": round(w_corr, 3), "partial": round(w_partial, 3)})
            else:
                window_corrs.append({"window": w+1, "raw": 0, "partial": 0})
        
        # 5. LRDS spread check (for RAVE→LRDS pair)
        if follower == "LRDS-USD":
            lrds_candles = candle_data["LRDS-USD"]
            spreads = []
            for c in lrds_candles[-100:]:  # Last 100 candles
                o = float(c["open"])
                h = float(c["high"])
                l = float(c["low"])
                # Approximate spread as (high-low)/mid
                mid = (h + l) / 2 if (h+l) > 0 else 1
                spread_pct = (h - l) / mid * 100
                spreads.append(spread_pct)
            avg_spread = statistics.mean(spreads) if spreads else 0
            max_spread = max(spreads) if spreads else 0
        else:
            avg_spread = "N/A"
            max_spread = "N/A"
        
        result = {
            "leader": leader, "follower": follower,
            "raw_corr_1min_lag": round(raw_corr, 3),
            "btc_follower_corr": round(btc_raw, 3),
            "leader_btc_corr": round(leader_btc, 3),
            "partial_corr_controlling_btc": round(partial_corr, 3),
            "best_lag": best_lag,
            "best_corr": round(best_corr, 3),
            "window_stability": window_corrs,
            "follower_avg_spread_pct": avg_spread if isinstance(avg_spread, (int, float)) else avg_spread,
            "follower_max_spread_pct": max_spread if isinstance(max_spread, (int, float)) else max_spread,
        }
        results.append(result)
        
        print(f"  Raw corr (leader leads 1min): {raw_corr:.3f}")
        print(f"  BTC→follower corr:            {btc_raw:.3f}")
        print(f"  Leader→BTC corr:              {leader_btc:.3f}")
        print(f"  PARTIAL corr (ctrl BTC):      {partial_corr:.3f}")
        print(f"  Best lag:                     {best_lag} min (corr={best_corr:.3f})")
        print(f"  Window stability:")
        for w in window_corrs:
            print(f"    Window {w['window']}: raw={w['raw']:.3f}, partial={w['partial']:.3f}")
        print(f"  Follower avg spread:          {avg_spread}")
        print(f"  Follower max spread:          {max_spread}")
        
        # Verdict
        if abs(partial_corr) > 0.1:
            verdict = "✅ LEAD SURVIVES after BTC control"
        elif abs(raw_corr) > 0.3 and abs(partial_corr) < 0.1:
            verdict = "⚠️ Lead is mostly BTC-driven"
        else:
            verdict = "❌ No meaningful lead-lag relationship"
        print(f"  VERDICT: {verdict}")
    
    # Summary
    print(f"\n{'=' * 100}")
    print(f"AUDIT SUMMARY")
    print(f"{'=' * 100}")
    print(f"{'Pair':<30} {'Raw':>6} {'Partial':>8} {'Best Lag':>9} {'BTC-driven?':>12} {'Verdict':>10}")
    print("-" * 100)
    for r in results:
        btc_driven = "YES" if abs(r["raw_corr_1min_lag"]) > 0.3 and abs(r["partial_corr_controlling_btc"]) < 0.1 else "NO"
        if abs(r["partial_corr_controlling_btc"]) > 0.1:
            verdict = "✅ SURVIVES"
        elif abs(r["raw_corr_1min_lag"]) > 0.3:
            verdict = "⚠️ BTC-driven"
        else:
            verdict = "❌ NO EDGE"
        print(f"{r['leader']}→{r['follower']:<20} {r['raw_corr_1min_lag']:>6.3f} {r['partial_corr_controlling_btc']:>8.3f} {r['best_lag']:>6}min {btc_driven:>12} {verdict:>10}")
    
    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "lead_follower_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
