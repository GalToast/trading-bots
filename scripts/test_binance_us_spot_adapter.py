#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from binance_us_spot_adapter import BinanceUSSpotAdapter


class FakeClient:
    def book_ticker(self, symbol: str):
        from binance_us_client import BinanceUSBookTicker

        return BinanceUSBookTicker(symbol=symbol, bid_price=72500.0, bid_qty=1.2, ask_price=72510.0, ask_qty=0.8)


class BinanceUSSpotAdapterTests(unittest.TestCase):
    def test_current_market_price_uses_executable_side(self) -> None:
        adapter = BinanceUSSpotAdapter(client=FakeClient())
        buy = adapter.current_market_price("BTCUSD", "BUY")
        sell = adapter.current_market_price("BTCUSD", "SELL")
        self.assertEqual(buy["price"], 72510.0)
        self.assertEqual(sell["price"], 72500.0)

    def test_strategy_compatibility_rejects_short(self) -> None:
        adapter = BinanceUSSpotAdapter(client=FakeClient())
        with self.assertRaises(RuntimeError):
            adapter.assert_strategy_compatible(requires_short=True, requires_two_sided_inventory=False)


if __name__ == "__main__":
    unittest.main()
