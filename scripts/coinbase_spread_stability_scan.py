#!/usr/bin/env python3
"""Spread Stability Scanner for Coinbase Spot Products.

The ratio50 gate (live/board spread ratio < 0.50) successfully blocked all 3 FOLKS-USD losses
while forfeiting only 2 tiny wins ($0.25 avoided vs $0.03 forfeited).

This scanner searches for MORE products like FOLKS/HOUSE/BTR — products where:
1. Spread is STABLE (doesn't collapse during holds)
2. Live spread is consistently >= board spread * 0.50 (passes ratio50 gate)
3. Has enough volatility for the maker geometry to work

Approach:
1. Fetch live order book snapshots for all 389 active USD products
2. Compute live spread (best ask - best bid) / mid
3. Compare with known board spreads (from DEFAULT_SPREAD_BPS or historical)
4. Rank by spread stability and ratio50 pass rate

Output: reports/coinbase_spread_stability_scan.md
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
OUTPUT_MD = REPORTS / "coinbase_spread_stability_scan.md"
OUTPUT_JSON = REPORTS / "coinbase_spread_stability_scan.json"

# Known board spreads (from historical analysis)
KNOWN_BOARD_SPREADS_BPS = {
    "RAVE-USD": 13.5,
    "IOTX-USD": 25.0,
    "BAL-USD": 70.0,
    "BLUR-USD": 31.8,
    "ALEPH-USD": 50.0,
    "SOL-USD": 2.0,
    "BTC-USD": 1.0,
    "ETH-USD": 1.0,
    "FOLKS-USD": 50.0,  # Spread collapsed from 124 to 47 during hold
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
        result = client.list_products(get_all_products=True, product_type="SPOT")
        products = result.get("products", [])
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


def fetch_spreads_batch(client, product_ids: list[str]) -> dict[str, float]:
    """Fetch live spreads for a batch of products using best_bid_ask endpoint."""
    try:
        result = client.best_bid_ask(product_ids)
        # Key is 'pricebooks' (plural), not 'pricebook'
        pricebooks = result.get("pricebooks", [])
        
        spreads = {}
        for book in pricebooks:
            pid = book.get("product_id", "")
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            
            if bids and asks:
                bid = float(bids[0].get("price", 0))
                ask = float(asks[0].get("price", 0))
                if bid > 0 and ask > 0 and ask > bid:
                    mid = (bid + ask) / 2
                    spread_bps = (ask - bid) / mid * 10000
                    spreads[pid] = round(spread_bps, 2)
                else:
                    spreads[pid] = 0.0
            else:
                spreads[pid] = 0.0
        
        return spreads
    except Exception as e:
        print(f"[WARN] Failed to fetch spreads: {e}")
        return {}


def compute_spread_bps(spread_bps: float) -> float:
    """Return the pre-computed spread in basis points."""
    return spread_bps


def compute_spread_ratio(live_bps: float, board_bps: float) -> float:
    """Compute live/board spread ratio.
    
    ratio < 0.50 means live spread is too thin (blocks entry via ratio50 gate)
    ratio >= 0.50 means spread is healthy
    """
    if board_bps <= 0:
        return 1.0 if live_bps > 0 else 0.0
    return live_bps / board_bps


def main() -> int:
    print("=" * 80)
    print("SPREAD STABILITY SCAN — Coinbase Spot Products")
    print("=" * 80)

    ClientClass = try_import_client()
    if ClientClass is None:
        print("[ERROR] coinbase_advanced_client not found.")
        return 1

    try:
        client = ClientClass()
    except Exception as e:
        print(f"[ERROR] Failed to initialize client: {e}")
        return 1

    # Fetch products
    products = fetch_products(client)
    if not products:
        print("[ERROR] No products found")
        return 1

    # Scan all products in batches of 100 (API limit)
    print(f"\n[INFO] Scanning {len(products)} products for spread stability...")
    
    # Build product_id -> product_data map
    product_map = {p.get("product_id", ""): p for p in products}
    all_pids = list(product_map.keys())
    
    # Fetch spreads in batches
    spread_map = {}
    batch_size = 100
    for i in range(0, len(all_pids), batch_size):
        batch = all_pids[i:i+batch_size]
        print(f"[INFO] Fetching spreads for batch {i//batch_size + 1}/{(len(all_pids)+batch_size-1)//batch_size}...")
        batch_spreads = fetch_spreads_batch(client, batch)
        spread_map.update(batch_spreads)
    
    # Build results
    results = []
    for p in products:
        pid = p.get("product_id", "")
        live_bps = spread_map.get(pid, 0.0)
        board_bps = KNOWN_BOARD_SPREADS_BPS.get(pid, 0)
        ratio = compute_spread_ratio(live_bps, board_bps) if board_bps > 0 else 0
        
        price = float(p.get("price", 0))
        volume_24h = float(p.get("volume_24h", 0))
        
        results.append({
            "product_id": pid,
            "live_spread_bps": live_bps,
            "board_spread_bps": board_bps,
            "spread_ratio": round(ratio, 3) if ratio > 0 else 0,
            "price": price,
            "volume_24h": volume_24h,
            "passes_ratio50": ratio >= 0.50 if board_bps > 0 else False,
            "has_known_board_spread": board_bps > 0,
        })

    # Sort by spread ratio (highest first — most stable spreads)
    results_with_board = [r for r in results if r["has_known_board_spread"]]
    results_without_board = [r for r in results if not r["has_known_board_spread"]]
    
    # For products without known board spread, use live spread as proxy
    # High live spread = potentially maker-friendly
    results_without_board.sort(key=lambda x: x["live_spread_bps"], reverse=True)

    # Analysis
    print(f"\n{'='*80}")
    print(f"SPREAD STABILITY RESULTS")
    print(f"{'='*80}")
    print(f"Total products scanned: {len(results)}")
    print(f"Products with known board spread: {len(results_with_board)}")
    print(f"Products without known board spread: {len(results_without_board)}")
    
    # Products that pass ratio50 gate
    passing_known = [r for r in results_with_board if r["passes_ratio50"]]
    failing_known = [r for r in results_with_board if not r["passes_ratio50"]]
    
    print(f"\nKnown board spread products:")
    print(f"  Passing ratio50 (live/board >= 0.50): {len(passing_known)}")
    print(f"  Failing ratio50 (live/board < 0.50): {len(failing_known)}")
    
    print(f"\nProducts with known board spread — ratio50 status:")
    print(f"{'Product':<15} {'Live bps':>10} {'Board bps':>10} {'Ratio':>8} {'Pass?':>8}")
    print("-" * 60)
    for r in sorted(results_with_board, key=lambda x: x["spread_ratio"], reverse=True):
        print(f"{r['product_id']:<15} {r['live_spread_bps']:>10.2f} {r['board_spread_bps']:>10.1f} "
              f"{r['spread_ratio']:>8.3f} {'✅' if r['passes_ratio50'] else '❌':>8}")

    # Top 20 products with highest live spreads (no known board spread)
    print(f"\nTop 20 products with HIGHEST live spreads (potential maker-friendly):")
    print(f"{'Product':<15} {'Live bps':>10} {'Price':>12} {'Vol 24h':>12}")
    print("-" * 55)
    for r in results_without_board[:20]:
        print(f"{r['product_id']:<15} {r['live_spread_bps']:>10.2f} {r['price']:>12.6f} "
              f"{r['volume_24h']:>12.0f}")

    # Generate markdown report
    md_lines = [
        "# Coinbase Spread Stability Scan",
        f"**Generated:** {utc_now_iso()}",
        f"**Products scanned:** {len(results)}",
        "",
        "## Methodology",
        "",
        "The ratio50 gate (live/board spread ratio >= 0.50) blocked all 3 FOLKS-USD losses",
        "while forfeiting only $0.03 in tiny wins. This scan searches for MORE products",
        "that would pass this gate.",
        "",
        "## Products with Known Board Spreads — Ratio50 Status",
        "",
        f"- **Passing ratio50:** {len(passing_known)}",
        f"- **Failing ratio50:** {len(failing_known)}",
        "",
        "| Product | Live Spread (bps) | Board Spread (bps) | Ratio | Pass? |",
        "|---------|------------------|-------------------|-------|-------|",
    ]
    
    for r in sorted(results_with_board, key=lambda x: x["spread_ratio"], reverse=True):
        md_lines.append(
            f"| {r['product_id']} | {r['live_spread_bps']:.2f} | {r['board_spread_bps']:.1f} "
            f"| {r['spread_ratio']:.3f} | {'✅' if r['passes_ratio50'] else '❌'} |"
        )

    md_lines.extend([
        "",
        "## Top 20 Products with Highest Live Spreads (No Known Board Spread)",
        "",
        "These products have WIDE live spreads — potentially maker-friendly opportunities.",
        "",
        "| Product | Live Spread (bps) | Price | Volume 24h |",
        "|---------|------------------|-------|------------|",
    ])
    
    for r in results_without_board[:20]:
        md_lines.append(
            f"| {r['product_id']} | {r['live_spread_bps']:.2f} | {r['price']:.6f} | {r['volume_24h']:.0f} |"
        )

    md_lines.extend([
        "",
        "## Key Findings",
        "",
        f"1. **{len(passing_known)} products** with known board spreads pass the ratio50 gate",
        f"2. **{len(failing_known)} products** would be blocked by ratio50 gate",
        f"3. Top unknown product by live spread: **{results_without_board[0]['product_id']}** at {results_without_board[0]['live_spread_bps']:.2f}bps" if results_without_board else "",
        "",
        "## Next Steps",
        "",
        "1. Add top wide-spread products to the Kraken maker candidate list",
        "2. Run ratio50 gate test on new candidates",
        "3. Monitor spread stability over time (single snapshot may not represent typical state)",
        "",
    ])

    md_content = "\n".join(md_lines)

    # Save outputs
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md_content, encoding="utf-8")
    print(f"\n[INFO] Saved markdown report to {OUTPUT_MD}")

    result = {
        "generated_at": utc_now_iso(),
        "products_scanned": len(results),
        "passing_ratio50": len(passing_known),
        "failing_ratio50": len(failing_known),
        "known_spread_products": results_with_board,
        "wide_spread_unknown_products": results_without_board[:20],
    }
    OUTPUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[INFO] Saved JSON data to {OUTPUT_JSON}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
