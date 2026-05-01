#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_kraken_sideaware_staged_entry_exit as staged


class KrakenSideawareStagedEntryExitTests(unittest.TestCase):
    def test_net_roundtrip_bps_charges_two_maker_fees(self) -> None:
        self.assertAlmostEqual(staged.net_roundtrip_bps(100.0, 102.0, 25.0), 150.0)

    def test_net_roundtrip_bps_handles_missing_prices(self) -> None:
        self.assertEqual(staged.net_roundtrip_bps(0.0, 102.0, 25.0), 0.0)

    def test_clears_exit_net_floor_requires_profit_after_fees(self) -> None:
        clears, net_bps = staged.clears_exit_net_floor(100.0, 101.0, 25.0, 40.0)
        self.assertTrue(clears)
        self.assertAlmostEqual(net_bps, 50.0)

        clears, net_bps = staged.clears_exit_net_floor(100.0, 100.4, 25.0, 0.0)
        self.assertFalse(clears)
        self.assertAlmostEqual(net_bps, -10.0)

    def test_minimum_exit_price_includes_two_maker_fees_and_target(self) -> None:
        self.assertAlmostEqual(staged.minimum_exit_price(100.0, 25.0, 10.0), 100.6)

    def test_price_above_ask_bps_only_counts_positive_distance(self) -> None:
        self.assertAlmostEqual(staged.price_above_ask_bps(101.0, 100.0), 100.0)
        self.assertEqual(staged.price_above_ask_bps(99.0, 100.0), 0.0)

    def test_is_fill_like_accepts_hard_cross_only_from_fill_set(self) -> None:
        self.assertTrue(staged.is_fill_like("hard_cross_fill_proxy"))
        self.assertFalse(staged.is_fill_like("unfilled_timeout"))

    def test_load_entry_products_from_radar_filters_and_sorts(self) -> None:
        args = SimpleNamespace(
            entry_product_source="radar",
            radar_path=Path(__file__).with_name("_missing_radar.json"),
            quotes="USD",
            min_entry_spread_bps=50.0,
            max_radar_spread_bps=250.0,
            top_products=2,
        )
        payload = {
            "rows": [
                {"product_id": "LOW-USD", "quote_currency": "USD", "spread_bps": 10, "velocity_score": 999, "best_short_bps": 999},
                {"product_id": "HOT-USD", "quote_currency": "USD", "spread_bps": 120, "velocity_score": 5, "best_short_bps": 20},
                {"product_id": "FAST-USD", "quote_currency": "USD", "spread_bps": 100, "velocity_score": 10, "best_short_bps": 0},
                {"product_id": "WIDE-USD", "quote_currency": "USD", "spread_bps": 500, "velocity_score": 100, "best_short_bps": 100},
                {"product_id": "ETH-ETH", "quote_currency": "ETH", "spread_bps": 100, "velocity_score": 50, "best_short_bps": 50},
            ]
        }
        original = staged.load_json
        try:
            staged.load_json = lambda _path: payload
            products, rows = staged.load_entry_products(args)
        finally:
            staged.load_json = original

        self.assertEqual(products, ["FAST-USD", "HOT-USD"])
        self.assertEqual([row["product_id"] for row in rows], ["FAST-USD", "HOT-USD"])


if __name__ == "__main__":
    unittest.main()
