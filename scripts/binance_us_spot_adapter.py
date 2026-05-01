from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from binance_us_client import BinanceUSBookTicker, BinanceUSClient, normalize_symbol


@dataclass
class VenueCapabilities:
    supports_short: bool = False
    supports_two_sided_inventory: bool = False
    supports_margin: bool = False
    venue: str = "binance_us_spot"


class BinanceUSSpotAdapter:
    def __init__(self, client: BinanceUSClient | None = None) -> None:
        self.client = client or BinanceUSClient()
        self.capabilities = VenueCapabilities()

    def book_ticker(self, symbol: str) -> BinanceUSBookTicker:
        return self.client.book_ticker(normalize_symbol(symbol))

    def current_market_price(self, symbol: str, direction: str) -> dict[str, Any]:
        ticker = self.book_ticker(symbol)
        side = str(direction or "").upper()
        price = ticker.ask_price if side == "BUY" else ticker.bid_price
        return {
            "ok": True,
            "symbol": ticker.symbol,
            "direction": side,
            "price": float(price),
            "bid": float(ticker.bid_price),
            "ask": float(ticker.ask_price),
            "bid_qty": float(ticker.bid_qty),
            "ask_qty": float(ticker.ask_qty),
        }

    def test_market_buy(self, *, symbol: str, quote_order_qty: float | None = None, quantity: float | None = None) -> dict[str, Any]:
        return self.client.new_order(
            symbol=normalize_symbol(symbol),
            side="BUY",
            order_type="MARKET",
            quantity=quantity,
            quote_order_qty=quote_order_qty,
            test=True,
        )

    def test_market_sell(self, *, symbol: str, quantity: float) -> dict[str, Any]:
        return self.client.new_order(
            symbol=normalize_symbol(symbol),
            side="SELL",
            order_type="MARKET",
            quantity=quantity,
            test=True,
        )

    def assert_strategy_compatible(self, *, requires_short: bool, requires_two_sided_inventory: bool) -> None:
        if requires_short:
            raise RuntimeError("Binance.US spot adapter is not strategy-compatible with short-selling lanes.")
        if requires_two_sided_inventory:
            raise RuntimeError("Binance.US spot adapter is not strategy-compatible with simultaneous long/short inventory.")
