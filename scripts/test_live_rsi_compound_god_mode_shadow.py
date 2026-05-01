#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import live_rsi_compound_god_mode_shadow as runner
from coinbase_advanced_client import CoinbaseAdvancedClientError


class _RetryClient:
    def __init__(self, failures: int, payload: dict | None = None) -> None:
        self.failures = failures
        self.calls = 0
        self.payload = payload or {"candles": [{"start": "1"}]}

    def market_candles(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise CoinbaseAdvancedClientError("HTTP 429 /api/v3/brokerage/market/products/IOTX-USD/candles: {}")
        return self.payload


class _HardFailClient:
    def market_candles(self, *args, **kwargs):
        raise CoinbaseAdvancedClientError("HTTP 500 /api/v3/brokerage/market/products/IOTX-USD/candles: {}")


class RSICompoundRateLimitTests(unittest.TestCase):
    def test_safe_market_candles_retries_429_then_succeeds(self) -> None:
        client = _RetryClient(failures=2)

        resp = runner.safe_market_candles(client, "IOTX-USD", start=1, end=2, granularity="FIVE_MINUTE", retries=4, base_delay=0.01)

        self.assertEqual(resp, {"candles": [{"start": "1"}]})
        self.assertEqual(client.calls, 3)

    def test_safe_market_candles_returns_none_after_exhausted_429s(self) -> None:
        client = _RetryClient(failures=5)

        resp = runner.safe_market_candles(client, "IOTX-USD", start=1, end=2, granularity="FIVE_MINUTE", retries=3, base_delay=0.01)

        self.assertIsNone(resp)
        self.assertEqual(client.calls, 3)

    def test_safe_market_candles_raises_non_429_errors(self) -> None:
        client = _HardFailClient()

        with self.assertRaises(CoinbaseAdvancedClientError):
            runner.safe_market_candles(client, "IOTX-USD", start=1, end=2, granularity="FIVE_MINUTE", retries=3, base_delay=0.01)


if __name__ == "__main__":
    unittest.main()
