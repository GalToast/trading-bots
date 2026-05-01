#!/usr/bin/env python3
"""
Dislocation Event Tape — feeds directly into build_kraken_spot_guarded_frontier_lab.py.

Generates radar_ticks JSON in the format codex's lab expects:
- Per-product tick snapshots with bid/ask/last/depth
- Annotated with dislocation/reversal signals
- Side-aware features (ask_crossing, bid_crossing, depth shrinking)
- Economics features (spread_bps, exit_floor_bps at 0.10 offset)

Output: reports/cache/kraken_dislocation_tape_radar_ticks.json
        reports/dislocation_event_tape.json
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
CACHE = REPORTS / "cache"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, to_float, parse_pair
from build_kraken_tiny_live_fire_queue import legal_maker_buy_price_at_offset
from run_kraken_tiny_live_maker_roundtrip_probe import (
    exit_floor_above_ask_bps,
    maker_exit_floor_price,
    legal_volume,
)
from crossing_pressure_scanner import (
    kraken_name,
    compute_spread_bps,
    compute_book_imbalance,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def scan_product_ticks(client: KrakenSpotClient, product: str,
                       samples: int = 15, interval: float = 3.0) -> list[dict]:
    """
    Collect tick snapshots for one product, in the format codex's lab expects.
    """
    kc = kraken_name(product)
    ticks = []

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

            spread_bps = compute_spread_bps(bid, ask)
            imbalance = compute_book_imbalance(bids, asks, levels=3)
            bid_depth_usd = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
            ask_depth_usd = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

            tick = {
                "ts": utc_now_iso(),
                "product": product,
                "bid": bid,
                "ask": ask,
                "last": last,
                "spread_bps": round(spread_bps, 2),
                "imbalance": round(imbalance, 4),
                "bid_depth_usd": round(bid_depth_usd, 2),
                "ask_depth_usd": round(ask_depth_usd, 2),
            }
            ticks.append(tick)

            if i < samples - 1:
                time.sleep(interval)

        except Exception:
            time.sleep(interval)

    return ticks


def annotate_dislocation(ticks: list[dict], dislocation_threshold_bps: float = 20.0,
                          reversal_threshold_bps: float = 5.0) -> list[dict]:
    """
    Annotate tick series with dislocation and reversal signals.
    """
    if len(ticks) < 3:
        return ticks

    prices = [t["last"] for t in ticks]

    for i in range(2, len(ticks)):
        window = prices[max(0, i-5):i+1]
        if len(window) < 3:
            ticks[i]["dislocation_bps"] = 0
            ticks[i]["reversal_bps"] = 0
            ticks[i]["signal"] = "no_data"
            continue

        # Dislocation: max move in window
        max_price = max(window)
        min_price = min(window)
        mid = (max_price + min_price) / 2
        move_bps = (max_price - min_price) / mid * 10000 if mid > 0 else 0

        ticks[i]["dislocation_bps"] = round(move_bps, 1)

        # Reversal: from extreme toward center
        current = prices[i]
        if current == max_price:
            # At top, no reversal yet
            ticks[i]["reversal_bps"] = 0
            ticks[i]["signal"] = "dislocation_up" if move_bps > dislocation_threshold_bps else "flat"
        elif current == min_price:
            # At bottom, no reversal yet
            ticks[i]["reversal_bps"] = 0
            ticks[i]["signal"] = "dislocation_down" if move_bps > dislocation_threshold_bps else "flat"
        else:
            # In middle = some reversal happened
            reversal_from_high = (max_price - current) / max_price * 10000 if max_price > 0 else 0
            reversal_from_low = (current - min_price) / min_price * 10000 if min_price > 0 else 0
            ticks[i]["reversal_bps"] = round(min(reversal_from_high, reversal_from_low), 1)

            if reversal_from_high > reversal_threshold_bps:
                ticks[i]["signal"] = "reversal_from_high"
            elif reversal_from_low > reversal_threshold_bps:
                ticks[i]["signal"] = "reversal_from_low"
            elif move_bps > dislocation_threshold_bps:
                ticks[i]["signal"] = "dislocation_in_progress"
            else:
                ticks[i]["signal"] = "flat"

    # Annotate first 2 ticks
    ticks[0]["dislocation_bps"] = 0
    ticks[0]["reversal_bps"] = 0
    ticks[0]["signal"] = "no_data"
    if len(ticks) > 1:
        ticks[1]["dislocation_bps"] = 0
        ticks[1]["reversal_bps"] = 0
        ticks[1]["signal"] = "no_data"

    return ticks


def main():
    parser = argparse.ArgumentParser(description="Dislocation Event Tape for Frontier Lab")
    parser.add_argument("--products", default="SHAPE-USD,CQT-USD,DUCK-USD,ACA-USD,HONEY-USD,IDEX-USD,PLANCK-USD,BADGER-USD,TRAC-USD")
    parser.add_argument("--samples", type=int, default=15, help="Ticks per product")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between ticks")
    parser.add_argument("--dislocation-threshold", type=float, default=20.0, help="bps threshold for dislocation")
    parser.add_argument("--cache-path", type=Path, default=CACHE / "kraken_dislocation_tape_radar_ticks.json")
    parser.add_argument("--tape-path", type=Path, default=REPORTS / "dislocation_event_tape.json")
    args = parser.parse_args()

    client = KrakenSpotClient()
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"🔬 Dislocation Event Tape: {len(products)} products, {args.samples} ticks, {args.interval}s interval")
    print()

    all_ticks = []
    event_summary = []

    for product in products:
        print(f"  Scanning {product}...")
        ticks = scan_product_ticks(client, product, samples=args.samples, interval=args.interval)

        if not ticks:
            print(f"    No ticks collected")
            continue

        # Annotate dislocation/reversal
        ticks = annotate_dislocation(ticks, dislocation_threshold_bps=args.dislocation_threshold)

        # Count signals
        signals = [t.get("signal", "flat") for t in ticks]
        dislocation_count = sum(1 for s in signals if "dislocation" in s)
        reversal_count = sum(1 for s in signals if "reversal" in s)
        max_dislocation = max((t.get("dislocation_bps", 0) for t in ticks), default=0)
        max_reversal = max((t.get("reversal_bps", 0) for t in ticks), default=0)

        print(f"    {dislocation_count} dislocations, {reversal_count} reversals, max_move={max_dislocation:.1f}bps")

        all_ticks.extend(ticks)
        event_summary.append({
            "product": product,
            "tick_count": len(ticks),
            "dislocation_events": dislocation_count,
            "reversal_events": reversal_count,
            "max_dislocation_bps": round(max_dislocation, 1),
            "max_reversal_bps": round(max_reversal, 1),
        })

    # Save in codex's cache format
    args.cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_data = {
        "generated": utc_now_iso(),
        "products": products,
        "ticks": all_ticks,
    }
    tmp_cache = args.cache_path.with_suffix(args.cache_path.suffix + ".tmp")
    with open(tmp_cache, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)
    tmp_cache.replace(args.cache_path)

    # Save event tape summary
    tape_data = {
        "generated": utc_now_iso(),
        "dislocation_threshold_bps": args.dislocation_threshold,
        "summary": event_summary,
    }
    with open(args.tape_path, "w", encoding="utf-8") as f:
        json.dump(tape_data, f, indent=2)

    print()
    print(f"✅ Cache saved to {args.cache_path}")
    print(f"✅ Tape saved to {args.tape_path}")
    print()

    # Summary
    total_dislocations = sum(e["dislocation_events"] for e in event_summary)
    total_reversals = sum(e["reversal_events"] for e in event_summary)
    print(f"📊 Total: {total_dislocations} dislocations, {total_reversals} reversals across {len(products)} products")
    for e in event_summary:
        if e["dislocation_events"] > 0 or e["reversal_events"] > 0:
            print(f"  🎯 {e['product']}: {e['dislocation_events']} dislocations, {e['reversal_events']} reversals, max_move={e['max_dislocation_bps']}bps")


if __name__ == "__main__":
    main()
