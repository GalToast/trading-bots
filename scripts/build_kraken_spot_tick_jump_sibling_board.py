#!/usr/bin/env python3
"""
Kraken Tick-Jump Sibling Board
Scans the Kraken universe for MOG-like geometry (low price, high tick-to-fee ratio).
Targeting the "Mad Scientist" path of geometric alpha.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "scripts"))

from kraken_spot_client import KrakenSpotClient

REPORTS = ROOT / "reports"
JSON_PATH = REPORTS / "kraken_spot_tick_jump_sibling_board.json"
MD_PATH = REPORTS / "kraken_spot_tick_jump_sibling_board.md"

KRAKEN_TAKER_FEE_BPS = 40.0
ROUND_TRIP_HURDLE_BPS = KRAKEN_TAKER_FEE_BPS * 2.0

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def build_board():
    client = KrakenSpotClient()
    print("Fetching Kraken Assets and Tickers...")
    
    pairs = client.asset_pairs()
    if not pairs:
        print("Error: No asset pairs returned.")
        return
        
    pair_names = [p for p in pairs.keys() if "USD" in p]
    tickers = client.ticker(pair_names)
    if not tickers:
        print("Error: No tickers returned.")
        return
    
    rows = []
    for pair_name in pair_names:
        info = pairs.get(pair_name, {})
        ticker = tickers.get(pair_name)
        if not ticker: continue
        
        price = float(ticker["c"][0])
        if price == 0: continue
        
        ask = float(ticker["a"][0])
        bid = float(ticker["b"][0])
        spread_bps = ((ask - bid) / price) * 10000.0
        
        pair_decimals = int(info.get("pair_decimals", 0))
        tick_size = 10 ** (-pair_decimals)
        tick_move_pct = (tick_size / price) * 100.0
        tick_move_bps = tick_move_pct * 100.0
        
        # MOG Metric: How many round-trip fees does ONE tick jump cover?
        ratio = tick_move_bps / ROUND_TRIP_HURDLE_BPS
        
        # Filter for "Geometry Candidates"
        if ratio > 0.5: # Covers at least half a round-trip in one tick
            rows.append({
                "product_id": pair_name,
                "price": price,
                "spread_bps": round(spread_bps, 2),
                "tick_bps": round(tick_move_bps, 2),
                "fee_hurdle_bps": ROUND_TRIP_HURDLE_BPS,
                "tick_fee_ratio": round(ratio, 2),
                "vol_24h": float(ticker["v"][1]) * price # Base volume * price
            })
            
    rows.sort(key=lambda x: x["tick_fee_ratio"], reverse=True)
    
    payload = {
        "generated_at": utc_now_iso(),
        "rows": rows
    }
    
    # Save JSON
    JSON_PATH.write_text(json.dumps(payload, indent=2))
    
    # Save Markdown
    lines = [
        "# Kraken Tick-Jump Sibling Board",
        "",
        "## Geometric Alpha Summary",
        f"- **Round-Trip Fee Hurdle**: {ROUND_TRIP_HURDLE_BPS} bps",
        "- **MOG Regime**: Products where 1 tick jump > Fee Hurdle (Ratio > 1.0)",
        "",
        "| Rank | Product | Price | Spread bps | Tick bps | Ratio | 24h Vol |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: |"
    ]
    
    for idx, r in enumerate(rows[:30]):
        lines.append(
            f"| {idx+1} | {r['product_id']} | {r['price']:.8f} | {r['spread_bps']:.2f} | {r['tick_bps']:.2f} | {r['tick_fee_ratio']:.2f}x | ${r['vol_24h']:,.0f} |"
        )
        
    MD_PATH.write_text("\n".join(lines))
    print(f"DONE! Saved {len(rows)} candidates to {MD_PATH}")

if __name__ == "__main__":
    build_board()
