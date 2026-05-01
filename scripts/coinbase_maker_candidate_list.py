#!/usr/bin/env python3
"""Coinbase Maker Candidate List Generator.

Takes the spread stability scan results and produces a candidate list
that can be fed directly into the Kraken maker runner's product filter.

Filters:
1. Live spread >= 50bps (wide enough for maker orders to profit)
2. 24h volume >= $1M (enough liquidity)
3. Price > $0.001 (not dust)
4. Exclude known bad products (spread collapsed or tested losers)

Output: reports/coinbase_maker_candidates.md
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
SCAN_JSON = REPORTS / "coinbase_spread_stability_scan.json"
OUTPUT_MD = REPORTS / "coinbase_maker_candidates.md"
OUTPUT_JSON = REPORTS / "coinbase_maker_candidates.json"

# Known products to exclude (spread collapsed, tested losers)
EXCLUDE = {"BAL-USD", "ALEPH-USD", "ETH-USD", "BTC-USD"}

# Minimum thresholds
MIN_SPREAD_BPS = 50.0
MIN_VOLUME_24H = 1_000_000.0
MIN_PRICE = 0.001


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    print("=" * 80)
    print("COINBASE MAKER CANDIDATE LIST GENERATOR")
    print("=" * 80)

    # Load scan results
    if not SCAN_JSON.exists():
        print(f"[ERROR] Scan JSON not found at {SCAN_JSON}")
        print("Run coinbase_spread_stability_scan.py first.")
        return 1

    data = json.loads(SCAN_JSON.read_text(encoding="utf-8"))
    
    # Combine known board spread products + wide spread unknowns
    all_products = data.get("known_spread_products", []) + data.get("wide_spread_unknown_products", [])
    
    # Filter
    candidates = []
    for p in all_products:
        pid = p.get("product_id", "")
        if pid in EXCLUDE:
            print(f"[EXCLUDED] {pid} (known bad)")
            continue
        
        spread = p.get("live_spread_bps", 0)
        volume = p.get("volume_24h", 0)
        price = p.get("price", 0)
        
        if spread < MIN_SPREAD_BPS:
            continue
        if volume < MIN_VOLUME_24H:
            print(f"[LOW VOL] {pid}: ${volume:,.0f} (need ${MIN_VOLUME_24H:,.0f})")
            continue
        if price < MIN_PRICE:
            print(f"[DUST] {pid}: ${price:.6f} (need ${MIN_PRICE:.3f})")
            continue
        
        candidates.append({
            "product_id": pid,
            "live_spread_bps": spread,
            "volume_24h": volume,
            "price": price,
            "passes_ratio50": p.get("passes_ratio50", False),
            "board_spread_bps": p.get("board_spread_bps", 0),
        })

    # Sort by spread descending (widest first — most maker-friendly)
    candidates.sort(key=lambda x: x["live_spread_bps"], reverse=True)

    # Output
    print(f"\n{'='*80}")
    print(f"COINBASE MAKER CANDIDATES: {len(candidates)} products")
    print(f"{'='*80}")
    print(f"{'Product':<15} {'Spread bps':>12} {'Volume 24h':>14} {'Price':>10} {'Ratio50?':>10}")
    print("-" * 70)
    for c in candidates:
        print(f"{c['product_id']:<15} {c['live_spread_bps']:>12.2f} ${c['volume_24h']:>13,.0f} "
              f"${c['price']:>9.6f} {'✅' if c['passes_ratio50'] else '⏳':>10}")

    # Generate markdown
    md_lines = [
        "# Coinbase Maker Candidate List",
        f"**Generated:** {utc_now_iso()}",
        f"**Source:** `coinbase_spread_stability_scan.json`",
        "",
        "## Selection Criteria",
        "",
        f"- Live spread >= {MIN_SPREAD_BPS}bps (wide enough for maker profit)",
        f"- 24h volume >= ${MIN_VOLUME_24H:,.0f} (sufficient liquidity)",
        f"- Price >= ${MIN_PRICE:.3f} (not dust)",
        f"- Excluded: {', '.join(sorted(EXCLUDE))} (known bad products)",
        "",
        "## Candidates (sorted by spread, widest first)",
        "",
        f"**{len(candidates)} candidates**",
        "",
        "| Product | Spread (bps) | Volume 24h | Price | Passes Ratio50? |",
        "|---------|-------------|------------|-------|----------------|",
    ]

    for c in candidates:
        ratio50_status = "✅ Yes" if c["passes_ratio50"] else "⏳ Unknown (no board spread)"
        md_lines.append(
            f"| {c['product_id']} | {c['live_spread_bps']:.2f} | ${c['volume_24h']:,.0f} | "
            f"${c['price']:.6f} | {ratio50_status} |"
        )

    md_lines.extend([
        "",
        "## How to Use",
        "",
        "1. Add these product IDs to the Kraken maker runner's `--product-ids` or config",
        "2. Use same admission gate: spread>=50bps, MER>=2.5, live/board ratio>=0.50",
        "3. The ratio50 gate will automatically filter products with spread-collapse risk",
        "",
        "## Priority Tiers",
        "",
        f"- **Tier 1 (spread > 200bps):** {len([c for c in candidates if c['live_spread_bps'] > 200])} products",
        f"- **Tier 2 (spread 100-200bps):** {len([c for c in candidates if 100 < c['live_spread_bps'] <= 200])} products",
        f"- **Tier 3 (spread 50-100bps):** {len([c for c in candidates if 50 <= c['live_spread_bps'] <= 100])} products",
        "",
        "## Caveats",
        "",
        "- Spread snapshot is single-point-in-time — may not represent typical state",
        "- Volume data is 24h trailing — may be seasonal",
        "- Maker fees on Coinbase may differ from Kraken — verify before deploying",
        "- These products have NOT been backtested with maker geometry yet",
        "",
    ])

    md_content = "\n".join(md_lines)

    # Save
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md_content, encoding="utf-8")
    print(f"\n[INFO] Saved markdown to {OUTPUT_MD}")

    result = {
        "generated_at": utc_now_iso(),
        "total_candidates": len(candidates),
        "tier_1_count": len([c for c in candidates if c["live_spread_bps"] > 200]),
        "tier_2_count": len([c for c in candidates if 100 < c["live_spread_bps"] <= 200]),
        "tier_3_count": len([c for c in candidates if 50 <= c["live_spread_bps"] <= 100]),
        "candidates": candidates,
    }
    OUTPUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[INFO] Saved JSON to {OUTPUT_JSON}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
