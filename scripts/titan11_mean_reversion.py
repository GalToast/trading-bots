#!/usr/bin/env python3
"""
Titan 11.0 — Liquidity Dislocation / Mean Reversion Scanner.

Instead of asking "can we fill at offset X?", this scanner asks:
"What market condition produces guaranteed fills?"

The thesis: After a sharp price move, the book snaps back (mean reversion).
If we place a resting order at bid/ask DURING the snap, our order catches
the overshoot and fills naturally.

Detection:
1. Sharp price move detected (X% in Y seconds)
2. Book imbalance peaks (one side thins out dramatically)
3. Reversal begins (price starts moving back)
4. RESTING ORDER at the reversal origin catches the fill

Usage:
    python scripts/titan11_mean_reversion.py --products SHAPE-USD,CQT-USD,DUCK-USD,HONEY-USD,IDEX-USD --samples 30 --interval 2
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
from crossing_pressure_scanner import kraken_name, compute_spread_bps, compute_book_imbalance


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_dislocation(price_history: list[float], lookback: int = 5) -> dict:
    """
    Detect sharp price moves that create dislocation.

    Returns:
        magnitude_bps: size of the move in bps
        direction: "up" or "down"
        is_dislocated: True if magnitude > threshold
    """
    if len(price_history) < lookback + 1:
        return {"magnitude_bps": 0, "direction": "neutral", "is_dislocated": False}

    recent = price_history[-lookback:]
    oldest = price_history[-(lookback + 1)]

    if oldest <= 0:
        return {"magnitude_bps": 0, "direction": "neutral", "is_dislocated": False}

    move_bps = (recent[-1] - oldest) / oldest * 10000
    magnitude = abs(move_bps)
    direction = "up" if move_bps > 0 else "down"

    # Dislocation threshold: 50bps in lookback window
    threshold = 50.0
    is_dislocated = magnitude > threshold

    return {
        "magnitude_bps": round(magnitude, 1),
        "direction": direction,
        "is_dislocated": is_dislocated,
    }


def detect_reversal(price_history: list[float], dislocation_direction: str, lookback: int = 3) -> dict:
    """
    Detect if price is reversing from the dislocation.

    If dislocation was UP, reversal = price going DOWN from peak.
    If dislocation was DOWN, reversal = price going UP from trough.
    """
    if len(price_history) < lookback + 1:
        return {"reversal_bps": 0, "is_reversing": False}

    if dislocation_direction == "up":
        peak = max(price_history[-(lookback + 1):])
        current = price_history[-1]
        reversal_bps = (peak - current) / peak * 10000 if peak > 0 else 0
    elif dislocation_direction == "down":
        trough = min(price_history[-(lookback + 1):])
        current = price_history[-1]
        reversal_bps = (current - trough) / trough * 10000 if trough > 0 else 0
    else:
        return {"reversal_bps": 0, "is_reversing": False}

    # Reversal threshold: 10bps from extreme
    threshold = 10.0
    is_reversing = reversal_bps > threshold

    return {
        "reversal_bps": round(reversal_bps, 1),
        "is_reversing": is_reversing,
    }


def compute_mean_reversion_signal(product: str, snapshots: list[dict]) -> dict:
    """
    Compute mean-reversion signal from price/book snapshots.

    Signal fires when:
    1. Dislocation detected (sharp move)
    2. Reversal detected (price snapping back)
    3. Book imbalance favors our resting order
    """
    prices = [s["last"] for s in snapshots if s.get("last", 0) > 0]
    bids = [s["bid"] for s in snapshots if s.get("bid", 0) > 0]
    asks = [s["ask"] for s in snapshots if s.get("ask", 0) > 0]
    imbalances = [s["imbalance"] for s in snapshots if "imbalance" in s]

    if len(prices) < 6:
        return {"signal": "no_data", "confidence": 0.0, "details": {}}

    # Detect dislocation
    dislocation = detect_dislocation(prices, lookback=5)

    if not dislocation["is_dislocated"]:
        return {
            "signal": "no_dislocation",
            "confidence": 0.0,
            "details": {"magnitude_bps": dislocation["magnitude_bps"]},
        }

    # Detect reversal
    reversal = detect_reversal(prices, dislocation["direction"], lookback=3)

    if not reversal["is_reversing"]:
        return {
            "signal": f"dislocation_{dislocation['direction']}_no_reversal",
            "confidence": 0.0,
            "details": {
                "dislocation_bps": dislocation["magnitude_bps"],
                "reversal_bps": reversal["reversal_bps"],
            },
        }

    # Both dislocation AND reversal detected — this is the signal!
    # Determine which side to place resting order:
    # If dislocation was UP + reversal DOWN → place SELL order at ask (catch the snap down)
    # If dislocation was DOWN + reversal UP → place BUY order at bid (catch the snap up)
    if dislocation["direction"] == "down" and reversal["is_reversing"]:
        signal = "BUY"  # Price crashed, now recovering — buy at bid
        # Resting BUY order at bid catches the recovery
        confidence = min(1.0, (dislocation["magnitude_bps"] + reversal["reversal_bps"]) / 200.0)
    elif dislocation["direction"] == "up" and reversal["is_reversing"]:
        signal = "SELL"  # Price spiked, now dropping — sell at ask
        # Resting SELL order at ask catches the drop
        confidence = min(1.0, (dislocation["magnitude_bps"] + reversal["reversal_bps"]) / 200.0)
    else:
        signal = "NEUTRAL"
        confidence = 0.0

    # Current book state
    cur_imbalance = imbalances[-1] if imbalances else 0

    return {
        "signal": signal,
        "confidence": round(confidence, 2),
        "details": {
            "dislocation_direction": dislocation["direction"],
            "dislocation_bps": dislocation["magnitude_bps"],
            "reversal_bps": reversal["reversal_bps"],
            "current_imbalance": round(cur_imbalance, 4),
            "price_samples": len(prices),
        },
    }


def scan_product(client: KrakenSpotClient, product: str,
                 samples: int = 20, interval: float = 2.0) -> dict:
    """Take rapid snapshots and detect mean-reversion signals."""
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

            spread = compute_spread_bps(bid, ask)
            imbalance = compute_book_imbalance(bids, asks, levels=3)

            snapshots.append({
                "ts": utc_now_iso(),
                "bid": bid,
                "ask": ask,
                "last": last,
                "spread_bps": round(spread, 2),
                "imbalance": round(imbalance, 4),
            })

            if i < samples - 1:
                time.sleep(interval)

        except Exception:
            time.sleep(interval)

    if len(snapshots) < 6:
        return {"product": product, "error": "insufficient_snapshots",
                "snapshot_count": len(snapshots)}

    signal = compute_mean_reversion_signal(product, snapshots)

    return {
        "product": product,
        "snapshot_count": len(snapshots),
        "duration_seconds": len(snapshots) * interval,
        "first_last": snapshots[0]["last"],
        "last_last": snapshots[-1]["last"],
        "first_spread_bps": snapshots[0]["spread_bps"],
        "last_spread_bps": snapshots[-1]["spread_bps"],
        "signal": signal["signal"],
        "confidence": signal["confidence"],
        "details": signal.get("details", {}),
    }


def main():
    parser = argparse.ArgumentParser(description="Titan 11.0 Mean Reversion Scanner")
    parser.add_argument("--products", default="SHAPE-USD,CQT-USD,DUCK-USD,ACA-USD,HONEY-USD,IDEX-USD,PLANCK-USD")
    parser.add_argument("--samples", type=int, default=20, help="Snapshots per product")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between snapshots")
    parser.add_argument("--json-path", type=Path, default=REPORTS / "titan11_mean_reversion.json")
    parser.add_argument("--md-path", type=Path, default=REPORTS / "titan11_mean_reversion.md")
    args = parser.parse_args()

    client = KrakenSpotClient()
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"🔬 Titan 11.0 Mean Reversion Scanner: {len(products)} products, {args.samples} samples, {args.interval}s interval")
    print()

    results = []
    for product in products:
        print(f"  Scanning {product}...")
        result = scan_product(client, product, samples=args.samples, interval=args.interval)
        signal = result.get("signal", "?")
        confidence = result.get("confidence", 0)
        emoji = "🎯" if confidence >= 0.5 else "⏳" if confidence >= 0.2 else "➡️"
        print(f"    {emoji} {signal} (confidence: {confidence:.2f})")
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
        "# Titan 11.0 Mean Reversion Analysis",
        f"- Generated: `{utc_now_iso()}`",
        "",
        "## Ranked by Confidence",
        "",
        "| Product | Signal | Confidence | Dislocation bps | Reversal bps | Spread bps |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['product']} | ERROR | - | - | - | - |")
            continue
        d = r.get("details", {})
        lines.append(
            f"| {r['product']} "
            f"| {r['signal']} "
            f"| {r['confidence']:.2f} "
            f"| {d.get('dislocation_bps', '?')} "
            f"| {d.get('reversal_bps', '?')} "
            f"| {r.get('last_spread_bps', '?')} |"
        )

    lines.append("")
    lines.append("## Active Mean-Reversion Signals")
    lines.append("")
    signals = [r for r in results if r.get("confidence", 0) >= 0.2 and "error" not in r]
    if signals:
        for r in signals:
            d = r.get("details", {})
            emoji = "🎯 BUY" if r["signal"] == "BUY" else "🎯 SELL" if r["signal"] == "SELL" else r["signal"]
            lines.append(f"### {emoji} {r['product']} (confidence: {r['confidence']:.2f})")
            lines.append(f"- Dislocation: {d.get('dislocation_direction', '?')} {d.get('dislocation_bps', '?')}bps")
            lines.append(f"- Reversal: {d.get('reversal_bps', '?')}bps")
            lines.append(f"- Spread: {r.get('last_spread_bps', '?')}bps")
            lines.append(f"- Book Imbalance: {d.get('current_imbalance', '?')}")
            if r["signal"] == "BUY":
                lines.append(f"- **Action**: Place BUY order at bid — price crashed and recovering")
            elif r["signal"] == "SELL":
                lines.append(f"- **Action**: Place SELL order at ask — price spiked and dropping")
            lines.append("")
    else:
        lines.append("No mean-reversion signals detected. Books are in equilibrium — no sharp dislocations detected.")
        lines.append("")

    with open(args.md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print()
    print(f"✅ Results saved to {args.json_path} and {args.md_path}")

    buy_signals = [r for r in results if r.get("signal") == "BUY"]
    sell_signals = [r for r in results if r.get("signal") == "SELL"]
    neutral = [r for r in results if r.get("signal") not in ("BUY", "SELL") and "error" not in r]
    print(f"\n📊 Summary: {len(buy_signals)} BUY, {len(sell_signals)} SELL, {len(neutral)} NEUTRAL")
    for r in results[:3]:
        if "error" not in r:
            emoji = "🎯" if r.get("confidence", 0) >= 0.3 else "⏳" if r.get("confidence", 0) >= 0.1 else "➡️"
            print(f"  {emoji} {r['product']}: {r['signal']} (conf={r['confidence']:.2f})")


if __name__ == "__main__":
    main()
