#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClient


DEFAULT_CSV_PATH = ROOT / "reports" / "coinbase_cross_quote_scan.csv"
DEFAULT_MD_PATH = ROOT / "reports" / "coinbase_cross_quote_scan.md"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan Coinbase spot cross-quote direct vs implied price gaps.")
    parser.add_argument("--min-direct-volume", type=float, default=250000.0)
    parser.add_argument("--max-bases", type=int, default=120)
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    return parser.parse_args()


def _product_map(client: CoinbaseAdvancedClient) -> dict[str, dict[str, dict[str, Any]]]:
    payload = client.list_products(get_all_products=True, product_type="SPOT", limit=1000)
    by_base: dict[str, dict[str, dict[str, Any]]] = {}
    for product in (payload.get("products") or []):
        product_id = str(product.get("product_id") or "")
        base = str(product.get("base_currency_id") or "")
        quote = str(product.get("quote_currency_id") or "")
        if not product_id or not base or not quote:
            continue
        by_base.setdefault(base, {})[quote] = product
    return by_base


def _fetch_books(client: CoinbaseAdvancedClient, product_ids: list[str]) -> dict[str, dict[str, float]]:
    payload = client.best_bid_ask(product_ids)
    books = payload.get("pricebooks") or payload.get("pricebook") or []
    if isinstance(books, dict):
        books = [books]
    out: dict[str, dict[str, float]] = {}
    for book in books:
        product_id = str(book.get("product_id") or "")
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not product_id or not bids or not asks:
            continue
        bid = float(bids[0].get("price") or 0.0)
        ask = float(asks[0].get("price") or 0.0)
        if bid > 0.0 and ask > 0.0:
            out[product_id] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}
    return out


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
        "# Coinbase Cross Quote Scan",
        "",
        "- Purpose: compare direct spot prices against implied prices via BTC or ETH quote paths.",
        "- Interpretation: raw mispricing is not executable edge by itself; spreads and fees still decide viability.",
        "",
        "| Base | Route | Direct | Implied | Gap % | Direct Spread % | Route Spread % | Direct 24h Volume |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows[:30]:
        lines.append(
            "| {base} | {route} | {direct_mid:.8f} | {implied_mid:.8f} | {gap_pct:.4f}% | {direct_spread_pct:.4f}% | {route_spread_pct:.4f}% | {direct_volume_24h:.2f} |".format(
                **row
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    client = CoinbaseAdvancedClient()
    by_base = _product_map(client)

    candidate_bases: list[tuple[float, str]] = []
    for base, quotes in by_base.items():
        usd_product = quotes.get("USD")
        if not usd_product:
            continue
        volume_24h = float(usd_product.get("volume_24h") or 0.0)
        if volume_24h < float(args.min_direct_volume):
            continue
        if "BTC" in quotes or "ETH" in quotes:
            candidate_bases.append((volume_24h, base))
    candidate_bases.sort(reverse=True)
    selected_bases = [base for _, base in candidate_bases[: int(args.max_bases)]]

    needed_products = {"BTC-USD", "ETH-USD"}
    for base in selected_bases:
        quotes = by_base[base]
        needed_products.add(f"{base}-USD")
        if "BTC" in quotes:
            needed_products.add(f"{base}-BTC")
        if "ETH" in quotes:
            needed_products.add(f"{base}-ETH")
    books = _fetch_books(client, sorted(needed_products))

    btc_usd = books.get("BTC-USD")
    eth_usd = books.get("ETH-USD")
    rows: list[dict[str, Any]] = []
    for base in selected_bases:
        direct_product = by_base[base].get("USD")
        direct_book = books.get(f"{base}-USD")
        if not direct_product or not direct_book:
            continue
        direct_spread_pct = ((direct_book["ask"] - direct_book["bid"]) / direct_book["mid"]) * 100.0
        direct_volume_24h = float(direct_product.get("volume_24h") or 0.0)

        for quote, bridge_book in (("BTC", btc_usd), ("ETH", eth_usd)):
            cross_book = books.get(f"{base}-{quote}")
            if not cross_book or not bridge_book:
                continue
            implied_bid = cross_book["bid"] * bridge_book["bid"]
            implied_ask = cross_book["ask"] * bridge_book["ask"]
            implied_mid = (implied_bid + implied_ask) / 2.0
            route_spread_pct = ((implied_ask - implied_bid) / implied_mid) * 100.0 if implied_mid > 0.0 else 999.0
            gap_pct = ((direct_book["mid"] / implied_mid) - 1.0) * 100.0 if implied_mid > 0.0 else 0.0
            rows.append(
                {
                    "base": base,
                    "route": f"{base}-{quote} -> {quote}-USD",
                    "direct_product": f"{base}-USD",
                    "cross_product": f"{base}-{quote}",
                    "bridge_product": f"{quote}-USD",
                    "direct_mid": round(direct_book["mid"], 10),
                    "implied_mid": round(implied_mid, 10),
                    "gap_pct": round(gap_pct, 6),
                    "direct_spread_pct": round(direct_spread_pct, 6),
                    "route_spread_pct": round(route_spread_pct, 6),
                    "direct_volume_24h": round(direct_volume_24h, 3),
                }
            )

    rows.sort(key=lambda row: abs(row["gap_pct"]), reverse=True)
    csv_path = Path(args.csv_path)
    md_path = Path(args.md_path)
    _write_csv(csv_path, rows)
    _write_md(md_path, rows)
    print(json.dumps({"csv_path": str(csv_path), "md_path": str(md_path), "rows": rows[:30]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
