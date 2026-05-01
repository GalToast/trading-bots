#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_spot_burst_lab import _load_candles, evaluate_forward_path, is_compression_breakout
from coinbase_advanced_client import CoinbaseAdvancedClient, CoinbaseAdvancedClientError


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_spot_product_filters_72h.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_spot_product_filters_72h.md"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Coinbase spot product filters from verified candle data.")
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--granularity", default="FIVE_MINUTE")
    parser.add_argument("--min-volume-24h", type=float, default=250000.0)
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--fee-bps-per-side", type=float, default=40.0)
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def _select_products(client: CoinbaseAdvancedClient, *, min_volume_24h: float, top_n: int) -> tuple[list[str], dict[str, dict[str, float]]]:
    payload = client.list_products(get_all_products=True, product_type="SPOT", limit=1000)
    candidates: list[tuple[float, float, str]] = []
    meta: dict[str, dict[str, float]] = {}
    for product in (payload.get("products") or []):
        product_id = str(product.get("product_id") or "")
        base = str(product.get("base_currency_id") or "")
        quote = str(product.get("quote_currency_id") or "")
        if quote != "USD" or not product_id or base in {"USD", "USDC", "USDT"} or product_id.startswith("USDC-"):
            continue
        try:
            pct24h = float(product.get("price_percentage_change_24h") or 0.0)
            volume_24h = float(product.get("volume_24h") or 0.0)
        except Exception:
            continue
        if volume_24h < min_volume_24h:
            continue
        candidates.append((abs(pct24h), volume_24h, product_id))
        meta[product_id] = {"pct24h": pct24h, "volume_24h": volume_24h}
    candidates.sort(reverse=True)
    return [product_id for _, _, product_id in candidates[:top_n]], meta


def _fetch_spread_pcts(client: CoinbaseAdvancedClient, product_ids: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        payload = client.best_bid_ask(product_ids)
        books = payload.get("pricebooks") or payload.get("pricebook") or []
        if isinstance(books, dict):
            books = [books]
        for book in books:
            product_id = str(book.get("product_id") or "")
            bids = book.get("bids") or []
            asks = book.get("asks") or []
            if not product_id or not bids or not asks:
                continue
            bid = float(bids[0].get("price") or 0.0)
            ask = float(asks[0].get("price") or 0.0)
            if bid > 0.0 and ask > 0.0:
                mid = (bid + ask) / 2.0
                out[product_id] = ((ask - bid) / mid) * 100.0
    except Exception:
        return out
    return out


def _compression_score(candles: list[Any], fee_bps_per_side: float) -> tuple[int, float]:
    events = [
        evaluate_forward_path(candles, idx, target_pct=0.03, stop_pct=0.01, max_hold_bars=12, fee_bps_per_side=fee_bps_per_side)
        for idx in range(len(candles))
        if is_compression_breakout(candles, idx)
    ]
    if not events:
        return 0, 0.0
    return len(events), sum(event.net_return_pct for event in events) * 100.0


def _load_candles_with_retry(
    client: CoinbaseAdvancedClient,
    product_id: str,
    *,
    start_ts: int,
    end_ts: int,
    granularity: str,
    retries: int = 4,
) -> list[Any]:
    delay = 1.0
    for attempt in range(retries):
        try:
            return _load_candles(client, product_id, start_ts=start_ts, end_ts=end_ts, granularity=granularity)
        except CoinbaseAdvancedClientError as exc:
            if "HTTP 429" not in str(exc) or attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2.0
    return []


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_md(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Coinbase Spot Product Filters",
        "",
        "- Purpose: compare product-level microstructure features against the current best surviving tactic pocket.",
        "- Pocket score uses compression breakout `3% target / 1% stop / 12 bars`.",
        "",
        "| Product | Spread % | Median Range % | >1% % | >2% % | Persistence % | Compression Signals | Compression Net % |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows[:25]:
        lines.append(
            "| {product_id} | {spread_pct:.3f}% | {median_range_pct:.3f}% | {pct_1pct:.2f}% | {pct_2pct:.2f}% | {persistence_pct:.2f}% | {compression_signals} | {compression_net_pct:.3f}% |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    end_ts = int(time.time())
    start_ts = end_ts - int(args.hours) * 3600
    product_ids, meta = _select_products(client, min_volume_24h=float(args.min_volume_24h), top_n=int(args.top_n))
    spread_pcts = _fetch_spread_pcts(client, product_ids)

    rows: list[dict[str, Any]] = []
    for product_id in product_ids:
        candles = _load_candles_with_retry(
            client,
            product_id,
            start_ts=start_ts,
            end_ts=end_ts,
            granularity=str(args.granularity),
        )
        if len(candles) < 30:
            continue
        ranges = [((candle.high / candle.low) - 1.0) * 100.0 for candle in candles if candle.low > 0.0]
        returns = [((candles[i].close / candles[i - 1].close) - 1.0) * 100.0 for i in range(1, len(candles)) if candles[i - 1].close > 0.0]
        pct_1pct = (sum(1 for value in ranges if value >= 1.0) / len(ranges)) * 100.0 if ranges else 0.0
        pct_2pct = (sum(1 for value in ranges if value >= 2.0) / len(ranges)) * 100.0 if ranges else 0.0
        persistence_hits = 0
        persistence_total = 0
        for i in range(1, len(returns)):
            if returns[i - 1] >= 1.0:
                persistence_total += 1
                if returns[i] > 0.0:
                    persistence_hits += 1
        persistence_pct = (persistence_hits / persistence_total) * 100.0 if persistence_total else 0.0
        compression_signals, compression_net_pct = _compression_score(candles, float(args.fee_bps_per_side))
        rows.append(
            {
                "product_id": product_id,
                "volume_24h": round(meta[product_id]["volume_24h"], 3),
                "pct24h": round(meta[product_id]["pct24h"], 3),
                "spread_pct": round(spread_pcts.get(product_id, 999.0), 4),
                "median_range_pct": round(statistics.median(ranges) if ranges else 0.0, 4),
                "avg_range_pct": round(statistics.fmean(ranges) if ranges else 0.0, 4),
                "pct_1pct": round(pct_1pct, 2),
                "pct_2pct": round(pct_2pct, 2),
                "persistence_pct": round(persistence_pct, 2),
                "compression_signals": compression_signals,
                "compression_net_pct": round(compression_net_pct, 4),
            }
        )

    rows.sort(key=lambda row: (row["compression_net_pct"], -row["spread_pct"], row["pct_2pct"]), reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, rows)
    _write_md(md_path, rows)
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows[:20]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
