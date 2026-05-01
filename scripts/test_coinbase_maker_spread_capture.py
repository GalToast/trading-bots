#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_coinbase_maker_spread_capture import current_round_trip_economics, simulate_maker_ping_pong


class CoinbaseMakerSpreadCaptureTests(unittest.TestCase):
    def test_current_round_trip_economics_are_negative_when_fees_exceed_spread(self) -> None:
        row = current_round_trip_economics(bid=100.0, ask=100.01, quote_per_side=24.0, maker_fee_bps=40.0)
        self.assertLess(row["net_spread_capture_usd"], 0.0)

    def test_ping_pong_proxy_blocks_same_candle_round_trip(self) -> None:
        candles = [
            {"start": 60, "low": 99.99, "high": 100.01, "open": 100.0, "close": 100.0},
            {"start": 120, "low": 99.98, "high": 100.02, "open": 100.0, "close": 100.0},
        ]
        result = simulate_maker_ping_pong(
            candles,
            bid=99.99,
            ask=100.01,
            starting_cash=48.0,
            quote_per_side=24.0,
            maker_fee_bps=0.0,
        )
        self.assertEqual(result["proxy_round_trips"], 1)
        self.assertGreaterEqual(result["proxy_median_hold_minutes"], 1.0)

    def test_ping_pong_proxy_respects_cash_limit(self) -> None:
        candles = [
            {"start": 60, "low": 99.99, "high": 100.0, "open": 100.0, "close": 100.0},
            {"start": 120, "low": 99.99, "high": 100.0, "open": 100.0, "close": 100.0},
            {"start": 180, "low": 99.99, "high": 100.0, "open": 100.0, "close": 100.0},
        ]
        result = simulate_maker_ping_pong(
            candles,
            bid=99.99,
            ask=100.01,
            starting_cash=48.0,
            quote_per_side=24.0,
            maker_fee_bps=0.0,
        )
        self.assertEqual(result["proxy_open_inventory"], 2)


if __name__ == "__main__":
    unittest.main()
