#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from live_lattice_warp_grinder_strict_shadow import EXECUTION_MODEL, StrictLatticeWarpShadow


class StrictLatticeWarpShadowTests(unittest.TestCase):
    def test_entry_and_exit_require_later_completed_candles(self) -> None:
        engine = StrictLatticeWarpShadow(starting_cash=324.0, products=["BAL-USD"])

        no_signal_events = engine.process_book(
            "BAL-USD",
            bid=1.0,
            ask=1.02,
            warp_active=False,
            signal_velocity=0.0,
            kraken_price=71692.0,
            now_ts=120.0,
        )
        self.assertEqual(no_signal_events, [])
        self.assertEqual(engine.snapshot()["pending_entry_count"], 0)

        place_events = engine.process_book(
            "BAL-USD",
            bid=1.0,
            ask=1.02,
            warp_active=True,
            signal_velocity=6.0,
            kraken_price=71698.0,
            now_ts=120.0,
        )
        self.assertEqual([event["action"] for event in place_events], ["place_entry_order"])
        self.assertEqual(engine.snapshot()["open_positions"], 0)
        self.assertEqual(engine.snapshot()["pending_entry_count"], 1)

        same_minute_events = engine.process_book(
            "BAL-USD",
            bid=1.0,
            ask=1.02,
            warp_active=False,
            signal_velocity=0.0,
            kraken_price=71698.0,
            now_ts=150.0,
            completed_candle={"start": 120, "open": 1.0, "high": 1.02, "low": 0.99, "close": 1.01, "volume": 8},
        )
        self.assertEqual(same_minute_events, [])
        self.assertEqual(engine.snapshot()["open_positions"], 0)

        fill_entry_events = engine.process_book(
            "BAL-USD",
            bid=1.005,
            ask=1.018,
            warp_active=False,
            signal_velocity=0.0,
            kraken_price=71698.0,
            now_ts=181.0,
            completed_candle={"start": 180, "open": 1.01, "high": 1.02, "low": 0.995, "close": 1.015, "volume": 12},
        )
        self.assertEqual([event["action"] for event in fill_entry_events], ["fill_entry_order", "place_exit_order"])
        self.assertEqual(engine.snapshot()["open_positions"], 1)
        self.assertEqual(engine.snapshot()["pending_exit_count"], 1)

        fill_exit_events = engine.process_book(
            "BAL-USD",
            bid=1.004,
            ask=1.012,
            warp_active=False,
            signal_velocity=0.0,
            kraken_price=71690.0,
            now_ts=241.0,
            completed_candle={"start": 240, "open": 1.006, "high": 1.01, "low": 1.002, "close": 1.008, "volume": 9},
        )
        self.assertEqual([event["action"] for event in fill_exit_events], ["fill_exit_order"])
        self.assertEqual(engine.snapshot()["open_positions"], 0)
        self.assertEqual(engine.realized_closes, 1)
        self.assertGreater(engine.realized_net, 0.0)

    def test_legacy_state_resets_optimistic_snapshot(self) -> None:
        payload = {
            "engine": {
                "products": ["BAL-USD"],
                "starting_cash": 324.0,
                "cash": 410.0,
                "realized_net": 86.0,
                "closes": 8,
                "vol": 27874.0,
            }
        }

        engine = StrictLatticeWarpShadow.from_state(payload, default_products=["BAL-USD"])
        self.assertEqual(engine.execution_model, EXECUTION_MODEL)
        self.assertEqual(engine.cash, 324.0)
        self.assertEqual(engine.realized_net, 0.0)
        self.assertEqual(engine.realized_closes, 0)
        self.assertEqual(engine.total_volume, 0.0)
        self.assertIsNotNone(engine.reset_notice)
        self.assertEqual(engine.reset_notice["reason"], "execution_model_reset")


if __name__ == "__main__":
    unittest.main()
