#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import kraken_config as cfg
from kraken_spot_client import KrakenPair, KrakenSpotClient, parse_pair, to_float


REPORTS = ROOT / "reports"
DEFAULT_JSON_PATH = REPORTS / "kraken_spot_pulse_board.json"
DEFAULT_CSV_PATH = REPORTS / "kraken_spot_pulse_board.csv"
DEFAULT_MD_PATH = REPORTS / "kraken_spot_pulse_board.md"
DEFAULT_CACHE_PATH = REPORTS / "cache" / "kraken_spot_pulse_candles.json"


def parse_quote_currencies(value: str) -> set[str]:
    return {part.strip().upper() for part in value.split(",") if part.strip()}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a live Kraken spot pulse board for hot-symbol rotation.")
    parser.add_argument("--top-products", type=int, default=100, help="Top liquid spot products to candle-scan.")
    parser.add_argument("--hours", type=int, default=6, help="Recent candle window for pulse scoring.")
    parser.add_argument("--quote-currencies", default="USD,USDT,USDC", help="Comma-separated quote currencies to scan.")
    parser.add_argument("--min-quote-volume-usd", type=float, default=20_000.0, help="Minimum native 24h quote volume.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--cache-ttl-seconds", type=float, default=300.0, help="Reuse local candles younger than this many seconds.")
    parser.add_argument("--max-candle-fetches", type=int, default=0, help="Maximum stale/missing candle API calls this run; 0 means no cap.")
    parser.add_argument("--request-sleep-seconds", type=float, default=0.1, help="Pause between candle API calls.")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore cached candles and refresh selected products.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def list_pairs(client: KrakenSpotClient, quotes: set[str]) -> list[KrakenPair]:
    payload = client.asset_pairs()
    pairs: list[KrakenPair] = []
    for rest_pair, row in payload.items():
        pair = parse_pair(rest_pair, row)
        if not pair:
            continue
        if pair.status not in {"online", ""}:
            continue
        if pair.quote not in quotes:
            continue
        pairs.append(pair)
    return pairs


def fetch_tickers(client: KrakenSpotClient, pairs: list[KrakenPair], chunk_size: int = 50) -> dict[str, dict[str, Any]]:
    tickers: dict[str, dict[str, Any]] = {}
    for i in range(0, len(pairs), chunk_size):
        chunk = pairs[i : i + chunk_size]
        rest_pairs = [p.rest_pair for p in chunk]
        payload = client.ticker(rest_pairs)
        for rest_pair, row in payload.items():
            tickers[rest_pair] = row
    return tickers


def fetch_recent_candles(client: KrakenSpotClient, rest_pair: str, *, hours: int) -> list[dict[str, float]]:
    # Kraken OHLC interval: 1 minute is default
    # since_epoch: 1m granularity, max 720 points
    now = int(time.time())
    since = now - (hours * 3600)
    payload = client.ohlc(rest_pair, interval_minutes=1, since_epoch=since)
    
    # Kraken returns {rest_pair: [[ts, o, h, l, c, vwp, v, count], ...], "last": ts}
    data = payload.get(rest_pair, [])
    candles = []
    for row in data:
        candles.append({
            "start": float(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[6]), # row[6] is volume, row[5] is vwp
        })
    return sorted(candles, key=lambda x: x["start"])


def cache_key(product_id: str, *, hours: int) -> str:
    return f"{product_id.upper()}|1m|{int(hours)}h"


def load_candle_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "entries": {}}
    return payload


def save_candle_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def cached_candles(
    cache: dict[str, Any],
    product_id: str,
    *,
    hours: int,
    now_epoch: float,
    ttl_seconds: float,
) -> tuple[list[dict[str, float]], float | None, str]:
    entry = (cache.get("entries") or {}).get(cache_key(product_id, hours=hours)) or {}
    candles = entry.get("candles")
    fetched_at = to_float(entry.get("fetched_at_epoch"))
    if not isinstance(candles, list):
        return [], None, "missing"
    age = max(0.0, now_epoch - fetched_at) if fetched_at > 0 else None
    if age is not None and age <= ttl_seconds:
        return candles, age, "cache_hit"
    return candles, age, "cache_stale"


def score_product(
    *,
    pair: KrakenPair,
    ticker: dict[str, Any],
    candles: list[dict[str, float]],
    candle_source: str = "api_fetch",
    cache_age_seconds: float | None = None,
) -> dict[str, Any]:
    product_id = pair.wsname.replace("/", "-")
    
    # Kraken ticker: v: [volume today, volume last 24h]
    volume_24h = to_float(ticker.get("v", [0, 0])[1])
    # Ticker: p: [vwp today, vwp last 24h]
    vwp_24h = to_float(ticker.get("p", [0, 0])[1])
    quote_volume = volume_24h * vwp_24h
    
    ask = to_float(ticker.get("a", [0, 0])[0])
    bid = to_float(ticker.get("b", [0, 0])[0])
    mid = (ask + bid) / 2.0 if ask > 0 and bid > 0 else 0.0
    spread_bps = ((ask - bid) / mid) * 10000.0 if mid > 0 else 0.0
    
    if len(candles) < 20:
        return {
            "product_id": product_id,
            "status": "insufficient_candles",
            "pulse_score": 0.0,
            "spread_bps": round(spread_bps, 2),
            "candles": len(candles),
        }

    close = candles[-1]["close"]
    
    def get_ret(offset: int) -> float:
        if len(candles) <= abs(offset):
            prior = candles[0]["close"]
        else:
            prior = candles[offset]["close"]
        return ((close / prior) - 1.0) * 100.0

    ret_15m = get_ret(-16)
    ret_60m = get_ret(-61)
    ret_4h = get_ret(-241)
    
    recent = candles[-60:]
    range_pcts = [((c["high"] / c["low"]) - 1.0) * 100.0 for c in recent if c["low"] > 0]
    median_range = statistics.median(range_pcts) if range_pcts else 0.0
    p90_range = statistics.quantiles(range_pcts, n=10)[8] if len(range_pcts) >= 10 else (max(range_pcts) if range_pcts else 0.0)
    
    momentum_score = max(ret_15m, 0.0) * 5.0 + max(ret_60m, 0.0) * 3.0 + max(ret_4h, 0.0)
    movement_score = min(p90_range * 4.0, 10.0) + min(median_range * 2.0, 4.0)
    spread_penalty = min(spread_bps / 10.0, 15.0)
    pulse_score = momentum_score + movement_score - spread_penalty
    
    return {
        "product_id": product_id,
        "base_currency": pair.base,
        "quote_currency": pair.quote,
        "pulse_score": round(pulse_score, 4),
        "price": round(close, 12),
        "spread_bps": round(spread_bps, 2),
        "quote_volume_24h": round(quote_volume, 2),
        "ret_15m_pct": round(ret_15m, 4),
        "ret_60m_pct": round(ret_60m, 4),
        "ret_4h_pct": round(ret_4h, 4),
        "median_range_60m_pct": round(median_range, 4),
        "p90_range_60m_pct": round(p90_range, 4),
        "candles": len(candles),
        "candle_source": candle_source,
        "cache_age_seconds": round(cache_age_seconds, 1) if cache_age_seconds is not None else "",
    }


def main():
    args = parse_args()
    client = KrakenSpotClient()
    
    print(f"Listing Kraken pairs for quotes: {args.quote_currencies}...")
    quotes = parse_quote_currencies(args.quote_currencies)
    pairs = list_pairs(client, quotes)
    print(f"Found {len(pairs)} online pairs.")
    
    print("Fetching tickers for volume ranking...")
    tickers = fetch_tickers(client, pairs)
    
    # Rank by volume
    pair_volumes = []
    for pair in pairs:
        ticker = tickers.get(pair.rest_pair)
        if not ticker:
            continue
        vol = to_float(ticker.get("v", [0, 0])[1])
        vwp = to_float(ticker.get("p", [0, 0])[1])
        pair_volumes.append((pair, vol * vwp, ticker))
    
    pair_volumes.sort(key=lambda x: x[1], reverse=True)
    selected = [x for x in pair_volumes if x[1] >= args.min_quote_volume_usd][:args.top_products]
    print(f"Selected {len(selected)} products for candle scanning.")
    print(f"Top 20 selected: {[x[0].wsname for x in selected[:20]]}")
    # Check if DYM is in selected
    dym_selected = [x for x in selected if "DYM" in x[0].wsname]
    print(f"DYM in selected: {len(dym_selected) > 0}")
    
    cache = load_candle_cache(Path(args.cache_path))
    now_epoch = time.time()
    fetches = 0
    cache_dirty = False
    rows = []
    
    for pair, vol, ticker in selected:
        product_id = pair.wsname.replace("/", "-")
        candles, age, source = cached_candles(cache, product_id, hours=args.hours, now_epoch=now_epoch, ttl_seconds=args.cache_ttl_seconds)
        
        should_fetch = args.refresh_cache or source != "cache_hit"
        if should_fetch and (args.max_candle_fetches == 0 or fetches < args.max_candle_fetches):
            try:
                print(f"Fetching candles for {product_id}...")
                candles = fetch_recent_candles(client, pair.rest_pair, hours=args.hours)
                fetches += 1
                source = "api_fetch"
                age = 0.0
                cache["entries"][cache_key(product_id, hours=args.hours)] = {
                    "product_id": product_id,
                    "fetched_at_epoch": time.time(),
                    "candles": candles
                }
                cache_dirty = True
                time.sleep(args.request_sleep_seconds)
            except Exception as e:
                print(f"Error fetching candles for {product_id}: {e}")
                continue
        
        rows.append(score_product(pair=pair, ticker=ticker, candles=candles, candle_source=source, cache_age_seconds=age))
        
    if cache_dirty:
        save_candle_cache(Path(args.cache_path), cache)
        
    rows.sort(key=lambda x: x.get("pulse_score", 0), reverse=True)
    
    payload = {
        "generated_at": utc_now_iso(),
        "mode": "kraken_spot_pulse",
        "summary": {
            "products_scanned": len(rows),
            "candle_api_fetches": fetches
        },
        "rows": rows
    }
    
    with open(args.json_path, "w") as f:
        json.dump(payload, f, indent=2)
        
    print(f"DONE! Saved {len(rows)} products to {args.json_path}")


if __name__ == "__main__":
    main()
