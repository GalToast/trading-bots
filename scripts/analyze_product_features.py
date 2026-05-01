#!/usr/bin/env python3
"""
Product-level feature analysis for Coinbase spot edge discovery.

Computes candle-derived features across all scanned USD pairs to identify
which product characteristics separate potential edge candidates from losers.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "reports" / "coinbase_spot_burst_scan_72h.json"
FEATURE_PATH = ROOT / "reports" / "coinbase_product_feature_analysis.json"

KNOWN_USD_SPOT = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD",
    "AVAX-USD", "SUI-USD", "LINK-USD", "DOT-USD", "UNI-USD",
    "ATOM-USD", "LTC-USD", "BCH-USD", "NEAR-USD", "FIL-USD", "APT-USD",
    "ARB-USD", "OP-USD", "INJ-USD", "TIA-USD", "SEI-USD", "STX-USD",
    "PEPE-USD", "WIF-USD", "BONK-USD", "FLOKI-USD", "SHIB-USD",
    "AAVE-USD", "ALGO-USD", "GRT-USD", "IMX-USD", "MKR-USD",
    "COMP-USD", "SNX-USD", "CRV-USD", "SAND-USD", "MANA-USD", "AXS-USD",
    "RENDER-USD", "FET-USD", "ICP-USD", "HBAR-USD", "VET-USD",
    "XLM-USD", "ETC-USD", "EOS-USD", "XTZ-USD",
]


def fetch_candles_72h(client: CoinbaseAdvancedClient, product_id: str) -> list[dict]:
    gsec = 60
    max_per_req = 300
    end = int(time.time())
    start = end - (72 * 3600)
    all_candles = []
    seen = set()
    chunk_end = end
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity="ONE_MINUTE")
        raw = resp.get("candles") or []
        if not raw:
            break
        for c in raw:
            t = int(c["start"])
            if t not in seen:
                seen.add(t)
                all_candles.append({
                    "time": t, "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
        chunk_end = chunk_start - 1
        time.sleep(0.15)
    return sorted(all_candles, key=lambda x: x["time"])


def compute_features(candles: list[dict]) -> dict[str, Any]:
    if len(candles) < 120:
        return {"error": "insufficient candles"}

    n = len(candles)
    prices = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    # 1. Intra-candle range (%)
    ranges = [(c["high"] - c["low"]) / c["open"] * 100 for c in candles if c["open"] > 0]
    median_range = sorted(ranges)[len(ranges) // 2] if ranges else 0
    p90_range = sorted(ranges)[int(len(ranges) * 0.9)] if ranges else 0
    max_range = max(ranges) if ranges else 0

    # 2. Burst density: >0.5% and >1% moves per hour
    returns_1m = []
    for i in range(1, n):
        if prices[i - 1] > 0:
            returns_1m.append((prices[i] - prices[i - 1]) / prices[i - 1])

    hours = n / 60
    moves_gt_05 = sum(1 for r in returns_1m if abs(r) > 0.005)
    moves_gt_1 = sum(1 for r in returns_1m if abs(r) > 0.01)
    burst_density_05 = moves_gt_05 / hours if hours > 0 else 0
    burst_density_1 = moves_gt_1 / hours if hours > 0 else 0

    # 3. Trend persistence: return autocorrelation (lag 1-5)
    autocorr = {}
    for lag in [1, 3, 5]:
        if len(returns_1m) > lag:
            r1 = returns_1m[:-lag]
            r2 = returns_1m[lag:]
            mean1 = sum(r1) / len(r1)
            mean2 = sum(r2) / len(r2)
            cov = sum((a - mean1) * (b - mean2) for a, b in zip(r1, r2)) / len(r1)
            std1 = (sum((a - mean1) ** 2 for a in r1) / len(r1)) ** 0.5
            std2 = (sum((b - mean2) ** 2 for b in r2) / len(r2)) ** 0.5
            if std1 > 0 and std2 > 0:
                autocorr[f"lag_{lag}"] = round(cov / (std1 * std2), 4)
            else:
                autocorr[f"lag_{lag}"] = 0
        else:
            autocorr[f"lag_{lag}"] = 0

    # Momentum vs mean-reversion signal:
    # Positive autocorr = momentum (trends persist)
    # Negative autocorr = mean-reversion (prices bounce back)
    trend_type = "momentum" if autocorr.get("lag_1", 0) > 0.05 else ("mean_reversion" if autocorr.get("lag_1", 0) < -0.05 else "random_walk")

    # 4. Volatility clustering: big moves followed by big moves?
    # Measure: correlation of |return| with next |return|
    abs_returns = [abs(r) for r in returns_1m]
    if len(abs_returns) > 1:
        r1 = abs_returns[:-1]
        r2 = abs_returns[1:]
        mean1 = sum(r1) / len(r1)
        mean2 = sum(r2) / len(r2)
        cov = sum((a - mean1) * (b - mean2) for a, b in zip(r1, r2)) / len(r1)
        std1 = (sum((a - mean1) ** 2 for a in r1) / len(r1)) ** 0.5
        std2 = (sum((b - mean2) ** 2 for b in r2) / len(r2)) ** 0.5
        vol_clustering = round(cov / (std1 * std2), 4) if std1 > 0 and std2 > 0 else 0
    else:
        vol_clustering = 0

    # 5. Volume/movement correlation
    if n > 1 and len(volumes) == n:
        vol_changes = [(volumes[i] - volumes[i - 1]) / volumes[i - 1] if volumes[i - 1] > 0 else 0 for i in range(1, n)]
        price_moves = [abs(r) for r in returns_1m]
        if len(vol_changes) == len(price_moves) and len(vol_changes) > 10:
            mv = sum(vol_changes) / len(vol_changes)
            mp = sum(price_moves) / len(price_moves)
            cov = sum((a - mv) * (b - mp) for a, b in zip(vol_changes, price_moves)) / len(vol_changes)
            sv = (sum((a - mv) ** 2 for a in vol_changes) / len(vol_changes)) ** 0.5
            sp = (sum((b - mp) ** 2 for b in price_moves) / len(price_moves)) ** 0.5
            vol_move_corr = round(cov / (sv * sp), 4) if sv > 0 and sp > 0 else 0
        else:
            vol_move_corr = 0
    else:
        vol_move_corr = 0

    # 6. Spread-to-range ratio (how much of each candle is spread noise)
    # Use (high-low)/open as total range, estimate spread as median of smallest ranges
    small_ranges = sorted(ranges)[:max(1, len(ranges) // 20)]
    est_spread_pct = sum(small_ranges) / len(small_ranges) if small_ranges else 0
    spread_to_range_ratio = est_spread_pct / median_range if median_range > 0 else 0

    # 7. Additional: 72h drawdown and recovery
    peak = prices[0]
    max_dd = 0
    for p in prices:
        if p > peak:
            peak = p
        dd = (peak - p) / peak
        if dd > max_dd:
            max_dd = dd
    current_from_peak = (prices[-1] - peak) / peak if peak > 0 else 0

    # 8. Up/down asymmetry
    up_moves = [r for r in returns_1m if r > 0]
    down_moves = [r for r in returns_1m if r < 0]
    avg_up = sum(up_moves) / len(up_moves) if up_moves else 0
    avg_down = sum(down_moves) / len(down_moves) if down_moves else 0
    up_down_ratio = abs(avg_up / avg_down) if avg_down != 0 else 0

    return {
        "median_range_pct": round(median_range, 4),
        "p90_range_pct": round(p90_range, 4),
        "max_range_pct": round(max_range, 4),
        "burst_density_05_per_hr": round(burst_density_05, 2),
        "burst_density_1_per_hr": round(burst_density_1, 2),
        "autocorr_lag1": autocorr.get("lag_1", 0),
        "autocorr_lag3": autocorr.get("lag_3", 0),
        "autocorr_lag5": autocorr.get("lag_5", 0),
        "trend_type": trend_type,
        "vol_clustering": vol_clustering,
        "vol_move_correlation": vol_move_corr,
        "est_spread_bps": round(est_spread_pct * 100, 1),
        "spread_to_range_ratio": round(spread_to_range_ratio, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "current_from_peak_pct": round(current_from_peak * 100, 2),
        "avg_up_move_pct": round(avg_up * 100, 4),
        "avg_down_move_pct": round(avg_down * 100, 4),
        "up_down_asymmetry": round(up_down_ratio, 3),
        "candles": n,
        "current_price": prices[-1],
    }


def main() -> None:
    client = CoinbaseAdvancedClient()
    results = []

    for i, pid in enumerate(KNOWN_USD_SPOT):
        print(f"[{i + 1}/{len(KNOWN_USD_SPOT)}] {pid}...")
        try:
            candles = fetch_candles_72h(client, pid)
            if len(candles) < 120:
                print(f"  Skip — {len(candles)} candles")
                results.append({"product_id": pid, "error": f"only {len(candles)} candles"})
                continue
            features = compute_features(candles)
            print(f"  Range: {features['median_range_pct']:.2f}% | Burst/hr: {features['burst_density_1_per_hr']:.2f} | Trend: {features['trend_type']} | Vol cluster: {features['vol_clustering']:.3f}")
            results.append({"product_id": pid, **features})
            time.sleep(0.15)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"product_id": pid, "error": str(e)})

    # Rank by edge potential score (composite)
    # Scoring: high burst density + high volatility + high vol clustering + mean reversion
    # Mean reversion products with high burst density are the best scalping candidates
    for r in results:
        if "error" in r:
            r["edge_score"] = -1
            continue
        score = 0
        # High burst density = more opportunities
        score += r["burst_density_1_per_hr"] * 10
        # High median range = real movement, not spread noise
        score += r["median_range_pct"] * 5
        # Volatility clustering = predictable volatility bursts
        score += r["vol_clustering"] * 10
        # Mean reversion = good for grid/scalp (buy dips, sell bounces)
        if r["trend_type"] == "mean_reversion":
            score += 5
        elif r["trend_type"] == "momentum":
            score -= 2  # harder to scalp, trends run against you
        # Low spread-to-range = more signal, less noise
        score += (1 - min(r["spread_to_range_ratio"], 1)) * 5
        # High vol-move correlation = volume confirms moves
        score += abs(r["vol_move_correlation"]) * 3
        r["edge_score"] = round(score, 2)

    results.sort(key=lambda x: x.get("edge_score", 0), reverse=True)

    out = Path(FEATURE_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "products_scanned": len(KNOWN_USD_SPOT),
        "results": results,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Top 15 ranked
    print(f"\n{'='*120}")
    print(f"{'Product':<14} {'Score':>6} {'MedRange%':>9} {'Burst1%/hr':>10} {'Trend':>15} {'VolCluster':>11} {'Spread/Range':>12} {'VolMoveCorr':>11} {'Price':>10}")
    print(f"{'='*120}")
    for r in results[:15]:
        if "error" in r:
            print(f"{r['product_id']:<14} {'ERR':>6} {'—':>9} {'—':>10} {'—':>15} {'—':>11} {'—':>12} {'—':>11} {'—':>10}")
        else:
            print(f"{r['product_id']:<14} {r['edge_score']:>6.1f} {r['median_range_pct']:>8.3f}% {r['burst_density_1_per_hr']:>9.2f} {r['trend_type']:>15} {r['vol_clustering']:>10.3f} {r['spread_to_range_ratio']:>11.3f} {r['vol_move_correlation']:>10.3f} ${r['current_price']:>9.4f}")

    print(f"\nFull report: {out}")

    # Hypothesis summary
    print(f"\n{'='*80}")
    print("HYPOTHESIS: Products with edge potential share these characteristics:")
    print("  - burst_density_1_per_hr > 0.5 (at least one >1% move every 2 hours)")
    print("  - median_range_pct > 0.15 (meaningful intrabar movement)")
    print("  - vol_clustering > 0 (volatility comes in bursts, not uniformly)")
    print("  - trend_type = mean_reversion OR random_walk (not strong momentum)")
    print("  - spread_to_range_ratio < 0.5 (signal dominates spread noise)")
    print(f"{'='*80}")

    qualified = [r for r in results if not r.get("error")
                 and r.get("burst_density_1_per_hr", 0) > 0.3
                 and r.get("median_range_pct", 0) > 0.10
                 and r.get("spread_to_range_ratio", 1) < 0.6]
    print(f"\nProducts meeting all thresholds: {len(qualified)}")
    for r in qualified[:10]:
        print(f"  {r['product_id']}: score={r['edge_score']:.1f} burst={r['burst_density_1_per_hr']:.2f}/hr range={r['median_range_pct']:.3f}% trend={r['trend_type']}")


if __name__ == "__main__":
    main()
