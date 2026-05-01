#!/usr/bin/env python3
"""
Regime Analysis — What made the last 72h explode vs other windows?
Compares regime markers across walk-forward windows.
"""
import json
import time
import statistics
from datetime import datetime, timezone
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coinbase_advanced_client import CoinbaseAdvancedClient

PRODUCT = "RAVE-USD"
BTC = "BTC-USD"

def fetch_candles(client, pid, start, end, granularity="FIVE_MINUTE"):
    chunk_sec = 300 * 5 * 60
    if granularity == "ONE_MINUTE": chunk_sec = 300 * 60
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

def compute_regime_markers(candles, btc_candles):
    """Compute regime markers for a set of candles."""
    closes = [float(c["close"]) for c in candles]
    volumes = [float(c["volume"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    
    # 1. Volatility: ATR / price
    atrs = []
    for i in range(14, len(candles)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        atrs.append(tr)
    avg_atr = statistics.mean(atrs) if atrs else 0
    atr_pct = avg_atr / statistics.mean(closes) * 100 if closes else 0
    
    # 2. Range: (max - min) / min over window
    price_range = (max(closes) - min(closes)) / min(closes) * 100 if closes else 0
    
    # 3. Volume: avg volume
    avg_vol = statistics.mean(volumes) if volumes else 0
    
    # 4. Trendiness: % of candles where close > open vs < open
    bullish = sum(1 for c in candles if float(c["close"]) > float(c["open"]))
    bearish = sum(1 for c in candles if float(c["close"]) < float(c["open"]))
    trend_ratio = bullish / max(1, bullish + bearish)
    
    # 5. Mean reversion score: how often do big moves reverse?
    # Count candles where body < 30% of range (doji/indecision)
    small_bodies = 0
    for c in candles:
        body = abs(float(c["close"]) - float(c["open"]))
        candle_range = float(c["high"]) - float(c["low"])
        if candle_range > 0 and body / candle_range < 0.3:
            small_bodies += 1
    doji_pct = small_bodies / max(1, len(candles)) * 100
    
    # 6. BTC correlation
    btc_returns = []
    rave_returns = []
    for i in range(1, len(candles)):
        if i < len(btc_candles) and i > 0:
            rave_ret = (closes[i] - closes[i-1]) / closes[i-1] if closes[i-1] else 0
            btc_ret = (float(btc_candles[i]["close"]) - float(btc_candles[i-1]["close"])) / float(btc_candles[i-1]["close"]) if float(btc_candles[i-1]["close"]) else 0
            rave_returns.append(rave_ret)
            btc_returns.append(btc_ret)
    
    btc_corr = 0
    if len(rave_returns) > 2:
        mean_rave = statistics.mean(rave_returns)
        mean_btc = statistics.mean(btc_returns)
        cov = sum((r - mean_rave) * (b - mean_btc) for r, b in zip(rave_returns, btc_returns)) / len(rave_returns)
        std_rave = statistics.stdev(rave_returns) if len(rave_returns) > 1 else 0
        std_btc = statistics.stdev(btc_returns) if len(btc_returns) > 1 else 0
        if std_rave > 0 and std_btc > 0:
            btc_corr = cov / (std_rave * std_btc)
    
    # 7. Volatility regime classification
    # High vol: ATR% > 2%, Low vol: ATR% < 1%
    vol_regime = "HIGH" if atr_pct > 2 else ("LOW" if atr_pct < 1 else "MED")
    
    # 8. Trend regime
    # Ranging: trend_ratio 0.4-0.6, Trending: >0.6 or <0.4
    trend_regime = "RANGING" if 0.4 <= trend_ratio <= 0.6 else ("UPTREND" if trend_ratio > 0.6 else "DOWNTREND")
    
    return {
        "atr_pct": round(atr_pct, 2),
        "price_range_pct": round(price_range, 2),
        "avg_volume": round(avg_vol, 2),
        "trend_ratio": round(trend_ratio, 3),
        "doji_pct": round(doji_pct, 1),
        "btc_correlation": round(btc_corr, 3),
        "vol_regime": vol_regime,
        "trend_regime": trend_regime,
        "candle_count": len(candles),
    }

def main():
    client = CoinbaseAdvancedClient()
    now = int(time.time())
    days = 11
    start = now - days * 24 * 3600

    print(f"Fetching {days}-day data for regime analysis...")
    rave_candles = fetch_candles(client, PRODUCT, start, now)
    btc_m5 = fetch_candles(client, BTC, start, now, granularity="FIVE_MINUTE")
    
    # Align BTC candles to RAVE
    rave_timestamps = {int(c["start"]) for c in rave_candles}
    btc_aligned = [c for c in btc_m5 if int(c["start"]) in rave_timestamps]
    # If not exact match, use nearest
    if len(btc_aligned) < len(rave_candles) * 0.9:
        btc_aligned = btc_m5[:len(rave_candles)]
    
    print(f"  RAVE: {len(rave_candles)}, BTC: {len(btc_aligned)}")
    
    # Walk-forward windows
    candles_per_72h = int(72 * 60 / 5)  # ~864
    total_candles = len(rave_candles)
    num_windows = total_candles // candles_per_72h
    
    # Also analyze the LAST 72h separately (the outlier window)
    last_72h = rave_candles[-candles_per_72h:]
    last_72h_btc = btc_aligned[-candles_per_72h:] if len(btc_aligned) >= candles_per_72h else btc_aligned
    
    print(f"\n{'Window':<15} {'ATR%':>6} {'Range%':>7} {'AvgVol':>10} {'Trend':>6} {'Doji%':>6} {'BTC Corr':>8} {'Vol Regime':>11} {'Trend Regime':>13}")
    print("-" * 100)
    
    windows_data = []
    for w in range(num_windows):
        w_start = w * candles_per_72h
        w_end = min((w + 1) * candles_per_72h, total_candles)
        window_candles = rave_candles[w_start:w_end]
        window_btc = btc_aligned[w_start:w_end] if len(btc_aligned) >= w_end else btc_aligned
        
        markers = compute_regime_markers(window_candles, window_btc)
        day_label = f"Days {w*3+1}-{(w+1)*3}"
        windows_data.append({"window": day_label, **markers})
        
        print(f"{day_label:<15} {markers['atr_pct']:>5.1f}% {markers['price_range_pct']:>6.1f}% {markers['avg_volume']:>10.0f} {markers['trend_ratio']:>6.3f} {markers['doji_pct']:>5.1f}% {markers['btc_correlation']:>7.3f} {markers['vol_regime']:>11} {markers['trend_regime']:>13}")
    
    # Outlier window
    outlier_markers = compute_regime_markers(last_72h, last_72h_btc)
    print(f"{'LAST 72h':<15} {outlier_markers['atr_pct']:>5.1f}% {outlier_markers['price_range_pct']:>6.1f}% {outlier_markers['avg_volume']:>10.0f} {outlier_markers['trend_ratio']:>6.3f} {outlier_markers['doji_pct']:>5.1f}% {outlier_markers['btc_correlation']:>7.3f} {outlier_markers['vol_regime']:>11} {outlier_markers['trend_regime']:>13}")
    
    # Analysis: what's different about the outlier?
    print(f"\n{'=' * 100}")
    print("REGIME COMPARISON — What made the outlier explode?")
    print(f"{'=' * 100}")
    
    non_outlier_atr = statistics.mean([w["atr_pct"] for w in windows_data])
    non_outlier_range = statistics.mean([w["price_range_pct"] for w in windows_data])
    non_outlier_vol = statistics.mean([w["avg_volume"] for w in windows_data])
    non_outlier_btc = statistics.mean([w["btc_correlation"] for w in windows_data])
    
    comparisons = [
        ("ATR%", non_outlier_atr, outlier_markers["atr_pct"]),
        ("Price Range%", non_outlier_range, outlier_markers["price_range_pct"]),
        ("Avg Volume", non_outlier_vol, outlier_markers["avg_volume"]),
        ("BTC Corr", non_outlier_btc, outlier_markers["btc_correlation"]),
    ]
    
    for name, avg, outlier in comparisons:
        delta = outlier - avg
        pct = delta / abs(avg) * 100 if avg != 0 else 0
        direction = "↑" if delta > 0 else "↓"
        print(f"  {name}: Avg={avg:.2f}, Outlier={outlier:.2f} ({direction}{abs(pct):.0f}%)")
    
    # Regime pattern discovery
    print(f"\n{'=' * 100}")
    print("REGIME PATTERNS")
    print(f"{'=' * 100}")
    
    # Check: do HIGH vol windows perform better?
    high_vol_windows = [w for w in windows_data if w["vol_regime"] == "HIGH"]
    med_vol_windows = [w for w in windows_data if w["vol_regime"] == "MED"]
    low_vol_windows = [w for w in windows_data if w["vol_regime"] == "LOW"]
    
    print(f"\n  Volatility Regime Distribution:")
    print(f"    HIGH: {len(high_vol_windows)} windows")
    print(f"    MED: {len(med_vol_windows)} windows")
    print(f"    LOW: {len(low_vol_windows)} windows")
    print(f"    Outlier: {outlier_markers['vol_regime']}")
    
    # Check: do RANGING windows perform better?
    ranging = [w for w in windows_data if w["trend_regime"] == "RANGING"]
    trending = [w for w in windows_data if w["trend_regime"] != "RANGING"]
    
    print(f"\n  Trend Regime Distribution:")
    print(f"    RANGING: {len(ranging)} windows")
    print(f"    TRENDING: {len(trending)} windows")
    print(f"    Outlier: {outlier_markers['trend_regime']}")
    
    # Check: does high BTC correlation help or hurt?
    high_btc_corr = [w for w in windows_data if abs(w["btc_correlation"]) > 0.5]
    low_btc_corr = [w for w in windows_data if abs(w["btc_correlation"]) <= 0.5]
    
    print(f"\n  BTC Correlation:")
    print(f"    High (|r|>0.5): {len(high_btc_corr)} windows")
    print(f"    Low (|r|<=0.5): {len(low_btc_corr)} windows")
    print(f"    Outlier: {outlier_markers['btc_correlation']}")
    
    # Hypothesis generation
    print(f"\n{'=' * 100}")
    print("HYPOTHESES FOR REGIME FILTER")
    print(f"{'=' * 100}")
    
    if outlier_markers["atr_pct"] > non_outlier_atr:
        print(f"  ✅ HIGH VOLATILITY: Outlier ATR% {outlier_markers['atr_pct']:.1f} vs avg {non_outlier_atr:.1f} — higher vol = bigger swings = TP hits")
    else:
        print(f"  ❌ Volatility NOT the driver")
        
    if outlier_markers["price_range_pct"] > non_outlier_range:
        print(f"  ✅ WIDE PRICE RANGE: Outlier {outlier_markers['price_range_pct']:.1f}% vs avg {non_outlier_range:.1f}% — wider swings = more mean reversion")
    else:
        print(f"  ❌ Price range NOT the driver")
        
    if outlier_markers["trend_regime"] == "RANGING":
        print(f"  ✅ RANGING MARKET: Outlier is ranging — mean reversion works best in ranges")
    else:
        print(f"  ❌ Ranging NOT the driver")
    
    if outlier_markers["doji_pct"] > 40:
        print(f"  ✅ HIGH INDECISION: {outlier_markers['doji_pct']:.0f}% doji candles — consolidation before big moves")
    
    if abs(outlier_markers["btc_correlation"]) < 0.3:
        print(f"  ✅ LOW BTC CORRELATION: {outlier_markers['btc_correlation']:.3f} — RAVE moving independently = coin-specific edge")
    
    # Save
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "windows": windows_data,
        "outlier": outlier_markers,
        "comparisons": {
            "avg_atr_pct": non_outlier_atr,
            "avg_range_pct": non_outlier_range,
            "avg_volume": non_outlier_vol,
            "avg_btc_corr": non_outlier_btc,
        }
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "regime_analysis.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {out_path}")

if __name__ == "__main__":
    main()
