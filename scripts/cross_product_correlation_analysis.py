#!/usr/bin/env python3
"""Cross-Product Correlation Analysis for Coinbase Spot Hedging.

The fee-survival problem is fundamental: taker fees (120bps/side = 2.4% round trip)
destroy edge for most geometries. Maker-entry helped but wasn't enough alone.

This script explores CROSS-PRODUCT HEDGING as a third path:
- Long symbol A + short symbol B (where A and B are highly correlated)
- The delta exposure cancels out, leaving only the spread/fee arbitrage
- If A outperforms B slightly (or vice versa), you capture the differential
- Both sides pay maker fees (or one maker, one taker) but the hedge reduces directional risk

Approach:
1. Fetch M1 candles for all active Coinbase spot products (last 24h)
2. Compute return correlation matrix
3. Identify pairs with:
   a. High price correlation (>0.7) — move together, so delta-neutral hedge works
   b. Divergent spreads — one has wide spread (maker-friendly), one narrow
   c. Both have sufficient volatility (ATR > threshold)
4. Score pairs by hedging potential

Output: reports/cross_product_hedging_analysis.md
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
OUTPUT_MD = REPORTS / "cross_product_hedging_analysis.md"
OUTPUT_JSON = REPORTS / "cross_product_hedging_analysis.json"

# Default spread estimates (from previous analysis)
DEFAULT_SPREAD_BPS = {
    "RAVE-USD": 13.5,
    "IOTX-USD": 25.0,
    "BAL-USD": 70.0,
    "BLUR-USD": 31.8,
    "ALEPH-USD": 50.0,
    "SOL-USD": 2.0,
    "BTC-USD": 1.0,
    "ETH-USD": 1.0,
    "FOLKS-USD": 50.0,
    "HOUSE-USD": 30.0,
    "BTR-USD": 40.0,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def try_import_client():
    """Try to import the Coinbase client, return None if unavailable."""
    try:
        from coinbase_advanced_client import CoinbaseAdvancedClient
        return CoinbaseAdvancedClient
    except ImportError:
        return None


def fetch_products(client) -> list[dict]:
    """Fetch all active spot products from Coinbase."""
    try:
        # list_products returns dict with 'products' key
        result = client.list_products(get_all_products=True, product_type="SPOT")
        products = result.get("products", [])
        # Filter to active USD pairs (status is "online" not "active")
        active = [
            p for p in products
            if p.get("status") == "online"
            and p.get("quote_currency_id") == "USD"
            and p.get("trading_disabled") is False
        ]
        print(f"[INFO] Found {len(active)} active USD spot products")
        return active
    except Exception as e:
        print(f"[ERROR] Failed to fetch products: {e}")
        return []


def fetch_candles(client, product_id: str, limit: int = 300) -> list[dict]:
    """Fetch M1 candles for a product (up to 350 max per API limit)."""
    try:
        # API limits to 350 candles max. Use limit instead of start/end.
        result = client.market_candles(
            product_id=product_id,
            granularity="ONE_MINUTE",
            limit=min(limit, 350),
        )
        candles = result.get("candles", [])
        return candles or []
    except Exception as e:
        print(f"[WARN] Failed to fetch candles for {product_id}: {e}")
        return []


def compute_returns(candles: list) -> list[float]:
    """Compute M1 return series from candles.
    
    Candles can be dicts with 'close' key or lists [ts, low, high, open, close, volume].
    """
    closes = []
    for c in candles:
        if isinstance(c, dict):
            close = float(c.get("close", 0))
        elif isinstance(c, (list, tuple)) and len(c) >= 5:
            close = float(c[4])  # close is index 4 in [ts, low, high, open, close, volume]
        else:
            continue
        if close > 0:
            closes.append(close)

    if len(closes) < 10:
        return []

    returns = []
    for i in range(1, len(closes)):
        ret = (closes[i] - closes[i - 1]) / closes[i - 1]
        returns.append(ret)

    return returns


def correlation(x: list[float], y: list[float]) -> float:
    """Compute Pearson correlation between two return series."""
    n = min(len(x), len(y))
    if n < 30:
        return 0.0

    x = x[:n]
    y = y[:n]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)

    if std_x < 1e-12 or std_y < 1e-12:
        return 0.0

    return cov / (std_x * std_y)


def compute_atr(candles: list, period: int = 14) -> float:
    """Compute average true range from candles.
    
    Candles can be dicts or lists [ts, low, high, open, close, volume].
    """
    if len(candles) < period + 1:
        return 0.0

    def extract_hlc(c):
        if isinstance(c, dict):
            return float(c.get("high", 0)), float(c.get("low", 0)), float(c.get("close", 0))
        elif isinstance(c, (list, tuple)) and len(c) >= 5:
            return float(c[2]), float(c[1]), float(c[4])  # [ts, low, high, open, close, volume]
        return 0.0, 0.0, 0.0

    trs = []
    for i in range(1, len(candles)):
        high, low, close = extract_hlc(candles[i])
        _, _, prev_close = extract_hlc(candles[i - 1])

        if high <= 0 or low <= 0 or prev_close <= 0:
            continue

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    if not trs:
        return 0.0

    return sum(trs[-period:]) / min(period, len(trs))


def hedge_score(corr: float, spread_a: float, spread_b: float, atr_a: float, atr_b: float) -> float:
    """Score a pair for hedging potential.

    Higher is better. Factors:
    - High correlation (0.5-1.0): delta-neutral hedge works
    - Spread differential: one wide, one narrow = arb opportunity
    - Both have meaningful ATR: enough volatility to harvest
    """
    if corr < 0.3:
        return 0.0

    # Correlation component: scales 0-0.5
    corr_score = max(0, (corr - 0.3) / 0.7) * 0.5

    # Spread differential: |spread_a - spread_b| / max(spread_a, spread_b)
    max_spread = max(spread_a, spread_b)
    if max_spread < 1:
        return 0.0

    spread_diff = abs(spread_a - spread_b)
    spread_score = (spread_diff / max_spread) * 0.3

    # Volatility: both need meaningful ATR (at least 0.1% of price)
    avg_atr = (atr_a + atr_b) / 2
    vol_score = min(avg_atr / 0.002, 1.0) * 0.2  # Normalize to 0.2 max

    return corr_score + spread_score + vol_score


def main() -> int:
    print("=" * 80)
    print("CROSS-PRODUCT CORRELATION ANALYSIS — Coinbase Spot Hedging")
    print("=" * 80)

    # Try to import client
    ClientClass = try_import_client()
    if ClientClass is None:
        print("[ERROR] coinbase_advanced_client not found. Cannot proceed with live analysis.")
        print("[INFO] This script requires the Coinbase API client to be installed.")
        return 1

    # Initialize client
    try:
        client = ClientClass()
    except Exception as e:
        print(f"[ERROR] Failed to initialize client: {e}")
        print("[INFO] Check .env for COINBASE_API_KEY and COINBASE_API_SECRET")
        return 1

    # Fetch products
    products = fetch_products(client)
    if not products:
        print("[ERROR] No products found")
        return 1

    # Select products for analysis (top by volume + known bubbling products)
    # Limit to ~30 products to avoid API rate limits
    bubbling = {"RAVE-USD", "BAL-USD", "BLUR-USD", "IOTX-USD", "ALEPH-USD",
                "FOLKS-USD", "HOUSE-USD", "BTR-USD", "SOL-USD", "ETH-USD", "BTC-USD"}

    # Add high-volume products from the list
    selected = []
    for p in products:
        pid = p.get("product_id", "")
        if pid in bubbling:
            selected.append(p)
        elif p.get("quote_currency_id") == "USD" and p.get("status") == "active":
            # Include if it has reasonable base currency
            base = p.get("base_currency_id", "")
            if len(base) <= 10:  # Skip weird long names
                selected.append(p)

    # Limit to 30 products
    selected = selected[:30]
    product_ids = [p.get("product_id") for p in selected]
    print(f"[INFO] Analyzing {len(product_ids)} products: {product_ids}")

    # Fetch candles for all products
    returns_map = {}
    atr_map = {}
    for pid in product_ids:
        print(f"[INFO] Fetching candles for {pid}...")
        candles = fetch_candles(client, pid, limit=300)  # ~5 hours of M1 data
        if not candles:
            print(f"[WARN] No candles for {pid}")
            continue

        returns = compute_returns(candles)
        atr = compute_atr(candles)

        if returns:
            returns_map[pid] = returns
            atr_map[pid] = atr
            print(f"  -> {len(returns)} returns, ATR={atr:.6f}")

    if len(returns_map) < 2:
        print("[ERROR] Not enough products with data")
        return 1

    # Compute correlation matrix
    print("\n[INFO] Computing correlation matrix...")
    pids = sorted(returns_map.keys())
    correlations = {}
    for i, pid_a in enumerate(pids):
        for pid_b in pids[i + 1:]:
            corr = correlation(returns_map[pid_a], returns_map[pid_b])
            correlations[(pid_a, pid_b)] = corr

    # Score all pairs
    print("[INFO] Scoring hedging pairs...")
    scored_pairs = []
    for (pid_a, pid_b), corr in correlations.items():
        spread_a = DEFAULT_SPREAD_BPS.get(pid_a, 20.0)
        spread_b = DEFAULT_SPREAD_BPS.get(pid_b, 20.0)
        atr_a = atr_map.get(pid_a, 0.0)
        atr_b = atr_map.get(pid_b, 0.0)

        score = hedge_score(corr, spread_a, spread_b, atr_a, atr_b)
        scored_pairs.append({
            "product_a": pid_a,
            "product_b": pid_b,
            "correlation": round(corr, 4),
            "spread_a_bps": spread_a,
            "spread_b_bps": spread_b,
            "spread_diff_bps": abs(spread_a - spread_b),
            "atr_a": round(atr_a, 6),
            "atr_b": round(atr_b, 6),
            "hedge_score": round(score, 4),
        })

    # Sort by score descending
    scored_pairs.sort(key=lambda x: x["hedge_score"], reverse=True)

    # Output
    print("\n" + "=" * 80)
    print("TOP 20 HEDGING PAIRS")
    print("=" * 80)
    print(f"{'Pair':<30} {'Corr':>6} {'SpreadΔ':>10} {'ATR A':>10} {'ATR B':>10} {'Score':>6}")
    print("-" * 80)
    for p in scored_pairs[:20]:
        print(f"{p['product_a']} vs {p['product_b']:<15} {p['correlation']:>6.3f} "
              f"{p['spread_diff_bps']:>9.1f}bps {p['atr_a']:>10.6f} {p['atr_b']:>10.6f} "
              f"{p['hedge_score']:>6.3f}")

    # Generate markdown report
    md_lines = [
        "# Cross-Product Hedging Analysis — Coinbase Spot",
        f"**Generated:** {utc_now_iso()}",
        f"**Products analyzed:** {len(pids)}",
        f"**Candle window:** 24h M1",
        "",
        "## Methodology",
        "",
        "Cross-product hedging tests whether long A + short B can harvest volatility",
        "delta-neutral when A and B are highly correlated but have divergent spreads.",
        "",
        "**Hedge Score Components:**",
        "- Correlation (0.5 weight): Higher correlation = better delta-neutral hedge",
        "- Spread differential (0.3 weight): Wider spread gap = more arb opportunity",
        "- Volatility (0.2 weight): Both products need meaningful ATR",
        "",
        "## Top 20 Hedging Pairs",
        "",
        "| Pair | Correlation | Spread Δ (bps) | ATR A | ATR B | Hedge Score |",
        "|------|------------|----------------|-------|-------|-------------|",
    ]

    for p in scored_pairs[:20]:
        md_lines.append(
            f"| {p['product_a']} vs {p['product_b']} "
            f"| {p['correlation']:.3f} "
            f"| {p['spread_diff_bps']:.1f} "
            f"| {p['atr_a']:.6f} "
            f"| {p['atr_b']:.6f} "
            f"| {p['hedge_score']:.3f} |"
        )

    md_lines.extend([
        "",
        "## Interpretation",
        "",
        "- **Score > 0.5**: Strong hedging candidate — high correlation + spread divergence",
        "- **Score 0.3-0.5**: Moderate candidate — worth testing with small size",
        "- **Score < 0.3**: Weak candidate — correlation too low or spreads too similar",
        "",
        "## Next Steps",
        "",
        "1. Take top 3 pairs and run backtest with long A + short B simulation",
        "2. Measure net PnL after fees on both sides (maker + taker assumptions)",
        "3. Test with different position sizing ratios (1:1, beta-weighted, etc.)",
        "4. Validate that correlation holds across different market regimes",
        "",
        "## Caveats",
        "",
        "- Correlation is based on 24h M1 data — may not hold over longer periods",
        "- Spread estimates are defaults, not live measurements",
        "- Short selling on Coinbase spot may have borrow costs or availability limits",
        "- This analysis does NOT account for execution slippage on simultaneous entries/exits",
        "",
    ])

    md_content = "\n".join(md_lines)

    # Save outputs
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md_content, encoding="utf-8")
    print(f"\n[INFO] Saved markdown report to {OUTPUT_MD}")

    # Save JSON for programmatic access
    result = {
        "generated_at": utc_now_iso(),
        "products_analyzed": len(pids),
        "candle_window_minutes": 300,  # ~5h at M1 (API limit 350)
        "top_pairs": scored_pairs[:20],
        "all_pairs": scored_pairs,
    }
    OUTPUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[INFO] Saved JSON data to {OUTPUT_JSON}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
