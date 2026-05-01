#!/usr/bin/env python3
"""Book Dominance Diagnostic.

Audits the Kraken orderbook depth to see how our proposed $15 order
compares to the resting liquidity at various offsets.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from kraken_spot_client import KrakenSpotClient

def audit_dominance(product_id: str, order_size_usd: float, rest_pair_override: str | None = None):
    client = KrakenSpotClient()
    rest_pair = rest_pair_override or product_id.replace("-", "").upper()
    if not rest_pair_override:
        if rest_pair == "XBTUSD": rest_pair = "XXBTZUSD"
        if rest_pair == "ETCETH": rest_pair = "XETCXETH"
        if rest_pair == "ETHXBT": rest_pair = "XETHXXBT"
        if rest_pair == "XRPXBT": rest_pair = "XXRPXXBT"
    
    try:
        depth = client.depth(rest_pair, count=10)
    except Exception as e:
        print(f"Error fetching depth: {e}")
        return

    bids = depth.get(rest_pair, {}).get("bids", [])
    asks = depth.get(rest_pair, {}).get("asks", [])
    
    if not bids or not asks:
        print(f"No depth data for {product_id}")
        return

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    spread_bps = ((best_ask - best_bid) / best_bid) * 10000.0
    
    print(f"--- {product_id} Book Audit ---")
    print(f"Best Bid: {best_bid:.8f}")
    print(f"Best Ask: {best_ask:.8f}")
    print(f"Spread:   {spread_bps:.2f} bps")
    print(f"\nOrder Size: ${order_size_usd:.2f}")
    
    # Audit Bid Side Dominance (for buy entries)
    print(f"\n[BID SIDE DOMINANCE]")
    for i, (price, vol, ts) in enumerate(bids[:5]):
        price = float(price)
        vol = float(vol)
        level_usd = price * vol
        dominance = (order_size_usd / (level_usd + order_size_usd)) * 100.0
        print(f"L{i+1} @ {price:.8f} | Depth: ${level_usd:8.2f} | Dominance: {dominance:5.1f}%")

    # Midpoint Calculation
    midpoint = (best_bid + best_ask) / 2.0
    print(f"\nMidpoint: {midpoint:.8f}")
    print(f"Note: Midpoint is EMPTY depth. Our ${order_size_usd:.2f} would be 100% of that level.")

if __name__ == "__main__":
    product = sys.argv[1] if len(sys.argv) > 1 else "WEN-USD"
    size = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
    audit_dominance(product, size)
