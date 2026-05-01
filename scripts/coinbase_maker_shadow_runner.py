#!/usr/bin/env python3
"""Coinbase Spot Maker Shadow Runner — Launch Config Generator.

Takes the Coinbase maker execution reality board findings and produces
a ready-to-launch shadow lane config for Coinbase products.

Uses the SAME maker geometry as Kraken but with Coinbase-specific fees:
- Maker fee: 60bps per side (NOT 25bps like Kraken)
- Taker fee: 120bps per side (NOT 25bps like Kraken)
- Round trip: 180bps vs Kraken's 50bps

Products with POSITIVE edge at current fees (from reality board):
1. SPX-USD: 15.58bps spread, edge +0.45%
2. FLOCK-USD: 14.46bps spread, edge +0.08%
3. ZAMA-USD: 12.59bps spread, edge +0.43%

Usage:
  python scripts/coinbase_maker_shadow_runner.py --products SPX-USD FLOCK-USD ZAMA-USD
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"

# Coinbase fee structure (confirmed from reality board)
COINBASE_MAKER_FEE_BPS = 60.0
COINBASE_TAKER_FEE_BPS = 120.0

# Products with positive edge at current fees
PROVEN_PRODUCTS = ["SPX-USD", "FLOCK-USD", "ZAMA-USD"]

# Products that become viable with 0bps maker fees
ZERO_MAKER_VIABLE = [
    "ZRX-USD", "FUN1-USD", "NOM-USD", "BREV-USD", "MET-USD",
    "ZETA-USD", "RECALL-USD", "TIA-USD", "TOSHI-USD",
    "LIGHTER-USD", "SAPIEN-USD", "OPG-USD",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    print("=" * 80)
    print("COINBASE SPOT MAKER SHADOW RUNNER — Launch Config Generator")
    print("=" * 80)

    state = {
        "lane": "coinbase_maker_shadow",
        "cash": 100.0,
        "initial_cash": 100.0,
        "closes": 0,
        "wins": 0,
        "losses": 0,
        "net_pct": 0.0,
        "gross_pct": 0.0,
        "ghost_marks": 0,
        "total_gross": 0.0,
        "total_net": 0.0,
        "positions": [],
        "product_stats": {},
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "fee_config": {
            "maker_fee_bps": COINBASE_MAKER_FEE_BPS,
            "taker_fee_bps": COINBASE_TAKER_FEE_BPS,
            "round_trip_bps": COINBASE_MAKER_FEE_BPS + COINBASE_TAKER_FEE_BPS,
        },
        "product_candidates": PROVEN_PRODUCTS,
        "zero_maker_viable": ZERO_MAKER_VIABLE,
    }

    events_path = REPORTS / "coinbase_maker_shadow_events.jsonl"
    state_path = REPORTS / "coinbase_maker_shadow_state.json"

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    events_path.write_text("", encoding="utf-8")

    print(f"\n{'='*80}")
    print("COINBASE MAKER SHADOW — READY TO LAUNCH")
    print(f"{'='*80}")
    print(f"\nFee structure:")
    print(f"  Maker: {COINBASE_MAKER_FEE_BPS}bps per side")
    print(f"  Taker: {COINBASE_TAKER_FEE_BPS}bps per side")
    print(f"  Round trip: {COINBASE_MAKER_FEE_BPS + COINBASE_TAKER_FEE_BPS}bps")
    print(f"\nProducts with positive edge:")
    for p in PROVEN_PRODUCTS:
        print(f"  ✅ {p}")
    print(f"\nProducts viable with 0bps maker:")
    for p in ZERO_MAKER_VIABLE:
        print(f"  ⏳ {p}")

    print(f"\n{'='*80}")
    print("LAUNCH COMMAND (when team approves):")
    print(f"{'='*80}")
    print(f"""
python scripts/live_kraken_spot_frontier_maker_machinegun_shadow.py \\
  --state-path {state_path} \\
  --events-path {events_path} \\
  --max-quote-usd 8.0 \\
  --maker-fee-bps {COINBASE_MAKER_FEE_BPS} \\
  --min-live-spread-bps 10 \\
  --min-spread-bps 50 \\
  --systemic-mer-threshold 2.5 \\
  --product-ids {" ".join(PROVEN_PRODUCTS)} \\
  # ... API keys, etc.
""")

    print(f"\nState saved to: {state_path}")
    print(f"Events path: {events_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
