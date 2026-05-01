#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from live_spread_gobblin_mm_v2 import EXECUTION_MODEL, SpreadGobblinMMV2


class SpreadGobblinMMV2Tests(unittest.TestCase):
    def test_entry_and_exit_require_later_completed_candles(self) -> None:
        engine = SpreadGobblinMMV2(starting_cash=324.0, products=["MOG-USD"])

        place_events = engine.process_book("MOG-USD", bid=1.0, ask=1.08, now_ts=120.0)
        self.assertEqual([event["action"] for event in place_events], ["place_entry_order"])
        self.assertEqual(engine.snapshot()["open_positions"], 0)
        self.assertEqual(engine.snapshot()["pending_entry_count"], 1)

        same_minute_events = engine.process_book(
            "MOG-USD",
            bid=1.0,
            ask=1.08,
            now_ts=150.0,
            completed_candle={"start": 120, "open": 1.0, "high": 1.08, "low": 0.99, "close": 1.04, "volume": 10},
        )
        self.assertEqual(same_minute_events, [])
        self.assertEqual(engine.snapshot()["open_positions"], 0)

        fill_entry_events = engine.process_book(
            "MOG-USD",
            bid=1.01,
            ask=1.07,
            now_ts=181.0,
            completed_candle={"start": 180, "open": 1.02, "high": 1.07, "low": 0.98, "close": 1.05, "volume": 12},
        )
        self.assertEqual([event["action"] for event in fill_entry_events], ["fill_entry_order", "place_exit_order"])
        self.assertEqual(engine.snapshot()["open_positions"], 1)
        self.assertEqual(engine.snapshot()["pending_exit_count"], 1)
        self.assertGreater(engine.total_volume, 0.0)
        self.assertGreater(engine.total_fees, 0.0)

        fill_exit_events = engine.process_book(
            "MOG-USD",
            bid=1.02,
            ask=1.05,
            now_ts=241.0,
            completed_candle={"start": 240, "open": 1.03, "high": 1.08, "low": 1.01, "close": 1.06, "volume": 9},
        )
        self.assertEqual([event["action"] for event in fill_exit_events], ["fill_exit_order"])
        self.assertEqual(engine.snapshot()["open_positions"], 0)
        self.assertEqual(engine.realized_closes, 1)
        self.assertGreater(engine.realized_net, 0.0)

    def test_restore_from_structured_state_uses_saved_volume_and_pending_orders(self) -> None:
        payload = {
            "engine": {
                "execution_model": EXECUTION_MODEL,
                "products": ["MOG-USD"],
                "starting_cash": 324.0,
                "cash": 280.0,
                "realized_net": 12.5,
                "realized_closes": 4,
                "realized_wins": 3,
                "realized_losses": 1,
                "total_volume": 9876.5,
                "total_fees": 18.2,
                "positions": [
                    {
                        "product_id": "MOG-USD",
                        "entry_price": 1.0,
                        "units": 100.0,
                        "quote_size": 100.0,
                        "entry_fee": 0.4,
                        "fee_rate": 0.004,
                        "opened_at": "2026-04-12T00:00:00Z",
                        "last_bid": 1.0,
                        "last_ask": 1.08,
                    }
                ],
                "pending_entries": [
                    {
                        "product_id": "MOG-USD",
                        "limit_price": 1.0,
                        "quote_size": 100.0,
                        "fee_rate": 0.004,
                        "placed_at": 120.0,
                        "eligible_after_candle_start": 120,
                        "expires_at": 300.0,
                        "spread_pct": 8.0,
                    }
                ],
                "pending_exits": [
                    {
                        "product_id": "MOG-USD",
                        "limit_price": 1.02,
                        "fee_rate": 0.004,
                        "placed_at": 181.0,
                        "eligible_after_candle_start": 180,
                        "target_multiple": 1.02,
                    }
                ],
                "market_state": [
                    {
                        "product_id": "MOG-USD",
                        "last_candle_start": 180,
                        "last_candle_open": 1.0,
                        "last_candle_high": 1.07,
                        "last_candle_low": 0.99,
                        "last_candle_close": 1.05,
                        "last_candle_volume": 11.0,
                        "last_candle_poll_minute": 180,
                    }
                ],
            }
        }

        engine = SpreadGobblinMMV2.from_state(payload, default_products=["MOG-USD"])
        self.assertEqual(engine.execution_model, EXECUTION_MODEL)
        self.assertEqual(engine.total_volume, 9876.5)
        self.assertEqual(engine.cash, 280.0)
        self.assertIn("MOG-USD", engine.positions)
        self.assertIn("MOG-USD", engine.pending_entries)
        self.assertIn("MOG-USD", engine.pending_exits)
        self.assertEqual(engine.market_state["MOG-USD"]["last_candle_start"], 180)

    def test_legacy_state_resets_contaminated_snapshot(self) -> None:
        payload = {
            "engine": {
                "products": ["MOG-USD"],
                "starting_cash": 324.0,
                "cash": 537.7,
                "realized_net": 213.8,
                "realized_closes": 57,
                "total_volume": 8347.1,
            }
        }

        engine = SpreadGobblinMMV2.from_state(payload, default_products=["MOG-USD"])
        self.assertEqual(engine.execution_model, EXECUTION_MODEL)
        self.assertEqual(engine.cash, 324.0)
        self.assertEqual(engine.realized_net, 0.0)
        self.assertEqual(engine.realized_closes, 0)
        self.assertEqual(engine.total_volume, 0.0)
        self.assertIsNotNone(engine.reset_notice)
        self.assertEqual(engine.reset_notice["reason"], "execution_model_reset")


if __name__ == "__main__":
    unittest.main()
