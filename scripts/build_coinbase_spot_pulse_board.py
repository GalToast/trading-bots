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

from coinbase_advanced_client import CoinbaseAdvancedClient


REPORTS = ROOT / "reports"
DEFAULT_JSON_PATH = REPORTS / "coinbase_spot_pulse_board.json"
DEFAULT_CSV_PATH = REPORTS / "coinbase_spot_pulse_board.csv"
DEFAULT_MD_PATH = REPORTS / "coinbase_spot_pulse_board.md"
DEFAULT_CACHE_PATH = REPORTS / "cache" / "coinbase_spot_pulse_candles.json"
FIAT_OR_STABLE_QUOTES = {"USD", "USDC", "USDT", "EUR", "GBP"}

PINNED_PRODUCTS = {
    "RAVE-USD",
    "PRL-USD",
    "FARTCOIN-USD",
    "ARB-USD",
    "VVV-USD",
    "LIGHTER-USD",
    "XRP-USD",
    "DOGE-USD",
    "SUI-USD",
    "ADA-USD",
}


def parse_quote_currencies(value: str) -> set[str]:
    return {part.strip().upper() for part in value.split(",") if part.strip()}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def product_live_blockers(product: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    product_id = str(product.get("product_id") or "").upper()
    quote = product_quote_currency(product)
    if not product_id or not quote:
        blockers.append("missing_product_id_or_quote")
    if str(product.get("product_type") or "SPOT").upper() != "SPOT":
        blockers.append("not_spot")
    if str(product.get("status") or "").lower() != "online":
        blockers.append("not_online")
    for flag in ("trading_disabled", "cancel_only", "post_only", "limit_only", "auction_mode"):
        if to_bool(product.get(flag)):
            blockers.append(flag)
    if to_float(product.get("quote_min_size")) <= 0.0:
        blockers.append("missing_quote_min_size")
    if to_float(product.get("base_min_size")) <= 0.0:
        blockers.append("missing_base_min_size")
    return blockers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a live Coinbase spot pulse board for hot-symbol rotation.")
    parser.add_argument("--top-products", type=int, default=80, help="Top liquid spot products to candle-scan.")
    parser.add_argument("--hours", type=int, default=6, help="Recent candle window for pulse scoring.")
    parser.add_argument("--granularity", default="ONE_MINUTE")
    parser.add_argument("--quote-currencies", default="USD", help="Comma-separated quote currencies to scan, e.g. USD,USDC,BTC,ETH.")
    parser.add_argument("--all-spot-quotes", action="store_true", help="Scan every online Coinbase spot quote currency.")
    parser.add_argument("--top-per-quote", type=int, default=20, help="Also scan this many top products per selected quote currency.")
    parser.add_argument("--min-quote-volume-usd", type=float, default=50_000.0, help="Minimum native 24h quote volume for the product.")
    parser.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    parser.add_argument("--cache-ttl-seconds", type=float, default=300.0, help="Reuse local candles younger than this many seconds.")
    parser.add_argument("--max-candle-fetches", type=int, default=0, help="Maximum stale/missing candle API calls this run; 0 means no cap.")
    parser.add_argument("--request-sleep-seconds", type=float, default=0.02, help="Pause between candle API calls.")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore cached candles and refresh selected products.")
    parser.add_argument("--json-path", default=str(DEFAULT_JSON_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def product_quote_volume(product: dict[str, Any]) -> float:
    return max(
        to_float(product.get("approximate_quote_24h_volume")),
        to_float(product.get("approx_quote_24h_volume")),
        to_float(product.get("volume_24h")),
        to_float(product.get("volume_24_h")),
    )


def product_quote_currency(product: dict[str, Any]) -> str:
    quote = str(product.get("quote_currency_id") or product.get("quote_currency") or "").upper()
    if quote:
        return quote
    product_id = str(product.get("product_id") or "").upper()
    return product_id.rsplit("-", 1)[-1] if "-" in product_id else ""


def product_base_currency(product: dict[str, Any]) -> str:
    base = str(product.get("base_currency_id") or product.get("base_currency") or "").upper()
    if base:
        return base
    product_id = str(product.get("product_id") or "").upper()
    return product_id.rsplit("-", 1)[0] if "-" in product_id else ""


def product_is_allowed_spot(product: dict[str, Any], *, quote_currencies: set[str], all_spot_quotes: bool) -> bool:
    quote = product_quote_currency(product)
    if not all_spot_quotes and quote not in quote_currencies:
        return False
    return not product_live_blockers(product)


def volume_floor_for_quote(quote_currency: str, min_quote_volume: float) -> float:
    if quote_currency in FIAT_OR_STABLE_QUOTES:
        return min_quote_volume
    return 0.0


def select_products(
    products: list[dict[str, Any]],
    *,
    top_products: int,
    top_per_quote: int,
    min_quote_volume_usd: float,
    quote_currencies: set[str],
    all_spot_quotes: bool,
) -> list[dict[str, Any]]:
    candidates = [
        p
        for p in products
        if product_is_allowed_spot(p, quote_currencies=quote_currencies, all_spot_quotes=all_spot_quotes)
    ]
    liquid = [
        p
        for p in candidates
        if product_quote_volume(p) >= volume_floor_for_quote(product_quote_currency(p), min_quote_volume_usd)
    ]
    selected_by_id: dict[str, dict[str, Any]] = {}
    for product in sorted(liquid, key=product_quote_volume, reverse=True)[:top_products]:
        selected_by_id[str(product.get("product_id") or "")] = product
    if top_per_quote > 0:
        products_by_quote: dict[str, list[dict[str, Any]]] = {}
        for product in liquid:
            products_by_quote.setdefault(product_quote_currency(product), []).append(product)
        for quote_products in products_by_quote.values():
            for product in sorted(quote_products, key=product_quote_volume, reverse=True)[:top_per_quote]:
                selected_by_id[str(product.get("product_id") or "")] = product
    for product in candidates:
        product_id = str(product.get("product_id") or "")
        if product_id in PINNED_PRODUCTS:
            selected_by_id[product_id] = product
    return sorted(selected_by_id.values(), key=product_quote_volume, reverse=True)


def fetch_pricebooks(client: CoinbaseAdvancedClient, product_ids: list[str], *, chunk_size: int = 100) -> dict[str, dict[str, float]]:
    books: dict[str, dict[str, float]] = {}
    for index in range(0, len(product_ids), chunk_size):
        chunk = product_ids[index : index + chunk_size]
        payload = client.best_bid_ask(chunk)
        for book in payload.get("pricebooks") or []:
            product_id = str(book.get("product_id") or "")
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not bids or not asks:
                continue
            bid = to_float(bids[0].get("price"))
            ask = to_float(asks[0].get("price"))
            if bid <= 0.0 or ask <= 0.0:
                continue
            mid = (bid + ask) / 2.0
            books[product_id] = {
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_bps": ((ask - bid) / mid) * 10_000.0 if mid > 0.0 else 0.0,
            }
    return books


def fetch_recent_candles(client: CoinbaseAdvancedClient, product_id: str, *, hours: int, granularity: str) -> list[dict[str, float]]:
    now = int(time.time())
    # Calculate granularity in seconds
    granularity_seconds = {"ONE_MINUTE": 60, "FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900, "ONE_HOUR": 3600, "SIX_HOURS": 21600, "ONE_DAY": 86400}.get(granularity, 60)
    # Calculate max hours to stay under 349 candles (API limit)
    max_hours = int(349 * granularity_seconds / 3600)
    if hours > max_hours:
        hours = max_hours
    start = now - int(hours) * 3600
    payload = client.market_candles(product_id, start=start, end=now, granularity=granularity, limit=350)
    dedup: dict[int, dict[str, float]] = {}
    for row in payload.get("candles") or []:
        ts = int(row["start"])
        dedup[ts] = {
            "start": float(ts),
            "open": to_float(row.get("open")),
            "high": to_float(row.get("high")),
            "low": to_float(row.get("low")),
            "close": to_float(row.get("close")),
            "volume": to_float(row.get("volume")),
        }
    return [dedup[key] for key in sorted(dedup.keys())]


def cache_key(product_id: str, *, hours: int, granularity: str) -> str:
    return f"{product_id.upper()}|{granularity.upper()}|{int(hours)}h"


def load_candle_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "entries": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "entries": {}}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        payload["entries"] = {}
    return payload


def save_candle_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def cached_candles(
    cache: dict[str, Any],
    product_id: str,
    *,
    hours: int,
    granularity: str,
    now_epoch: float,
    ttl_seconds: float,
) -> tuple[list[dict[str, float]], float | None, str]:
    entry = (cache.get("entries") or {}).get(cache_key(product_id, hours=hours, granularity=granularity)) or {}
    candles = entry.get("candles") if isinstance(entry, dict) else None
    fetched_at = to_float(entry.get("fetched_at_epoch")) if isinstance(entry, dict) else 0.0
    if not isinstance(candles, list):
        return [], None, "missing"
    age = max(0.0, now_epoch - fetched_at) if fetched_at > 0 else None
    if age is not None and age <= ttl_seconds:
        return candles, age, "cache_hit"
    return candles, age, "cache_stale"


def update_candle_cache(
    cache: dict[str, Any],
    product_id: str,
    *,
    hours: int,
    granularity: str,
    candles: list[dict[str, float]],
    now_epoch: float,
) -> None:
    cache.setdefault("version", 1)
    cache.setdefault("entries", {})
    cache["entries"][cache_key(product_id, hours=hours, granularity=granularity)] = {
        "product_id": product_id,
        "hours": int(hours),
        "granularity": granularity,
        "fetched_at": datetime.fromtimestamp(now_epoch, timezone.utc).isoformat(),
        "fetched_at_epoch": now_epoch,
        "candles": candles,
    }


def pct_change(current: float, prior: float) -> float:
    if prior <= 0.0:
        return 0.0
    return ((current / prior) - 1.0) * 100.0


def candle_at_offset(candles: list[dict[str, float]], offset: int) -> dict[str, float] | None:
    if len(candles) <= abs(offset):
        return None
    return candles[offset]


def score_product(
    *,
    product: dict[str, Any],
    book: dict[str, float],
    candles: list[dict[str, float]],
    candle_source: str = "api_fetch",
    cache_age_seconds: float | None = None,
) -> dict[str, Any]:
    product_id = str(product.get("product_id") or "")
    quote_volume = product_quote_volume(product)
    base_currency = product_base_currency(product)
    quote_currency = product_quote_currency(product)
    blockers = product_live_blockers(product)
    live_route_state = "ready_direct_usd_or_stable" if quote_currency in {"USD", "USDC"} else "requires_quote_inventory_or_conversion_costing"
    if len(candles) < 20:
        return {
            "product_id": product_id,
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "live_tradable": not blockers,
            "live_blockers": ",".join(blockers),
            "live_route_state": live_route_state,
            "candle_source": candle_source,
            "cache_age_seconds": round(cache_age_seconds, 1) if cache_age_seconds is not None else "",
            "status": "insufficient_candles",
            "pulse_score": 0.0,
            "quote_volume_native": quote_volume,
            "spread_bps": round(book.get("spread_bps", 0.0), 2),
            "candles": len(candles),
        }

    latest = candles[-1]
    close = latest["close"]
    c15 = candle_at_offset(candles, -16)
    c60 = candle_at_offset(candles, -61)
    c240 = candle_at_offset(candles, -241)
    ret_15m = pct_change(close, c15["close"]) if c15 else 0.0
    ret_60m = pct_change(close, c60["close"]) if c60 else pct_change(close, candles[0]["close"])
    ret_4h = pct_change(close, c240["close"]) if c240 else pct_change(close, candles[0]["close"])
    recent = candles[-60:] if len(candles) >= 60 else candles
    range_pcts = [
        ((row["high"] - row["low"]) / row["open"]) * 100.0
        for row in recent
        if row["open"] > 0.0 and row["high"] >= row["low"]
    ]
    median_range_pct = statistics.median(range_pcts) if range_pcts else 0.0
    p90_range_pct = statistics.quantiles(range_pcts, n=10)[8] if len(range_pcts) >= 10 else (max(range_pcts) if range_pcts else 0.0)
    spread_bps = book.get("spread_bps", 0.0)
    volume_score = min(max(quote_volume, 0.0) / 5_000_000.0, 8.0)
    momentum_score = max(ret_15m, 0.0) * 5.0 + max(ret_60m, 0.0) * 3.0 + max(ret_4h, 0.0)
    movement_score = min(p90_range_pct * 4.0, 10.0) + min(median_range_pct * 2.0, 4.0)
    spread_penalty = min(spread_bps / 10.0, 15.0)
    pulse_score = momentum_score + movement_score + volume_score - spread_penalty
    if ret_15m > 0.0 and ret_60m > 0.25 and spread_bps <= 80.0:
        pulse_state = "hot_momentum"
    elif ret_60m > 0.0 and p90_range_pct >= 0.2 and spread_bps <= 120.0:
        pulse_state = "warming"
    elif spread_bps > 150.0:
        pulse_state = "too_wide"
    else:
        pulse_state = "cold_or_chop"
    return {
        "product_id": product_id,
        "base_currency": base_currency,
        "quote_currency": quote_currency,
        "live_tradable": not blockers,
        "live_blockers": ",".join(blockers),
        "live_route_state": live_route_state,
        "candle_source": candle_source,
        "cache_age_seconds": round(cache_age_seconds, 1) if cache_age_seconds is not None else "",
        "status": "ok",
        "pulse_state": pulse_state,
        "pulse_score": round(pulse_score, 4),
        "price": round(close, 12),
        "bid": book.get("bid"),
        "ask": book.get("ask"),
        "spread_bps": round(spread_bps, 2),
        "quote_volume_native": round(quote_volume, 2),
        "ret_15m_pct": round(ret_15m, 4),
        "ret_60m_pct": round(ret_60m, 4),
        "ret_4h_pct": round(ret_4h, 4),
        "median_range_60m_pct": round(median_range_pct, 4),
        "p90_range_60m_pct": round(p90_range_pct, 4),
        "quote_min_size": to_float(product.get("quote_min_size")),
        "base_min_size": to_float(product.get("base_min_size")),
        "candles": len(candles),
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    client = CoinbaseAdvancedClient()
    products_payload = client.list_products(get_all_products=True, product_type="SPOT", limit=1000)
    products = products_payload.get("products") or []
    quote_currencies = parse_quote_currencies(str(args.quote_currencies))
    selected = select_products(
        products,
        top_products=int(args.top_products),
        top_per_quote=int(args.top_per_quote),
        min_quote_volume_usd=float(args.min_quote_volume_usd),
        quote_currencies=quote_currencies,
        all_spot_quotes=bool(args.all_spot_quotes),
    )
    product_ids = [str(product.get("product_id") or "") for product in selected]
    books = fetch_pricebooks(client, product_ids)
    cache_path = Path(str(args.cache_path))
    cache = load_candle_cache(cache_path)
    cache_dirty = False
    now_epoch = time.time()
    max_fetches = max(0, int(args.max_candle_fetches))
    fetches = 0
    cache_stats = Counter()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for product in selected:
        product_id = str(product.get("product_id") or "")
        book = books.get(product_id)
        if not book:
            errors.append({"product_id": product_id, "error": "missing_pricebook"})
            continue
        try:
            candles, cache_age, candle_source = cached_candles(
                cache,
                product_id,
                hours=int(args.hours),
                granularity=str(args.granularity),
                now_epoch=now_epoch,
                ttl_seconds=float(args.cache_ttl_seconds),
            )
            should_fetch = bool(args.refresh_cache) or candle_source != "cache_hit"
            if should_fetch and (max_fetches <= 0 or fetches < max_fetches):
                candles = fetch_recent_candles(
                    client,
                    product_id,
                    hours=int(args.hours),
                    granularity=str(args.granularity),
                )
                fetches += 1
                candle_source = "api_fetch"
                cache_age = 0.0
                update_candle_cache(
                    cache,
                    product_id,
                    hours=int(args.hours),
                    granularity=str(args.granularity),
                    candles=candles,
                    now_epoch=time.time(),
                )
                cache_dirty = True
                if float(args.request_sleep_seconds) > 0.0:
                    time.sleep(float(args.request_sleep_seconds))
            elif candle_source == "cache_stale":
                candle_source = "stale_cache_cap" if max_fetches > 0 else "cache_stale"
            elif candle_source == "missing":
                errors.append({"product_id": product_id, "error": "missing_candles_fetch_cap"})
                cache_stats["missing_fetch_cap"] += 1
                continue
            cache_stats[candle_source] += 1
            rows.append(
                score_product(
                    product=product,
                    book=book,
                    candles=candles,
                    candle_source=candle_source,
                    cache_age_seconds=cache_age,
                )
            )
        except Exception as exc:
            errors.append({"product_id": product_id, "error": str(exc)[:300]})
    if cache_dirty:
        save_candle_cache(cache_path, cache)

    rows.sort(key=lambda row: (float(row.get("pulse_score") or 0.0), float(row.get("ret_60m_pct") or 0.0)), reverse=True)
    hot = [row for row in rows if row.get("pulse_state") == "hot_momentum"]
    warming = [row for row in rows if row.get("pulse_state") == "warming"]
    return {
        "generated_at": utc_now_iso(),
        "mode": "coinbase_spot_pulse",
        "parameters": {
            "top_products": int(args.top_products),
            "top_per_quote": int(args.top_per_quote),
            "hours": int(args.hours),
            "granularity": str(args.granularity),
            "quote_currencies": sorted(quote_currencies),
            "all_spot_quotes": bool(args.all_spot_quotes),
            "min_quote_volume_native": float(args.min_quote_volume_usd),
            "non_fiat_quote_volume_floor": 0.0,
            "cache_path": str(cache_path),
            "cache_ttl_seconds": float(args.cache_ttl_seconds),
            "max_candle_fetches": max_fetches,
        },
        "summary": {
            "products_considered": len(products),
            "products_scanned": len(rows),
            "errors": len(errors),
            "hot_momentum": len(hot),
            "warming": len(warming),
            "live_tradable_rows": sum(1 for row in rows if row.get("live_tradable")),
            "quote_currencies_scanned": sorted({str(row.get("quote_currency") or "") for row in rows if row.get("quote_currency")}),
            "cache_stats": dict(sorted(cache_stats.items())),
            "candle_api_fetches": fetches,
        },
        "leadership_read": [
            "This board is a spot-only scout, not a trading permission slip.",
            "Hot rows are candidates for shadow lane launch or temporary allocation only after fee, spread, and runtime proof checks.",
            "Non-USD quote rows are direct Coinbase spot products; any bankroll conversion into or out of the quote asset must be costed separately before live use.",
            "A tiny account should use this board to rotate attention, not to spray capital across every green candle.",
        ],
        "rows": rows,
        "errors": errors,
    }


def write_reports(payload: dict[str, Any], *, json_path: Path, csv_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    columns = [
        "product_id",
        "base_currency",
        "quote_currency",
        "live_tradable",
        "live_route_state",
        "pulse_state",
        "pulse_score",
        "price",
        "spread_bps",
        "quote_volume_native",
        "ret_15m_pct",
        "ret_60m_pct",
        "ret_4h_pct",
        "median_range_60m_pct",
        "p90_range_60m_pct",
        "quote_min_size",
        "candles",
        "candle_source",
        "cache_age_seconds",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({column: row.get(column, "") for column in columns})

    lines = [
        "# Coinbase Spot Pulse Board",
        "",
        "## Leadership Read",
        "",
    ]
    for line in payload["leadership_read"]:
        lines.append(f"- {line}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Products scanned: `{payload['summary']['products_scanned']}`",
            f"- Live-tradable rows: `{payload['summary']['live_tradable_rows']}`",
            f"- Hot momentum rows: `{payload['summary']['hot_momentum']}`",
            f"- Warming rows: `{payload['summary']['warming']}`",
            f"- Errors: `{payload['summary']['errors']}`",
            f"- Candle API fetches: `{payload['summary'].get('candle_api_fetches', 0)}`",
            f"- Cache stats: `{payload['summary'].get('cache_stats', {})}`",
            f"- Quote currencies scanned: `{', '.join(payload['summary'].get('quote_currencies_scanned', []))}`",
            "",
            "## Quote Breakdown",
            "",
            "| Quote | Products | Hot | Warming | Too Wide | Cold/Chop | Insufficient |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    quote_counts = Counter(str(row.get("quote_currency") or "") for row in payload["rows"])
    quote_state_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in payload["rows"]:
        quote = str(row.get("quote_currency") or "")
        state = str(row.get("pulse_state") or row.get("status") or "unknown")
        quote_state_counts[quote][state] += 1
    for quote in sorted(quote_counts, key=lambda item: (-quote_counts[item], item)):
        states = quote_state_counts[quote]
        lines.append(
            "| {quote} | {products} | {hot} | {warming} | {too_wide} | {cold} | {insufficient} |".format(
                quote=quote,
                products=quote_counts[quote],
                hot=states["hot_momentum"],
                warming=states["warming"],
                too_wide=states["too_wide"],
                cold=states["cold_or_chop"],
                insufficient=states["insufficient_candles"],
            )
        )
    lines.extend(
        [
            "",
            "## Top Pulse Rows",
            "",
            "| Product | Quote | Live Route | State | Score | 15m % | 60m % | 4h % | Spread bps | Quote Vol | P90 Range % | Candles |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in payload["rows"][:40]:
        render_row = {
            "pulse_state": row.get("pulse_state") or row.get("status") or "",
            "pulse_score": 0.0,
            "ret_15m_pct": 0.0,
            "ret_60m_pct": 0.0,
            "ret_4h_pct": 0.0,
            "spread_bps": 0.0,
            "quote_volume_native": 0.0,
            "p90_range_60m_pct": 0.0,
            "candles": 0,
            "quote_currency": "",
            "live_route_state": "",
            **row,
        }
        lines.append(
            "| {product_id} | {quote_currency} | {live_route_state} | {pulse_state} | {pulse_score:.4f} | {ret_15m_pct:.4f} | {ret_60m_pct:.4f} | {ret_4h_pct:.4f} | {spread_bps:.2f} | {quote_volume_native:.2f} | {p90_range_60m_pct:.4f} | {candles} |".format(**render_row)
        )
    rows_by_quote: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in payload["rows"]:
        rows_by_quote[str(row.get("quote_currency") or "")].append(row)
    lines.extend(["", "## Top Rows By Quote", ""])
    for quote in sorted(rows_by_quote, key=lambda item: (-len(rows_by_quote[item]), item)):
        lines.extend(
            [
                f"### {quote}",
                "",
                "| Product | Live Route | State | Score | 15m % | 60m % | Spread bps | Candles | Source |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        quote_rows = sorted(rows_by_quote[quote], key=lambda row: float(row.get("pulse_score") or 0.0), reverse=True)
        for row in quote_rows[:10]:
            render_row = {
                "pulse_state": row.get("pulse_state") or row.get("status") or "",
                "pulse_score": 0.0,
                "ret_15m_pct": 0.0,
                "ret_60m_pct": 0.0,
                "spread_bps": 0.0,
                "candles": 0,
                "live_route_state": "",
                "candle_source": "",
                **row,
            }
            lines.append(
                "| {product_id} | {live_route_state} | {pulse_state} | {pulse_score:.4f} | {ret_15m_pct:.4f} | {ret_60m_pct:.4f} | {spread_bps:.2f} | {candles} | {candle_source} |".format(
                    **render_row
                )
            )
        lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    payload = build_payload(args)
    write_reports(
        payload,
        json_path=Path(args.json_path),
        csv_path=Path(args.csv_path),
        md_path=Path(args.md_path),
    )
    print(json.dumps({"json_path": args.json_path, "csv_path": args.csv_path, "md_path": args.md_path, "summary": payload["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
