#!/usr/bin/env python3
"""
Crossing-Pressure Trigger Tape Scanner for Kraken Spot.

Builds a real-time crossing-pressure score for top candidates that need
validate-only evidence. Answers "WHEN will it fill?" not "will it fill?"

Crossing pressure = likelihood that takers will cross our post-only order
in the next 60-120 seconds. Computed from:
1. Order book imbalance (bid_depth vs ask_depth at L1-L3)
2. Spread vs baseline (is spread abnormally wide = taker opportunity?)
3. Trade velocity spike (are trades accelerating?)
4. Taker direction bias (are takers buying or selling aggressively?)

Usage:
    python scripts/crossing_pressure_scanner.py --products GWEI-USD,BILLY-USD,AIN-USD --samples 20 --interval 5
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, normalize_pair_name, parse_pair, to_float


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def kraken_name(product: str) -> str:
    """Convert e.g. GWEI-USD to GWEIUSD."""
    return product.replace("-", "").upper()


def compute_spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return 0.0
    return (ask - bid) / mid * 10000.0


def compute_book_imbalance(bids: list, asks: list, levels: int = 3) -> float:
    """
    Returns -1.0 (ask-heavy) to +1.0 (bid-heavy).
    Positive = more bid depth = takers more likely to BUY = our sell order fills.
    Negative = more ask depth = takers more likely to SELL = our buy order fills.
    """
    bid_vol = sum(to_float(row[1]) for row in bids[:levels])
    ask_vol = sum(to_float(row[1]) for row in asks[:levels])
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def compute_midpoint_depth_usd(bids: list, asks: list, mid: float) -> dict:
    """Compute USD depth at L1 for both sides."""
    bid_depth_usd = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0.0
    ask_depth_usd = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0.0
    return {
        "bid_l1_usd": round(bid_depth_usd, 2),
        "ask_l1_usd": round(ask_depth_usd, 2),
        "total_l1_usd": round(bid_depth_usd + ask_depth_usd, 2),
    }


def compute_crossing_pressure(
    spread_bps: float,
    baseline_spread_bps: float,
    imbalance: float,
    velocity_ratio: float,
    taker_direction: float,
) -> dict:
    """
    Compute crossing-pressure score from multiple signals.

    Returns:
        score: 0.0-1.0 overall crossing pressure
        fire_signal: True if score exceeds threshold
        breakdown: per-component scores
    """
    # Component 1: Spread opportunity (0-0.3)
    # Wider spread than baseline = more room for taker to cross
    spread_opportunity = min(1.0, max(0.0, (spread_bps - baseline_spread_bps) / max(baseline_spread_bps, 10.0))) * 0.3

    # Component 2: Book imbalance (0-0.25)
    # Strong imbalance = takers more likely to cross the thin side
    imbalance_score = abs(imbalance) * 0.25

    # Component 3: Velocity spike (0-0.25)
    # Accelerating trades = more crossing activity
    velocity_score = min(1.0, max(0.0, (velocity_ratio - 1.0) / 2.0)) * 0.25

    # Component 4: Taker direction bias (0-0.2)
    # Strong directional taker flow = our resting order on the other side is valuable
    direction_score = abs(taker_direction) * 0.2

    total = spread_opportunity + imbalance_score + velocity_score + direction_score

    return {
        "score": round(total, 4),
        "max_score": 1.0,
        "breakdown": {
            "spread_opportunity": round(spread_opportunity, 4),
            "book_imbalance": round(imbalance_score, 4),
            "velocity_spike": round(velocity_score, 4),
            "taker_direction": round(direction_score, 4),
        },
    }


def scan_product(client: KrakenSpotClient, product: str, samples: int = 10, interval: float = 3.0) -> dict:
    """
    Take multiple snapshots of a product's order book to compute crossing pressure.
    """
    kc = kraken_name(product)
    snapshots = []
    spread_history = []

    for i in range(samples):
        try:
            # Get depth
            depth_data = client.depth(kc, count=20)
            if kc not in depth_data:
                # Try alt key
                for key in depth_data:
                    if key.upper() == kc or normalize_pair_name(key).replace("/", "") == kc:
                        depth_data = {kc: depth_data[key]}
                        break

            if kc not in depth_data:
                print(f"  WARN: Could not get depth for {product} (key={kc})")
                time.sleep(interval)
                continue

            book = depth_data[kc]
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            # Get ticker for last trade info
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
            vol_today = to_float((t.get("v") or [None, None])[1])

            # If bid/ask not from ticker, compute from book
            if bid <= 0 and bids:
                bid = to_float(bids[0][0])
            if ask <= 0 and asks:
                ask = to_float(asks[0][0])

            if bid <= 0 or ask <= 0:
                time.sleep(interval)
                continue

            spread = compute_spread_bps(bid, ask)
            imbalance = compute_book_imbalance(bids, asks, levels=3)
            l1_depth = compute_midpoint_depth_usd(bids, asks, (bid + ask) / 2)

            snapshot = {
                "ts": utc_now_iso(),
                "bid": bid,
                "ask": ask,
                "last": last,
                "spread_bps": round(spread, 2),
                "imbalance": round(imbalance, 4),
                "bid_l1_usd": l1_depth["bid_l1_usd"],
                "ask_l1_usd": l1_depth["ask_l1_usd"],
                "vol_today_usd": round(vol_today, 2),
            }
            snapshots.append(snapshot)
            spread_history.append(spread)

            if i < samples - 1:
                time.sleep(interval)

        except Exception as e:
            print(f"  ERROR on sample {i+1} for {product}: {e}")
            time.sleep(interval)

    if not snapshots:
        return {"product": product, "error": "no_snapshots", "samples": []}

    # Compute derived metrics
    baseline_spread = sum(spread_history) / len(spread_history)
    current_spread = spread_history[-1] if spread_history else 0
    spread_std = (sum((s - baseline_spread) ** 2 for s in spread_history) / max(len(spread_history) - 1, 1)) ** 0.5

    # Current snapshot
    cur = snapshots[-1]

    # Velocity ratio: approximate from volume acceleration (using today's vol as proxy)
    # In a real system we'd track per-minute volume. Here we use spread velocity as proxy.
    if len(spread_history) >= 3:
        recent_spread_change = abs(spread_history[-1] - spread_history[-3])
        velocity_ratio = 1.0 + (recent_spread_change / max(baseline_spread, 10.0))
    else:
        velocity_ratio = 1.0

    # Taker direction: approximate from last price position relative to spread
    # If last > midpoint, takers are buying (pushing price up)
    mid = (cur["bid"] + cur["ask"]) / 2
    if mid > 0 and cur["last"] > 0:
        taker_direction = (cur["last"] - mid) / mid * 10000.0  # bps from midpoint
        taker_direction_norm = min(1.0, abs(taker_direction) / 50.0)  # normalize to 0-1
        taker_sign = 1.0 if taker_direction > 0 else -1.0
    else:
        taker_direction_norm = 0.0
        taker_sign = 0.0

    # Crossing pressure score
    pressure = compute_crossing_pressure(
        spread_bps=current_spread,
        baseline_spread_bps=baseline_spread,
        imbalance=cur["imbalance"],
        velocity_ratio=velocity_ratio,
        taker_direction=taker_sign * taker_direction_norm,
    )

    return {
        "product": product,
        "samples": len(snapshots),
        "snapshot_ts": cur["ts"],
        "bid": cur["bid"],
        "ask": cur["ask"],
        "last": cur["last"],
        "current_spread_bps": round(current_spread, 2),
        "baseline_spread_bps": round(baseline_spread, 2),
        "spread_std_bps": round(spread_std, 2),
        "spread_history": [round(s, 2) for s in spread_history],
        "book_imbalance": cur["imbalance"],
        "bid_l1_usd": cur["bid_l1_usd"],
        "ask_l1_usd": cur["ask_l1_usd"],
        "velocity_ratio": round(velocity_ratio, 3),
        "taker_direction_bps": round(taker_direction, 2),
        "pressure_score": pressure["score"],
        "pressure_breakdown": pressure["breakdown"],
        "raw_snapshots": snapshots[-3:],  # last 3 only
    }


def main():
    parser = argparse.ArgumentParser(description="Crossing-Pressure Trigger Tape Scanner")
    parser.add_argument("--products", default="GWEI-USD,BILLY-USD,AIN-USD,TRAC-USD,WARD-USD,CHEX-USD,UP-USD,BICO-USD,SWEAT-USD,FIGHT-USD",
                        help="Comma-separated products to scan")
    parser.add_argument("--samples", type=int, default=5, help="Number of book snapshots per product")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between snapshots")
    parser.add_argument("--fire-threshold", type=float, default=0.35, help="Pressure score threshold to fire")
    parser.add_argument("--json-path", type=Path, default=REPORTS / "crossing_pressure_tape.json")
    parser.add_argument("--md-path", type=Path, default=REPORTS / "crossing_pressure_tape.md")
    args = parser.parse_args()

    client = KrakenSpotClient()
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"🔬 Crossing-Pressure Scanner: {len(products)} products, {args.samples} samples each, {args.interval}s interval")
    print(f"   Fire threshold: {args.fire_threshold}")
    print()

    results = []
    for product in products:
        print(f"  Scanning {product}...")
        result = scan_product(client, product, samples=args.samples, interval=args.interval)
        score = result.get("pressure_score", 0)
        status = "🔥 FIRE" if score >= args.fire_threshold else "⏳ WAIT"
        print(f"    Score: {score:.4f} [{status}]")
        results.append(result)

    # Sort by pressure score descending
    results.sort(key=lambda r: r.get("pressure_score", 0), reverse=True)

    # JSON output
    output = {
        "generated": utc_now_iso(),
        "fire_threshold": args.fire_threshold,
        "results": results,
    }
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_path, "w") as f:
        json.dump(output, f, indent=2)

    # Markdown output
    lines = [
        "# Crossing-Pressure Trigger Tape",
        f"- Generated: `{utc_now_iso()}`",
        f"- Fire threshold: `{args.fire_threshold}`",
        "",
        "## Ranked by Pressure Score",
        "",
        "| Product | Pressure Score | Status | Spread bps | Baseline bps | Imbalance | L1 Depth USD | Velocity | Taker Dir bps |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['product']} | ERROR | {r['error']} | - | - | - | - | - | - |")
            continue
        status = "🔥 FIRE" if r["pressure_score"] >= args.fire_threshold else "⏳ WAIT"
        lines.append(
            f"| {r['product']} "
            f"| {r['pressure_score']:.4f} "
            f"| {status} "
            f"| {r['current_spread_bps']:.1f} "
            f"| {r['baseline_spread_bps']:.1f} "
            f"| {r['book_imbalance']:+.4f} "
            f"| ${r['bid_l1_usd']:.0f}/${r['ask_l1_usd']:.0f} "
            f"| {r['velocity_ratio']:.2f}x "
            f"| {r['taker_direction_bps']:+.1f} |"
        )

    lines.append("")
    lines.append("## Fire Signals")
    lines.append("")
    fire_products = [r for r in results if r.get("pressure_score", 0) >= args.fire_threshold and "error" not in r]
    if fire_products:
        for r in fire_products:
            lines.append(f"### {r['product']} — Score: {r['pressure_score']:.4f}")
            lines.append(f"- **Spread**: {r['current_spread_bps']:.1f}bps (baseline: {r['baseline_spread_bps']:.1f}bps)")
            lines.append(f"- **Book Imbalance**: {r['book_imbalance']:+.4f}")
            lines.append(f"- **L1 Depth**: ${r['bid_l1_usd']:.0f} bid / ${r['ask_l1_usd']:.0f} ask")
            lines.append(f"- **Velocity**: {r['velocity_ratio']:.2f}x baseline")
            lines.append(f"- **Taker Direction**: {r['taker_direction_bps']:+.1f}bps from midpoint")
            bd = r.get("pressure_breakdown", {})
            lines.append(f"- **Pressure Drivers**: spread={bd.get('spread_opportunity', 0):.3f}, imbalance={bd.get('book_imbalance', 0):.3f}, velocity={bd.get('velocity_spike', 0):.3f}, direction={bd.get('taker_direction', 0):.3f}")
            lines.append("")
    else:
        lines.append("No products currently exceed the fire threshold. This is NORMAL — the trigger tape fires opportunistically, not continuously.")
        lines.append("")
        lines.append("## Interpretation")
        lines.append("")
        lines.append("- **Pressure Score > 0.50**: HIGH conviction — takers are actively crossing, our resting order has good fill probability")
        lines.append("- **Pressure Score 0.35-0.50**: MODERATE — conditions are forming, monitor for acceleration")
        lines.append("- **Pressure Score < 0.35**: LOW — wait for crossing-pressure to build")
        lines.append("")
        lines.append("## How This Solves the Exit-Floor Problem")
        lines.append("")
        lines.append("Previous approach: 'Enter when spread is wide' → exit doesn't fill because no taker activity.")
        lines.append("New approach: 'Enter when crossing-pressure is HIGH' → exit fills because takers are already active.")
        lines.append("")
        lines.append("The trigger tape only fires when multiple signals align: wide spread + book imbalance + velocity spike + taker direction.")
        lines.append("This is the 'WHEN will it fill?' answer.")

    with open(args.md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Summary
    print()
    print(f"✅ Results saved to {args.json_path} and {args.md_path}")
    print()
    fire_count = len([r for r in results if r.get("pressure_score", 0) >= args.fire_threshold and "error" not in r])
    print(f"🔥 FIRE signals: {fire_count}/{len(results)}")
    for r in results[:3]:
        if "error" not in r:
            status = "🔥" if r["pressure_score"] >= args.fire_threshold else "⏳"
            print(f"   {status} {r['product']}: {r['pressure_score']:.4f}")


if __name__ == "__main__":
    main()
