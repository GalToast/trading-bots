#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_coinbase_spot_machinegun_strategy_board import choose_playbook, is_broad_toxic_exception, radar_hurdle_row


class CoinbaseSpotMachinegunStrategyBoardTests(unittest.TestCase):
    def test_fast_hurdle_maps_to_breakout_trailer(self) -> None:
        playbook = choose_playbook({"hurdle_state": "clears_fast_hurdle", "suggested_trail_giveback_pct": 0.5})
        self.assertEqual(playbook["playbook"], "fee_hurdle_breakout_trailer")
        self.assertIn("trail", playbook["exit_rule"])

    def test_pullback_maps_to_reload_not_chase(self) -> None:
        playbook = choose_playbook({"hurdle_state": "pullback_reentry_watch", "suggested_trail_giveback_pct": 0.5})
        self.assertEqual(playbook["playbook"], "rubber_band_reload")
        self.assertIn("do not buy the red candle", playbook["entry_rule"])

    def test_radar_hurdle_row_requires_fee_cleared_live_move(self) -> None:
        blocked = radar_hurdle_row(
            {
                "product_id": "KAT-USD",
                "quote_currency": "USD",
                "live_route_state": "ready_direct_usd_or_stable",
                "signal_state": "live_hot",
                "spread_bps": 10.0,
                "move_last_bps": 250.0,
            }
        )
        self.assertIsNone(blocked)

        admitted = radar_hurdle_row(
            {
                "product_id": "KAT-USD",
                "quote_currency": "USD",
                "live_route_state": "ready_direct_usd_or_stable",
                "signal_state": "live_hot",
                "spread_bps": 10.0,
                "move_last_bps": 340.0,
                "velocity_score": 100.0,
                "samples": 3,
            }
        )
        self.assertIsNotNone(admitted)
        assert admitted is not None
        self.assertEqual(admitted["hurdle_state"], "radar_clears_live_hurdle")
        self.assertEqual(admitted["source"], "live_radar")

    def test_radar_hurdle_maps_to_live_breakout(self) -> None:
        playbook = choose_playbook({"hurdle_state": "radar_clears_live_hurdle", "suggested_trail_giveback_pct": 0.5})
        self.assertEqual(playbook["playbook"], "radar_live_breakout_trailer")
        self.assertIn("best-bid radar", playbook["entry_rule"])

    def test_broad_toxic_exception_requires_direct_fee_cleared_strength(self) -> None:
        row = {
            "hurdle_state": "clears_hour_hurdle",
            "live_route_state": "ready_direct_usd_or_stable",
            "ret_15m_pct": 1.5,
            "edge_over_hurdle_pct": 1.2,
            "spread_bps": 20.0,
        }
        self.assertTrue(is_broad_toxic_exception(row))

        red_short_term = dict(row, ret_15m_pct=-0.1)
        self.assertFalse(is_broad_toxic_exception(red_short_term))

        thin_edge = dict(row, edge_over_hurdle_pct=0.5)
        self.assertFalse(is_broad_toxic_exception(thin_edge))


if __name__ == "__main__":
    unittest.main()
