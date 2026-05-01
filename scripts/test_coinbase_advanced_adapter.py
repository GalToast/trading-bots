#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_adapter import CoinbaseAdvancedAdapter


class FakeClient:
    def public_exchange_ticker(self, product_id: str):
        from coinbase_advanced_client import CoinbasePublicTicker

        return CoinbasePublicTicker(
            product_id=product_id,
            price=72690.0,
            bid_price=72680.0,
            ask_price=72700.0,
            size=0.1,
            volume=123.45,
            time="2026-04-11T00:00:00Z",
        )


class CoinbaseAdvancedAdapterTests(unittest.TestCase):
    def test_current_market_price_uses_executable_side(self) -> None:
        adapter = CoinbaseAdvancedAdapter(client=FakeClient())
        buy = adapter.current_market_price("BTC-USD", "BUY")
        sell = adapter.current_market_price("BTC-USD", "SELL")
        self.assertEqual(buy["price"], 72700.0)
        self.assertEqual(sell["price"], 72680.0)

    def test_strategy_compatibility_rejects_two_sided_inventory(self) -> None:
        adapter = CoinbaseAdvancedAdapter(client=FakeClient())
        with self.assertRaises(RuntimeError):
            adapter.assert_strategy_compatible(requires_short=True, requires_two_sided_inventory=True)


if __name__ == "__main__":
    unittest.main()
