#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coinbase_advanced_client import CoinbaseAdvancedClientError
from coinbase_fee_model import rate_to_bps, resolve_spot_fee_tier


class CoinbaseFeeModelTests(unittest.TestCase):
    def test_rate_to_bps_converts_decimal_fee_rate(self) -> None:
        self.assertEqual(rate_to_bps("0.0060"), 60.0)

    def test_resolve_spot_fee_tier_uses_transaction_summary(self) -> None:
        class FakeClient:
            def has_auth(self) -> bool:
                return True

            def transaction_summary(self, *, product_type=None):
                self.product_type = product_type
                return {"fee_tier": {"pricing_tier": "Intro", "taker_fee_rate": "0.0060", "maker_fee_rate": "0.0040"}}

        client = FakeClient()
        fee = resolve_spot_fee_tier(client, fallback_taker_bps=60.0)
        self.assertEqual(client.product_type, "SPOT")
        self.assertEqual(fee.taker_bps, 60.0)
        self.assertEqual(fee.maker_bps, 40.0)
        self.assertEqual(fee.source, "coinbase_transaction_summary_spot")

    def test_resolve_spot_fee_tier_falls_back_on_error(self) -> None:
        class FakeClient:
            def has_auth(self) -> bool:
                return True

            def transaction_summary(self, *, product_type=None):
                raise CoinbaseAdvancedClientError("boom")

        fee = resolve_spot_fee_tier(FakeClient(), fallback_taker_bps=60.0)
        self.assertEqual(fee.taker_bps, 60.0)
        self.assertEqual(fee.source, "fallback_transaction_summary_error")


if __name__ == "__main__":
    unittest.main()
