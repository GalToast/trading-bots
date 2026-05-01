#!/usr/bin/env python3
"""
Titan 13.0 — Unified Convergence Feed.

Merges three data sources into ONE JSON the Swarm Brain can consume:
1. Economics: spread, net_margin at 0.10, exit_floor_bps
2. Dislocation: dislocation_bps, reversal_bps, signal type
3. Side-Aware: ask_crossing, bid_crossing, depth_shrinking, last_trade_direction

Output: reports/titan13_convergence_feed.json

Usage:
    python scripts/titan13_convergence_feed.py --products SHAPE-USD,CQT-USD,DUCK-USD,ACA-USD,HONEY-USD,IDEX-USD,EDU-USD --samples 10 --interval 3
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


def scan_product_convergence(client: KrakenSpotClient, product: str,
                              samples: int = 10, interval: float = 3.0,
                              offset: float = 0.10) -> dict:
    """
    Single product convergence scan: economics + dislocation + side-aware.
    """
    kc = kraken_name(product)

    # Get pair info for economics
    assets = client.asset_pairs()
    pair = None
    for k, v in assets.items():
        if k == kc or v.get("altname", "").upper() == product.replace("-", "").upper():
            pair = parse_pair(k, v)
            break

    ticks = []
    prices = []
    bid_depths = []
    ask_depths = []
    spreads = []
    imbalances = []

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
            bid_d = to_float(bids[0][1]) * to_float(bids[0][0]) if bids else 0
            ask_d = to_float(asks[0][1]) * to_float(asks[0][0]) if asks else 0

            ticks.append({
                "ts": utc_now_iso(),
                "bid": bid, "ask": ask, "last": last,
                "spread_bps": round(spread, 2),
                "imbalance": round(imbalance, 4),
                "bid_depth_usd": round(bid_d, 2),
                "ask_depth_usd": round(ask_d, 2),
            })
            prices.append(last)
            bid_depths.append(bid_d)
            ask_depths.append(ask_d)
            spreads.append(spread)
            imbalances.append(imbalance)

            if i < samples - 1:
                time.sleep(interval)

        except Exception:
            time.sleep(interval)

    if len(ticks) < 2:
        return {"product": product, "error": "insufficient_ticks", "tick_count": len(ticks)}

    # === ECONOMICS ===
    cur = ticks[-1]
    econ = {"spread_bps": cur["spread_bps"], "bid": cur["bid"], "ask": cur["ask"], "last": cur["last"]}
    if pair and pair.tick_size > 0:
        entry = legal_maker_buy_price_at_offset(cur["bid"], cur["ask"], pair.tick_size, offset)
        if entry > 0:
            vol = legal_volume(9.0 / entry, pair.lot_decimals)
            if vol > 0:
                entry_cost = entry * vol
                entry_fee = entry_cost * 0.0025
                exit_legal, _ = maker_exit_floor_price(
                    entry_cost=entry_cost, entry_fee=entry_fee, volume=vol,
                    maker_fee_bps=25.0, target_net_pct=0.001, tick_size=pair.tick_size,
                )
                floor_above_ask = exit_floor_above_ask_bps(exit_legal, cur["ask"])
                gross = (exit_legal - entry) / entry * 10000
                net = gross - 50
                econ.update({
                    "offset": offset,
                    "entry_price": round(entry, 8),
                    "exit_price": round(exit_legal, 8),
                    "entry_concession_bps": round(max(0, (entry - cur["bid"]) / cur["bid"] * 10000), 1),
                    "exit_floor_above_ask_bps": round(floor_above_ask, 1),
                    "net_margin_bps": round(net, 1),
                    "volume": round(vol, 8),
                })
            else:
                econ["error"] = "invalid_volume"
        else:
            econ["error"] = "invalid_entry_price"
    else:
        econ["error"] = "no_pair_info"

    # === DISLOCATION ===
    if len(prices) >= 3:
        max_p = max(prices)
        min_p = min(prices)
        mid = (max_p + min_p) / 2
        dislocation_bps = (max_p - min_p) / mid * 10000 if mid > 0 else 0
        # Reversal from extreme
        last_p = prices[-1]
        if last_p == max_p:
            reversal_bps = 0
            signal = "at_high"
        elif last_p == min_p:
            reversal_bps = 0
            signal = "at_low"
        else:
            rev_from_high = (max_p - last_p) / max_p * 10000 if max_p > 0 else 0
            rev_from_low = (last_p - min_p) / min_p * 10000 if min_p > 0 else 0
            reversal_bps = min(rev_from_high, rev_from_low)
            if rev_from_high > 10:
                signal = "reversing_from_high"
            elif rev_from_low > 10:
                signal = "reversing_from_low"
            else:
                signal = "in_range"
    else:
        dislocation_bps = 0
        reversal_bps = 0
        signal = "insufficient_data"

    dislocation = {
        "dislocation_bps": round(dislocation_bps, 1),
        "reversal_bps": round(reversal_bps, 1),
        "signal": signal,
        "price_range": f"{min(prices):.8f} - {max(prices):.8f}",
    }

    # === SIDE-AWARE ===
    if len(ticks) >= 3:
        # Ask crossing: ask price going down
        asks = [t["ask"] for t in ticks]
        bid_prices = [t["bid"] for t in ticks]
        ask_crossing = sum(1 for i in range(1, len(asks)) if asks[i] < asks[i-1]) > len(asks) * 0.5
        bid_crossing = sum(1 for i in range(1, len(bid_prices)) if bid_prices[i] < bid_prices[i-1]) > len(bid_prices) * 0.5
        # Depth shrinking
        ask_depth_shrinking = ask_depths[-1] < ask_depths[0] * 0.8 if ask_depths[0] > 0 else False
        bid_depth_shrinking = bid_depths[-1] < bid_depths[0] * 0.8 if bid_depths[0] > 0 else False
        # Last trade direction
        last_trade_offset = (cur["last"] - (cur["bid"] + cur["ask"]) / 2) / ((cur["bid"] + cur["ask"]) / 2) * 10000 if cur["bid"] > 0 else 0
    else:
        ask_crossing = False
        bid_crossing = False
        ask_depth_shrinking = False
        bid_depth_shrinking = False
        last_trade_offset = 0

    side_aware = {
        "ask_crossing": ask_crossing,
        "bid_crossing": bid_crossing,
        "ask_depth_shrinking": ask_depth_shrinking,
        "bid_depth_shrinking": bid_depth_shrinking,
        "last_trade_offset_bps": round(last_trade_offset, 1),
    }

    # === CONVERGENCE SCORE ===
    # High score = economics pass + dislocation detected + side-aware signal
    econ_passes = econ.get("net_margin_bps", 0) > 20 and econ.get("spread_bps", 0) >= 150
    dislocation_active = dislocation_bps > 20  # 20bps+ move
    side_aware_signal = ask_crossing or bid_crossing or ask_depth_shrinking or bid_depth_shrinking

    convergence_score = 0
    if econ_passes:
        convergence_score += 1
    if dislocation_active:
        convergence_score += 1
    if side_aware_signal:
        convergence_score += 1

    return {
        "product": product,
        "tick_count": len(ticks),
        "timestamp": utc_now_iso(),
        "economics": econ,
        "dislocation": dislocation,
        "side_aware": side_aware,
        "convergence_score": convergence_score,
        "convergence_status": "FIRE" if convergence_score == 3 else "WATCH" if convergence_score >= 2 else "WAIT",
        "all_ticks": ticks[-3:],  # Last 3 for Swarm Brain consumption
    }


def main():
    parser = argparse.ArgumentParser(description="Titan 13.0 Unified Convergence Feed")
    parser.add_argument("--products", default="SHAPE-USD,CQT-USD,DUCK-USD,ACA-USD,HONEY-USD,IDEX-USD,EDU-USD")
    parser.add_argument("--samples", type=int, default=10, help="Ticks per product")
    parser.add_argument("--interval", type=float, default=3.0, help="Seconds between ticks")
    parser.add_argument("--offset", type=float, default=0.10, help="Entry offset for economics")
    parser.add_argument("--json-path", type=Path, default=REPORTS / "titan13_convergence_feed.json")
    args = parser.parse_args()

    client = KrakenSpotClient()
    products = [p.strip() for p in args.products.split(",") if p.strip()]

    print(f"🔬 Titan 13.0 Convergence Feed: {len(products)} products, {args.samples} ticks, {args.interval}s interval")
    print()

    results = []
    for product in products:
        print(f"  Scanning {product}...")
        result = scan_product_convergence(client, product, samples=args.samples,
                                           interval=args.interval, offset=args.offset)
        status = result.get("convergence_status", "?")
        score = result.get("convergence_score", 0)
        emoji = "🔥" if status == "FIRE" else "⏳" if status == "WATCH" else "➡️"
        print(f"    {emoji} {status} (score: {score}/3)")
        if result.get("error"):
            print(f"    Error: {result['error']}")
        results.append(result)

    # Sort by convergence_score descending
    results.sort(key=lambda r: r.get("convergence_score", 0), reverse=True)

    # Output
    output = {
        "generated": utc_now_iso(),
        "offset": args.offset,
        "products": products,
        "results": results,
    }
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print()
    print(f"✅ Feed saved to {args.json_path}")
    print()

    fire = [r for r in results if r.get("convergence_status") == "FIRE"]
    watch = [r for r in results if r.get("convergence_status") == "WATCH"]
    wait = [r for r in results if r.get("convergence_status") == "WAIT"]
    error = [r for r in results if "error" in r]
    print(f"📊 Summary: {len(fire)} FIRE, {len(watch)} WATCH, {len(wait)} WAIT, {len(error)} ERROR")
    for r in results:
        if "error" not in r:
            econ = r.get("economics", {})
            disl = r.get("dislocation", {})
            sa = r.get("side_aware", {})
            status = r.get("convergence_status", "?")
            score = r.get("convergence_score", 0)
            print(f"  {r['product']:15s} {status:6s} ({score}/3) spread={econ.get('spread_bps','?'):6.0f}bps net={econ.get('net_margin_bps','?'):7.1f}bps dislocation={disl['dislocation_bps']:6.1f}bps")


if __name__ == "__main__":
    main()
