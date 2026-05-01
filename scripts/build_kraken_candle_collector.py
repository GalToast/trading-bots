#!/usr/bin/env python3
"""
Kraken Spot OHLC Candle Collector.

Pulls historical OHLC data from Kraken for target products across multiple
timeframes (1m, 5m, 15m, 30m, 1h). Saves full OHLC candles INCLUDING WICKS
(high/low) to a JSON cache for later backtesting.

Key insight: The wicks (high/low) are the signal. They show where orders
actually traded — the extremes of buyer/seller aggression. The close is
just a timestamped snapshot; the wicks tell the real story.

Usage:
    python scripts/build_kraken_candle_collector.py                          # Collect all targets, all granularities
    python scripts/build_kraken_candle_collector.py --products SHAPE-USD     # Single product
    python scripts/build_kraken_candle_collector.py --granularity 5          # Just 5m candles
    python scripts/build_kraken_candle_collector.py --products SHAPE-USD,HONEY-USD,SWEAT-USD --granularity 1  # 1m for specific products
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
CACHE = REPORTS / "cache"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from kraken_spot_client import KrakenSpotClient, normalize_asset, parse_pair, to_float  # noqa: E402

DEFAULT_CACHE_PATH = CACHE / "kraken_ohlc_collector.json"

# Default target products (pairs with positive economics + real spread)
DEFAULT_PRODUCTS = "SHAPE-USD,SWEAT-USD,HONEY-USD,CQT-USD,BILLY-USD,DUCK-USD,CLOUD-USD,CHEX-USD,UNITE-USD,ACA-USD,ALGO-USD,RENDER-USD,NEAR-USD,LDO-USD,XION-USD,RAVE-USD,IOTX-USD,ANLOG-USD,PLANCK-USD"

# Granularities to collect (minutes)
DEFAULT_GRANULARITIES = [1, 5, 15, 30, 60]

# Kraken OHLC returns up to 720 candles per call
KRAKEN_MAX_CANDLES = 720


@dataclass
class Candle:
    """Single OHLC candle with full wick data."""
    t: float        # timestamp (epoch)
    o: float        # open
    h: float        # high (upper wick)
    l: float        # low (lower wick)
    c: float        # close
    v: float        # volume
    upper_wick: float = 0.0  # distance from close to high
    lower_wick: float = 0.0  # distance from close to low
    body: float = 0.0        # absolute |open - close|
    range: float = 0.0       # high - low (total range)

    @classmethod
    def from_kraken(cls, row: list) -> "Candle":
        """Parse a Kraken OHLC row: [time, open, high, low, close, vwap, volume, count]."""
        t = to_float(row[0])
        o = to_float(row[1])
        h = to_float(row[2])
        l = to_float(row[3])
        c = to_float(row[4])
        v = to_float(row[6])

        upper_wick = abs(h - c)
        lower_wick = abs(c - l)
        body = abs(o - c)
        candle_range = h - l

        return cls(
            t=t, o=o, h=h, l=l, c=c, v=v,
            upper_wick=round(upper_wick, 12),
            lower_wick=round(lower_wick, 12),
            body=round(body, 12),
            range=round(candle_range, 12),
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def product_to_kraken(product: str) -> str:
    """SHAPE-USD → SHAPEUSD."""
    return product.replace("-", "").upper()


def kraken_to_product(rest_pair: str) -> str:
    """SHAPEUSD → SHAPE-USD."""
    # Try to parse from Kraken API
    rest_pair = rest_pair.upper()
    for quote in ("USDT", "USDC", "USD"):
        if rest_pair.endswith(quote):
            base = rest_pair[:-len(quote)]
            return f"{base}-{quote}"
    return rest_pair


def load_existing_cache(path: Path) -> dict[str, Any]:
    """Load existing candle cache."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 2, "products": {}}


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    """Atomic save of candle cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def collect_ohlc(client: KrakenSpotClient, rest_pair: str, interval: int, since: int | None = None) -> tuple[list, int | None]:
    """
    Collect OHLC candles from Kraken.

    Returns:
        (candles, last_timestamp) — candles is a list of Candle objects,
        last_timestamp is the 'last' field from Kraken for pagination.
    """
    try:
        resp = client.ohlc(rest_pair, interval_minutes=interval, since_epoch=since)
        if not resp:
            return [], None

        # Kraken returns {pair: [[...], ...], last: timestamp}
        pair_key = None
        for key in resp:
            if key != "last" and isinstance(resp.get(key), list):
                pair_key = key
                break

        if pair_key is None:
            return [], None

        raw_candles = resp.get(pair_key, [])
        last_ts = resp.get("last")

        candles = []
        for row in raw_candles:
            if len(row) >= 5:
                candles.append(Candle.from_kraken(row))

        return candles, last_ts

    except Exception as e:
        print(f"  ERROR collecting {rest_pair} {interval}m: {e}")
        return [], None


def collect_full_history(client: KrakenSpotClient, rest_pair: str, interval: int, max_iterations: int = 10) -> list[Candle]:
    """
    Paginate through Kraken OHLC to collect as much history as possible.
    Kraken returns up to 720 candles per call, going back in time.
    We paginate using the 'last' timestamp to go further back.

    Args:
        max_iterations: Max number of pagination cycles (720 * 10 = 7200 candles max)
    """
    all_candles: list[Candle] = []
    since = None

    for i in range(max_iterations):
        candles, last_ts = collect_ohlc(client, rest_pair, interval, since=since)
        if not candles:
            break

        # Prepend older candles (Kraken returns newest first in pagination)
        all_candles = candles + all_candles

        if last_ts is None or last_ts == 0:
            break

        # Move back in time
        since = int(last_ts) - (interval * 60)
        time.sleep(0.5)  # Rate limit

    # Sort by time
    all_candles.sort(key=lambda c: c.t)

    # Deduplicate by timestamp
    seen = set()
    unique = []
    for c in all_candles:
        if c.t not in seen:
            seen.add(c.t)
            unique.append(c)

    return unique


def analyze_candles(candles: list[Candle]) -> dict:
    """Compute statistics on a candle series — especially wick analysis."""
    if not candles:
        return {}

    total_range = sum(c.range for c in candles)
    total_body = sum(c.body for c in candles)
    total_upper_wick = sum(c.upper_wick for c in candles)
    total_lower_wick = sum(c.lower_wick for c in candles)
    total_volume = sum(c.v for c in candles)

    n = len(candles)
    avg_range = total_range / n if n else 0
    avg_body = total_body / n if n else 0
    avg_upper_wick = total_upper_wick / n if n else 0
    avg_lower_wick = total_lower_wick / n if n else 0

    # Wick ratio: how much of the range is wick vs body
    wick_ratio = (total_upper_wick + total_lower_wick) / total_range if total_range > 0 else 0

    # Price range (highest high to lowest low)
    high = max(c.h for c in candles)
    low = min(c.l for c in candles)
    price_range_bps = ((high - low) / low) * 10000 if low > 0 else 0

    return {
        "count": n,
        "total_range_bps": round(avg_range / (candles[0].c if candles[0].c else 1) * 10000, 2),
        "avg_body_bps": round(avg_body / (candles[0].c if candles[0].c else 1) * 10000, 2),
        "avg_upper_wick_bps": round(avg_upper_wick / (candles[0].c if candles[0].c else 1) * 10000, 2),
        "avg_lower_wick_bps": round(avg_lower_wick / (candles[0].c if candles[0].c else 1) * 10000, 2),
        "wick_ratio": round(wick_ratio, 4),
        "price_range_bps": round(price_range_bps, 2),
        "total_volume": round(total_volume, 6),
        "span_minutes": round((candles[-1].t - candles[0].t) / 60, 1) if len(candles) > 1 else 0,
    }


def collect_product(client: KrakenSpotClient, product: str, granularities: list[int], cache: dict, max_iterations: int = 10) -> dict:
    """Collect all granularities for one product."""
    kraken = product_to_kraken(product)
    result: dict[str, Any] = {"kraken_pair": kraken, "collected_at": utc_now_iso(), "granularities": {}}

    for grain in granularities:
        print(f"  Collecting {product} {grain}m candles...", end="", flush=True)

        candles = collect_full_history(client, kraken, grain, max_iterations=max_iterations)
        stats = analyze_candles(candles)

        # Store as dicts for JSON
        candle_dicts = [asdict(c) for c in candles]
        result["granularities"][str(grain)] = {
            "candles": candle_dicts,
            "stats": stats,
        }

        print(f" {len(candles)} candles, span={stats.get('span_minutes', 0):.0f}m, price_range={stats.get('price_range_bps', 0):.0f}bps, wick_ratio={stats.get('wick_ratio', 0):.2f}")

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Kraken OHLC candles with full wick data for backtesting")
    parser.add_argument("--products", default=DEFAULT_PRODUCTS, help="Comma-separated products")
    parser.add_argument("--granularity", type=int, nargs="+", default=None, help="Granularity in minutes (default: 1 5 15 30 60)")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--max-iterations", type=int, default=10, help="Max pagination cycles per product/granularity (720 * N candles)")
    parser.add_argument("--append", action="store_true", help="Append to existing cache instead of replacing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    granularities = args.granularity if args.granularity else DEFAULT_GRANULARITIES
    products = [p.strip() for p in args.products.split(",") if p.strip()]
    cache_path = Path(args.cache_path)

    print(f"🕯️ Kraken Candle Collector")
    print(f"   Products: {len(products)}")
    print(f"   Granularities: {granularities}")
    print(f"   Max candles per product/grain: {720 * args.max_iterations}")
    print(f"   Cache: {cache_path}")
    print()

    client = KrakenSpotClient()

    # Load existing cache
    if args.append:
        cache = load_existing_cache(cache_path)
        print(f"📂 Loaded existing cache with {len(cache.get('products', {}))} products")
    else:
        cache = {"version": 2, "products": {}, "collected_at": utc_now_iso()}

    # Collect
    for i, product in enumerate(products):
        print(f"\n[{i+1}/{len(products)}] {product}")
        result = collect_product(client, product, granularities, cache, max_iterations=args.max_iterations)
        cache["products"][product] = result

        # Save after each product (incremental)
        save_cache(cache_path, cache)
        print(f"  ✅ Saved cache ({cache_path.stat().st_size / 1024:.0f}KB)")

        if i < len(products) - 1:
            time.sleep(1)  # Rate limit between products

    # Generate summary report
    print(f"\n{'='*60}")
    print(f"📊 COLLECTION SUMMARY")
    print(f"{'='*60}")

    md_lines = [
        "# Kraken OHLC Candle Collection Report",
        "",
        f"- Generated: `{utc_now_iso()}`",
        f"- Products: {len(products)}",
        f"- Granularities: {granularities}",
        "",
        "## Wick Analysis",
        "",
        "| Product | Granularity | Candles | Span (min) | Price Range (bps) | Wick Ratio | Avg Body (bps) | Avg Upper Wick (bps) | Avg Lower Wick (bps) |",
        "|---------|-------------|--------:|-----------:|------------------:|-----------:|---------------:|--------------------:|--------------------:|",
    ]

    for product in products:
        prod_data = cache["products"].get(product, {})
        for grain_str in [str(g) for g in granularities]:
            grain_data = prod_data.get("granularities", {}).get(grain_str, {})
            stats = grain_data.get("stats", {})
            if stats:
                md_lines.append(
                    f"| {product} | {grain_str}m | {stats.get('count', 0)} | {stats.get('span_minutes', 0):.0f} | {stats.get('price_range_bps', 0):.0f}bps | {stats.get('wick_ratio', 0):.2f} | {stats.get('avg_body_bps', 0):.1f}bps | {stats.get('avg_upper_wick_bps', 0):.1f}bps | {stats.get('avg_lower_wick_bps', 0):.1f}bps |"
                )

    md_path = REPORTS / "kraken_ohlc_collection_report.md"
    md_path.write_text("\n".join(md_lines) + "\n")
    print(f"\n📁 Report: {md_path}")


if __name__ == "__main__":
    main()
