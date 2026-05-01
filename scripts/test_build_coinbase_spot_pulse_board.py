#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_coinbase_spot_pulse_board as board


class CoinbaseSpotPulseBoardTests(unittest.TestCase):
    def test_select_products_requires_live_tradable_spot_metadata(self) -> None:
        good = {
            "product_id": "DEGEN-USD",
            "base_currency_id": "DEGEN",
            "quote_currency_id": "USD",
            "product_type": "SPOT",
            "status": "online",
            "quote_min_size": "1",
            "base_min_size": "1",
            "approximate_quote_24h_volume": "100000",
        }
        blocked = {
            **good,
            "product_id": "HALT-USD",
            "trading_disabled": True,
        }
        selected = board.select_products(
            [good, blocked],
            top_products=10,
            top_per_quote=10,
            min_quote_volume_usd=50_000.0,
            quote_currencies={"USD"},
            all_spot_quotes=False,
        )
        self.assertEqual([row["product_id"] for row in selected], ["DEGEN-USD"])
        self.assertEqual(board.product_live_blockers(good), [])
        self.assertIn("trading_disabled", board.product_live_blockers(blocked))

    def test_score_marks_non_usd_route_as_conversion_gated(self) -> None:
        product = {
            "product_id": "SOL-BTC",
            "base_currency_id": "SOL",
            "quote_currency_id": "BTC",
            "product_type": "SPOT",
            "status": "online",
            "quote_min_size": "0.00001",
            "base_min_size": "0.01",
            "approximate_quote_24h_volume": "10",
        }
        candles = [
            {"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0 + index * 0.001, "volume": 1.0}
            for index in range(70)
        ]
        row = board.score_product(
            product=product,
            book={"bid": 1.068, "ask": 1.069, "mid": 1.0685, "spread_bps": 9.36},
            candles=candles,
        )
        self.assertTrue(row["live_tradable"])
        self.assertEqual(row["quote_currency"], "BTC")
        self.assertEqual(row["live_route_state"], "requires_quote_inventory_or_conversion_costing")

    def test_candle_cache_reuses_fresh_entries(self) -> None:
        cache: dict[str, object] = {"version": 1, "entries": {}}
        candles = [{"start": 1.0, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 10.0}]
        board.update_candle_cache(
            cache,
            "DEGEN-USD",
            hours=3,
            granularity="ONE_MINUTE",
            candles=candles,
            now_epoch=1000.0,
        )
        cached, age, source = board.cached_candles(
            cache,
            "DEGEN-USD",
            hours=3,
            granularity="ONE_MINUTE",
            now_epoch=1100.0,
            ttl_seconds=300.0,
        )
        self.assertEqual(cached, candles)
        self.assertEqual(age, 100.0)
        self.assertEqual(source, "cache_hit")

        _cached, stale_age, stale_source = board.cached_candles(
            cache,
            "DEGEN-USD",
            hours=3,
            granularity="ONE_MINUTE",
            now_epoch=1500.0,
            ttl_seconds=300.0,
        )
        self.assertEqual(stale_age, 500.0)
        self.assertEqual(stale_source, "cache_stale")


if __name__ == "__main__":
    unittest.main()
