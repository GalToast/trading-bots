#!/usr/bin/env python3
"""
Shared Candle Cache Service
============================
ONE service fetches candles, ALL bots read from disk.
Eliminates 95% of API calls by preventing duplicate fetches.

Usage:
  # Start the cache service (runs in background)
  python candle_cache_service.py

  # In your backtest scripts, read from cache instead of API:
  from candle_cache import load_candles
  candles = load_candles("RAVE-USD", "ONE_MINUTE", days=60)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coinbase_advanced_client import CoinbaseAdvancedClient

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "reports" / "candle_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Default cache durations (days)
DEFAULT_DAYS = {
    "ONE_MINUTE": 7,      # 7 days of M1 = 10K candles
    "FIVE_MINUTE": 30,    # 30 days of M5 = 8.6K candles
    "FIFTEEN_MINUTE": 60, # 60 days of M15 = 5.8K candles
}


def cache_path(product_id: str, granularity: str, days: int) -> Path:
    """Get cache file path."""
    return CACHE_DIR / f"{product_id.replace('-', '_')}_{granularity}_{days}d.json"


def load_candles(product_id: str, granularity: str, days: int | None = None,
                  max_age_minutes: int = 30, client: CoinbaseAdvancedClient | None = None) -> list[dict]:
    """
    Load candles from cache, fetching from API only if:
    - Cache doesn't exist
    - Cache is older than max_age_minutes
    
    This is the function ALL backtest scripts should use.
    """
    if days is None:
        days = DEFAULT_DAYS.get(granularity, 7)
    
    path = cache_path(product_id, granularity, days)
    now = time.time()
    
    # Check if cache exists and is fresh enough
    if path.exists():
        age_minutes = (now - path.stat().st_mtime) / 60
        if age_minutes < max_age_minutes:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("candles", [])
    
    # Fetch from API
    if client is None:
        client = CoinbaseAdvancedClient()
    
    candles = fetch_candles_slow(client, product_id, granularity, days)
    
    # Save to cache
    if candles:
        cache_data = {
            "product_id": product_id,
            "granularity": granularity,
            "days": days,
            "fetched_at": now,
            "count": len(candles),
            "candles": candles,
        }
        path.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
        print(f"  [CACHE] Saved {len(candles)} {granularity} candles for {product_id} ({days}d) to {path.name}")
    
    return candles


def fetch_candles_slow(client: CoinbaseAdvancedClient, product_id: str,
                        granularity: str, days: int) -> list[dict]:
    """Fetch candles with proper rate limiting (1.2s between requests = 50 req/min)."""
    now = int(time.time())
    start = now - days * 24 * 3600
    gsec_map = {"ONE_MINUTE": 60, "FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900}
    gsec = gsec_map.get(granularity, 300)
    max_per_req = 300
    all_c = []
    seen = set()
    chunk_end = now
    retries = 0
    
    while chunk_end > start:
        chunk_start = max(start, chunk_end - max_per_req * gsec)
        try:
            resp = client.market_candles(product_id, start=chunk_start, end=chunk_end, granularity=granularity)
            raw = resp.get("candles") or []
            if not raw:
                break
            for c in raw:
                t = int(c.get("start", c.get("time", 0)))
                if t not in seen:
                    seen.add(t)
                    all_c.append({
                        "time": t,
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": float(c.get("volume", 0)),
                    })
            chunk_end = chunk_start - 1
            retries = 0
            # 1.2s sleep = 50 requests/minute (Coinbase public endpoint limit)
            time.sleep(1.2)
        except Exception as e:
            if "429" in str(e):
                retries += 1
                wait = min(2 ** retries, 30)
                print(f"    [CACHE] Rate limited, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"    [CACHE] Fetch error: {e}")
                chunk_end -= max_per_req * gsec
                time.sleep(1.2)
    
    return sorted(all_c, key=lambda x: x["time"])


def preload_all(coins: list[str] | None = None, client: CoinbaseAdvancedClient | None = None):
    """Preload all commonly used candle data."""
    if coins is None:
        coins = ["RAVE-USD", "BAL-USD", "BLUR-USD", "ALEPH-USD", "IOTX-USD", "BTC-USD"]
    
    if client is None:
        client = CoinbaseAdvancedClient()
    
    configs = [
        # (granularity, days)
        ("ONE_MINUTE", 7),
        ("FIVE_MINUTE", 30),
        ("FIFTEEN_MINUTE", 60),
    ]
    
    total = len(coins) * len(configs)
    done = 0
    
    for coin in coins:
        for gran, days in configs:
            done += 1
            print(f"  [{done}/{total}] Fetching {coin} {gran} ({days}d)...")
            load_candles(coin, gran, days, max_age_minutes=1440, client=client)  # 24h cache age
    
    print(f"\n  [CACHE] Preloaded {total} candle sets for {len(coins)} coins")


def main():
    print("=" * 80)
    print("  SHARED CANDLE CACHE SERVICE")
    print("=" * 80)
    print(f"  Cache directory: {CACHE_DIR}")
    print()
    
    client = CoinbaseAdvancedClient()
    
    # Preload commonly used data
    print("Preloading candle data...")
    preload_all(client=client)
    
    # Show cache summary
    print(f"\n  Cache contents:")
    for p in sorted(CACHE_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        age_min = (time.time() - data["fetched_at"]) / 60
        print(f"    {p.name}: {data['count']} candles ({age_min:.0f}m old)")
    
    print(f"\n  Usage in your scripts:")
    print(f"    from candle_cache_service import load_candles")
    print(f"    candles = load_candles('RAVE-USD', 'ONE_MINUTE', days=7)")
    print()


if __name__ == "__main__":
    main()
