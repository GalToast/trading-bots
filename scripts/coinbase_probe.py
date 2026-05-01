#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

import coinbase_config as cfg
from coinbase_advanced_adapter import CoinbaseAdvancedAdapter
from coinbase_advanced_client import CoinbaseAdvancedClient, CoinbaseAdvancedClientError, normalize_product_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Coinbase Advanced/public connectivity for this repo.")
    parser.add_argument("--product-id", default=cfg.DEFAULT_PRODUCT_ID)
    parser.add_argument("--accounts", action="store_true")
    parser.add_argument("--products", action="store_true")
    parser.add_argument("--all-products", action="store_true")
    parser.add_argument("--product-type", default=None, help="Optional product_type filter, e.g. SPOT or FUTURE")
    parser.add_argument("--products-summary", action="store_true", help="Return a compact products summary instead of the full catalog")
    parser.add_argument("--limit", type=int, default=None, help="Optional product list limit")
    parser.add_argument("--get-product", action="store_true", help="Fetch full details for --product-id")
    parser.add_argument("--best-bid-ask", action="store_true")
    parser.add_argument("--permissions", action="store_true")
    parser.add_argument("--sandbox-accounts", action="store_true")
    parser.add_argument("--sandbox-market-buy-quote", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    product_id = normalize_product_id(args.product_id)
    client = CoinbaseAdvancedClient()
    adapter = CoinbaseAdvancedAdapter(client)
    try:
        ticker_payload: dict[str, object] = adapter.current_market_price(product_id, "BUY")
    except CoinbaseAdvancedClientError as exc:
        ticker_payload = {
            "ok": False,
            "product_id": product_id,
            "error": str(exc),
        }
    output: dict[str, object] = {
        "base_url": client.base_url,
        "exchange_public_url": client.exchange_public_url,
        "sandbox_base_url": client.sandbox_base_url,
        "product_id": product_id,
        "has_auth": client.has_auth(),
        "capabilities": adapter.capabilities.__dict__,
        "public_ticker": ticker_payload,
    }
    if args.accounts:
        output["accounts"] = client.accounts()
    if args.products:
        products_payload = client.list_products(
            get_all_products=args.all_products,
            product_type=args.product_type,
            limit=args.limit,
        )
        if args.products_summary:
            products = products_payload.get("products", []) or []
            summary = {
                "num_products": products_payload.get("num_products"),
                "count": len(products),
                "product_ids": [p.get("product_id") for p in products[:50]],
                "product_types": sorted({p.get("product_type") for p in products if p.get("product_type")}),
                "venues": sorted({p.get("product_venue") for p in products if p.get("product_venue")}),
            }
            output["products"] = summary
        else:
            output["products"] = products_payload
    if args.get_product:
        output["product"] = client.get_product(product_id)
    if args.best_bid_ask:
        output["best_bid_ask"] = client.best_bid_ask([product_id])
    if args.permissions:
        output["api_key_permissions"] = client.api_key_permissions()
    if args.sandbox_accounts:
        output["sandbox_accounts"] = client.accounts(sandbox=True)
    if args.sandbox_market_buy_quote is not None:
        output["sandbox_market_buy"] = adapter.test_market_buy(
            product_id=product_id,
            quote_size=float(args.sandbox_market_buy_quote),
            sandbox=True,
        )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
