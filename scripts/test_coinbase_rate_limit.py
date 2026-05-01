#!/usr/bin/env python3
import unittest

import coinbase_rate_limit as rate_limit
from coinbase_advanced_client import CoinbaseAdvancedClientError


class RetryThenSuccessClient:
    def __init__(self):
        self.calls = 0

    def market_candles(self, *args, **kwargs):
        self.calls += 1
        if self.calls < 3:
            raise CoinbaseAdvancedClientError("HTTP 429 /api/v3/brokerage/market/products/BTC-USD/candles: {}")
        return {"candles": [{"start": "10"}, {"start": "20"}]}


class AlwaysRateLimitedClient:
    def market_candles(self, *args, **kwargs):
        raise CoinbaseAdvancedClientError("HTTP 429 /api/v3/brokerage/market/products/RAVE-USD/candles: {}")


class NonRateLimitedClient:
    def market_candles(self, *args, **kwargs):
        raise CoinbaseAdvancedClientError("HTTP 500 /api/v3/brokerage/market/products/RAVE-USD/candles: {}")


class CoinbaseRateLimitTests(unittest.TestCase):
    def test_safe_market_candles_retries_then_succeeds(self):
        client = RetryThenSuccessClient()

        resp = rate_limit.safe_market_candles(
            client,
            "BTC-USD",
            start=1,
            end=2,
            granularity="ONE_MINUTE",
            retries=4,
            base_delay=0.01,
        )

        self.assertEqual(client.calls, 3)
        self.assertEqual(len(resp["candles"]), 2)

    def test_fetch_live_candles_logs_skip_after_exhausted_429(self):
        events = []

        candles = rate_limit.fetch_live_candles(
            AlwaysRateLimitedClient(),
            "RAVE-USD",
            start=100,
            end=200,
            granularity="FIVE_MINUTE",
            filter_after=150,
            event_logger=events.append,
            retries=2,
            base_delay=0.01,
        )

        self.assertEqual(candles, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "rate_limit_skip_live_fetch")
        self.assertEqual(events[0]["product"], "RAVE-USD")

    def test_fetch_candles_chunked_raises_non_429(self):
        with self.assertRaises(CoinbaseAdvancedClientError):
            rate_limit.safe_market_candles(
                NonRateLimitedClient(),
                "RAVE-USD",
                start=1,
                end=2,
                granularity="FIVE_MINUTE",
                retries=2,
                base_delay=0.01,
            )


if __name__ == "__main__":
    unittest.main()
