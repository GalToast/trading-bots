from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from coinbase_advanced_client import CoinbaseAdvancedClient, CoinbasePublicTicker, normalize_product_id


@dataclass
class VenueCapabilities:
    supports_short: bool = True
    supports_two_sided_inventory: bool = False
    supports_spot: bool = True
    supports_futures: bool = True
    supports_weekend_crypto: bool = True
    venue: str = "coinbase_advanced"


class CoinbaseAdvancedAdapter:
    def __init__(self, client: CoinbaseAdvancedClient | None = None) -> None:
        self.client = client or CoinbaseAdvancedClient()
        self.capabilities = VenueCapabilities()

    def public_ticker(self, product_id: str) -> CoinbasePublicTicker:
        return self.client.public_exchange_ticker(normalize_product_id(product_id))

    def current_market_price(self, product_id: str, direction: str) -> dict[str, Any]:
        ticker = self.public_ticker(product_id)
        side = str(direction or "").upper()
        price = ticker.ask_price if side == "BUY" else ticker.bid_price
        return {
            "ok": True,
            "product_id": ticker.product_id,
            "direction": side,
            "price": float(price),
            "bid": float(ticker.bid_price),
            "ask": float(ticker.ask_price),
            "last": float(ticker.price),
            "volume": float(ticker.volume),
            "time": ticker.time,
        }

    def test_market_buy(
        self,
        *,
        product_id: str,
        quote_size: float | None = None,
        base_size: float | None = None,
        futures_mode: bool = False,
        sandbox: bool = False,
    ) -> dict[str, Any]:
        return self.client.create_market_order(
            product_id=normalize_product_id(product_id),
            side="BUY",
            base_size=base_size,
            quote_size=quote_size,
            client_order_id=str(uuid.uuid4()),
            futures_mode=futures_mode,
            sandbox=sandbox,
        )

    def test_market_sell(
        self,
        *,
        product_id: str,
        base_size: float,
        futures_mode: bool = False,
        sandbox: bool = False,
    ) -> dict[str, Any]:
        return self.client.create_market_order(
            product_id=normalize_product_id(product_id),
            side="SELL",
            base_size=base_size,
            quote_size=None,
            client_order_id=str(uuid.uuid4()),
            futures_mode=futures_mode,
            sandbox=sandbox,
        )

    def assert_strategy_compatible(self, *, requires_short: bool, requires_two_sided_inventory: bool) -> None:
        if requires_short and not self.capabilities.supports_short:
            raise RuntimeError("Coinbase adapter is not strategy-compatible with short-selling lanes.")
        if requires_two_sided_inventory and not self.capabilities.supports_two_sided_inventory:
            raise RuntimeError(
                "Coinbase adapter is not a drop-in fit for simultaneous long/short inventory; the venue should be treated as net-position based."
            )
