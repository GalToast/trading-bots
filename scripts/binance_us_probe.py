#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

import binance_us_config as cfg
from binance_us_client import BinanceUSClient, normalize_symbol
from binance_us_spot_adapter import BinanceUSSpotAdapter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Binance.US public/auth connectivity for this repo.")
    parser.add_argument("--symbol", default=cfg.DEFAULT_SYMBOL)
    parser.add_argument("--account", action="store_true")
    parser.add_argument("--test-buy-quote", type=float, default=None, help="Run /order/test market BUY using quoteOrderQty")
    parser.add_argument("--test-sell-qty", type=float, default=None, help="Run /order/test market SELL using quantity")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbol = normalize_symbol(args.symbol)
    client = BinanceUSClient()
    spot = BinanceUSSpotAdapter(client)
    output: dict[str, object] = {
        "base_url": client.base_url,
        "symbol": symbol,
        "has_auth": client.has_auth(),
        "capabilities": spot.capabilities.__dict__,
    }
    output["ping"] = client.ping()
    output["server_time"] = client.server_time()
    ticker = spot.current_market_price(symbol, "BUY")
    output["book_ticker"] = ticker
    if args.account:
        output["account"] = client.account()
    if args.test_buy_quote is not None:
        output["test_market_buy"] = spot.test_market_buy(symbol=symbol, quote_order_qty=float(args.test_buy_quote))
    if args.test_sell_qty is not None:
        output["test_market_sell"] = spot.test_market_sell(symbol=symbol, quantity=float(args.test_sell_qty))
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
