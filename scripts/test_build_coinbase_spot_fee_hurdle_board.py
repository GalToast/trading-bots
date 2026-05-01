#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_coinbase_spot_fee_hurdle_board import build_row


class CoinbaseSpotFeeHurdleBoardTests(unittest.TestCase):
    def test_build_row_requires_fee_spread_and_profit_buffer(self) -> None:
        row = build_row(
            {
                "product_id": "HOT-USD",
                "quote_currency": "USD",
                "live_route_state": "ready_direct_usd_or_stable",
                "pulse_state": "hot_momentum",
                "pulse_score": 10,
                "ret_15m_pct": 3.5,
                "ret_60m_pct": 4.0,
                "ret_4h_pct": 5.0,
                "spread_bps": 10,
                "median_range_60m_pct": 0.4,
                "p90_range_60m_pct": 0.8,
                "candles": 50,
            },
            taker_fee_bps=120,
            profit_buffer_pct=0.75,
            max_spread_bps=75,
        )
        self.assertEqual(row["all_in_hurdle_pct"], 3.25)
        self.assertEqual(row["hurdle_state"], "clears_fast_hurdle")

    def test_build_row_blocks_wide_spread(self) -> None:
        row = build_row(
            {
                "product_id": "WIDE-USD",
                "quote_currency": "USD",
                "live_route_state": "ready_direct_usd_or_stable",
                "ret_15m_pct": 10,
                "ret_60m_pct": 10,
                "ret_4h_pct": 10,
                "spread_bps": 100,
            },
            taker_fee_bps=120,
            profit_buffer_pct=0.75,
            max_spread_bps=75,
        )
        self.assertEqual(row["hurdle_state"], "spread_blocked")


if __name__ == "__main__":
    unittest.main()
