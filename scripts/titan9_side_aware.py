#!/usr/bin/env python3
"""
Titan 9.1 — Side-Aware Crossing Detector for Kraken Spot.

Detects WHEN the order book is being actively crossed by takers,
and in which DIRECTION. This is the key insight from @codex-live-selector-4:

"Select buy entries only when ask is being crossed/lowered, not merely
when bid momentum is hot."

Tracks:
1. Ask price movement (lowering = takers eating asks, book adjusting down)
2. Bid price movement (lowering = takers eating bids, book adjusting down)
3. Ask depth changes (shrinking = takers consuming ask liquidity)
4. Bid depth changes (shrinking = takers consuming bid liquidity)
5. Last trade direction (was the last trade a buy or sell?)

Signal logic:
- BUY entry when: ask is being crossed/lowered + ask depth shrinking
  (Takers are eating asks, our post-only BUY at midpoint gets filled by next taker)
- SELL entry when: bid is being crossed/lowered + bid depth shrinking
  (Takers are eating bids, our post-only SELL at midpoint gets filled by next taker)

Usage:
    python scripts/titan9_side_aware.py --products IAG-USD,MEZO-USD,BLESS-USD,ES-USD,BMB-USD --samples 20 --interval 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, to_float
from crossing_pressure_scanner import kraken_name, compute_spread_bps


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def analyze_book_movement(snapshots: list[dict]) -> dict:
    """
    Analyze a sequence of book snapshots to detect side-aware crossing.

    Returns directional signals:
    - ask_crossing: True if ask price is being lowered (takers eating asks)
    - bid_crossing: True if bid price is being lowered (takers eating bids)
    - ask_depth_shrinking: True if ask L1 depth is decreasing
    - bid_depth_shrinking: True if bid L1 depth is decreasing
    - net_direction: "buy" (takers buying) or "sell" (takers selling) or "neutral"
    """
    if len(snapshots) < 2:
        return {
            "ask_crossing": False,
            "bid_crossing": False,
            "ask_depth_shrinking": False,
            "bid_depth_shrinking": False,
            "net_direction": "neutral",
            "confidence": 0.0,
        }

    # Price movement analysis
    asks = [s["ask"] for s in snapshots if s.get("ask", 0) > 0]
    bids = [s["bid"] for s in snapshots if s.get("bid", 0) > 0]

    # Ask crossing: ask price going DOWN (takers eating asks, best ask lowering)
    ask_crossing = False
    if len(asks) >= 2:
        ask_changes = [asks[i] - asks[i+1] for i in range(len(asks)-1)]
        # If ask is mostly going down (positive changes)
        ask_down_count = sum(1 for c in ask_changes if c > 0)
        ask_crossing = ask_down_count > len(ask_changes) * 0.5

    # Bid crossing: bid price going DOWN (takers eating bids, best bid lowering)
    bid_crossing = False
    if len(bids) >= 2:
        bid_changes = [bids[i] - bids[i+1] for i in range(len(bids)-1)]
        # If bid is mostly going down (positive changes)
        bid_down_count = sum(1 for c in bid_changes if c > 0)
        bid_crossing = bid_down_count > len(bid_changes) * 0.5

    # Depth analysis
    ask_depths = [s.get("ask_depth_usd", 0) for s in snapshots]
    bid_depths = [s.get("bid_depth_usd", 0) for s in snapshots]

    ask_depth_shrinking = False
    if len(ask_depths) >= 2:
        # Compare first half avg to last half avg
        mid = len(ask_depths) // 2
        first_half = sum(ask_depths[:mid]) / max(mid, 1)
        second_half = sum(ask_depths[mid:]) / max(len(ask_depths) - mid, 1)
        ask_depth_shrinking = second_half < first_half * 0.8  # 20%+ shrink

    bid_depth_shrinking = False
    if len(bid_depths) >= 2:
        mid = len(bid_depths) // 2
        first_half = sum(bid_depths[:mid]) / max(mid, 1)
        second_half = sum(bid_depths[mid:]) / max(len(bid_depths) - mid, 1)
        bid_depth_shrinking = second_half < first_half * 0.8

    # Net direction: if ask is crossing + ask depth shrinking = takers BUYING
    # If bid is crossing + bid depth shrinking = takers SELLING
    buy_score = (1.0 if ask_crossing else 0.0) + (1.0 if ask_depth_shrinking else 0.0)
    sell_score = (1.0 if bid_crossing else 0.0) + (1.0 if bid_depth_shrinking else 0.0)

    # Also factor in last trade direction
    last_prices = [s["last"] for s in snapshots if s.get("last", 0) > 0]
    if len(last_prices) >= 2:
        last_mid = (snapshots[-1]["bid"] + snapshots[-1]["ask"]) / 2
        if last_mid > 0:
            last_trade_offset = (last_prices[-1] - last_mid) / last_mid * 10000
            if last_trade_offset > 10:
                buy_score += 0.5  # Last trade above midpoint = taker buy
            elif last_trade_offset < -10:
                sell_score += 0.5  # Last trade below midpoint = taker sell

    if buy_score > sell_score and buy_score >= 1.0:
        net_direction = "buy"
        confidence = min(1.0, buy_score / 3.0)
    elif sell_score > buy_score and sell_score >= 1.0:
        net_direction = "sell"
        confidence = min(1.0, sell_score / 3.0)
    else:
        net_direction = "neutral"
        confidence = 0.0

    return {
        "ask_crossing": ask_crossing,
        "bid_crossing": bid_crossing,
        "ask_depth_shrinking": ask_depth_shrinking,
        "bid_depth_shrinking": bid_depth_shrinking,
        "net_direction": net_direction,
        "buy_score": round(buy_score, 2),
        "sell_score": round(sell_score, 2),
        "confidence": round(confidence, 2),
    }


def scan_product_side_aware(client: KrakenSpotClient, product: str,
                             samples: int = 10, interval: float = 3.0) -> dict:
    """
    Take rapid snapshots of a product's book and detect side-aware crossing.
    """
    kc = kraken_name(product)
    snapshots = []

    for i in range(samples):
        try:
            depth_data = client.depth(kc, count=20)
            if kc not in depth_data:
                for key in depth_data:
                    if key.upper() == kc:
                        depth_data = {kc: depth_data[key]}
                        break
            if kc not in depth_data:
                time.sleep(interval)
                continue

            book = depth_data[kc]
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            ticker_data = client.ticker([kc])
            if kc not in ticker_data:
                for key in ticker_data:
                    if key.upper() == kc:
                        ticker_data = {kc: ticker_data[key]}
                        break
            t = ticker_data.get(kc, {})
            bid = to_float((t.get("b") or [None])[0])
            ask = to_float((t.get("a") or [None])[0])
            last = to_float((t.get("c") or [None])[0])

            if bid <= 0 and bids:
                bid = to_float(bids[0][0])
            if ask <= 0 and asks:
                ask = to_float(asks[0][0])

            if bid <= 0 or ask <= 0:
                time.sleep(interval)
                continue

            bid_depth_usd = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
            ask_depth_usd = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0
            spread = compute_spread_bps(bid, ask)

            snapshots.append({
                "ts": utc_now_iso(),
                "bid": bid,
                "ask": ask,
                "last": last,
                "spread_bps": round(spread, 2),
                "bid_depth_usd": round(bid_depth_usd, 2),
                "ask_depth_usd": round(ask_depth_usd, 2),
            })

            if i < samples - 1:
                time.sleep(interval)

        except Exception:
            time.sleep(interval)

    if len(snapshots) < 2:
        return {"product": product, "error": "insufficient_snapshots",
                "snapshot_count": len(snapshots)}

    analysis = analyze_book_movement(snapshots)

    return {
        "product": product,
        "snapshot_count": len(snapshots),
        "duration_seconds": len(snapshots) * interval,
        "first_bid": snapshots[0]["bid"],
        "last_bid": snapshots[-1]["bid"],
        "first_ask": snapshots[0]["ask"],
        "last_ask": snapshots[-1]["ask"],
        "first_spread_bps": snapshots[0]["spread_bps"],
        "last_spread_bps": snapshots[-1]["spread_bps"],
        "bid_depth_usd_range": f"${min(s['bid_depth_usd'] for s in snapshots):.0f}-${max(s['bid_depth_usd'] for s in snapshots):.0f}",
        "ask_depth_usd_range": f"${min(s['ask_depth_usd'] for s in snapshots):.0f}-${max(s['ask_depth_usd'] for s in snapshots):.0f}",
        **analysis,
    }


def main():
    parser = argparse.ArgumentParser(description="Titan 9.1 Side-Aware Crossing Detector")
    parser.add_argument("--products", default="IAG-USD,MEZO-USD,BLESS-USD,ES-USD,BMB-USD,BILLY-USD,TRAC-USD,WARD-USD")
    parser.add_argument("--samples", type=int, default=15, help="Snapshots per product")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between snapshots")
    parser.add_argument("--json-path", type=Path, default=REPORTS / "titan9_side_aware.json")
    parser.add_argument("--md-path", type=Path, default=REPORTS / "titan9_side_aware.md")
    args = parser.parse_args()

    client = KrakenSpotClient()
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"🔬 Titan 9.1 Side-Aware Scanner: {len(products)} products, {args.samples} samples, {args.interval}s interval")
    print()

    results = []
    for product in products:
        print(f"  Scanning {product}...")
        result = scan_product_side_aware(client, product, samples=args.samples, interval=args.interval)
        direction = result.get("net_direction", "?")
        confidence = result.get("confidence", 0)
        emoji = "⬆️ BUY" if direction == "buy" else "⬇️ SELL" if direction == "sell" else "➡️ NEUTRAL"
        print(f"    {emoji} (confidence: {confidence:.2f})")
        if result.get("error"):
            print(f"    Error: {result['error']}")
        results.append(result)

    # Sort by confidence descending
    results.sort(key=lambda r: r.get("confidence", 0), reverse=True)

    # JSON output
    output = {
        "generated": utc_now_iso(),
        "results": results,
    }
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    # Markdown output
    lines = [
        "# Titan 9.1 Side-Aware Crossing Analysis",
        f"- Generated: `{utc_now_iso()}`",
        "",
        "## Ranked by Confidence",
        "",
        "| Product | Direction | Confidence | Ask Crossing | Bid Crossing | Ask Depth Shrinking | Bid Depth Shrinking | Spread bps | Bid Depth | Ask Depth |",
        "| --- | --- | ---: | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['product']} | ERROR | - | - | - | - | - | - | - | - |")
            continue
        emoji = "⬆️" if r["net_direction"] == "buy" else "⬇️" if r["net_direction"] == "sell" else "➡️"
        lines.append(
            f"| {r['product']} "
            f"| {emoji} {r['net_direction']} "
            f"| {r['confidence']:.2f} "
            f"| {'✅' if r['ask_crossing'] else '❌'} "
            f"| {'✅' if r['bid_crossing'] else '❌'} "
            f"| {'✅' if r['ask_depth_shrinking'] else '❌'} "
            f"| {'✅' if r['bid_depth_shrinking'] else '❌'} "
            f"| {r.get('last_spread_bps', '?')} "
            f"| {r.get('bid_depth_usd_range', '?')} "
            f"| {r.get('ask_depth_usd_range', '?')} |"
        )

    lines.append("")
    lines.append("## Active Signals")
    lines.append("")
    signals = [r for r in results if r.get("net_direction") in ("buy", "sell") and r.get("confidence", 0) >= 0.5]
    if signals:
        for r in signals:
            emoji = "⬆️ BUY" if r["net_direction"] == "buy" else "⬇️ SELL"
            lines.append(f"### {emoji} {r['product']} (confidence: {r['confidence']:.2f})")
            lines.append(f"- Spread: {r.get('last_spread_bps', '?')}bps")
            lines.append(f"- Ask crossing: {'Yes' if r['ask_crossing'] else 'No'}")
            lines.append(f"- Bid crossing: {'Yes' if r['bid_crossing'] else 'No'}")
            lines.append(f"- Ask depth shrinking: {'Yes' if r['ask_depth_shrinking'] else 'No'}")
            lines.append(f"- Bid depth shrinking: {'Yes' if r['bid_depth_shrinking'] else 'No'}")
            if r["net_direction"] == "buy" and r["ask_crossing"]:
                lines.append(f"- **Action**: Place BUY order at 0.50 offset — takers are eating asks")
            elif r["net_direction"] == "sell" and r["bid_crossing"]:
                lines.append(f"- **Action**: Place SELL order at 0.50 offset — takers are eating bids")
            lines.append("")
    else:
        lines.append("No active side-aware signals above 0.5 confidence. Books are in equilibrium — no taker pressure detected.")
        lines.append("")
        lines.append("## Interpretation")
        lines.append("")
        lines.append("- **BUY signal**: Ask is being crossed/lowered + ask depth shrinking → takers buying → our post-only BUY at midpoint gets filled")
        lines.append("- **SELL signal**: Bid is being crossed/lowered + bid depth shrinking → takers selling → our post-only SELL at midpoint gets filled")
        lines.append("- **NEUTRAL**: Book is in equilibrium — no directional taker pressure")

    with open(args.md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print()
    print(f"✅ Results saved to {args.json_path} and {args.md_path}")

    buy_signals = [r for r in results if r.get("net_direction") == "buy"]
    sell_signals = [r for r in results if r.get("net_direction") == "sell"]
    neutral = [r for r in results if r.get("net_direction") == "neutral"]
    print(f"\n📊 Summary: {len(buy_signals)} BUY signals, {len(sell_signals)} SELL signals, {len(neutral)} NEUTRAL")
    for r in results[:3]:
        if "error" not in r:
            emoji = "⬆️" if r["net_direction"] == "buy" else "⬇️" if r["net_direction"] == "sell" else "➡️"
            print(f"  {emoji} {r['product']}: {r['net_direction']} (conf={r['confidence']:.2f})")


if __name__ == "__main__":
    main()
