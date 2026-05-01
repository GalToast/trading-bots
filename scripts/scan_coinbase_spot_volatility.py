#!/usr/bin/env python3
"""
Coinbase spot product volatility burst scan.

Scans all USD-quote spot products on Coinbase for 72h volatility,
identifying pairs with repeated >1% and >2% moves that could outrun fees.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT_PATH = ROOT / "reports" / "coinbase_spot_burst_scan_72h.json"


# Known Coinbase USD spot products (from public product list / prior scans)
KNOWN_USD_SPOT = [
    # Large caps
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD",
    # Mid caps
    "AVAX-USD", "SUI-USD", "LINK-USD", "DOT-USD", "MATIC-USD", "UNI-USD",
    "ATOM-USD", "LTC-USD", "BCH-USD", "NEAR-USD", "FIL-USD", "APT-USD",
    "ARB-USD", "OP-USD", "INJ-USD", "TIA-USD", "SEI-USD", "STX-USD",
    # Smaller / meme / volatile
    "PEPE-USD", "WIF-USD", "BONK-USD", "FLOKI-USD", "SHIB-USD",
    # Other alts
    "AAVE-USD", "ALGO-USD", "GRT-USD", "IMX-USD", "RUNE-USD", "MKR-USD",
    "COMP-USD", "SNX-USD", "CRV-USD", "SAND-USD", "MANA-USD", "AXS-USD",
    "RENDER-USD", "FET-USD", "TRX-USD", "ICP-USD", "HBAR-USD", "VET-USD",
    "XLM-USD", "ETC-USD", "EOS-USD", "XTZ-USD",
]


def fetch_candles_72h(client: CoinbaseAdvancedClient, product_id: str) -> list[dict]:
    """Fetch 72h of 1-min candles, paginated."""
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


def analyze_volatility(candles: list[dict]) -> dict:
    """Analyze volatility characteristics."""
    if len(candles) < 10:
        return {"error": "insufficient data"}

    # 1-min returns
    returns_1m = []
    for i in range(1, len(candles)):
        prev = candles[i - 1]["close"]
        curr = candles[i]["close"]
        if prev > 0:
            returns_1m.append((curr - prev) / prev)

    # Max single 1-min move
    max_1m = max(abs(r) for r in returns_1m) if returns_1m else 0

    # Count moves > 1%, > 2%, > 0.5%
    moves_gt_05pct = sum(1 for r in returns_1m if abs(r) > 0.005)
    moves_gt_1pct = sum(1 for r in returns_1m if abs(r) > 0.01)
    moves_gt_2pct = sum(1 for r in returns_1m if abs(r) > 0.02)

    # High-low range as % of open (intra-candle volatility)
    intra_vols = []
    for c in candles:
        if c["open"] > 0:
            intra_vols.append((c["high"] - c["low"]) / c["open"])
    avg_intra_vol = sum(intra_vols) / len(intra_vols) if intra_vols else 0
    max_intra_vol = max(intra_vols) if intra_vols else 0

    # Total 72h range
    prices = [c["close"] for c in candles]
    total_range_pct = (max(prices) - min(prices)) / min(prices) if min(prices) > 0 else 0

    # Average spread (use bid-ask proxy: high-low / close)
    avg_spread_bps = avg_intra_vol * 10000

    return {
        "candles": len(candles),
        "max_1m_move_pct": round(max_1m * 100, 4),
        "moves_gt_05pct": moves_gt_05pct,
        "moves_gt_1pct": moves_gt_1pct,
        "moves_gt_2pct": moves_gt_2pct,
        "total_72h_range_pct": round(total_range_pct * 100, 2),
        "avg_intra_candle_vol_pct": round(avg_intra_vol * 100, 4),
        "max_intra_candle_vol_pct": round(max_intra_vol * 100, 4),
        "avg_spread_bps_estimate": round(avg_spread_bps, 1),
        "price_range": f"${min(prices):.4f} - ${max(prices):.4f}",
        "current_price": prices[-1],
    }


def main() -> None:
    client = CoinbaseAdvancedClient()
    products = KNOWN_USD_SPOT
    print(f"Scanning {len(products)} known USD spot products...")

    results = []
    for i, pid in enumerate(products):
        print(f"[{i+1}/{len(products)}] {pid}...")
        try:
            candles = fetch_candles_72h(client, pid)
            if len(candles) < 60:
                print(f"  Skipping — only {len(candles)} candles")
                results.append({"product_id": pid, "error": f"only {len(candles)} candles"})
                continue
            analysis = analyze_volatility(candles)
            print(f"  1m max move: {analysis['max_1m_move_pct']:.2f}% | >1% moves: {analysis['moves_gt_1pct']} | 72h range: {analysis['total_72h_range_pct']:.1f}%")
            results.append({"product_id": pid, **analysis})
            time.sleep(0.2)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"product_id": pid, "error": str(e)})

    # Sort by volatility (moves > 1% descending)
    results.sort(key=lambda x: x.get("moves_gt_1pct", 0), reverse=True)

    # Write report
    out = Path(DEFAULT_REPORT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_products": len(products),
        "results": results,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Summary table
    print(f"\n{'='*100}")
    print(f"{'Product':<16} {'1m Max%':>8} {'>0.5%':>6} {'>1%':>6} {'>2%':>6} {'72h Range%':>10} {'Avg Spread bps':>14} {'Current $':>12}")
    print(f"{'='*100}")
    for r in results[:30]:
        if "error" in r:
            print(f"{r['product_id']:<16} {'ERR':>8} {'—':>6} {'—':>6} {'—':>6} {'—':>10} {'—':>14} {'—':>12}")
        else:
            print(f"{r['product_id']:<16} {r['max_1m_move_pct']:>7.2f}% {r['moves_gt_05pct']:>6} {r['moves_gt_1pct']:>6} {r['moves_gt_2pct']:>6} {r['total_72h_range_pct']:>9.1f}% {r['avg_spread_bps_estimate']:>13.1f} ${r['current_price']:>10.4f}")

    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
